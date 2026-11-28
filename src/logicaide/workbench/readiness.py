"""Read-only readiness diagnostics and optional heritage index auto-rebuild for LogicAIDE."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB
from logicaide.heritage.chunker import parse_heritage_document
from logicaide.heritage.rag_index import (
    DEFAULT_INDEX_DB, _collect_source_files, _resolve_repo_root, build_heritage_index,
)
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH
from logicaide.telemetry.rollups import summarize_telemetry_lake

_OS_SYSTEM_LINK_PREFIXES = frozenset({"/tmp", "/var", "/var/tmp"})
_CATALOG_COUNT_TABLES = (
    "vehicles", "sessions", "bookmarks", "tuning_issues",
    "parquet_chunks", "stimulus_fixtures", "candidate_builds",
)
_HERITAGE_REQUIRED_TABLES = frozenset(
    {"heritage_parents", "heritage_children", "heritage_fts", "heritage_index_meta"}
)


def _validate_no_symlink(raw_path: Path | str, label: str) -> Path:
    """Reject paths that are symlinks or traverse any existing symlinked parent."""
    path = Path(raw_path)
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {raw_path}")
    cur, parts = (Path(path.anchor), path.parts[1:]) if path.is_absolute() else (
        (Path.cwd(), path.parts) if ".." in path.parts else (Path("."), path.parts)
    )
    for part in parts:
        if part in ("", "."):
            continue
        cur = cur.parent if part == ".." else (cur / part)
        if str(cur) not in _OS_SYSTEM_LINK_PREFIXES and cur.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink: {raw_path}")
    return path


def _check_sqlite_header(
    path: Path, required_tables: frozenset[str] | tuple[str, ...]
) -> tuple[sqlite3.Connection | None, str | None, str | None]:
    """Open SQLite read-only and verify file, quick_check(1), and required tables."""
    if not path.is_file():
        return None, None, f"Path is not a regular file: {path}"
    if path.stat().st_size == 0:
        return None, None, f"SQLite database is empty (0 bytes): {path}"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        qc = conn.execute("PRAGMA quick_check(1)").fetchone()
        qc_val = str(qc[0]) if qc is not None else "failed"
        if qc_val != "ok":
            conn.close()
            return None, qc_val, f"SQLite quick_check failed: {qc_val}"
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        if missing := sorted(set(required_tables) - tables):
            conn.close()
            return None, qc_val, f"Missing required tables: {', '.join(missing)}"
        return conn, qc_val, None
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        return None, None, f"SQLite error: {exc}"


def _inspect_catalog_sqlite(db_path: Path) -> dict[str, Any]:
    """Inspect the SQLite catalog database read-only."""
    zero_counts = {table: 0 for table in _CATALOG_COUNT_TABLES}
    base: dict[str, Any] = {
        "path": str(db_path), "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.is_file() else 0,
        "quick_check": None, "counts": zero_counts, "total_records": 0, "error": None,
    }
    if not db_path.exists():
        return {"status": "missing", **base}
    conn, qc_val, err = _check_sqlite_header(db_path, _CATALOG_COUNT_TABLES)
    if err is not None or conn is None:
        return {**base, "status": "error", "quick_check": qc_val, "error": err or "Unable to open catalog SQLite"}
    try:
        counts = {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in _CATALOG_COUNT_TABLES}
    except sqlite3.Error as exc:
        return {**base, "status": "error", "quick_check": qc_val, "error": f"SQLite query error: {exc}"}
    finally:
        conn.close()
    total = sum(counts.values())
    return {**base, "status": "ready" if total > 0 else "empty", "quick_check": qc_val, "counts": counts, "total_records": total}


def _inspect_telemetry_lake(lake_dir: Path) -> dict[str, Any]:
    """Inspect the Hive-partitioned Parquet telemetry lake read-only."""
    base: dict[str, Any] = {
        "lake_dir": str(lake_dir), "exists": lake_dir.is_dir(), "chunk_count": 0, "session_count": 0,
        "total_rows": 0, "quarantined_rows": 0, "active_rows": 0, "rollup_bin_count": 0,
        "run_kinds": [], "car_ids": [], "error": None,
    }
    if lake_dir.exists() and not lake_dir.is_dir():
        return {**base, "status": "error", "error": f"Telemetry lake path is not a directory: {lake_dir}"}
    try:
        summary = summarize_telemetry_lake(lake_dir)
    except Exception as exc:
        return {**base, "status": "error", "error": str(exc)}
    chunk_count = int(summary.get("chunk_count") or 0)
    return {
        "status": "ready" if chunk_count > 0 else "empty",
        "lake_dir": str(summary.get("lake_dir", lake_dir)), "exists": lake_dir.is_dir(),
        "chunk_count": chunk_count, "session_count": int(summary.get("session_count") or 0),
        "total_rows": int(summary.get("total_rows") or 0), "quarantined_rows": int(summary.get("quarantined_rows") or 0),
        "active_rows": int(summary.get("active_rows") or 0), "rollup_bin_count": int(summary.get("rollup_bin_count") or 0),
        "run_kinds": list(summary.get("run_kinds") or []), "car_ids": list(summary.get("car_ids") or []), "error": None,
    }


def _resolve_manifest_file(cat_path: Path, rom_id: str, ref: Any) -> Path | None:
    """Resolve and symlink/anchor-check a partitioned rom_manifest.json candidate path."""
    if not isinstance(rom_id, str) or not (rom_clean := rom_id.strip()):
        raise ValueError(f"Invalid rom_id: {rom_id!r}")
    rom_path = Path(rom_clean)
    if "\x00" in rom_clean or rom_path.is_absolute() or ".." in rom_path.parts or "/" in rom_clean or "\\" in rom_clean or rom_clean == ".":
        raise ValueError(f"Unsafe rom_id: {rom_id!r}")
    raw_anchor = cat_path.parent.parent.parent.resolve()
    anchor = cat_path.parent.resolve() if raw_anchor == raw_anchor.parent else raw_anchor
    candidates: list[Path] = []
    if isinstance(ref, str) and (ref_str := ref.strip()):
        ref_path = Path(ref_str)
        if "\x00" in ref_str or ref_str.startswith(("/", "\\")) or "\\" in ref_str or ref_path.is_absolute() or ".." in ref_path.parts:
            raise ValueError(f"Unsafe manifest_ref for {rom_clean!r}: {ref_str!r}")
        candidates.extend([cat_path.parent / ref_str, cat_path.parent.parent / ref_str, cat_path.parent.parent.parent / ref_str])
    candidates.append(cat_path.parent / rom_clean / "rom_manifest.json")
    for cand in candidates:
        if cand.is_symlink() or cand.exists():
            _validate_no_symlink(cand, f"manifest:{rom_clean}")
            if not cand.resolve().is_relative_to(anchor):
                raise ValueError(f"Manifest path escapes anchor for {rom_clean!r}: {cand}")
            if cand.is_file():
                return cand
    return None


def _inspect_rom_manifests(rom_catalog_path: Path, tags_path: Path) -> dict[str, Any]:
    """Inspect partitioned ROM manifest catalog, per-ROM files, and ctags presence."""
    base: dict[str, Any] = {
        "catalog_path": str(rom_catalog_path), "tags_path": str(tags_path),
        "tags_present": tags_path.is_file(), "manifest_count": 0, "rom_ids": [],
        "verified_ram_count": 0, "calibration_table_count": 0, "error": None,
    }
    if not rom_catalog_path.exists():
        return {"status": "missing", **base}
    if not rom_catalog_path.is_file():
        return {**base, "status": "error", "error": f"ROM catalog path is not a file: {rom_catalog_path}"}
    try:
        catalog = json.loads(rom_catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {**base, "status": "error", "error": f"Failed to parse ROM catalog: {exc}"}
    entries = catalog.get("manifests") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        return {**base, "status": "error", "error": "ROM catalog missing 'manifests' list"}
    if not entries:
        return {"status": "empty", **base}

    rom_ids: list[str] = []
    ram_total = cal_total = 0
    for entry in entries:
        if not isinstance(entry, dict) or not (rom_id := str(entry.get("rom_id") or "").strip()):
            return {**base, "status": "error", "error": "ROM catalog entry missing valid 'rom_id'"}
        try:
            mpath = _resolve_manifest_file(rom_catalog_path, rom_id, entry.get("manifest_ref"))
            if mpath is None:
                return {**base, "status": "error", "error": f"Missing manifest file for {rom_id!r}"}
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return {**base, "status": "error", "error": f"Invalid manifest for {rom_id!r}: {exc}"}
        if not isinstance(manifest, dict):
            return {**base, "status": "error", "error": f"Manifest for {rom_id!r} must be a JSON object"}
        rom_ids.append(rom_id)
        summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
        watchlist, cals = manifest.get("ram_watchlist"), manifest.get("calibration_tables")
        ram_total += (
            sum(1 for i in watchlist if isinstance(i, dict) and i.get("source_verified", True) is True)
            if isinstance(watchlist, list) else int(summary.get("verified_ram_count") or 0)
        )
        cal_total += len(cals) if isinstance(cals, list) else int(summary.get("calibration_table_count") or 0)
    return {
        **base, "status": "ready", "manifest_count": len(rom_ids), "rom_ids": rom_ids,
        "verified_ram_count": ram_total, "calibration_table_count": cal_total,
    }


def _parse_iso_epoch(raw_ts: str | None) -> float | None:
    if not raw_ts:
        return None
    try:
        dt = datetime.fromisoformat(raw_ts)
    except ValueError:
        return None
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)).timestamp()


def _inspect_heritage_index(
    heritage_db_path: Path, knowledge_dir: Path, wiki_dir: Path
) -> dict[str, Any]:
    """Inspect heritage SQLite RAG index and detect source/mtime/doc_id staleness."""
    md_files = _collect_source_files(knowledge_dir, wiki_dir)
    repo_root = _resolve_repo_root(knowledge_dir, wiki_dir)
    source_stats: list[tuple[Path, int, float]] = []
    source_doc_ids: set[str] = set()
    parse_error: str | None = None
    for md_path in md_files:
        try:
            st = md_path.stat()
            parsed = parse_heritage_document(md_path, repo_root=repo_root)
        except Exception as exc:
            parse_error = f"Failed to parse source markdown {md_path}: {exc}"
            break
        source_stats.append((md_path, st.st_mtime_ns, st.st_mtime))
        if parsed and parsed.get("doc_id"):
            source_doc_ids.add(str(parsed["doc_id"]))

    base: dict[str, Any] = {
        "db_path": str(heritage_db_path), "exists": heritage_db_path.exists(),
        "size_bytes": heritage_db_path.stat().st_size if heritage_db_path.is_file() else 0,
        "quick_check": None, "source_file_count": len(md_files), "source_doc_ids": sorted(source_doc_ids),
        "indexed_doc_ids": [], "parent_count": 0, "child_count": 0,
        "built_at_utc": None, "vector_backend": None, "stale_reasons": [], "error": parse_error,
    }
    if parse_error is not None:
        return {"status": "error", **base}
    if not heritage_db_path.exists():
        return {"status": "missing", **base}
    conn, qc_val, err = _check_sqlite_header(heritage_db_path, _HERITAGE_REQUIRED_TABLES)
    if err is not None or conn is None:
        return {**base, "status": "error", "quick_check": qc_val, "error": err}
    try:
        parent_count = int(conn.execute("SELECT COUNT(*) FROM heritage_parents").fetchone()[0])
        child_count = int(conn.execute("SELECT COUNT(*) FROM heritage_children").fetchone()[0])
        indexed_doc_ids = {str(r[0]) for r in conn.execute("SELECT DISTINCT doc_id FROM heritage_parents")}
        meta = {str(r[0]): str(r[1]) for r in conn.execute("SELECT key, value FROM heritage_index_meta")}
    except sqlite3.Error as exc:
        return {**base, "status": "error", "quick_check": qc_val, "error": f"SQLite query error: {exc}"}
    finally:
        conn.close()

    built_at_utc = meta.get("built_at_utc")
    built_at_epoch = _parse_iso_epoch(built_at_utc)
    db_mtime_ns = heritage_db_path.stat().st_mtime_ns
    stale_reasons: list[str] = []
    if len(md_files) > 0 and parent_count == 0:
        stale_reasons.append("index_empty_with_sources")
    if indexed_doc_ids != source_doc_ids:
        if missing_docs := sorted(source_doc_ids - indexed_doc_ids):
            stale_reasons.append(f"missing_doc_ids:{','.join(missing_docs)}")
        if extra_docs := sorted(indexed_doc_ids - source_doc_ids):
            stale_reasons.append(f"extra_doc_ids:{','.join(extra_docs)}")
    for md_path, mtime_ns, mtime in source_stats:
        if mtime_ns > db_mtime_ns or (built_at_epoch is not None and mtime > built_at_epoch):
            stale_reasons.append(f"source_newer:{md_path.name}")

    status = "stale" if stale_reasons else ("ready" if parent_count > 0 else "empty")
    return {
        **base, "status": status, "quick_check": qc_val, "indexed_doc_ids": sorted(indexed_doc_ids),
        "parent_count": parent_count, "child_count": child_count, "built_at_utc": built_at_utc,
        "vector_backend": meta.get("vector_backend"), "stale_reasons": stale_reasons, "error": None,
    }


def _collect_recommended_actions(stores: Mapping[str, Mapping[str, Any]]) -> list[str]:
    actions: list[str] = []
    action_maps = (
        ("catalog_sqlite", {
            "missing": "Initialize SQLite catalog via logicaide.catalog.sqlite_store.init_catalog_db().",
            "error": "Inspect or repair corrupt catalog SQLite database.",
            "empty": "Ingest vehicle profiles or drive sessions into catalog SQLite.",
        }),
        ("telemetry_lake", {
            "error": "Inspect telemetry Parquet lake for corrupt or incompatible files.",
            "empty": "Ingest telemetry Parquet chunks into telemetry lake.",
        }),
        ("heritage_index", {
            "missing": "Rebuild Heritage RAG index via logicaide.heritage.rag_index.build_heritage_index().",
            "stale": "Rebuild Heritage RAG index via logicaide.heritage.rag_index.build_heritage_index().",
            "error": "Rebuild Heritage RAG index via logicaide.heritage.rag_index.build_heritage_index().",
            "empty": "Add heritage markdown docs under knowledge/ or wiki/ and rebuild Heritage RAG index.",
        }),
    )
    for key, mapping in action_maps:
        if msg := mapping.get(stores[key]["status"]):
            actions.append(msg)
    rom = stores["rom_manifests"]
    if rom["status"] in {"missing", "empty", "error"}:
        actions.append("Rebuild partitioned ROM manifests via scripts/seed_rom_manifest.py.")
    elif not rom.get("tags_present", False):
        actions.append("Generate ctags index via logicaide.rom.manifest.sync_manifests().")
    return actions


def check_workbench_readiness(
    *,
    db_path: Path | str = DEFAULT_CATALOG_DB,
    lake_dir: Path | str = Path("data/drives"),
    rom_catalog_path: Path | str = DEFAULT_CATALOG_PATH,
    tags_path: Path | str = Path("tags"),
    heritage_db_path: Path | str = DEFAULT_INDEX_DB,
    knowledge_dir: Path | str = Path("knowledge"),
    wiki_dir: Path | str = Path("wiki"),
    auto_rebuild: bool = False,
    verify_frontmatter: bool = True,
) -> dict[str, Any]:
    """Check readiness across catalog, telemetry lake, ROM manifests, and heritage RAG."""
    cat_path, lake_path, rom_cat_path, tags_file, her_db_path, know_path, wiki_path = (
        _validate_no_symlink(raw, lbl) for raw, lbl in (
            (db_path, "db_path"), (lake_dir, "lake_dir"), (rom_catalog_path, "rom_catalog_path"),
            (tags_path, "tags_path"), (heritage_db_path, "heritage_db_path"),
            (knowledge_dir, "knowledge_dir"), (wiki_dir, "wiki_dir"),
        )
    )
    heritage_report = _inspect_heritage_index(her_db_path, know_path, wiki_path)
    rebuilt, rebuild_summary = False, None
    if auto_rebuild and heritage_report["status"] in {"stale", "missing"} and heritage_report["source_file_count"] > 0:
        rebuild_summary = build_heritage_index(
            knowledge_dir=know_path, wiki_dir=wiki_path, db_path=her_db_path, verify_frontmatter=verify_frontmatter,
        )
        rebuilt = True
        heritage_report = _inspect_heritage_index(her_db_path, know_path, wiki_path)

    stores: dict[str, dict[str, Any]] = {
        "catalog_sqlite": _inspect_catalog_sqlite(cat_path),
        "telemetry_lake": _inspect_telemetry_lake(lake_path),
        "rom_manifests": _inspect_rom_manifests(rom_cat_path, tags_file),
        "heritage_index": heritage_report,
    }
    statuses = [store["status"] for store in stores.values()]
    all_ready = all(s == "ready" for s in statuses)
    operational = not any(s in {"error", "missing", "stale"} for s in statuses)
    overall_status = "ready" if all_ready else ("partial" if operational else "degraded")
    return {
        "overall_status": overall_status, "all_ready": all_ready, "operational": operational,
        "rebuilt": rebuilt, "rebuild_summary": rebuild_summary,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "stores": stores, "recommended_actions": _collect_recommended_actions(stores),
    }


def format_readiness_text(report: Mapping[str, Any]) -> str:
    """Format a workbench readiness report dictionary as a concise human summary."""
    overall = str(report.get("overall_status", "unknown")).upper()
    stores = report.get("stores") or {}
    cat, lake, rom, her = (stores.get(k) or {} for k in ("catalog_sqlite", "telemetry_lake", "rom_manifests", "heritage_index"))
    cat_counts = ", ".join(f"{k}={v}" for k, v in (cat.get("counts") or {}).items()) or "none"
    stale_tag = f" [stale: {', '.join(her['stale_reasons'])}]" if her.get("stale_reasons") else ""
    lines = [
        f"LogicAIDE Workbench Readiness: {overall} (operational={bool(report.get('operational'))}, all_ready={bool(report.get('all_ready'))}, rebuilt={bool(report.get('rebuilt'))})",
        f"- catalog_sqlite: {cat.get('status', 'unknown')} ({cat_counts})" + (f" [error: {cat['error']}]" if cat.get("error") else ""),
        f"- telemetry_lake: {lake.get('status', 'unknown')} (chunks={lake.get('chunk_count', 0)}, sessions={lake.get('session_count', 0)}, rows={lake.get('total_rows', 0)}, quarantined={lake.get('quarantined_rows', 0)}, rollup_bins={lake.get('rollup_bin_count', 0)})" + (f" [error: {lake['error']}]" if lake.get("error") else ""),
        f"- rom_manifests: {rom.get('status', 'unknown')} (manifests={rom.get('manifest_count', 0)}, verified_ram={rom.get('verified_ram_count', 0)}, cal_tables={rom.get('calibration_table_count', 0)}, tags={'present' if rom.get('tags_present') else 'missing'})" + (f" [error: {rom['error']}]" if rom.get("error") else ""),
        f"- heritage_index: {her.get('status', 'unknown')} (sources={her.get('source_file_count', 0)}, parents={her.get('parent_count', 0)}, children={her.get('child_count', 0)}, backend={her.get('vector_backend') or 'none'}){stale_tag}" + (f" [error: {her['error']}]" if her.get("error") else ""),
    ]
    if actions := report.get("recommended_actions"):
        lines.extend(["Recommended actions:", *(f"  * {action}" for action in actions)])
    return "\n".join(lines)


__all__ = ["check_workbench_readiness", "format_readiness_text"]
