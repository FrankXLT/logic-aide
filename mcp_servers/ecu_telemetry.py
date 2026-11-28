"""Thin MCP wrapper for read-only ECU telemetry lake queries, bookmarks, and GPS-stripped fixtures."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, TextIO

import duckdb
import pyarrow as pa

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[1]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from mcp_servers._common import (
    PROTOCOL_VERSION, REPO_ROOT, SRC_ROOT, ToolEntry as _ToolEntry,
    create_fastmcp_server as _create_fastmcp_server, default_error_payload,
    display_repo_path, ensure_repo_sys_path, handle_jsonrpc_request as _handle_jsonrpc_request,
    json_safe as _json_safe, reject_bool_ints, run_mcp_server,
    schema as _schema, serve_stdio as _serve_stdio, validate_path_arg,
)

ensure_repo_sys_path()

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB, list_vehicles as _list_vehicles
from logicaide.heritage.doc_curator import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.telemetry.fixtures import export_stimulus_fixture
from logicaide.telemetry.issues import (
    VALID_BOOKMARK_SOURCES, VALID_ISSUE_SEVERITIES, VALID_ISSUE_STATUSES,
    create_or_update_tuning_issue as _create_or_update_tuning_issue,
    list_bookmarks as _list_bookmarks, list_tuning_issues as _list_tuning_issues,
)
from logicaide.telemetry.parquet_chunker import VALID_RUN_KINDS
from logicaide.telemetry.rollups import (
    query_drive_timeseries as _query_drive_timeseries,
    query_load_cell_rollups as _query_load_cell_rollups,
    summarize_telemetry_lake,
)
from logicaide.telemetry.watch_cli import DEFAULT_LAKE_DIR

SERVER_NAME = "ecu-telemetry"
SERVER_VERSION = "0.1.0"
_PROTECTED_ROM_DIRS: tuple[str, ...] = ("rom/inputs", "rom/stock_baselines", "rom/src")
_PATH_FIELDS = frozenset({"db_path", "lake_dir", "output_path", "file_path", "parquet_chunks"})


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Return a structured non-raising error payload."""
    return default_error_payload(exc, status="ERROR")


def _reject_bool_ints(**params: Any) -> dict[str, Any] | None:
    """Return a structured error payload if any numeric parameter was passed as a bool."""
    return reject_bool_ints(_error_payload, **params)


def _validate_path_arg(
    name: str,
    raw_path: Path | str,
    *,
    repo_root: Path = REPO_ROOT,
    reject_protected: tuple[str, ...] = (),
) -> Path:
    """Reject symlinks, relative escapes outside repo_root, and optional protected directories."""
    return validate_path_arg(name, raw_path, repo_root=repo_root, reject_protected=reject_protected)


