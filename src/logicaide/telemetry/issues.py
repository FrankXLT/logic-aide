"""Telemetry bookmarking and tuning_issues persistence for live/replay SCI sessions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

from logicaide.catalog.sqlite_store import _connect_existing_readonly, connect_catalog
from logicaide.heritage.doc_curator import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.telemetry.fixtures import _is_gps_column
from logicaide.telemetry.parquet_chunker import (
    VALID_RUN_KINDS,
    _ensure_vehicle_exists,
    _utc_now_iso,
)

VALID_ISSUE_SEVERITIES: frozenset[str] = frozenset({"INFO", "CAUTION", "CRITICAL"})
VALID_ISSUE_STATUSES: frozenset[str] = frozenset({"OPEN", "IN_PROGRESS", "VERIFIED_FIXED", "WONT_FIX"})
VALID_BOOKMARK_SOURCES: frozenset[str] = frozenset({"SPACEBAR", "TOUCH", "AUTO_ANOMALY"})


def _scrub_text(value: str) -> str:
    return scrub_cleanroom_text(scrub_pii(value))


def _coerce_finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _validate_optional_finite_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool.")
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number, got {value!r}.") from exc
    if not math.isfinite(num):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return num


def _normalize_offsets(offsets: Iterable[Any]) -> list[str]:
    normalized: list[str] = []
    for item in offsets:
        if isinstance(item, int) and not isinstance(item, bool):
            normalized.append(f"0x{item:02X}")
        else:
            text = _scrub_text(str(item)).strip()
            if text:
                normalized.append(text)
    return normalized


def _sanitize_nested_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        nested: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key)
            if _is_gps_column(key):
                raise ValueError(f"GPS telemetry key {key!r} is prohibited by SECURITY.md.")
            nested[key] = _sanitize_nested_value(raw_val)
        return nested
    if isinstance(value, (list, tuple)):
        return [_sanitize_nested_value(item) for item in value]
    return _scrub_text(value) if isinstance(value, str) else value


def _sanitize_operating_point(
    operating_point: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], float | None, float | None]:
    if operating_point is None:
        return {}, None, None
    if not isinstance(operating_point, Mapping):
        raise TypeError("operating_point must be a mapping or None.")
    cleaned = _sanitize_nested_value(operating_point)
    op_rpm = _coerce_finite_float(
        cleaned.get("rpm") if cleaned.get("rpm") is not None else cleaned.get("engine_rpm")
    )
    op_map = _coerce_finite_float(
        cleaned.get("map_kpa") if cleaned.get("map_kpa") is not None else cleaned.get("map")
    )
    return cleaned, op_rpm, op_map


def _build_issue_description(
    *,
    frame_ts_utc_ns: int,
    sample_idx: int,
    op_snapshot: dict[str, Any],
    active_offsets: list[str],
    quarantined_offsets: list[str],
    clean_note: str,
) -> str:
    lines = [
        f"frame_ts_utc_ns={frame_ts_utc_ns} sample_idx={sample_idx}",
        f"operating_point={json.dumps(op_snapshot, sort_keys=True, default=str)}",
        f"active_offsets={json.dumps(active_offsets)}",
        f"quarantined_offsets={json.dumps(quarantined_offsets)}",
    ]
    if clean_note:
        lines.append(f"note={clean_note}")
    return _scrub_text("\n".join(lines))


def _ensure_session_exists(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    car_id: str,
    build_id: str,
    started_at_utc: str,
    run_kind: str,
    rom_sha256: str,
    quarantined_offsets: list[str],
) -> None:
    if conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone() is not None:
        return
    conn.execute(
        """INSERT INTO sessions (
            session_id, car_id, build_id, started_at_utc, ended_at_utc, run_kind,
            rom_sha256, baro_kpa, quarantined_cells_json, imu_attached, gps_stripped, notes
        ) VALUES (?, ?, ?, ?, NULL, ?, ?, 101.325, ?, 0, 1, NULL)""",
        (
            session_id, car_id, build_id, started_at_utc, run_kind,
            rom_sha256, json.dumps(quarantined_offsets),
        ),
    )


def _insert_bookmark(
    conn: sqlite3.Connection, *, session_id: str, issue_id: str, ts_ns: int,
    sample_idx: int, source: str, title: str, clean_note: str,
) -> str:
    bookmark_id = f"bmk_{uuid.uuid4().hex[:16]}"
    bmk_note = f"[{issue_id}] {clean_note}".strip()
    conn.execute(
        "INSERT INTO bookmarks (bookmark_id, session_id, ts_utc_ns, sample_idx, source, label, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (bookmark_id, session_id, ts_ns, sample_idx, source, title, bmk_note),
    )
    return bookmark_id


def _persist_issue_and_bookmark(
    db_path: Path | str, *, issue_id: str, car_id: str, session_id: str | None, build_id: str,
    ecu_family: str, run_kind: str, rom_sha256: str, severity: str, category: str, title: str,
    description: str, op_rpm: float | None, op_map: float | None, created_at: str, ts_ns: int,
    sample_idx: int, source: str, clean_note: str, quarantined_offsets: list[str],
) -> str | None:
    bookmark_id: str | None = None
    conn = connect_catalog(db_path)
    try:
        with conn:
            _ensure_vehicle_exists(conn, {"car_id": car_id, "ecu_family": ecu_family}, car_id)
            if session_id is not None:
                _ensure_session_exists(
                    conn, session_id=session_id, car_id=car_id, build_id=build_id,
                    started_at_utc=created_at, run_kind=run_kind,
                    rom_sha256=rom_sha256, quarantined_offsets=quarantined_offsets,
                )
            conn.execute(
                """INSERT INTO tuning_issues (
                    issue_id, car_id, session_id, build_id, campaign_id, severity, category,
                    status, title, description, rpm_min, rpm_max, map_min_kpa, map_max_kpa, created_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    issue_id, car_id, session_id, build_id, severity, category,
                    title, description, op_rpm, op_rpm, op_map, op_map, created_at,
                ),
            )
            if session_id is not None:
                bookmark_id = _insert_bookmark(
                    conn, session_id=session_id, issue_id=issue_id, ts_ns=ts_ns,
                    sample_idx=sample_idx, source=source, title=title, clean_note=clean_note,
                )
    finally:
        conn.close()
    return bookmark_id


