"""DuckDB analytical load-cell rollups and telemetry lake summaries."""

from __future__ import annotations

import math
from pathlib import Path
import sqlite3
from typing import Any

import duckdb

from logicaide.heritage.doc_curator import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.telemetry.log_parsers import ALL_ENGINEERED_CHANNELS, FORBIDDEN_GPS_COLUMNS

LOAD_CELL_ROLLUPS_SQL: str = """
CREATE OR REPLACE VIEW load_cell_rollups AS
SELECT
    car_id,
    build_id,
    session_id,
    run_kind,
    campaign_id,
    CAST(FLOOR(rpm / 250.0) * 250 AS INTEGER)     AS rpm_bin,
    CAST(FLOOR(map_kpa / 10.0) * 10 AS INTEGER)   AS map_bin_kpa,
    CASE
        WHEN tps_pct IS NULL THEN 'UNPOLLED'
        WHEN tps_pct < 3.0   THEN 'IDLE'
        WHEN tps_pct < 35.0  THEN 'PART_THROTTLE'
        WHEN tps_pct < 75.0  THEN 'TRANSITION'
        ELSE 'WOT'
    END                                           AS tps_regime,
    CASE
        WHEN act_c IS NULL   THEN 'UNPOLLED'
        WHEN act_c < 20.0    THEN 'COLD_<20C'
        WHEN act_c < 40.0    THEN 'WARM_20_40C'
        WHEN act_c < 60.0    THEN 'HOT_40_60C'
        ELSE 'HEATSOAK_>=60C'
    END                                           AS charge_temp_bin,
    COUNT(*)                                      AS sample_count,
    AVG(pw_ms)                                    AS mean_pw_ms,
    AVG(spark_deg_btdc)                           AS mean_spark_deg,
    AVG(afr)                                      AS mean_afr,
    SUM(knock_counts)                             AS total_knock_events,
    COUNT(knock_counts)                           AS knock_polled_samples,
    MIN(batt_v)                                   AS min_batt_v
FROM read_parquet('data/drives/**/*.parquet', hive_partitioning = true)
WHERE rpm IS NOT NULL
  AND map_kpa IS NOT NULL
  AND NOT dead_cell_quarantine
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9;
""".strip()


def _lake_parquet_files(lake_dir: Path | str) -> list[Path]:
    root = Path(lake_dir)
    if not root.exists():
        return []
    return sorted(root.rglob("*.parquet"))


def _escaped_lake_glob(lake_dir: Path | str) -> str:
    glob_path = Path(lake_dir).as_posix().rstrip("/") + "/**/*.parquet"
    return glob_path.replace("'", "''")


def _build_rollups_view_sql(lake_dir: Path | str) -> str:
    return LOAD_CELL_ROLLUPS_SQL.replace(
        "data/drives/**/*.parquet",
        _escaped_lake_glob(lake_dir),
        1,
    )


def _validate_int_arg(
    name: str,
    value: int | None,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int | None:
    """Validate an optional integer argument, rejecting booleans and out-of-range values."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}, got {value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be <= {max_value}, got {value}")
    return value


def _validate_float_arg(name: str, value: float | None) -> float | None:
    """Validate an optional finite numeric argument, rejecting booleans."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric, got {value!r}")
    fval = float(value)
    if not math.isfinite(fval):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return fval


