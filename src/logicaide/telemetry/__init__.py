"""Telemetry log parsers, 30s Zstd Parquet chunker, and DuckDB analytical rollups."""

from __future__ import annotations

from logicaide.telemetry.log_parsers import (
    AFR_MAX,
    AFR_MIN,
    BUS_STALL_THRESHOLD_S,
    EGT_RAIL_THRESHOLD_F,
    FORBIDDEN_GPS_COLUMNS,
    PSI_TO_KPA,
    RELATIVE_LOG_BASE_EPOCH_NS,
    STANDARD_BARO_KPA,
    ParsedTelemetrySession,
    TelemetrySample,
    discover_private_logs,
    parse_telemetry_file,
)
from logicaide.telemetry.fixtures import (
    GPS_FORBIDDEN_COLUMNS,
    export_stimulus_fixture,
)
from logicaide.telemetry.parquet_chunker import (
    CANONICAL_COLUMNS,
    CANONICAL_PARQUET_SCHEMA,
    PARQUET_STATISTICS_COLUMNS,
    VALID_RUN_KINDS,
    write_session_parquet_chunks,
)
from logicaide.telemetry.rollups import (
    LOAD_CELL_ROLLUPS_SQL,
    query_load_cell_rollups,
    summarize_telemetry_lake,
)

__all__ = [
    "AFR_MAX",
    "AFR_MIN",
    "BUS_STALL_THRESHOLD_S",
    "CANONICAL_COLUMNS",
    "CANONICAL_PARQUET_SCHEMA",
    "EGT_RAIL_THRESHOLD_F",
    "FORBIDDEN_GPS_COLUMNS",
    "GPS_FORBIDDEN_COLUMNS",
    "LOAD_CELL_ROLLUPS_SQL",
    "PARQUET_STATISTICS_COLUMNS",
    "PSI_TO_KPA",
    "ParsedTelemetrySession",
    "RELATIVE_LOG_BASE_EPOCH_NS",
    "STANDARD_BARO_KPA",
    "TelemetrySample",
    "VALID_RUN_KINDS",
    "discover_private_logs",
    "export_stimulus_fixture",
    "parse_telemetry_file",
    "query_load_cell_rollups",
    "summarize_telemetry_lake",
    "write_session_parquet_chunks",
]