def bookmark_tuning_issue(
    db_path: Path | str,
    car_id: str,
    session_id: str | None = None,
    build_id: str = "STOCK",
    frame_ts_utc_ns: int | None = None,
    sample_idx: int = 0,
    operating_point: Mapping[str, Any] | None = None,
    active_offsets: Iterable[Any] = (),
    quarantined_offsets: Iterable[Any] = (),
    note: str = "",
    title: str | None = None,
    severity: str = "CAUTION",
    category: str = "SCI_BOOKMARK",
    source: str = "SPACEBAR",
    ecu_family: str = "SBEC_SINGLE_BOARD",
    rom_sha256: str = "0" * 64,
    issue_id: str | None = None,
    run_kind: str = "VEHICLE_SCI",
) -> dict[str, Any]:
    """Persist a tuning issue and optional linked session bookmark in one SQLite transaction."""
    if not isinstance(car_id, str) or not car_id.strip():
        raise ValueError("car_id must be a non-empty string.")
    clean_car_id = car_id.strip()
    if severity not in VALID_ISSUE_SEVERITIES:
        raise ValueError(f"Unsupported severity {severity!r}; expected one of {sorted(VALID_ISSUE_SEVERITIES)}.")
    if source not in VALID_BOOKMARK_SOURCES:
        raise ValueError(f"Unsupported source {source!r}; expected one of {sorted(VALID_BOOKMARK_SOURCES)}.")
    if run_kind not in VALID_RUN_KINDS:
        raise ValueError(f"Unsupported run_kind {run_kind!r}; expected one of {sorted(VALID_RUN_KINDS)}.")

    idx = int(sample_idx)
    if idx < 0:
        raise ValueError(f"sample_idx must be non-negative, got {idx!r}.")

    op_snapshot, op_rpm, op_map = _sanitize_operating_point(operating_point)
    norm_active = _normalize_offsets(active_offsets)
    norm_quarantined = _normalize_offsets(quarantined_offsets)
    clean_note = _scrub_text(note or "").strip()
    clean_category = _scrub_text(category).strip() or "SCI_BOOKMARK"
    raw_title = title.strip() if isinstance(title, str) and title.strip() else f"{clean_category} @ sample {idx}"
    clean_title = _scrub_text(raw_title).strip() or f"{clean_category} @ sample {idx}"

    ts_ns = int(frame_ts_utc_ns) if frame_ts_utc_ns is not None else time.time_ns()
    created_at = _utc_now_iso()
    clean_issue_id = issue_id.strip() if isinstance(issue_id, str) and issue_id.strip() else f"iss_{uuid.uuid4().hex[:16]}"
    description = _build_issue_description(
        frame_ts_utc_ns=ts_ns, sample_idx=idx, op_snapshot=op_snapshot,
        active_offsets=norm_active, quarantined_offsets=norm_quarantined, clean_note=clean_note,
    )
    bookmark_id = _persist_issue_and_bookmark(
        db_path, issue_id=clean_issue_id, car_id=clean_car_id, session_id=session_id,
        build_id=build_id, ecu_family=ecu_family, run_kind=run_kind, rom_sha256=rom_sha256,
        severity=severity, category=clean_category, title=clean_title, description=description,
        op_rpm=op_rpm, op_map=op_map, created_at=created_at, ts_ns=ts_ns, sample_idx=idx,
        source=source, clean_note=clean_note, quarantined_offsets=norm_quarantined,
    )
    return {
        "issue_id": clean_issue_id, "bookmark_id": bookmark_id, "car_id": clean_car_id,
        "session_id": session_id, "severity": severity, "category": clean_category,
        "status": "OPEN", "title": clean_title, "description": description,
        "rpm": op_rpm, "map_kpa": op_map, "active_offsets": norm_active,
        "quarantined_offsets": norm_quarantined, "created_at": created_at,
    }


