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

from logicaide.catalog.sqlite_store import connect_catalog
from logicaide.heritage.doc_curator import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.telemetry.fixtures import _is_gps_column
from logicaide.telemetry.parquet_chunker import (
    VALID_RUN_KINDS,
    _ensure_vehicle_exists,
    _utc_now_iso,
)

VALID_ISSUE_SEVERITIES: frozenset[str] = frozenset({"INFO", "CAUTION", "CRITICAL"})
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


def _persist_issue_and_bookmark(
    db_path: Path | str,
    *,
    issue_id: str,
    car_id: str,
    session_id: str | None,
    build_id: str,
    ecu_family: str,
    run_kind: str,
    rom_sha256: str,
    severity: str,
    category: str,
    title: str,
    description: str,
    op_rpm: float | None,
    op_map: float | None,
    created_at: str,
    ts_ns: int,
    sample_idx: int,
    source: str,
    clean_note: str,
    quarantined_offsets: list[str],
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
                bookmark_id = f"bmk_{uuid.uuid4().hex[:16]}"
                bmk_note = f"[{issue_id}] {clean_note}".strip()
                conn.execute(
                    """INSERT INTO bookmarks (
                        bookmark_id, session_id, ts_utc_ns, sample_idx, source, label, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (bookmark_id, session_id, ts_ns, sample_idx, source, title, bmk_note),
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


def list_tuning_issues(
    db_path: Path | str,
    *,
    car_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List persisted tuning issues filtered optionally by car, session, or status."""
    clauses: list[str] = []
    params: list[Any] = []
    for col, val in (("car_id", car_id), ("session_id", session_id), ("status", status)):
        if val is not None:
            clauses.append(f"{col} = ?")
            params.append(val)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM tuning_issues {where_sql} ORDER BY created_at DESC, issue_id ASC"
    conn = connect_catalog(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_bookmarks(
    db_path: Path | str,
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """List persisted telemetry bookmarks filtered optionally by session_id."""
    clauses = ["session_id = ?"] if session_id is not None else []
    params = [session_id] if session_id is not None else []
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM bookmarks {where_sql} ORDER BY ts_utc_ns ASC, sample_idx ASC, bookmark_id ASC"
    conn = connect_catalog(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


__all__ = [
    "VALID_BOOKMARK_SOURCES",
    "VALID_ISSUE_SEVERITIES",
    "bookmark_tuning_issue",
    "list_bookmarks",
    "list_tuning_issues",
]
