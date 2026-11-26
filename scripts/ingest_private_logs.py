#!/usr/bin/env python3
"""Batch ingest private empirical telemetry logs into the Parquet lake and deterministic test slices."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from logicaide.heritage.discovery import scrub_pii  # noqa: E402
from logicaide.rom.asm_parsers import scrub_cleanroom_text  # noqa: E402
from logicaide.rom.manifest_specs import find_ecu_root  # noqa: E402
from logicaide.telemetry import (  # noqa: E402
    FORBIDDEN_GPS_COLUMNS,
    discover_private_logs,
    export_stimulus_fixture,
    parse_telemetry_file,
    summarize_telemetry_lake,
    write_session_parquet_chunks,
)

_CAR_ID_BY_FORMAT: dict[str, str] = {
    "lasky_realtimelog": "lm_87_realtimelog",
    "wot_log_mmss": "smec_89_mpscandroid",
    "k_smec_13col": "smec_k_logs36",
    "ecu_dlg": "sbec_90_van_dlg",
}


def _resolve_car_id(source_format: str) -> str:
    if source_format.startswith("mpscan_android_"):
        return "smec_89_mpscandroid"
    return _CAR_ID_BY_FORMAT.get(source_format, "CHRYSLER_UNKNOWN")


def _scrub_line(line: str) -> str:
    return scrub_cleanroom_text(scrub_pii(line))


def _assert_header_has_no_gps(header_line: str, delimiter: str) -> None:
    for col in header_line.split(delimiter):
        norm = col.strip().lower()
        if norm in FORBIDDEN_GPS_COLUMNS or norm.startswith("gps_"):
            raise ValueError(f"Forbidden GPS column detected in slice header: {col!r}")


def _write_raw_slice(
    src_path: Path,
    dest_path: Path,
    *,
    header_lines: int,
    max_data_rows: int,
    delimiter: str,
    header_row_index: int,
) -> dict[str, Any]:
    """Export a bounded, GPS-checked, PII-scrubbed, LF-encoded raw telemetry slice."""
    raw_lines = [ln for ln in src_path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if ln.strip()]
    if len(raw_lines) <= header_lines:
        raise ValueError(f"Source log {src_path} has insufficient rows for slicing.")
    _assert_header_has_no_gps(raw_lines[header_row_index], delimiter)

    selected = raw_lines[: header_lines + max_data_rows]
    cleaned = [_scrub_line(line) for line in selected]
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("\n".join(cleaned) + "\n", encoding="utf-8", newline="\n")
    return {
        "output_path": str(dest_path),
        "header_lines": header_lines,
        "data_rows": len(cleaned) - header_lines,
        "gps_stripped": True,
        "sha256": hashlib.sha256(dest_path.read_bytes()).hexdigest(),
    }


def _locate_slice_sources(private_root: Path) -> dict[str, Path]:
    ecu_root = find_ecu_root(private_root)
    candidates: dict[str, Path | None] = {
        "lasky": ecu_root / "Lasky" / "Realtimelog-Session v13.csv",
        "mpscandroid": ecu_root / "com.mpsuite.mpscandroid" / "files" / "logs" / "2019_06_09-20_38_18.csv",
        "ksmec": ecu_root / "K SMEC" / "logs" / "36" / "mpN7D7P6.csv",
        "dlg": next((p for p in sorted(ecu_root.rglob("run6.dlg")) if p.parent.name == "Datalogs"), None),
    }
    missing = [name for name, path in candidates.items() if path is None or not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing expected slice source logs under {ecu_root}: {missing}")
    return {name: path for name, path in candidates.items() if path is not None}


def _export_deterministic_fixtures(
    private_root: Path,
    lake_dir: Path,
    db_path: Path,
    fixtures_dir: Path,
    session_ids: set[str],
) -> list[dict[str, Any]]:
    """Export 4 raw cleanroom slices and 1 normalized Parquet-backed stimulus slice."""
    sources = _locate_slice_sources(private_root)
    exports: list[dict[str, Any]] = [
        _write_raw_slice(
            sources["lasky"], fixtures_dir / "lasky_realtimelog_v13_slice.csv",
            header_lines=3, max_data_rows=145, delimiter=",", header_row_index=1,
        ),
        _write_raw_slice(
            sources["mpscandroid"], fixtures_dir / "mpscandroid_2019_06_09_egt_rail_slice.csv",
            header_lines=1, max_data_rows=14, delimiter=",", header_row_index=0,
        ),
        _write_raw_slice(
            sources["ksmec"], fixtures_dir / "ksmec_mpN7D7P6_slice.csv",
            header_lines=1, max_data_rows=140, delimiter=",", header_row_index=0,
        ),
        _write_raw_slice(
            sources["dlg"], fixtures_dir / "ecu_run6_slice.dlg",
            header_lines=3, max_data_rows=141, delimiter="\t", header_row_index=2,
        ),
    ]
    if any(lake_dir.rglob("*.parquet")):
        target_session = "Realtimelog-Session v13__s1" if "Realtimelog-Session v13__s1" in session_ids else None
        norm_res = export_stimulus_fixture(
            lake_dir,
            fixtures_dir / "lasky_v13_normalized_slice.csv",
            max_rows=100,
            fmt="csv",
            car_id="lm_87_realtimelog",
            session_id=target_session,
            db_path=db_path,
        )
        exports.append(norm_res)
    return exports


def ingest_private_logs(
    *,
    private_root: Path,
    lake_dir: Path,
    db_path: Path,
    fixtures_dir: Path,
    export_fixtures: bool,
    deduplicate: bool,
) -> dict[str, Any]:
    """Ingest discovered telemetry logs into Parquet chunks and optionally export fixtures."""
    files = discover_private_logs(private_root, deduplicate=deduplicate)
    ingested_sessions: list[str] = []
    quarantined_rows = 0
    total_chunks = 0
    total_rows = 0

    for path in files:
        sessions = parse_telemetry_file(
            path, build_id="STOCK", run_kind="VEHICLE_SCI", private_root=private_root
        )
        for sess in sessions:
            sess.car_id = _resolve_car_id(sess.source_format)
            res = write_session_parquet_chunks(sess, lake_dir=lake_dir, db_path=db_path)
            ingested_sessions.append(sess.session_id)
            quarantined_rows += sess.quarantined_sample_count
            total_chunks += res["chunk_count"]
            total_rows += res["row_count"]

    fixtures: list[dict[str, Any]] = []
    if export_fixtures and private_root.exists():
        fixtures = _export_deterministic_fixtures(
            private_root, lake_dir, db_path, fixtures_dir, set(ingested_sessions)
        )

    lake_summary = summarize_telemetry_lake(lake_dir, db_path=db_path)
    return {
        "private_root": str(private_root),
        "files_discovered": len(files),
        "sessions_ingested": len(ingested_sessions),
        "chunks_written": total_chunks,
        "rows_written": total_rows,
        "quarantined_rows": quarantined_rows,
        "lake_summary": lake_summary,
        "fixtures_exported": fixtures,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest empirical telemetry logs into 30s Zstd Parquet chunks and deterministic test slices."
    )
    parser.add_argument("--private-root", type=Path, default=Path("private_references"))
    parser.add_argument("--lake-dir", type=Path, default=Path("data/drives"))
    parser.add_argument("--db-path", type=Path, default=Path("data/drives/catalog.sqlite"))
    parser.add_argument("--fixtures-dir", type=Path, default=Path("tests/fixtures/telemetry"))
    parser.add_argument("--export-fixtures", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--deduplicate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--json", action="store_true", help="Emit ingestion summary as JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for private telemetry log ingestion and fixture slicing."""
    args = _build_parser().parse_args(argv)
    summary = ingest_private_logs(
        private_root=args.private_root,
        lake_dir=args.lake_dir,
        db_path=args.db_path,
        fixtures_dir=args.fixtures_dir,
        export_fixtures=args.export_fixtures,
        deduplicate=args.deduplicate,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    lake = summary["lake_summary"]
    print(
        f"Ingested {summary['files_discovered']} files -> {summary['sessions_ingested']} sessions, "
        f"{summary['chunks_written']} chunks, {summary['rows_written']} rows "
        f"(quarantined={summary['quarantined_rows']})"
    )
    print(
        f"Lake {lake['lake_dir']}: sessions={lake['session_count']}, active_rows={lake['active_rows']}, "
        f"rollup_bins={lake['rollup_bin_count']}, fixtures={len(summary['fixtures_exported'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