def _validate_upsert_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    status, severity, source, run_kind = raw["status"], raw["severity"], raw["source"], raw["run_kind"]
    sample_idx, frame_ts_utc_ns, car_id, session_id = raw["sample_idx"], raw["frame_ts_utc_ns"], raw["car_id"], raw["session_id"]
    if status is not None and status not in VALID_ISSUE_STATUSES:
        raise ValueError(f"Unsupported status {status!r}; expected one of {sorted(VALID_ISSUE_STATUSES)}.")
    if severity is not None and severity not in VALID_ISSUE_SEVERITIES:
        raise ValueError(f"Unsupported severity {severity!r}; expected one of {sorted(VALID_ISSUE_SEVERITIES)}.")
    if source not in VALID_BOOKMARK_SOURCES:
        raise ValueError(f"Unsupported source {source!r}; expected one of {sorted(VALID_BOOKMARK_SOURCES)}.")
    if run_kind not in VALID_RUN_KINDS:
        raise ValueError(f"Unsupported run_kind {run_kind!r}; expected one of {sorted(VALID_RUN_KINDS)}.")
    if isinstance(sample_idx, bool) or not isinstance(sample_idx, int) or sample_idx < 0:
        raise ValueError(f"sample_idx must be a non-negative integer, got {sample_idx!r}.")
    if frame_ts_utc_ns is not None and (isinstance(frame_ts_utc_ns, bool) or not isinstance(frame_ts_utc_ns, int)):
        raise ValueError(f"frame_ts_utc_ns must be an integer, got {frame_ts_utc_ns!r}.")
    if car_id is not None and (not isinstance(car_id, str) or not car_id.strip()):
        raise ValueError("car_id must be a non-empty string.")
    if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
        raise ValueError("session_id must be a non-empty string when provided.")

    op_snapshot, op_rpm, op_map = _sanitize_operating_point(raw["operating_point"])
    out: dict[str, Any] = {
        "issue_id": raw["issue_id"].strip() if isinstance(raw["issue_id"], str) and raw["issue_id"].strip() else None,
        "car_id": car_id.strip() if car_id is not None else None,
        "session_id": session_id.strip() if session_id is not None else None,
        "build_id": raw["build_id"].strip() if isinstance(raw["build_id"], str) and raw["build_id"].strip() else None,
        "campaign_id": raw["campaign_id"].strip() if isinstance(raw["campaign_id"], str) and raw["campaign_id"].strip() else None,
        "severity": severity, "status": status, "op_snapshot": op_snapshot, "op_rpm": op_rpm, "op_map": op_map,
        "ts_ns": frame_ts_utc_ns if frame_ts_utc_ns is not None else time.time_ns(),
        "sample_idx": sample_idx, "source": source, "ecu_family": raw["ecu_family"],
        "rom_sha256": raw["rom_sha256"], "run_kind": run_kind, "create_bookmark": raw["create_bookmark"],
    }
    for key in ("category", "title", "description", "note"):
        out[key] = _scrub_text(raw[key]).strip() if raw[key] is not None else None
    for key in ("rpm_min", "rpm_max", "map_min_kpa", "map_max_kpa"):
        out[key] = _validate_optional_finite_float(raw[key], key)
    return out


