"""Deterministic, GPS/PII-free Agent Brief generators for LogicAIDE workbench contexts."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
from pathlib import Path
import re
import shlex
from typing import Any

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB, get_candidate_build, get_session_record, list_vehicles
from logicaide.heritage.doc_curator import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.telemetry.issues import get_tuning_issue, list_bookmarks, list_tuning_issues
from logicaide.telemetry.rollups import _is_gps_column, query_drive_timeseries, query_load_cell_rollups
from logicaide.telemetry.watch_cli import DEFAULT_LAKE_DIR

DEFAULT_STAGING_ROOT = Path("staging/candidates")
NOT_IN_WATCHLIST = "Not in Watchlist (null)"
_OS_SYSTEM_LINK_PREFIXES = frozenset({"/tmp", "/var", "/var/tmp"})
_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _scrub_text(value: str) -> str:
    return scrub_cleanroom_text(scrub_pii(value))


def _sanitize_value(value: Any) -> Any:
    """Recursively strip GPS keys, non-finite floats, and PII/cleanroom tokens."""
    if isinstance(value, Mapping):
        return {_scrub_text(str(k)): _sanitize_value(v) for k, v in value.items() if not _is_gps_column(str(k))}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return (value if math.isfinite(value) else None) if isinstance(value, float) else value


def _validate_nonempty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _validate_int_bin(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def _validate_build_id(build_id: Any) -> str:
    cleaned = _validate_nonempty_str(build_id, "build_id")
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned or not _BUILD_ID_RE.fullmatch(cleaned):
        raise ValueError(f"Unsafe build_id {build_id!r}")
    return cleaned


def _validate_no_symlink(raw_path: Path | str, label: str) -> Path:
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


def _lookup_ecu_family(db_path: Path | str, car_id: str | None) -> str | None:
    if not car_id or not Path(db_path).is_file():
        return None
    vehicles = list_vehicles(db_path, car_id=car_id)
    return str(vehicles[0]["ecu_family"]) if vehicles and vehicles[0].get("ecu_family") else None


def _safe_list_bookmarks(db_path: Path | str, *, session_id: str | None = None) -> list[dict[str, Any]]:
    return list_bookmarks(db_path, session_id=session_id) if Path(db_path).is_file() else []


def _safe_list_issues(db_path: Path | str, *, car_id: str | None = None, session_id: str | None = None) -> list[dict[str, Any]]:
    return list_tuning_issues(db_path, car_id=car_id, session_id=session_id) if Path(db_path).is_file() else []


def _weighted_mean(rows: list[Mapping[str, Any]], key: str) -> float | None:
    total_val, total_wt = 0.0, 0
    for row in rows:
        val, wt = row.get(key), row.get("sample_count")
        if val is None or isinstance(val, bool) or wt is None or isinstance(wt, bool):
            continue
        try:
            fval, iwt = float(val), int(wt)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fval) and iwt > 0:
            total_val += fval * iwt
            total_wt += iwt
    return (total_val / total_wt) if total_wt > 0 else None


def _ordered_bounds(lo: Any, hi: Any) -> tuple[float, float] | None:
    if lo is None and hi is None:
        return None
    try:
        a, b = float(lo if lo is not None else hi), float(hi if hi is not None else lo)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    return (b, a) if a > b else (a, b)


def _intervals_overlap(lo: Any, hi: Any, bin_lo: int, bin_hi: int) -> bool:
    bounds = _ordered_bounds(lo, hi)
    return bounds is not None and bounds[0] < float(bin_hi) and bounds[1] >= float(bin_lo)


def _surrounding_bounds(lo: Any, hi: Any, pad: float) -> tuple[float | None, float | None]:
    bounds = _ordered_bounds(lo, hi)
    return (max(0.0, bounds[0] - pad), bounds[1] + pad) if bounds else (None, None)


def _fmt_val(value: Any, *, decimals: int | None = None, fallback: str = NOT_IN_WATCHLIST) -> str:
    if value is None:
        return fallback
    return f"{value:.{decimals}f}" if isinstance(value, float) and decimals is not None else str(value)


def _build_mcp_follow_up(kind: str, ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    if kind == "session":
        rollup_args = {"session_id": ctx.get("session_id"), **({"car_id": ctx["car_id"]} if ctx.get("car_id") else {})}
        return [
            {"tool": "get_drive_timeseries", "args": {"session_id": ctx.get("session_id"), "limit": 200}},
            {"tool": "query_load_cell_rollups", "args": rollup_args},
        ]
    if kind == "load_cell":
        args = {"rpm_min": ctx["rpm_bin"], "rpm_max": ctx["rpm_bin"], "map_min_kpa": ctx["map_bin_kpa"], "map_max_kpa": ctx["map_bin_kpa"]}
        for key in ("car_id", "session_id"):
            if ctx.get(key) is not None:
                args[key] = ctx[key]
        return [
            {"tool": "query_load_cell_rollups", "args": args},
            {"tool": "get_drive_timeseries", "args": {**args, "rpm_max": float(ctx["rpm_bin"]) + 249.999, "map_max_kpa": float(ctx["map_bin_kpa"]) + 9.999, "limit": 100}},
        ]
    if kind == "issue":
        bm = {"tool": "list_bookmarks", "args": {k: ctx[k] for k in ("session_id", "car_id") if ctx.get(k) is not None}}
        return [bm, {"tool": "get_drive_timeseries", "args": {"session_id": ctx["session_id"], "limit": 100}}] if ctx.get("session_id") else [bm]
    cand_args = {k: ctx[k] for k in ("build_id", "car_id") if ctx.get(k) is not None}
    return [
        {"tool": "query_load_cell_rollups", "args": cand_args},
        {"tool": "inspect_rom_manifest", "args": {"catalog_path": "rom/manifests/catalog.json"}},
    ]


def _finish_md(lines: list[str], mcp_items: Any) -> str:
    if mcp_items:
        lines.extend(["", "## MCP Follow-Up Context", *(f"- `{it.get('tool')}`: `{json.dumps(it.get('args') or {}, sort_keys=True)}`" for it in mcp_items)])
    return "\n".join(lines) + "\n"


def _format_session_md(b: Mapping[str, Any]) -> str:
    unpolled, polled = b.get("unpolled_channels") or [], b.get("polled_channels") or []
    lines = [
        f"# Session Agent Brief — {b.get('session_id')}",
        f"- Vehicle: `{b.get('car_id') or 'N/A'}` | ECU Family: `{b.get('ecu_family') or 'N/A'}`",
        f"- Build: `{b.get('build_id') or 'N/A'}` | Run Kind: `{b.get('run_kind') or 'N/A'}` | ROM SHA-256: `{b.get('rom_sha256') or 'N/A'}`",
        f"- Rows: total=`{b.get('total_rows', 0)}`, active=`{b.get('active_rows', 0)}`, quarantined=`{b.get('quarantined_rows', 0)}` | Quarantined Cells: `{json.dumps(b.get('quarantined_cells') or [])}`",
        "", "## Telemetry Peaks & Bounds",
        f"- Peak RPM: `{_fmt_val(b.get('peak_rpm'), decimals=1)}` | Peak MAP (kPa): `{_fmt_val(b.get('peak_map_kpa'), decimals=2)}` | Peak Boost (psi): `{_fmt_val(b.get('peak_boost_psi'), decimals=2)}`",
        f"- Max Knock Counts: `{_fmt_val(b.get('max_knock_counts'))}` | Min Battery (V): `{_fmt_val(b.get('min_batt_v'), decimals=2)}`",
        f"- Polled Channels: `{', '.join(polled) if polled else 'None'}`",
        f"- Unpolled Channels ({NOT_IN_WATCHLIST}): `{', '.join(unpolled) if unpolled else 'None'}`",
    ]
    if bms := b.get("bookmarks"):
        lines.extend(["", "## Linked Bookmarks", *(f"- [{bm.get('source')}] `{bm.get('label')}` @ sample `{bm.get('sample_idx')}`: {bm.get('note') or ''}" for bm in bms)])
    if issues := b.get("tuning_issues"):
        lines.extend(["", "## Linked Tuning Issues", *(f"- [{i.get('severity')}/{i.get('status')}] `{i.get('issue_id')}`: {i.get('title')}" for i in issues)])
    return _finish_md(lines, b.get("mcp_follow_up"))


def _format_load_cell_md(b: Mapping[str, Any]) -> str:
    rpm_b, map_b = b.get("rpm_bin"), b.get("map_bin_kpa")
    lines = [
        f"# Load Cell Agent Brief — RPM {rpm_b}..{int(rpm_b) + 250} / MAP {map_b}..{int(map_b) + 10} kPa",
        f"- Car ID(s): `{', '.join(b.get('car_ids') or []) or b.get('car_id') or 'N/A'}` | ECU Family: `{b.get('ecu_family') or 'N/A'}`",
        f"- TPS Regime: `{b.get('tps_regime') or NOT_IN_WATCHLIST}` | Charge Temp Bin: `{b.get('charge_temp_bin') or NOT_IN_WATCHLIST}`",
        f"- Sample Count: `{b.get('sample_count', 0)}` | Knock Polled Samples: `{b.get('knock_polled_samples', 0)}`",
        "", "## Weighted Cell Means & Knock Summary",
        f"- Mean PW (ms): `{_fmt_val(b.get('mean_pw_ms'), decimals=3)}` | Mean Spark (deg BTDC): `{_fmt_val(b.get('mean_spark_deg'), decimals=2)}` | Mean AFR: `{_fmt_val(b.get('mean_afr'), decimals=2)}`",
        f"- Total Knock Events: `{_fmt_val(b.get('total_knock_events'))}` | Min Battery (V): `{_fmt_val(b.get('min_batt_v'), decimals=2)}`",
    ]
    if issues := b.get("overlapping_issues"):
        lines.extend(["", "## Overlapping Tuning Issues", *(f"- [{i.get('severity')}/{i.get('status')}] `{i.get('issue_id')}`: {i.get('title')}" for i in issues)])
    return _finish_md(lines, b.get("mcp_follow_up"))


def _format_issue_md(b: Mapping[str, Any]) -> str:
    iss = b.get("issue") or {}
    lines = [
        f"# Tuning Issue Agent Brief — {b.get('issue_id')}",
        f"- Title: `{iss.get('title')}` | Severity: `{iss.get('severity')}` | Status: `{iss.get('status')}`",
        f"- Vehicle: `{b.get('car_id')}` | ECU Family: `{b.get('ecu_family') or 'N/A'}` | Session: `{b.get('session_id') or 'N/A'}`",
        f"- Operating Window: RPM `{_fmt_val(iss.get('rpm_min'), fallback='N/A')}..{_fmt_val(iss.get('rpm_max'), fallback='N/A')}`, MAP `{_fmt_val(iss.get('map_min_kpa'), fallback='N/A')}..{_fmt_val(iss.get('map_max_kpa'), fallback='N/A')}` kPa",
        f"- Description: {iss.get('description') or ''} | Surrounding Cells: `{len(b.get('surrounding_load_cells') or [])}` | Nearby Frames: `{len(b.get('nearby_frames') or [])}`",
    ]
    if bms := b.get("linked_bookmarks"):
        lines.extend(["", "## Linked Bookmarks", *(f"- `{bm.get('bookmark_id')}` ({bm.get('source')}): {bm.get('note') or bm.get('label')}" for bm in bms)])
    return _finish_md(lines, b.get("mcp_follow_up"))


def _format_candidate_build_md(b: Mapping[str, Any]) -> str:
    status = str(b.get("safety_gate_status") or "BLOCKED")
    lines = [
        f"# Candidate Build Agent Brief — {b.get('build_id')}",
        f"- Status: `{status}` | Vehicle: `{b.get('car_id') or 'N/A'}` | ECU Family: `{b.get('ecu_family') or 'N/A'}`",
        f"- Baseline SHA-256: `{b.get('baseline_sha256')}` | Candidate SHA-256: `{b.get('candidate_sha256')}`",
        f"- Twin 0-LSB Parity: `twin_parity_ok={b.get('twin_parity_ok')}` | Vector Proof: `vectors_ok={b.get('vectors_ok')}`",
        f"- Loop Budget: `max_loop_cycles={b.get('max_loop_cycles')}`, `max_loop_ms={_fmt_val(b.get('max_loop_ms'), decimals=4, fallback='N/A')}` | Sweep Overflows: `{b.get('sweep_overflows')}`",
    ]
    if reasons := b.get("blocking_reasons"):
        lines.extend(["", "## Blocking Reasons", *(f"- {r}" for r in reasons)])
    if status == "PASS":
        lines.extend(["", "## Human-Only Manual Bench Verification & Flash Command", f"- Verification Reminder: {b.get('pin11_reminder')}", f"- Manual Flash Command:\n  ```sh\n  {b.get('manual_flash_command')}\n  ```"])
    else:
        lines.extend(["", "## Safety Gate Status Notice", "- DO NOT FLASH: Candidate build is blocked; hardware programming instructions are suppressed."])
    return _finish_md(lines, b.get("mcp_follow_up"))


def format_brief_markdown(brief: Mapping[str, Any]) -> str:
    """Format any LogicAIDE Agent Brief mapping into deterministic, scrubbed Markdown."""
    clean = _sanitize_value(brief)
    kind = str(clean.get("kind") or "").strip().lower()
    if not kind:
        kind = next((k for k, cond in (
            ("candidate_build", "safety_gate_status" in clean or ("build_id" in clean and "candidate_sha256" in clean)),
            ("issue", "issue_id" in clean), ("load_cell", "rpm_bin" in clean and "map_bin_kpa" in clean), ("session", "session_id" in clean),
        ) if cond), "")
    renderers = {"session": _format_session_md, "load_cell": _format_load_cell_md, "issue": _format_issue_md, "candidate_build": _format_candidate_build_md}
    if kind not in renderers:
        raise ValueError(f"Unsupported or unknown brief kind {kind!r}")
    return _scrub_text(renderers[kind](clean))


def _finalize_brief(payload: dict[str, Any]) -> dict[str, Any]:
    clean = _sanitize_value(payload)
    clean["markdown"] = format_brief_markdown(clean)
    return clean


def build_session_brief(
    session_id: str, *, db_path: Path | str = DEFAULT_CATALOG_DB, lake_dir: Path | str = DEFAULT_LAKE_DIR
) -> dict[str, Any]:
    """Build a GPS/PII-free Agent Brief summarizing a telemetry drive or sim session."""
    clean_id = _validate_nonempty_str(session_id, "session_id")
    session_rec = get_session_record(clean_id, db_path)
    ts_res = query_drive_timeseries(lake_dir, session_id=clean_id, limit=50)
    rollups = query_load_cell_rollups(lake_dir, session_id=clean_id, limit=10)
    total_rows = int(ts_res.get("total_matching_rows") or 0)
    if session_rec is None and total_rows == 0 and not rollups:
        raise KeyError(f"Session not found in catalog or telemetry lake: {clean_id!r}")

    sample_meta = (ts_res.get("rows") or [None])[0] or (rollups or [None])[0] or {}
    car_id = (session_rec or {}).get("car_id") or sample_meta.get("car_id")
    quarantined_rows = int(ts_res.get("quarantined_rows") or 0)
    return _finalize_brief({
        "kind": "session", "session_id": clean_id, "car_id": car_id, "ecu_family": _lookup_ecu_family(db_path, car_id),
        "build_id": (session_rec or {}).get("build_id") or sample_meta.get("build_id"),
        "run_kind": (session_rec or {}).get("run_kind") or sample_meta.get("run_kind"),
        "rom_sha256": (session_rec or {}).get("rom_sha256"),
        "total_rows": total_rows, "active_rows": max(0, total_rows - quarantined_rows), "quarantined_rows": quarantined_rows,
        "quarantined_cells": list((session_rec or {}).get("quarantined_cells") or []),
        "peak_rpm": ts_res.get("peak_rpm"), "peak_map_kpa": ts_res.get("peak_map_kpa"),
        "peak_boost_psi": ts_res.get("peak_boost_psi"), "max_knock_counts": ts_res.get("max_knock_counts"),
        "min_batt_v": ts_res.get("min_batt_v"),
        "polled_channels": list(ts_res.get("polled_channels") or []),
        "unpolled_channels": list(ts_res.get("unpolled_channels") or []),
        "bookmarks": _safe_list_bookmarks(db_path, session_id=clean_id),
        "tuning_issues": _safe_list_issues(db_path, session_id=clean_id),
        "load_cell_rollups": rollups,
        "mcp_follow_up": _build_mcp_follow_up("session", {"session_id": clean_id, "car_id": car_id}),
    })


def build_load_cell_brief(
    rpm_bin: int, map_bin_kpa: int, *, lake_dir: Path | str = DEFAULT_LAKE_DIR,
    db_path: Path | str = DEFAULT_CATALOG_DB, car_id: str | None = None, session_id: str | None = None,
    build_id: str | None = None, run_kind: str | None = None, tps_regime: str | None = None,
    charge_temp_bin: str | None = None,
) -> dict[str, Any]:
    """Build a weighted, null-preserving Agent Brief for an exact (rpm_bin, map_bin_kpa) cell."""
    rpm_v, map_v = _validate_int_bin(rpm_bin, "rpm_bin"), _validate_int_bin(map_bin_kpa, "map_bin_kpa")
    raw_rows = query_load_cell_rollups(
        lake_dir, car_id=car_id, run_kind=run_kind, session_id=session_id, build_id=build_id,
        rpm_min=rpm_v, rpm_max=rpm_v, map_min_kpa=map_v, map_max_kpa=map_v,
        tps_regime=tps_regime, charge_temp_bin=charge_temp_bin,
    )
    rows = [r for r in raw_rows if int(r["rpm_bin"]) == rpm_v and int(r["map_bin_kpa"]) == map_v]
    if not rows:
        raise KeyError(f"No load-cell rollups found for rpm_bin={rpm_v}, map_bin_kpa={map_v}")

    car_ids = sorted({str(r["car_id"]) for r in rows if r.get("car_id")})
    ecu_families = sorted({f for cid in car_ids if (f := _lookup_ecu_family(db_path, cid))})
    issues = [
        iss for iss in _safe_list_issues(db_path, car_id=car_id, session_id=session_id)
        if (not car_ids or iss.get("car_id") in car_ids)
        and _intervals_overlap(iss.get("rpm_min"), iss.get("rpm_max"), rpm_v, rpm_v + 250)
        and _intervals_overlap(iss.get("map_min_kpa"), iss.get("map_max_kpa"), map_v, map_v + 10)
    ]
    knock_polled = sum(int(r.get("knock_polled_samples") or 0) for r in rows)
    knock_vals = [int(r["total_knock_events"]) for r in rows if r.get("total_knock_events") is not None]
    batt_vals = [float(r["min_batt_v"]) for r in rows if isinstance(r.get("min_batt_v"), (int, float)) and not isinstance(r.get("min_batt_v"), bool) and math.isfinite(float(r["min_batt_v"]))]
    tps_list = sorted({str(r["tps_regime"]) for r in rows if r.get("tps_regime")})
    ct_list = sorted({str(r["charge_temp_bin"]) for r in rows if r.get("charge_temp_bin")})
    return _finalize_brief({
        "kind": "load_cell", "rpm_bin": rpm_v, "map_bin_kpa": map_v,
        "car_id": car_id or (car_ids[0] if len(car_ids) == 1 else None), "car_ids": car_ids, "session_id": session_id,
        "ecu_family": ecu_families[0] if len(ecu_families) == 1 else (", ".join(ecu_families) if ecu_families else None),
        "ecu_families": ecu_families, "sample_count": sum(int(r.get("sample_count") or 0) for r in rows),
        "mean_pw_ms": _weighted_mean(rows, "mean_pw_ms"), "mean_spark_deg": _weighted_mean(rows, "mean_spark_deg"),
        "mean_afr": _weighted_mean(rows, "mean_afr"),
        "total_knock_events": sum(knock_vals) if (knock_polled > 0 and knock_vals) else None,
        "knock_polled_samples": knock_polled, "min_batt_v": min(batt_vals) if batt_vals else None,
        "tps_regime": tps_list[0] if len(tps_list) == 1 else (", ".join(tps_list) if tps_list else None),
        "charge_temp_bin": ct_list[0] if len(ct_list) == 1 else (", ".join(ct_list) if ct_list else None),
        "overlapping_issues": issues,
        "mcp_follow_up": _build_mcp_follow_up("load_cell", {"rpm_bin": rpm_v, "map_bin_kpa": map_v, "car_id": car_id, "session_id": session_id}),
    })


def build_issue_brief(
    issue_id: str, *, db_path: Path | str = DEFAULT_CATALOG_DB, lake_dir: Path | str = DEFAULT_LAKE_DIR
) -> dict[str, Any]:
    """Build a GPS/PII-free Agent Brief for a tuning issue with surrounding lake context."""
    clean_id = _validate_nonempty_str(issue_id, "issue_id")
    issue = get_tuning_issue(clean_id, db_path)
    if issue is None:
        raise KeyError(f"Tuning issue not found: {clean_id!r}")

    car_id, sess_id = issue.get("car_id"), issue.get("session_id")
    rpm_lo, rpm_hi = _surrounding_bounds(issue.get("rpm_min"), issue.get("rpm_max"), 250.0)
    map_lo, map_hi = _surrounding_bounds(issue.get("map_min_kpa"), issue.get("map_max_kpa"), 10.0)
    tag = f"[{clean_id}]"
    bookmarks = [
        bm for bm in _safe_list_bookmarks(db_path)
        if tag in str(bm.get("note") or "") or (sess_id is not None and bm.get("session_id") == sess_id)
    ]
    rollups = query_load_cell_rollups(
        lake_dir, car_id=car_id, session_id=sess_id, rpm_min=rpm_lo, rpm_max=rpm_hi, map_min_kpa=map_lo, map_max_kpa=map_hi, limit=25
    )
    nearby = (
        query_drive_timeseries(lake_dir, session_id=sess_id, rpm_min=rpm_lo, rpm_max=rpm_hi, map_min_kpa=map_lo, map_max_kpa=map_hi, limit=20).get("rows", [])
        if sess_id else []
    )
    return _finalize_brief({
        "kind": "issue", "issue_id": clean_id, "car_id": car_id, "session_id": sess_id,
        "ecu_family": _lookup_ecu_family(db_path, car_id), "issue": issue, "linked_bookmarks": bookmarks,
        "surrounding_load_cells": rollups, "nearby_frames": nearby,
        "mcp_follow_up": _build_mcp_follow_up("issue", {"issue_id": clean_id, "car_id": car_id, "session_id": sess_id}),
    })


def build_candidate_build_brief(
    build_id: str, *, db_path: Path | str = DEFAULT_CATALOG_DB, staging_root: Path | str = DEFAULT_STAGING_ROOT
) -> dict[str, Any]:
    """Build a Hardware-Deadbolt-enforced Agent Brief for a staged candidate ROM build."""
    clean_id = _validate_build_id(build_id)
    root_path = _validate_no_symlink(staging_root, "staging_root")
    build_dir = root_path / clean_id
    if build_dir.is_symlink():
        raise ValueError(f"Candidate staging directory must not be a symlink: {build_dir}")
    resolved_root, resolved_dir = root_path.resolve(), build_dir.resolve()
    if resolved_dir == resolved_root or not resolved_dir.is_relative_to(resolved_root):
        raise ValueError(f"Candidate build directory escapes staging_root: {build_dir}")
    summary_path = build_dir / "gate_summary.json"
    if summary_path.is_symlink():
        raise ValueError(f"gate_summary.json must not be a symlink: {summary_path}")

    db_row = get_candidate_build(clean_id, db_path)
    gate_summary: Any = None
    if summary_path.is_file():
        gate_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if not isinstance(gate_summary, Mapping):
            raise ValueError(f"gate_summary.json must be a JSON object, got {type(gate_summary).__name__}")
    if db_row is None and gate_summary is None:
        raise KeyError(f"Candidate build not found in catalog or staging_root: {clean_id!r}")

    gs, db = gate_summary or {}, db_row or {}
    car_id = gs.get("car_id") or db.get("car_id")
    gs_blocked = bool(gs) and (
        gs.get("status") != "PASS" or gs.get("ok") is False or gs.get("twin_parity_ok") is False
        or gs.get("vectors_ok") is False or int(gs.get("sweep_overflows") or 0) > 0 or bool(gs.get("blocking_reasons"))
    )
    db_blocked = bool(db) and (
        db.get("safety_gate_status") != "PASS" or db.get("twin_parity_ok") == 0
        or db.get("vectors_ok") == 0 or int(db.get("sweep_overflows") or 0) > 0
    )
    is_pass = bool(gs or db) and not gs_blocked and not db_blocked
    status = "PASS" if is_pass else "BLOCKED"

    raw_rom = gs.get("rom_path")
    if raw_rom is not None and (not isinstance(raw_rom, str) or not raw_rom.strip()):
        raise ValueError(f"gate_summary.json rom_path must be a non-empty string, got {raw_rom!r}")
    rom_candidate = _validate_no_symlink(raw_rom.strip() if raw_rom is not None else (build_dir / f"{clean_id}.bin"), "rom_path")
    resolved_rom = rom_candidate.resolve()
    if resolved_rom == resolved_dir or not resolved_rom.is_relative_to(resolved_dir):
        raise ValueError(f"Candidate ROM path escapes candidate build directory: {rom_candidate}")
    cwd_res = Path.cwd().resolve()
    rom_display = resolved_rom.relative_to(cwd_res).as_posix() if resolved_rom.is_relative_to(cwd_res) else str(resolved_rom)
    reasons = list(gs.get("blocking_reasons") or []) or ([] if is_pass else ["Safety gate status is BLOCKED (no gate_summary.json detail available)."])
    pin11_reminder = (
        "Visually confirm ECU generation (SMEC vs SBEC), verify 60-way Pin 11 continuity with a "
        "multimeter (open on SMEC; grounded on SBEC), check Pins 2/3/25/45 against the exact "
        "model-year FSM, and confirm 12V/21V optical/diode isolation before bench programming."
        if is_pass else None
    )
    raw_cyc = gs.get("max_loop_cycles") if gs.get("max_loop_cycles") is not None else db.get("max_loop_cycles")
    raw_ovf = gs.get("sweep_overflows") if gs.get("sweep_overflows") is not None else db.get("sweep_overflows")
    raw_twin = gs.get("twin_parity_ok") if gs.get("twin_parity_ok") is not None else db.get("twin_parity_ok")
    raw_vec = gs.get("vectors_ok") if gs.get("vectors_ok") is not None else db.get("vectors_ok")
    return _finalize_brief({
        "kind": "candidate_build", "build_id": clean_id, "car_id": car_id, "ecu_family": _lookup_ecu_family(db_path, car_id),
        "safety_gate_status": status,
        "twin_parity_ok": bool(raw_twin), "vectors_ok": bool(raw_vec),
        "max_loop_cycles": int(raw_cyc or 0),
        "max_loop_ms": float(gs["max_loop_ms"]) if gs.get("max_loop_ms") is not None else None,
        "sweep_overflows": int(raw_ovf or 0),
        "baseline_sha256": gs.get("baseline_sha256") or db.get("baseline_sha256"),
        "candidate_sha256": gs.get("candidate_sha256") or db.get("candidate_sha256"),
        "blocking_reasons": reasons, "pin11_reminder": pin11_reminder,
        "manual_flash_command": f"minipro -p AT28C256 -w {shlex.quote(rom_display)}" if is_pass else None,
        "mcp_follow_up": _build_mcp_follow_up("candidate_build", {"build_id": clean_id, "car_id": car_id}),
    })


def generate_agent_brief(kind: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Dispatch an Agent Brief request by context kind ('session', 'load_cell', 'issue', 'candidate_build')."""
    norm = _validate_nonempty_str(kind, "kind").lower()
    dispatch = {"session": build_session_brief, "load_cell": build_load_cell_brief, "issue": build_issue_brief, "candidate_build": build_candidate_build_brief}
    if norm not in dispatch:
        raise ValueError(f"Unsupported brief kind {kind!r}; expected one of {tuple(dispatch)}")
    return dispatch[norm](*args, **kwargs)


__all__ = [
    "DEFAULT_STAGING_ROOT", "NOT_IN_WATCHLIST", "build_candidate_build_brief",
    "build_issue_brief", "build_load_cell_brief", "build_session_brief",
    "format_brief_markdown", "generate_agent_brief",
]