def _scrub_payload(value: Any, *, field_name: str | None = None) -> Any:
    """Recursively normalize repo-contained paths and scrub PII/cleanroom tokens from strings."""
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        normalized = display_repo_path(value) if field_name in _PATH_FIELDS and value else value
        return scrub_cleanroom_text(scrub_pii(normalized))
    if isinstance(value, Mapping):
        return {str(k): _scrub_payload(v, field_name=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_payload(v, field_name=field_name) for v in value]
    return value


def _call_upstream(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke an upstream domain function and normalize expected errors to JSON dicts."""
    try:
        return _json_safe(fn(*args, **kwargs))
    except (
        duckdb.Error, pa.lib.ArrowInvalid, sqlite3.Error,
        FileNotFoundError, KeyError, ValueError, TypeError, OSError,
    ) as exc:
        return _error_payload(exc)


def list_vehicles(
    db_path: Path | str = DEFAULT_CATALOG_DB, car_id: str | None = None, ecu_family: str | None = None,
) -> dict[str, Any]:
    """List registered vehicles from the read-only SQLite catalog."""
    def _run() -> dict[str, Any]:
        safe_db = _validate_path_arg("db_path", db_path)
        vehicles = _list_vehicles(db_path=safe_db, car_id=car_id, ecu_family=ecu_family)
        return _scrub_payload({"db_path": display_repo_path(safe_db), "count": len(vehicles), "vehicles": vehicles})

    return _call_upstream(_run)


def query_load_cell_rollups(
    lake_dir: Path | str = DEFAULT_LAKE_DIR, car_id: str | None = None, run_kind: str | None = None,
    session_id: str | None = None, build_id: str | None = None, rpm_min: float | None = None,
    rpm_max: float | None = None, map_min_kpa: float | None = None, map_max_kpa: float | None = None,
    tps_regime: str | None = None, charge_temp_bin: str | None = None, limit: int | None = None,
) -> dict[str, Any]:
    """Summarize the telemetry lake and query DuckDB load_cell_rollups bins."""
    if err := _reject_bool_ints(
        limit=limit, rpm_min=rpm_min, rpm_max=rpm_max, map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa,
    ):
        return err

    def _run() -> dict[str, Any]:
        safe_lake = _validate_path_arg("lake_dir", lake_dir)
        summary = summarize_telemetry_lake(safe_lake)
        bins = _query_load_cell_rollups(
            safe_lake, car_id=car_id, run_kind=run_kind, session_id=session_id,
            build_id=build_id, rpm_min=rpm_min, rpm_max=rpm_max,
            map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa,
            tps_regime=tps_regime, charge_temp_bin=charge_temp_bin, limit=limit,
        )
        return _scrub_payload({"lake_dir": display_repo_path(safe_lake), "summary": summary, "bin_count": len(bins), "rollups": bins})

    return _call_upstream(_run)


def get_drive_timeseries(
    lake_dir: Path | str = DEFAULT_LAKE_DIR, session_id: str | None = None, car_id: str | None = None,
    build_id: str | None = None, run_kind: str | None = None, t_start_ns: int | None = None,
    t_end_ns: int | None = None, rpm_min: float | None = None, rpm_max: float | None = None,
    map_min_kpa: float | None = None, map_max_kpa: float | None = None,
    include_quarantined: bool = True, offset: int = 0, limit: int = 200,
) -> dict[str, Any]:
    """Query paginated, GPS-stripped telemetry rows and summary peaks from the Parquet lake."""
    if err := _reject_bool_ints(
        t_start_ns=t_start_ns, t_end_ns=t_end_ns, rpm_min=rpm_min, rpm_max=rpm_max,
        map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa, offset=offset, limit=limit,
    ):
        return err

    def _run() -> dict[str, Any]:
        safe_lake = _validate_path_arg("lake_dir", lake_dir)
        return _scrub_payload(_query_drive_timeseries(
            safe_lake, session_id=session_id, car_id=car_id, build_id=build_id, run_kind=run_kind,
            t_start_ns=t_start_ns, t_end_ns=t_end_ns, rpm_min=rpm_min, rpm_max=rpm_max,
            map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa,
            include_quarantined=include_quarantined, offset=offset, limit=limit,
        ))

    return _call_upstream(_run)


def list_bookmarks(
    db_path: Path | str = DEFAULT_CATALOG_DB, car_id: str | None = None, session_id: str | None = None,
    status: str | None = None, severity: str | None = None, limit: int | None = None,
) -> dict[str, Any]:
    """List persisted bookmarks and tuning issues without mutating missing catalog DBs."""
    if err := _reject_bool_ints(limit=limit):
        return err

    def _run() -> dict[str, Any]:
        if limit is not None and (not isinstance(limit, int) or limit < 0):
            raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
        safe_db = _validate_path_arg("db_path", db_path)
        if not safe_db.is_file():
            return _scrub_payload({
                "db_path": display_repo_path(safe_db), "bookmark_count": 0, "issue_count": 0,
                "bookmarks": [], "tuning_issues": [],
            })
        _list_vehicles(db_path=safe_db)
        bookmarks = _list_bookmarks(safe_db, session_id=session_id)
        if limit is not None:
            bookmarks = bookmarks[:limit]
        issues = _list_tuning_issues(
            safe_db, car_id=car_id, session_id=session_id, status=status, severity=severity, limit=limit,
        )
        return _scrub_payload({
            "db_path": display_repo_path(safe_db), "bookmark_count": len(bookmarks), "issue_count": len(issues),
            "bookmarks": bookmarks, "tuning_issues": issues,
        })

    return _call_upstream(_run)


def create_or_update_tuning_issue(
    db_path: Path | str = DEFAULT_CATALOG_DB, issue_id: str | None = None, car_id: str | None = None,
    session_id: str | None = None, build_id: str | None = None, campaign_id: str | None = None,
    severity: str | None = None, category: str | None = None, status: str | None = None,
    title: str | None = None, description: str | None = None, note: str | None = None,
    rpm_min: float | None = None, rpm_max: float | None = None, map_min_kpa: float | None = None,
    map_max_kpa: float | None = None, operating_point: Mapping[str, Any] | None = None,
    frame_ts_utc_ns: int | None = None, sample_idx: int = 0, source: str = "TOUCH",
    ecu_family: str = "SBEC_SINGLE_BOARD", rom_sha256: str = "0" * 64,
    run_kind: str = "VEHICLE_SCI", create_bookmark: bool = False,
) -> dict[str, Any]:
    """Create or update a tuning_issues record and optionally attach a telemetry bookmark."""
    if err := _reject_bool_ints(
        rpm_min=rpm_min, rpm_max=rpm_max, map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa,
        frame_ts_utc_ns=frame_ts_utc_ns, sample_idx=sample_idx,
    ):
        return err

    def _run() -> dict[str, Any]:
        safe_db = _validate_path_arg("db_path", db_path, reject_protected=_PROTECTED_ROM_DIRS)
        return _scrub_payload(_create_or_update_tuning_issue(
            safe_db, issue_id=issue_id, car_id=car_id, session_id=session_id, build_id=build_id,
            campaign_id=campaign_id, severity=severity, category=category, status=status,
            title=title, description=description, note=note, rpm_min=rpm_min, rpm_max=rpm_max,
            map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa, operating_point=operating_point,
            frame_ts_utc_ns=frame_ts_utc_ns, sample_idx=sample_idx, source=source,
            ecu_family=ecu_family, rom_sha256=rom_sha256, run_kind=run_kind,
            create_bookmark=create_bookmark,
        ))

    return _call_upstream(_run)


def export_gps_stripped_fixture(
    source: Path | str = DEFAULT_LAKE_DIR, output_path: Path | str = "", max_rows: int = 150,
    fmt: str = "csv", car_id: str | None = None, session_id: str | None = None,
    db_path: Path | str | None = None, issue_id: str | None = None,
) -> dict[str, Any]:
    """Export a GPS-stripped, PII-scrubbed stimulus fixture slice from telemetry logs or Parquet."""
    if err := _reject_bool_ints(max_rows=max_rows):
        return err

    def _run() -> dict[str, Any]:
        safe_source = _validate_path_arg("source", source)
        safe_output = _validate_path_arg("output_path", output_path, reject_protected=_PROTECTED_ROM_DIRS)
        safe_db = (
            _validate_path_arg("db_path", db_path, reject_protected=_PROTECTED_ROM_DIRS)
            if db_path is not None else None
        )
        res = export_stimulus_fixture(
            safe_source, safe_output, max_rows=max_rows, fmt=fmt,
            car_id=car_id, session_id=session_id, db_path=safe_db, issue_id=issue_id,
        )
        return _scrub_payload({**res, "output_path": display_repo_path(safe_output)})

    return _call_upstream(_run)


TOOL_REGISTRY: dict[str, _ToolEntry] = {
    entry["name"]: entry
    for entry in (
        _ToolEntry(
            list_vehicles,
            "List registered vehicles and latest profile revision metadata from the SQLite catalog.",
            _schema({"db_path": {"type": "string"}, "car_id": {"type": "string"}, "ecu_family": {"type": "string"}}),
        ),
        _ToolEntry(
            query_load_cell_rollups,
            "Summarize the Parquet telemetry lake and query DuckDB load_cell_rollups bins.",
            _schema({
                "lake_dir": {"type": "string"}, "car_id": {"type": "string"}, "run_kind": {"type": "string"},
                "session_id": {"type": "string"}, "build_id": {"type": "string"},
                "rpm_min": {"type": "number"}, "rpm_max": {"type": "number"},
                "map_min_kpa": {"type": "number"}, "map_max_kpa": {"type": "number"},
                "tps_regime": {"type": "string"}, "charge_temp_bin": {"type": "string"},
                "limit": {"type": "integer", "minimum": 0},
            }),
        ),
        _ToolEntry(
            get_drive_timeseries,
            "Query paginated, GPS-stripped telemetry rows and summary peaks from the Parquet lake.",
            _schema({
                "lake_dir": {"type": "string"}, "session_id": {"type": "string"}, "car_id": {"type": "string"},
                "build_id": {"type": "string"}, "run_kind": {"type": "string"},
                "t_start_ns": {"type": "integer"}, "t_end_ns": {"type": "integer"},
                "rpm_min": {"type": "number"}, "rpm_max": {"type": "number"},
                "map_min_kpa": {"type": "number"}, "map_max_kpa": {"type": "number"},
                "include_quarantined": {"type": "boolean", "default": True},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
            }),
        ),
        _ToolEntry(
            list_bookmarks,
            "List persisted telemetry bookmarks and tuning issues from the SQLite catalog.",
            _schema({
                "db_path": {"type": "string"}, "car_id": {"type": "string"}, "session_id": {"type": "string"},
                "status": {"type": "string"}, "severity": {"type": "string"}, "limit": {"type": "integer", "minimum": 0},
            }),
        ),
        _ToolEntry(
            create_or_update_tuning_issue,
            "Create or update a tuning_issues record and optionally attach a telemetry bookmark.",
            _schema({
                "db_path": {"type": "string"}, "issue_id": {"type": "string"}, "car_id": {"type": "string"},
                "session_id": {"type": "string"}, "build_id": {"type": "string"}, "campaign_id": {"type": "string"},
                "severity": {"type": "string", "enum": sorted(VALID_ISSUE_SEVERITIES)}, "category": {"type": "string"},
                "status": {"type": "string", "enum": sorted(VALID_ISSUE_STATUSES)}, "title": {"type": "string"},
                "description": {"type": "string"}, "note": {"type": "string"},
                "rpm_min": {"type": "number"}, "rpm_max": {"type": "number"},
                "map_min_kpa": {"type": "number"}, "map_max_kpa": {"type": "number"},
                "operating_point": {"type": "object"}, "frame_ts_utc_ns": {"type": "integer"},
                "sample_idx": {"type": "integer", "minimum": 0, "default": 0},
                "source": {"type": "string", "enum": sorted(VALID_BOOKMARK_SOURCES), "default": "TOUCH"},
                "ecu_family": {"type": "string", "default": "SBEC_SINGLE_BOARD"}, "rom_sha256": {"type": "string"},
                "run_kind": {"type": "string", "enum": sorted(VALID_RUN_KINDS), "default": "VEHICLE_SCI"},
                "create_bookmark": {"type": "boolean", "default": False},
            }),
        ),
        _ToolEntry(
            export_gps_stripped_fixture,
            "Export a GPS-stripped, PII-scrubbed stimulus fixture slice (CSV/JSON/Parquet).",
            _schema({
                "source": {"type": "string", "default": str(DEFAULT_LAKE_DIR)}, "output_path": {"type": "string"},
                "max_rows": {"type": "integer", "minimum": 1, "default": 150},
                "fmt": {"type": "string", "enum": ["csv", "json", "parquet"], "default": "csv"},
                "car_id": {"type": "string"}, "session_id": {"type": "string"},
                "db_path": {"type": "string"}, "issue_id": {"type": "string"},
            }, required=("output_path",)),
        ),
    )
}
TOOLS = TOOL_REGISTRY


def create_fastmcp_server() -> Any | None:
    """Build a FastMCP('ecu-telemetry') instance if mcp/fastmcp is installed, else None."""
    return _create_fastmcp_server(SERVER_NAME, TOOL_REGISTRY)


def handle_jsonrpc_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an offline MCP JSON-RPC 2.0 request deterministically."""
    return _handle_jsonrpc_request(
        payload, server_name=SERVER_NAME, server_version=SERVER_VERSION,
        tool_registry=TOOL_REGISTRY, protocol_version=PROTOCOL_VERSION,
    )


def serve_stdio(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Serve newline-delimited MCP JSON-RPC 2.0 requests over stdio."""
    return _serve_stdio(handle_jsonrpc_request, stdin=stdin, stdout=stdout)


def main(argv: list[str] | None = None) -> int:
    """Entrypoint supporting FastMCP when installed or offline stdio JSON-RPC fallback."""
    return run_mcp_server(SERVER_NAME, SERVER_VERSION, TOOL_REGISTRY, argv=argv)


__all__ = [
    "SERVER_NAME", "SERVER_VERSION", "TOOL_REGISTRY", "TOOLS",
    "create_fastmcp_server", "create_or_update_tuning_issue", "export_gps_stripped_fixture",
    "get_drive_timeseries", "handle_jsonrpc_request", "list_bookmarks", "list_vehicles",
    "main", "query_load_cell_rollups", "serve_stdio",
]

if __name__ == "__main__":
    raise SystemExit(main())
