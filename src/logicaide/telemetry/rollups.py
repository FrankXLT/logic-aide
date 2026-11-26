"""DuckDB analytical load-cell rollups and telemetry lake summaries."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import duckdb

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


def query_load_cell_rollups(
    lake_dir: Path | str = Path("data/drives"),
    *,
    car_id: str | None = None,
    run_kind: str | None = None,
    session_id: str | None = None,
    build_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Query DuckDB ``load_cell_rollups`` over Hive-partitioned Parquet chunks."""
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be non-negative, got {limit}")

    if not _lake_parquet_files(lake_dir):
        return []

    filters: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("car_id", car_id),
        ("run_kind", run_kind),
        ("session_id", session_id),
        ("build_id", build_id),
    ):
        if val is not None:
            filters.append(f"{col} = ?")
            params.append(val)

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    limit_sql = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(int(limit))

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
        return [dict(zip(cols, row)) for row in cur.fetchall()]
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
