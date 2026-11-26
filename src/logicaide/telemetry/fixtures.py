"""GPS-stripped, PII-scrubbed stimulus fixture exporter for telemetry lakes and logs."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from logicaide.catalog.sqlite_store import connect_catalog
from logicaide.heritage.doc_curator import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.telemetry.parquet_chunker import (
    CANONICAL_COLUMNS,
    _FIELD_TYPES,
    _build_chunk_table,
    _ensure_vehicle_exists,
    _normalize_canonical_row,
    _normalize_scalar,
    _project_relative_path,
    _utc_now_iso,
)

GPS_FORBIDDEN_COLUMNS: frozenset[str] = frozenset({
    "lat", "lon", "lng", "latitude", "longitude",
    "altitude", "alt", "gps_track", "elevation", "gps_lat", "gps_lon",
})


def _is_gps_column(name: str) -> bool:
    norm = name.strip().lower()
    return norm in GPS_FORBIDDEN_COLUMNS or norm.startswith("gps_")


def _coerce_csv_value(col: str, raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    return _normalize_scalar(col, raw) if col in _FIELD_TYPES else raw


def _read_lake_rows(
    lake_dir: Path, *, car_id: str | None, session_id: str | None, max_rows: int
) -> tuple[list[dict[str, Any]], list[str]]:
    if not any(lake_dir.rglob("*.parquet")):
        raise FileNotFoundError(f"No Parquet chunks found under {lake_dir}")
    escaped_glob = (lake_dir.as_posix().rstrip("/") + "/**/*.parquet").replace("'", "''")
    filters = [f"{col} = ?" for col, val in (("car_id", car_id), ("session_id", session_id)) if val is not None]
    params: list[Any] = [val for val in (car_id, session_id) if val is not None] + [max_rows]
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = (
        f"SELECT * FROM read_parquet('{escaped_glob}', hive_partitioning = true) "
        f"{where_sql} ORDER BY ts_utc_ns ASC, session_id ASC LIMIT ?"
    )
    conn = duckdb.connect(":memory:")
    try:
        cur = conn.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, rec)) for rec in cur.fetchall()], cols
    finally:
        conn.close()


def _read_source_rows(
    source_path: Path, *, car_id: str | None, session_id: str | None, max_rows: int
) -> tuple[list[dict[str, Any]], list[str]]:
    if not source_path.exists():
        raise FileNotFoundError(f"Telemetry source not found: {source_path}")
    if source_path.is_dir():
        return _read_lake_rows(source_path, car_id=car_id, session_id=session_id, max_rows=max_rows)
    suffix = source_path.suffix.lower()
    if suffix == ".parquet":
        table = pq.read_table(source_path)
        cols, rows = list(table.column_names), table.to_pylist()
    elif suffix == ".csv":
        with source_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            cols = list(reader.fieldnames or [])
            rows = [{k: _coerce_csv_value(k, v) for k, v in rec.items()} for rec in reader]
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        text = source_path.read_text(encoding="utf-8").strip()
        if suffix in {".jsonl", ".ndjson"}:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            payload = json.loads(text) if text else []
            rows = payload["rows"] if isinstance(payload, dict) and "rows" in payload else list(payload)
        cols = list(rows[0].keys()) if rows else []
    else:
        raise ValueError(f"Unsupported stimulus source format: {source_path.suffix!r}")

    if car_id is not None:
        rows = [r for r in rows if r.get("car_id") == car_id]
    if session_id is not None:
        rows = [r for r in rows if r.get("session_id") == session_id]
    return rows[:max_rows], cols


def _scrub_and_strip_rows(
    rows: list[dict[str, Any]], columns: list[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    stripped_gps = sorted({k for k in columns if _is_gps_column(k)} | {k for r in rows for k in r if _is_gps_column(k)})
    cleaned_rows = [
        {
            k: (scrub_cleanroom_text(scrub_pii(v)) if isinstance(v, str) else v)
            for k, v in row.items()
            if not _is_gps_column(k)
        }
        for row in rows
    ]
    base_cols = [c for c in columns if not _is_gps_column(c)]
    for row in cleaned_rows:
        for key in row:
            if key not in base_cols:
                base_cols.append(key)
    fieldnames = [c for c in CANONICAL_COLUMNS if c in base_cols] + sorted(
        c for c in base_cols if c not in _FIELD_TYPES
    )
    return cleaned_rows, fieldnames, stripped_gps


def _write_fixture_file(output_path: Path, rows: list[dict[str, Any]], fieldnames: list[str], fmt: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
            if fieldnames:
                writer.writeheader()
            for row in rows:
                writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames})
        return
    if fmt == "json":
        output_path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return
    if fmt == "parquet":
        if rows and {"ts_utc_ns", "session_id", "car_id", "build_id", "run_kind"} <= set(fieldnames):
            table = _build_chunk_table([
                _normalize_canonical_row(
                    r, session_id=str(r["session_id"]), car_id=str(r["car_id"]),
                    build_id=str(r["build_id"]), run_kind=str(r["run_kind"]),
                )
                for r in rows
            ])
        else:
            table = pa.Table.from_pylist(rows) if rows else _build_chunk_table([])
        pq.write_table(table, output_path, compression="zstd", compression_level=3)
        return
    raise ValueError(f"Unsupported fixture output format {fmt!r}; expected 'csv', 'json', or 'parquet'.")


def _record_fixture_in_catalog(
    db_path: Path | str, *, output_path: Path, sha256: str, car_id: str,
    source_session_id: str | None, explicit_session: bool, issue_id: str | None,
) -> str:
    rel_path = _project_relative_path(output_path)
    fixture_id = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:16]
    conn = connect_catalog(db_path)
    try:
        with conn:
            _ensure_vehicle_exists(conn, {"car_id": car_id}, car_id)
            resolved_session = source_session_id
            if resolved_session is not None:
                found = conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (resolved_session,)).fetchone()
                if found is None:
                    if explicit_session:
                        raise ValueError(f"Unknown source session_id {resolved_session!r} in catalog {db_path}")
                    resolved_session = None
            conn.execute(
                """
                INSERT INTO stimulus_fixtures (
                    fixture_id, car_id, source_session_id, issue_id, file_path, gps_stripped, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    car_id=excluded.car_id, source_session_id=excluded.source_session_id,
                    issue_id=excluded.issue_id, gps_stripped=1, sha256=excluded.sha256,
                    created_at=excluded.created_at
                """,
                (fixture_id, car_id, resolved_session, issue_id, rel_path, sha256, _utc_now_iso()),
            )
    finally:
        conn.close()
    return fixture_id


def export_stimulus_fixture(
    source_path_or_lake: Path | str,
    output_path: Path | str,
    *,
    max_rows: int = 150,
    fmt: str = "csv",
    car_id: str | None = None,
    session_id: str | None = None,
    db_path: Path | str | None = None,
    issue_id: str | None = None,
) -> dict[str, Any]:
    """Export a GPS-stripped, PII-scrubbed stimulus fixture slice (<= max_rows) to CSV/JSON/Parquet."""
    if max_rows <= 0:
        raise ValueError(f"max_rows must be positive, got {max_rows}")
    fmt_norm = fmt.strip().lower()
    if fmt_norm not in {"csv", "json", "parquet"}:
        raise ValueError(f"Unsupported fixture format {fmt!r}; expected 'csv', 'json', or 'parquet'.")

    out_path = Path(output_path)
    raw_rows, columns = _read_source_rows(
        Path(source_path_or_lake), car_id=car_id, session_id=session_id, max_rows=max_rows
    )
    cleaned_rows, fieldnames, stripped_gps = _scrub_and_strip_rows(raw_rows, columns)
    _write_fixture_file(out_path, cleaned_rows, fieldnames, fmt_norm)
    sha256 = hashlib.sha256(out_path.read_bytes()).hexdigest()

    fixture_id: str | None = None
    if db_path is not None:
        inferred_car = car_id or (str(cleaned_rows[0]["car_id"]) if cleaned_rows and cleaned_rows[0].get("car_id") else "UNKNOWN_CAR")
        inferred_session = session_id or (str(cleaned_rows[0]["session_id"]) if cleaned_rows and cleaned_rows[0].get("session_id") else None)
        fixture_id = _record_fixture_in_catalog(
            db_path, output_path=out_path, sha256=sha256, car_id=inferred_car,
            source_session_id=inferred_session, explicit_session=session_id is not None, issue_id=issue_id,
        )

    return {
        "output_path": str(out_path), "format": fmt_norm, "row_count": len(cleaned_rows),
        "gps_stripped": True, "stripped_gps_columns": stripped_gps, "sha256": sha256,
        "fixture_id": fixture_id,
    }