def query_load_cell_rollups(
    lake_dir: Path | str = Path("data/drives"),
    *,
    car_id: str | None = None,
    run_kind: str | None = None,
    session_id: str | None = None,
    build_id: str | None = None,
    rpm_min: float | None = None,
    rpm_max: float | None = None,
    map_min_kpa: float | None = None,
    map_max_kpa: float | None = None,
    tps_regime: str | None = None,
    charge_temp_bin: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Query DuckDB ``load_cell_rollups`` over Hive-partitioned Parquet chunks."""
    valid_limit = _validate_int_arg("limit", limit, min_value=0)
    rpm_min_v = _validate_float_arg("rpm_min", rpm_min)
    rpm_max_v = _validate_float_arg("rpm_max", rpm_max)
    map_min_v = _validate_float_arg("map_min_kpa", map_min_kpa)
    map_max_v = _validate_float_arg("map_max_kpa", map_max_kpa)

    if not _lake_parquet_files(lake_dir):
        return []

    filters: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("car_id", car_id),
        ("run_kind", run_kind),
        ("session_id", session_id),
        ("build_id", build_id),
        ("tps_regime", tps_regime),
        ("charge_temp_bin", charge_temp_bin),
    ):
        if val is not None:
            filters.append(f"{col} = ?")
            params.append(val)
    for expr, val in (
        ("rpm_bin >= ?", rpm_min_v),
        ("rpm_bin <= ?", rpm_max_v),
        ("map_bin_kpa >= ?", map_min_v),
        ("map_bin_kpa <= ?", map_max_v),
    ):
        if val is not None:
            filters.append(expr)
            params.append(val)

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    limit_sql = " LIMIT ?" if valid_limit is not None else ""
    if valid_limit is not None:
        params.append(valid_limit)

    query_sql = (
        "SELECT * FROM load_cell_rollups "
        f"{where_sql} "
        "ORDER BY car_id, build_id, session_id, run_kind, rpm_bin, map_bin_kpa, tps_regime, charge_temp_bin"
        f"{limit_sql}"
    )

    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_build_rollups_view_sql(lake_dir))
        cur = conn.execute(query_sql, params)
        cols = [desc[0] for desc in cur.description]
        return [{col: _sanitize_scalar(val) for col, val in zip(cols, row)} for row in cur.fetchall()]
    finally:
        conn.close()


def _read_catalog_counts(db_path: Path | str) -> dict[str, int]:
    path = Path(db_path)
    if not path.exists():
        return {"catalog_session_count": 0, "catalog_chunk_count": 0, "catalog_fixture_count": 0}
    conn = sqlite3.connect(path)
    try:
        sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM parquet_chunks").fetchone()[0]
        fixtures = conn.execute("SELECT COUNT(*) FROM stimulus_fixtures").fetchone()[0]
        return {
            "catalog_session_count": int(sessions),
            "catalog_chunk_count": int(chunks),
            "catalog_fixture_count": int(fixtures),
        }
    finally:
        conn.close()


def summarize_telemetry_lake(
    lake_dir: Path | str = Path("data/drives"),
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Summarize Parquet lake sessions, chunks, row counts, quarantine totals, and rollup bins."""
    lake_path = Path(lake_dir)
    parquet_files = _lake_parquet_files(lake_path)

    if not parquet_files:
        summary: dict[str, Any] = {
            "lake_dir": str(lake_path),
            "session_count": 0,
            "chunk_count": 0,
            "total_rows": 0,
            "quarantined_rows": 0,
            "active_rows": 0,
            "rollup_bin_count": 0,
            "run_kinds": [],
            "car_ids": [],
        }
        if db_path is not None:
            summary.update(_read_catalog_counts(db_path))
        return summary

    escaped_glob = _escaped_lake_glob(lake_path)
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(_build_rollups_view_sql(lake_path))
        totals = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT session_id) AS session_count,
                COUNT(*) AS total_rows,
                COALESCE(SUM(CASE WHEN dead_cell_quarantine THEN 1 ELSE 0 END), 0) AS quarantined_rows
            FROM read_parquet('{escaped_glob}', hive_partitioning = true)
            """
        ).fetchone()
        run_kinds = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT run_kind FROM read_parquet('{escaped_glob}', hive_partitioning = true) ORDER BY 1"
            ).fetchall()
        ]
        car_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT car_id FROM read_parquet('{escaped_glob}', hive_partitioning = true) ORDER BY 1"
            ).fetchall()
        ]
        rollup_bin_count = conn.execute("SELECT COUNT(*) FROM load_cell_rollups").fetchone()[0]
    finally:
        conn.close()

    total_rows = int(totals[1] or 0)
    quarantined_rows = int(totals[2] or 0)
    summary = {
        "lake_dir": str(lake_path),
        "session_count": int(totals[0] or 0),
        "chunk_count": len(parquet_files),
        "total_rows": total_rows,
        "quarantined_rows": quarantined_rows,
        "active_rows": total_rows - quarantined_rows,
        "rollup_bin_count": int(rollup_bin_count or 0),
        "run_kinds": run_kinds,
        "car_ids": car_ids,
    }
    if db_path is not None:
        summary.update(_read_catalog_counts(db_path))
    return summary


def _is_gps_column(name: str) -> bool:
    norm = name.strip().lower()
    return norm in FORBIDDEN_GPS_COLUMNS or norm.startswith("gps_")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sanitize_scalar(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return scrub_cleanroom_text(scrub_pii(value))
    return value


def _empty_timeseries_summary(lake_dir: Path | str, *, offset: int, limit: int) -> dict[str, Any]:
    return {
        "lake_dir": str(Path(lake_dir)),
        "offset": offset,
        "limit": limit,
        "total_matching_rows": 0,
        "returned_rows": 0,
        "quarantined_rows": 0,
        "peak_rpm": None,
        "peak_map_kpa": None,
        "peak_boost_psi": None,
        "max_knock_counts": None,
        "min_batt_v": None,
        "polled_channels": [],
        "unpolled_channels": list(ALL_ENGINEERED_CHANNELS),
        "rows": [],
    }


def _build_timeseries_where(
    *,
    session_id: str | None,
    car_id: str | None,
    build_id: str | None,
    run_kind: str | None,
    t_start_ns: int | None,
    t_end_ns: int | None,
    rpm_min: float | None,
    rpm_max: float | None,
    map_min_kpa: float | None,
    map_max_kpa: float | None,
    include_quarantined: bool,
) -> tuple[str, list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("session_id", session_id),
        ("car_id", car_id),
        ("build_id", build_id),
        ("run_kind", run_kind),
    ):
        if val is not None:
            filters.append(f"{_quote_ident(col)} = ?")
            params.append(val)
    if t_start_ns is not None:
        filters.append('"ts_utc_ns" >= ?')
        params.append(t_start_ns)
    if t_end_ns is not None:
        filters.append('"ts_utc_ns" <= ?')
        params.append(t_end_ns)
    for col, op, val in (
        ("rpm", ">=", rpm_min),
        ("rpm", "<=", rpm_max),
        ("map_kpa", ">=", map_min_kpa),
        ("map_kpa", "<=", map_max_kpa),
    ):
        if val is not None:
            qcol = _quote_ident(col)
            filters.append(f"(isfinite(CAST({qcol} AS DOUBLE)) AND {qcol} {op} ?)")
            params.append(val)
    if not include_quarantined:
        filters.append('COALESCE("dead_cell_quarantine", FALSE) = FALSE')
    return (f"WHERE {' AND '.join(filters)}" if filters else ""), params


def _non_nan_agg_expr(func: str, col: str, available_cols: set[str]) -> str:
    if col not in available_cols:
        return "NULL"
    qcol = _quote_ident(col)
    return f"{func}({qcol}) FILTER (WHERE isfinite(CAST({qcol} AS DOUBLE)))"


def _query_timeseries_stats(
    conn: duckdb.DuckDBPyConnection,
    source_sql: str,
    where_sql: str,
    params: list[Any],
    available_cols: set[str],
) -> dict[str, Any]:
    q_expr = (
        'COALESCE(SUM(CASE WHEN "dead_cell_quarantine" THEN 1 ELSE 0 END), 0)'
        if "dead_cell_quarantine" in available_cols
        else "0"
    )
    select_exprs = [
        "COUNT(*)",
        q_expr,
        _non_nan_agg_expr("MAX", "rpm", available_cols),
        _non_nan_agg_expr("MAX", "map_kpa", available_cols),
        _non_nan_agg_expr("MAX", "boost_psi", available_cols),
        _non_nan_agg_expr("MAX", "knock_counts", available_cols),
        _non_nan_agg_expr("MIN", "batt_v", available_cols),
    ] + [
        _non_nan_agg_expr("COUNT", ch, available_cols) if ch in available_cols else "0"
        for ch in ALL_ENGINEERED_CHANNELS
    ]
    rec = conn.execute(f"SELECT {', '.join(select_exprs)} FROM {source_sql} {where_sql}", params).fetchone()
    assert rec is not None
    peak_rpm, peak_map, peak_boost, max_knock, min_batt = (_sanitize_scalar(v) for v in rec[2:7])
    ch_counts = rec[7:]
    polled = [ch for ch, cnt in zip(ALL_ENGINEERED_CHANNELS, ch_counts) if int(cnt or 0) > 0]
    unpolled = [ch for ch in ALL_ENGINEERED_CHANNELS if ch not in polled]
    return {
        "total_matching_rows": int(rec[0] or 0),
        "quarantined_rows": int(rec[1] or 0),
        "peak_rpm": float(peak_rpm) if peak_rpm is not None else None,
        "peak_map_kpa": float(peak_map) if peak_map is not None else None,
        "peak_boost_psi": float(peak_boost) if peak_boost is not None else None,
        "max_knock_counts": int(max_knock) if max_knock is not None else None,
        "min_batt_v": float(min_batt) if min_batt is not None else None,
        "polled_channels": polled,
        "unpolled_channels": unpolled,
    }


def query_drive_timeseries(
    lake_dir: Path | str = Path("data/drives"),
    *,
    session_id: str | None = None,
    car_id: str | None = None,
    build_id: str | None = None,
    run_kind: str | None = None,
    t_start_ns: int | None = None,
    t_end_ns: int | None = None,
    rpm_min: float | None = None,
    rpm_max: float | None = None,
    map_min_kpa: float | None = None,
    map_max_kpa: float | None = None,
    include_quarantined: bool = True,
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Query paginated, GPS-stripped telemetry rows and summary peaks from the Parquet lake."""
    offset_v = _validate_int_arg("offset", offset, min_value=0)
    limit_v = _validate_int_arg("limit", limit, min_value=1, max_value=2000)
    assert offset_v is not None and limit_v is not None
    t_start_v = _validate_int_arg("t_start_ns", t_start_ns)
    t_end_v = _validate_int_arg("t_end_ns", t_end_ns)
    rpm_min_v = _validate_float_arg("rpm_min", rpm_min)
    rpm_max_v = _validate_float_arg("rpm_max", rpm_max)
    map_min_v = _validate_float_arg("map_min_kpa", map_min_kpa)
    map_max_v = _validate_float_arg("map_max_kpa", map_max_kpa)

    if not _lake_parquet_files(lake_dir):
        return _empty_timeseries_summary(lake_dir, offset=offset_v, limit=limit_v)

    source_sql = f"read_parquet('{_escaped_lake_glob(lake_dir)}', hive_partitioning = true)"
    where_sql, params = _build_timeseries_where(
        session_id=session_id,
        car_id=car_id,
        build_id=build_id,
        run_kind=run_kind,
        t_start_ns=t_start_v,
        t_end_ns=t_end_v,
        rpm_min=rpm_min_v,
        rpm_max=rpm_max_v,
        map_min_kpa=map_min_v,
        map_max_kpa=map_max_v,
        include_quarantined=include_quarantined,
    )

    conn = duckdb.connect(":memory:")
    try:
        all_cols = [str(r[0]) for r in conn.execute(f"DESCRIBE SELECT * FROM {source_sql}").fetchall()]
        safe_cols = [c for c in all_cols if not _is_gps_column(c)]
        stats = _query_timeseries_stats(conn, source_sql, where_sql, params, set(all_cols))
        rows: list[dict[str, Any]] = []
        if safe_cols and stats["total_matching_rows"] > offset_v:
            select_sql = ", ".join(_quote_ident(c) for c in safe_cols)
            row_sql = (
                f"SELECT {select_sql} FROM {source_sql} {where_sql} "
                'ORDER BY "ts_utc_ns" ASC, "session_id" ASC LIMIT ? OFFSET ?'
            )
            cur = conn.execute(row_sql, [*params, limit_v, offset_v])
            rows = [{col: _sanitize_scalar(val) for col, val in zip(safe_cols, rec)} for rec in cur.fetchall()]
    finally:
        conn.close()

    return {
        "lake_dir": str(Path(lake_dir)),
        "offset": offset_v,
        "limit": limit_v,
        **stats,
        "returned_rows": len(rows),
        "rows": rows,
    }