def _create_issue_row(conn: sqlite3.Connection, p: dict[str, Any]) -> tuple[str, str | None, str]:
    if p["car_id"] is None:
        raise ValueError("car_id must be a non-empty string when creating a tuning issue.")
    target_issue_id = p["issue_id"] or f"iss_{uuid.uuid4().hex[:16]}"
    eff_build_id, eff_category = p["build_id"] or "STOCK", p["category"] or "SCI_BOOKMARK"
    eff_title = p["title"] or f"{eff_category} @ sample {p['sample_idx']}"
    created_at, clean_note = _utc_now_iso(), p["note"] or ""
    if p["description"] is not None:
        eff_description = f"{p['description']}\nnote={clean_note}" if clean_note else p["description"]
    else:
        eff_description = _build_issue_description(
            frame_ts_utc_ns=p["ts_ns"], sample_idx=p["sample_idx"], op_snapshot=p["op_snapshot"],
            active_offsets=[], quarantined_offsets=[], clean_note=clean_note,
        )
    _ensure_vehicle_exists(conn, {"car_id": p["car_id"], "ecu_family": p["ecu_family"]}, p["car_id"])
    if p["session_id"] is not None:
        _ensure_session_exists(
            conn, session_id=p["session_id"], car_id=p["car_id"], build_id=eff_build_id,
            started_at_utc=created_at, run_kind=p["run_kind"], rom_sha256=p["rom_sha256"], quarantined_offsets=[],
        )
    conn.execute(
        """INSERT INTO tuning_issues (
            issue_id, car_id, session_id, build_id, campaign_id, severity, category,
            status, title, description, rpm_min, rpm_max, map_min_kpa, map_max_kpa, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            target_issue_id, p["car_id"], p["session_id"], eff_build_id, p["campaign_id"],
            p["severity"] or "CAUTION", eff_category, p["status"] or "OPEN", eff_title, eff_description,
            p["rpm_min"] if p["rpm_min"] is not None else p["op_rpm"],
            p["rpm_max"] if p["rpm_max"] is not None else p["op_rpm"],
            p["map_min_kpa"] if p["map_min_kpa"] is not None else p["op_map"],
            p["map_max_kpa"] if p["map_max_kpa"] is not None else p["op_map"], created_at,
        ),
    )
    return target_issue_id, p["session_id"], eff_title


def _update_issue_row(conn: sqlite3.Connection, existing: dict[str, Any], p: dict[str, Any]) -> tuple[str, str | None, str]:
    target_issue_id = str(existing["issue_id"])
    target_car_id = p["car_id"] if p["car_id"] is not None else str(existing["car_id"])
    target_session_id = p["session_id"] if p["session_id"] is not None else existing["session_id"]
    target_build_id = p["build_id"] if p["build_id"] is not None else existing["build_id"]
    target_title, clean_note = p["title"] or str(existing["title"]), p["note"] or ""
    if p["description"] is not None:
        target_description = f"{p['description']}\nnote={clean_note}" if clean_note else p["description"]
    elif clean_note:
        prev = str(existing["description"] or "")
        target_description = f"{prev}\nnote={clean_note}" if prev else f"note={clean_note}"
    else:
        target_description = str(existing["description"])

    if p["car_id"] is not None:
        _ensure_vehicle_exists(conn, {"car_id": target_car_id, "ecu_family": p["ecu_family"]}, target_car_id)
    if p["session_id"] is not None:
        _ensure_session_exists(
            conn, session_id=p["session_id"], car_id=target_car_id, build_id=target_build_id or "STOCK",
            started_at_utc=_utc_now_iso(), run_kind=p["run_kind"], rom_sha256=p["rom_sha256"], quarantined_offsets=[],
        )
    conn.execute(
        """UPDATE tuning_issues
           SET car_id = ?, session_id = ?, build_id = ?, campaign_id = ?, severity = ?,
               category = ?, status = ?, title = ?, description = ?,
               rpm_min = ?, rpm_max = ?, map_min_kpa = ?, map_max_kpa = ?
           WHERE issue_id = ?""",
        (
            target_car_id, target_session_id, target_build_id,
            p["campaign_id"] if p["campaign_id"] is not None else existing["campaign_id"],
            p["severity"] if p["severity"] is not None else str(existing["severity"]),
            p["category"] or str(existing["category"]),
            p["status"] if p["status"] is not None else str(existing["status"]),
            target_title, target_description,
            p["rpm_min"] if p["rpm_min"] is not None else existing["rpm_min"],
            p["rpm_max"] if p["rpm_max"] is not None else existing["rpm_max"],
            p["map_min_kpa"] if p["map_min_kpa"] is not None else existing["map_min_kpa"],
            p["map_max_kpa"] if p["map_max_kpa"] is not None else existing["map_max_kpa"],
            target_issue_id,
        ),
    )
    return target_issue_id, target_session_id, target_title


def create_or_update_tuning_issue(
    db_path: Path | str,
    *,
    issue_id: str | None = None, car_id: str | None = None, session_id: str | None = None,
    build_id: str | None = None, campaign_id: str | None = None, severity: str | None = None,
    category: str | None = None, status: str | None = None, title: str | None = None,
    description: str | None = None, note: str | None = None,
    rpm_min: float | None = None, rpm_max: float | None = None,
    map_min_kpa: float | None = None, map_max_kpa: float | None = None,
    operating_point: Mapping[str, Any] | None = None, frame_ts_utc_ns: int | None = None,
    sample_idx: int = 0, source: str = "TOUCH", ecu_family: str = "SBEC_SINGLE_BOARD",
    rom_sha256: str = "0" * 64, run_kind: str = "VEHICLE_SCI", create_bookmark: bool = False,
) -> dict[str, Any]:
    """Create a new tuning_issues record or update an existing one by issue_id."""
    p = _validate_upsert_payload(locals())
    if (p["issue_id"] is None or not Path(db_path).is_file()) and p["car_id"] is None:
        raise ValueError("car_id must be a non-empty string when creating a tuning issue.")

    conn = connect_catalog(db_path)
    try:
        with conn:
            existing_row = (
                conn.execute("SELECT * FROM tuning_issues WHERE issue_id = ?", (p["issue_id"],)).fetchone()
                if p["issue_id"] is not None else None
            )
            action = "created" if existing_row is None else "updated"
            target_issue_id, bmk_session_id, bmk_title = (
                _create_issue_row(conn, p) if existing_row is None else _update_issue_row(conn, dict(existing_row), p)
            )
            bookmark_id = (
                _insert_bookmark(
                    conn, session_id=str(bmk_session_id), issue_id=target_issue_id, ts_ns=p["ts_ns"],
                    sample_idx=p["sample_idx"], source=p["source"], title=bmk_title, clean_note=p["note"] or "",
                )
                if p["create_bookmark"] and bmk_session_id is not None else None
            )
            saved_row = conn.execute("SELECT * FROM tuning_issues WHERE issue_id = ?", (target_issue_id,)).fetchone()
            result = dict(saved_row)
    finally:
        conn.close()
    result["bookmark_id"] = bookmark_id
    result["action"] = action
    return result


def _require_table(conn: sqlite3.Connection, table_name: str, db_path: Path | str) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)).fetchone() is None:
        raise ValueError(f"Catalog database missing required table {table_name!r}: {db_path}")


def get_tuning_issue(issue_id: str, db_path: Path | str) -> dict[str, Any] | None:
    """Return a single tuning_issues row by issue_id, or None if missing."""
    if not isinstance(issue_id, str) or not issue_id.strip():
        return None
    conn = _connect_existing_readonly(db_path)
    if conn is None:
        return None
    try:
        _require_table(conn, "tuning_issues", db_path)
        row = conn.execute("SELECT * FROM tuning_issues WHERE issue_id = ?", (issue_id.strip(),)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_tuning_issues(
    db_path: Path | str,
    *,
    car_id: str | None = None, session_id: str | None = None,
    status: str | None = None, severity: str | None = None, limit: int | None = None,
) -> list[dict[str, Any]]:
    """List persisted tuning issues filtered optionally by car, session, status, or severity."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise ValueError(f"limit must be a non-negative integer, got {limit!r}.")
    conn = _connect_existing_readonly(db_path)
    if conn is None:
        return []
    try:
        _require_table(conn, "tuning_issues", db_path)
        clauses: list[str] = []
        params: list[Any] = []
        for col, val in (("car_id", car_id), ("session_id", session_id), ("status", status), ("severity", severity)):
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            params.append(limit)
        sql = f"SELECT * FROM tuning_issues {where_sql} ORDER BY created_at DESC, issue_id ASC{limit_sql}"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def list_bookmarks(db_path: Path | str, *, session_id: str | None = None) -> list[dict[str, Any]]:
    """List persisted telemetry bookmarks filtered optionally by session_id."""
    conn = _connect_existing_readonly(db_path)
    if conn is None:
        return []
    try:
        _require_table(conn, "bookmarks", db_path)
        clauses = ["session_id = ?"] if session_id is not None else []
        params = [session_id] if session_id is not None else []
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM bookmarks {where_sql} ORDER BY ts_utc_ns ASC, sample_idx ASC, bookmark_id ASC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


__all__ = [
    "VALID_BOOKMARK_SOURCES", "VALID_ISSUE_SEVERITIES", "VALID_ISSUE_STATUSES",
    "bookmark_tuning_issue", "create_or_update_tuning_issue", "get_tuning_issue",
    "list_bookmarks", "list_tuning_issues",
]

