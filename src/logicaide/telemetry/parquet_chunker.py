"""30-second Zstandard Parquet lake chunker and SQLite catalog writer."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping

import pyarrow as pa
import pyarrow.parquet as pq

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB, connect_catalog, upsert_vehicle

PARQUET_STATISTICS_COLUMNS: list[str] = ["rpm", "map_kpa", "tps_pct", "act_c"]

_SCHEMA_SPECS: tuple[tuple[str, pa.DataType, bool], ...] = (
    ("ts_utc_ns", pa.int64(), False), ("session_id", pa.string(), False),
    ("car_id", pa.string(), False), ("build_id", pa.string(), False),
    ("run_kind", pa.string(), False), ("campaign_id", pa.string(), True),
    ("issue_id", pa.string(), True), ("trial_idx", pa.int32(), True),
    ("dead_cell_quarantine", pa.bool_(), False),
    ("raw_rpm_b", pa.uint8(), True), ("raw_map_b", pa.uint8(), True),
    ("raw_tps_b", pa.uint8(), True), ("raw_pw_u16", pa.uint16(), True),
    ("raw_spark_b", pa.uint8(), True), ("raw_act_b", pa.uint8(), True),
    ("raw_cts_b", pa.uint8(), True), ("raw_batt_b", pa.uint8(), True),
    ("raw_o2_b", pa.uint8(), True), ("raw_knock_b", pa.uint8(), True),
    ("rpm", pa.float32(), True), ("map_kpa", pa.float32(), True),
    ("boost_psi", pa.float32(), True), ("tps_pct", pa.float32(), True),
    ("pw_ms", pa.float32(), True), ("spark_deg_btdc", pa.float32(), True),
    ("act_c", pa.float32(), True), ("cts_c", pa.float32(), True),
    ("batt_v", pa.float32(), True), ("afr", pa.float32(), True),
    ("knock_counts", pa.uint16(), True), ("imu_grade_pct", pa.float32(), True),
    ("imu_lon_accel_mps2", pa.float32(), True), ("imu_lat_accel_mps2", pa.float32(), True),
)

CANONICAL_PARQUET_SCHEMA: pa.Schema = pa.schema(
    [pa.field(name, dtype, nullable=nullable) for name, dtype, nullable in _SCHEMA_SPECS]
)
CANONICAL_COLUMNS: tuple[str, ...] = tuple(f.name for f in CANONICAL_PARQUET_SCHEMA)
_FIELD_TYPES: dict[str, pa.DataType] = {f.name: f.type for f in CANONICAL_PARQUET_SCHEMA}
_INT_FIELDS: frozenset[str] = frozenset(
    f.name for f in CANONICAL_PARQUET_SCHEMA if pa.types.is_integer(f.type)
)
_FLOAT_FIELDS: frozenset[str] = frozenset(
    f.name for f in CANONICAL_PARQUET_SCHEMA if pa.types.is_floating(f.type)
)
VALID_RUN_KINDS: frozenset[str] = frozenset({"VEHICLE_SCI", "SINGLE_REPLAY", "MONTE_CARLO_SWEEP"})
_ECU_ALIASES: dict[str, str] = {
    "LM_PM": "LM_PM",
    "LM": "LM_PM",
    "SMEC": "SMEC_DUAL_BOARD",
    "SMEC_DUAL_BOARD": "SMEC_DUAL_BOARD",
    "SBEC": "SBEC_SINGLE_BOARD",
    "SBEC_SINGLE_BOARD": "SBEC_SINGLE_BOARD",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ns_to_iso(ts_utc_ns: int) -> str:
    return datetime.fromtimestamp(ts_utc_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def _project_relative_path(path: Path | str) -> str:
    resolved, cwd = Path(path).resolve(), Path.cwd().resolve()
    try:
        return resolved.relative_to(cwd).as_posix()
    except ValueError:
        return resolved.as_posix()


def _validate_partition_segment(label: str, value: str) -> str:
    if not value or not value.strip() or any(tok in value for tok in ("/", "\\", "..", "=", "\x00")):
        raise ValueError(f"Invalid Hive partition segment {label}={value!r}")
    return value


def _resolve_partition_dir(lake_root: Path, *, run_kind: str, car_id: str, build_id: str, session_id: str) -> Path:
    _validate_partition_segment("car_id", car_id)
    _validate_partition_segment("build_id", build_id)
    _validate_partition_segment("session_id", session_id)
    part_dir = (
        lake_root
        / f"run_kind={run_kind}"
        / f"car_id={car_id}"
        / f"build_id={build_id}"
        / f"session_id={session_id}"
    ).resolve()
    if not part_dir.is_relative_to(lake_root):
        raise ValueError(f"Partition directory {part_dir} escapes lake root {lake_root}")
    return part_dir


def _normalize_ecu_family(raw_family: Any) -> str:
    key = str(raw_family or "SBEC_SINGLE_BOARD").strip().upper()
    if key in _ECU_ALIASES:
        return _ECU_ALIASES[key]
    raise ValueError(f"Unsupported ecu_family: {raw_family!r}")


def _session_attr(session: Any, name: str, default: Any = None) -> Any:
    return session.get(name, default) if isinstance(session, Mapping) else getattr(session, name, default)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if is_dataclass(row) and not isinstance(row, type):
        return asdict(row)
    if hasattr(row, "__dict__"):
        return {k: v for k, v in vars(row).items() if not k.startswith("_")}
    return {col: getattr(row, col, None) for col in CANONICAL_COLUMNS}


def _normalize_scalar(col: str, val: Any) -> Any:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    if col == "dead_cell_quarantine":
        return val.strip().lower() in {"1", "true", "yes"} if isinstance(val, str) else bool(val)
    if col in _INT_FIELDS:
        return int(val)
    if col in _FLOAT_FIELDS:
        return float(val)
    return val if isinstance(val, str) else str(val)


def _normalize_canonical_row(
    raw_row: Any, *, session_id: str, car_id: str, build_id: str, run_kind: str
) -> dict[str, Any]:
    src = _row_to_dict(raw_row)
    if src.get("ts_utc_ns") is None:
        raise ValueError("Telemetry sample is missing required 'ts_utc_ns'.")
    defaults = {"session_id": session_id, "car_id": car_id, "build_id": build_id, "run_kind": run_kind}
    row: dict[str, Any] = {}
    for col in CANONICAL_COLUMNS:
        if col == "ts_utc_ns":
            row[col] = int(src["ts_utc_ns"])
        elif col in defaults:
            row[col] = str(src.get(col) or defaults[col])
        elif col == "dead_cell_quarantine":
            raw_flag = src.get("dead_cell_quarantine")
            row[col] = _normalize_scalar(col, False if raw_flag is None else raw_flag)
        else:
            row[col] = _normalize_scalar(col, src.get(col))
    return row


def _build_chunk_table(rows: list[dict[str, Any]]) -> pa.Table:
    cols = {f.name: pa.array([r[f.name] for r in rows], type=f.type) for f in CANONICAL_PARQUET_SCHEMA}
    return pa.Table.from_pydict(cols, schema=CANONICAL_PARQUET_SCHEMA)


def _min_max(values: list[float | None]) -> tuple[float | None, float | None]:
    present = [v for v in values if v is not None]
    return (float(min(present)), float(max(present))) if present else (None, None)


def _ensure_vehicle_exists(conn: sqlite3.Connection, session: Any, car_id: str) -> None:
    if conn.execute("SELECT 1 FROM vehicles WHERE car_id = ?", (car_id,)).fetchone() is not None:
        return
    year_raw = _session_attr(session, "year")
    upsert_vehicle(
        conn,
        {
            "car_id": car_id,
            "year": int(year_raw) if year_raw is not None else 1991,
            "model": str(_session_attr(session, "model") or "Unknown Mopar"),
            "ecu_family": _normalize_ecu_family(_session_attr(session, "ecu_family")),
            "engine_profile_id": str(_session_attr(session, "engine_profile_id") or "2.5L_TURBO"),
            "transaxle_code": str(_session_attr(session, "transaxle_code") or "A555"),
            "turbo_spec_id": _session_attr(session, "turbo_spec_id"),
            "pinout_verified": int(bool(_session_attr(session, "pinout_verified", False))),
        },
    )


def _persist_chunks_to_catalog(
    db_path: Path | str,
    *,
    session: Any,
    session_meta: dict[str, Any],
    chunks_meta: list[dict[str, Any]],
    lake_root: Path,
    new_paths: set[Path],
) -> None:
    conn = connect_catalog(db_path)
    prior_paths: list[str] = []
    try:
        with conn:
            _ensure_vehicle_exists(conn, session, session_meta["car_id"])
            conn.execute(
                """INSERT INTO sessions (
                    session_id, car_id, build_id, started_at_utc, ended_at_utc, run_kind,
                    rom_sha256, baro_kpa, quarantined_cells_json, imu_attached, gps_stripped, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    car_id=excluded.car_id, build_id=excluded.build_id,
                    started_at_utc=excluded.started_at_utc, ended_at_utc=excluded.ended_at_utc,
                    run_kind=excluded.run_kind, rom_sha256=excluded.rom_sha256,
                    baro_kpa=excluded.baro_kpa, quarantined_cells_json=excluded.quarantined_cells_json,
                    imu_attached=excluded.imu_attached, gps_stripped=1, notes=excluded.notes""",
                (
                    session_meta["session_id"], session_meta["car_id"], session_meta["build_id"],
                    session_meta["started_at_utc"], session_meta["ended_at_utc"], session_meta["run_kind"],
                    session_meta["rom_sha256"], session_meta["baro_kpa"],
                    session_meta["quarantined_cells_json"], session_meta["imu_attached"], session_meta["notes"],
                ),
            )
            prior_paths = [
                row[0]
                for row in conn.execute(
                    "SELECT file_path FROM parquet_chunks WHERE session_id = ?",
                    (session_meta["session_id"],),
                ).fetchall()
            ]
            conn.execute("DELETE FROM parquet_chunks WHERE session_id = ?", (session_meta["session_id"],))
            for chunk in chunks_meta:
                cid = chunk["campaign_id"]
                if cid is not None and conn.execute(
                    "SELECT 1 FROM optimization_campaigns WHERE campaign_id = ?", (cid,)
                ).fetchone() is None:
                    cid = None
                conn.execute(
                    """INSERT INTO parquet_chunks (
                        chunk_id, session_id, car_id, build_id, campaign_id, run_kind, file_path,
                        row_count, t_start_ns, t_end_ns, rpm_min, rpm_max, map_min_kpa, map_max_kpa,
                        zstd_level, sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 3, ?)""",
                    (
                        chunk["chunk_id"], session_meta["session_id"], session_meta["car_id"],
                        session_meta["build_id"], cid, session_meta["run_kind"], chunk["file_path"],
                        chunk["row_count"], chunk["t_start_ns"], chunk["t_end_ns"], chunk["rpm_min"],
                        chunk["rpm_max"], chunk["map_min_kpa"], chunk["map_max_kpa"], chunk["sha256"],
                    ),
                )
    finally:
        conn.close()

    for old_path_str in prior_paths:
        resolved = Path(old_path_str).resolve()
        if resolved not in new_paths and resolved.suffix == ".parquet" and resolved.is_relative_to(lake_root):
            resolved.unlink(missing_ok=True)


def _write_partitioned_chunks(
    rows: list[dict[str, Any]], *, part_dir: Path, session_id: str, chunk_seconds: int
) -> tuple[list[dict[str, Any]], set[Path]]:
    chunks_meta: list[dict[str, Any]] = []
    new_paths: set[Path] = set()
    if rows:
        t0_ns, window_ns = rows[0]["ts_utc_ns"], int(chunk_seconds) * 1_000_000_000
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault((row["ts_utc_ns"] - t0_ns) // window_ns, []).append(row)

        part_dir.mkdir(parents=True, exist_ok=True)
        for window_idx in sorted(grouped):
            crows = grouped[window_idx]
            cpath = (part_dir / f"chunk_{window_idx:04d}.parquet").resolve()
            pq.write_table(
                _build_chunk_table(crows), cpath, compression="zstd",
                compression_level=3, write_statistics=PARQUET_STATISTICS_COLUMNS,
            )
            new_paths.add(cpath)
            rpm_min, rpm_max = _min_max([r["rpm"] for r in crows])
            map_min_kpa, map_max_kpa = _min_max([r["map_kpa"] for r in crows])
            campaigns = [r["campaign_id"] for r in crows if r["campaign_id"] is not None]
            chunks_meta.append({
                "chunk_id": f"{session_id}:chunk_{window_idx:04d}",
                "file_path": _project_relative_path(cpath), "abs_path": str(cpath),
                "row_count": len(crows), "t_start_ns": crows[0]["ts_utc_ns"], "t_end_ns": crows[-1]["ts_utc_ns"],
                "rpm_min": rpm_min, "rpm_max": rpm_max, "map_min_kpa": map_min_kpa, "map_max_kpa": map_max_kpa,
                "campaign_id": campaigns[0] if campaigns else None,
                "sha256": hashlib.sha256(cpath.read_bytes()).hexdigest(),
            })

    if part_dir.exists():
        for leftover in part_dir.glob("chunk_*.parquet"):
            if leftover.resolve() not in new_paths:
                leftover.unlink(missing_ok=True)
    return chunks_meta, new_paths


def write_session_parquet_chunks(
    session: Any,
    lake_dir: Path | str = Path("data/drives"),
    db_path: Path | str | None = Path("data/drives/catalog.sqlite"),
    chunk_seconds: int = 30,
) -> dict[str, Any]:
    """Partition session telemetry into 30s Zstd Parquet chunks and upsert SQLite catalog."""
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be positive, got {chunk_seconds}")

    session_id = str(_session_attr(session, "session_id") or "")
    car_id = str(_session_attr(session, "car_id") or "")
    build_id = str(_session_attr(session, "build_id") or "STOCK")
    run_kind = str(_session_attr(session, "run_kind") or "VEHICLE_SCI")
    if run_kind not in VALID_RUN_KINDS:
        raise ValueError(f"Invalid run_kind {run_kind!r}; expected one of {sorted(VALID_RUN_KINDS)}")

    lake_root = Path(lake_dir).resolve()
    part_dir = _resolve_partition_dir(
        lake_root, run_kind=run_kind, car_id=car_id, build_id=build_id, session_id=session_id
    )

    rows = [
        _normalize_canonical_row(r, session_id=session_id, car_id=car_id, build_id=build_id, run_kind=run_kind)
        for r in (_session_attr(session, "rows") or [])
    ]
    rows.sort(key=lambda r: r["ts_utc_ns"])

    started_at = _session_attr(session, "started_at_utc") or (_ns_to_iso(rows[0]["ts_utc_ns"]) if rows else _utc_now_iso())
    ended_at = _session_attr(session, "ended_at_utc") or (_ns_to_iso(rows[-1]["ts_utc_ns"]) if rows else None)
    imu_attached = int(
        bool(_session_attr(session, "imu_attached"))
        or any(r[c] is not None for r in rows for c in ("imu_grade_pct", "imu_lon_accel_mps2", "imu_lat_accel_mps2"))
    )
    baro_raw = _session_attr(session, "baro_kpa")
    session_meta = {
        "session_id": session_id, "car_id": car_id, "build_id": build_id,
        "started_at_utc": str(started_at), "ended_at_utc": str(ended_at) if ended_at is not None else None,
        "run_kind": run_kind, "rom_sha256": str(_session_attr(session, "rom_sha256") or ("0" * 64)),
        "baro_kpa": float(baro_raw) if baro_raw is not None else 101.325,
        "quarantined_cells_json": json.dumps(list(_session_attr(session, "quarantined_cells") or []), sort_keys=True),
        "imu_attached": imu_attached, "notes": _session_attr(session, "notes"),
    }
    chunks_meta, new_paths = _write_partitioned_chunks(
        rows, part_dir=part_dir, session_id=session_id, chunk_seconds=chunk_seconds
    )
    if db_path is not None:
        _persist_chunks_to_catalog(
            db_path, session=session, session_meta=session_meta,
            chunks_meta=chunks_meta, lake_root=lake_root, new_paths=new_paths,
        )

    return {
        "session_id": session_id, "car_id": car_id, "build_id": build_id,
        "run_kind": run_kind, "chunk_count": len(chunks_meta), "row_count": len(rows),
        "chunks": chunks_meta,
    }


def __getattr__(name: str) -> Any:
    """Backward-compatibility re-export of fixture symbols moved to ``fixtures.py``."""
    if name in {"GPS_FORBIDDEN_COLUMNS", "export_stimulus_fixture"}:
        from logicaide.telemetry import fixtures
        return getattr(fixtures, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
