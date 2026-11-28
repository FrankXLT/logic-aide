"""Thin MCP wrapper for M3 68HC11 simulation, twin parity, Monte Carlo sweeps, and staging gate."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import shlex
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

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB
from logicaide.profiles.compiler import ProfileCompilationError
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH, load_rom_manifest, lookup_sci
from logicaide.sim.assembler import AssemblyError
from logicaide.sim.cli_sim import _compact_kwargs, _resolve_parity_rom
from logicaide.sim.hc11_runner import run_twin_parity as _run_twin_parity
from logicaide.sim.monte_carlo import MC_SAMPLERS, run_monte_carlo_sweep as _run_monte_carlo_sweep
from logicaide.sim.replay import run_single_replay as _run_single_replay
from logicaide.sim.safety_gate import run_staging_safety_gate as _run_staging_safety_gate
from logicaide.sim.splay import verify_roundtrip_sha256 as _verify_roundtrip_sha256
from logicaide.telemetry.watch_cli import DEFAULT_LAKE_DIR

SERVER_NAME = "hc11-emulator"
SERVER_VERSION = "0.1.0"
_PROTECTED_ROM_DIRS: tuple[str, ...] = ("rom/inputs", "rom/stock_baselines", "rom/src")
_REPO_PATH_FIELDS = frozenset({
    "catalog_path", "splay_dir", "entry_asm", "source_files", "lake_dir",
    "db_path", "parquet_chunks", "staging_dir", "rom_path", "report_path", "summary_path",
})


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Return a structured non-raising error payload."""
    return default_error_payload(exc, status="ERROR")


def _reject_bool_ints(**params: Any) -> dict[str, Any] | None:
    """Return a structured error payload if any numeric parameter was passed as a bool."""
    return reject_bool_ints(_error_payload, **params)


def _validate_path_arg(
    name: str, raw_path: Path | str, *, repo_root: Path = REPO_ROOT,
    require_within: Path | str | None = None, reject_protected: tuple[str, ...] = (),
) -> Path:
    """Reject symlinks, escaping paths, and optional protected directories."""
    return validate_path_arg(name, raw_path, repo_root=repo_root, require_within=require_within, reject_protected=reject_protected)


def _opt_path(name: str, raw_path: Path | str | None, *, reject_protected: tuple[str, ...] = ()) -> Path | None:
    """Validate an optional path argument when provided."""
    return _validate_path_arg(name, raw_path, reject_protected=reject_protected) if raw_path is not None else None


def _validate_staging_root(staging_root: Path | str) -> Path:
    """Contain relative staging paths within staging/candidates; check symlinks and protected dirs for absolute paths."""
    if not isinstance(staging_root, (Path, str)) or (isinstance(staging_root, str) and not staging_root.strip()):
        raise ValueError("staging_root must be a non-empty path")
    raw = Path(staging_root)
    if raw.is_symlink():
        raise ValueError(f"Refusing symlink for staging_root: {raw}")
    if raw.is_absolute():
        validated = _validate_path_arg("staging_root", raw, reject_protected=_PROTECTED_ROM_DIRS)
        resolved = validated.resolve()
        if resolved.is_relative_to(REPO_ROOT.resolve()) and not resolved.is_relative_to((REPO_ROOT / "staging" / "candidates").resolve()):
            raise ValueError(f"Staging root inside repository must stay under staging/candidates: {staging_root}")
        return validated
    return _validate_path_arg("staging_root", REPO_ROOT / raw, require_within="staging/candidates", reject_protected=_PROTECTED_ROM_DIRS)


def _normalize_repo_paths(value: Any, *, field_name: str | None = None) -> Any:
    """Recursively normalize REPO_ROOT-contained path fields via display_repo_path."""
    if isinstance(value, str):
        return display_repo_path(value) if field_name in _REPO_PATH_FIELDS and value else value
    if isinstance(value, Mapping):
        return {str(k): _normalize_repo_paths(v, field_name=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_repo_paths(v, field_name=field_name) for v in value]
    return value


def _call_upstream(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke an upstream domain function and normalize expected errors to JSON dicts."""
    try:
        return _normalize_repo_paths(_json_safe(fn(*args, **kwargs)))
    except (AssemblyError, ProfileCompilationError, duckdb.Error, pa.lib.ArrowInvalid, sqlite3.Error, FileNotFoundError, KeyError, ValueError, TypeError, OSError, RuntimeError) as exc:
        return _error_payload(exc)


def inspect_rom_manifest(
    rom_id: str | None = None, offset: str | int | None = None, var_name: str | None = None,
    sci_table: str | None = None, strict: bool = True, catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> dict[str, Any]:
    """Inspect ROM manifest catalog, bounded ROM manifest summary, or strict SCI offset/symbol lookup."""
    if err := _reject_bool_ints(offset=offset):
        return err

    def _run() -> dict[str, Any]:
        safe_cat = _validate_path_arg("catalog_path", catalog_path)
        if offset is not None or var_name is not None:
            if rom_id is None or not str(rom_id).strip():
                raise ValueError("rom_id is required when looking up offset or var_name")
            rom_ref: str | Path = _validate_path_arg("rom_id", rom_id) if str(rom_id).strip().endswith(".json") else str(rom_id).strip()
            return lookup_sci(
                rom_ref, offset=offset, var_name=var_name, sci_table=sci_table, strict=strict, catalog_path=safe_cat,
            )
        if rom_id is not None:
            if not str(rom_id).strip():
                raise ValueError("rom_id must be a non-empty string when provided")
            rom_ref = _validate_path_arg("rom_id", rom_id) if str(rom_id).strip().endswith(".json") else str(rom_id).strip()
            manifest = load_rom_manifest(rom_ref, catalog_path=safe_cat)
            verified = [e for e in manifest.get("ram_watchlist", []) if e.get("source_verified") is True]
            preview = verified[:50]
            cal_tables, unverified = list(manifest.get("calibration_tables", [])), list(manifest.get("unverified_xref_candidates", []))
            return {
                "rom_id": manifest.get("rom_id"), "ecu_family": manifest.get("ecu_family"),
                "mcu": manifest.get("mcu"), "rom_size_bytes": manifest.get("rom_size_bytes"),
                "base_address": manifest.get("base_address"), "address_range": manifest.get("address_range"),
                "sci_baud": manifest.get("sci_baud"), "sci_tables": manifest.get("sci_tables", []),
                "xref_protocol": manifest.get("xref_protocol"), "summary": manifest.get("summary", {}),
                "counts": {
                    "verified_ram_count": len(verified), "unverified_candidate_count": len(unverified),
                    "calibration_table_count": len(cal_tables), "ram_watchlist_preview_count": len(preview),
                    "fast_data_slot_count": len(manifest.get("fast_data_output_addresses", [])),
                    "baseline_count": len(manifest.get("reference_baselines", [])),
                },
                "calibration_tables": cal_tables, "ram_watchlist": preview,
            }
        if not safe_cat.is_file():
            raise FileNotFoundError(f"ROM manifest catalog not found: {safe_cat}")
        catalog = json.loads(safe_cat.read_text(encoding="utf-8"))
        manifests = catalog.get("manifests", [])
        return {
            "catalog_path": str(safe_cat), "schema_version": catalog.get("schema_version"),
            "manifest_count": catalog.get("manifest_count", len(manifests)), "manifests": manifests,
        }

    return _call_upstream(_run)


def verify_roundtrip_sha256(
    rom_path: Path | str | None = None, asm_path: Path | str = Path("rom/src/main.asm"),
    splay_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Verify byte-for-byte SHA-256 roundtrip equivalence between a ROM or assembly tree and its splayed build."""
    def _run() -> dict[str, Any]:
        return _verify_roundtrip_sha256(
            rom_path=_opt_path("rom_path", rom_path), asm_path=_opt_path("asm_path", asm_path),
            splay_dir=_opt_path("splay_dir", splay_dir, reject_protected=_PROTECTED_ROM_DIRS),
        ).as_dict()

    return _call_upstream(_run)


def run_twin_parity(
    asm_path: Path | str = Path("rom/src/main.asm"), rom_path: Path | str | None = None,
    profile_path: Path | str | None = None, use_adc: bool = False,
    rev_limit_rpm: int | None = None, overboost_cut_kpa: float | None = None,
    sample_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate ROM vectors and prove 0-LSB HC11-vs-Python-Twin parity across corner and sample cases."""
    if err := _reject_bool_ints(rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa):
        return err

    def _run() -> dict[str, Any]:
        safe_asm, safe_rom, safe_profile = (
            _validate_path_arg("asm_path", asm_path), _opt_path("rom_path", rom_path), _opt_path("profile_path", profile_path),
        )
        assembled = _resolve_parity_rom(
            rom_path=safe_rom, asm_path=safe_asm, profile_path=safe_profile,
            rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa,
        )
        return _run_twin_parity(
            assembled, profile_path=safe_profile, use_adc=use_adc, sample_cases=sample_cases,
            **_compact_kwargs(rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa),
        ).as_dict()

    return _call_upstream(_run)


def run_single_replay(
    fixture_path: Path | str | None = None, operating_points: list[dict[str, Any]] | None = None,
    profile_path: Path | str | None = None, rom_path: Path | str | None = None,
    asm_path: Path | str = Path("rom/src/main.asm"), car_id: str | None = None,
    build_id: str = "STOCK", session_id: str | None = None, rev_limit_rpm: int | None = None,
    overboost_cut_kpa: float | None = None, use_adc: bool = False, max_frames: int = 250,
    lake_dir: Path | str = DEFAULT_LAKE_DIR, db_path: Path | str = DEFAULT_CATALOG_DB,
    write_parquet: bool = True,
) -> dict[str, Any]:
    """Replay fixture or operating-point frames across Python Twins and m68hc11."""
    if err := _reject_bool_ints(rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa, max_frames=max_frames):
        return err

    def _run() -> dict[str, Any]:
        return _run_single_replay(
            fixture_path=_opt_path("fixture_path", fixture_path), operating_points=operating_points,
            profile_path=_opt_path("profile_path", profile_path), rom_path=_opt_path("rom_path", rom_path),
            asm_path=_validate_path_arg("asm_path", asm_path), car_id=car_id, build_id=build_id,
            session_id=session_id, rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa,
            use_adc=use_adc, max_frames=max_frames,
            lake_dir=_validate_path_arg("lake_dir", lake_dir, reject_protected=_PROTECTED_ROM_DIRS),
            db_path=_validate_path_arg("db_path", db_path, reject_protected=_PROTECTED_ROM_DIRS),
            write_parquet=write_parquet,
        ).as_dict()

    return _call_upstream(_run)


def run_monte_carlo_sweep(
    profile_path: Path | str | None = None, asm_path: Path | str = Path("rom/src/main.asm"),
    sampler: str = "SOBOL", n_trials: int = 64, seed: int = 42, map_sensor_bar: int = 2,
    rev_limit_rpm: int | None = None, overboost_cut_kpa: float | None = None,
    verify_hc11: bool = True, hc11_sample_limit: int = 32,
    lake_dir: Path | str = DEFAULT_LAKE_DIR, db_path: Path | str = DEFAULT_CATALOG_DB,
    build_id: str = "STOCK", campaign_id: str | None = None, write_parquet: bool = True,
) -> dict[str, Any]:
    """Run a deterministic Sobol, LHS, or Optuna sweep verifying Twin and HC11 invariants."""
    if err := _reject_bool_ints(
        n_trials=n_trials, seed=seed, map_sensor_bar=map_sensor_bar,
        rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa, hc11_sample_limit=hc11_sample_limit,
    ):
        return err

    def _run() -> dict[str, Any]:
        safe_asm = _validate_path_arg("asm_path", asm_path)
        return _run_monte_carlo_sweep(
            profile_path=_opt_path("profile_path", profile_path),
            cleanroom_src_dir=safe_asm if safe_asm.is_dir() else safe_asm.parent, sampler=sampler,
            n_trials=n_trials, seed=seed, map_sensor_bar=map_sensor_bar,
            rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa,
            verify_hc11=verify_hc11, hc11_sample_limit=hc11_sample_limit,
            lake_dir=_validate_path_arg("lake_dir", lake_dir, reject_protected=_PROTECTED_ROM_DIRS),
            db_path=_validate_path_arg("db_path", db_path, reject_protected=_PROTECTED_ROM_DIRS),
            build_id=build_id, campaign_id=campaign_id, write_parquet=write_parquet,
        ).as_dict()

    return _call_upstream(_run)


def evaluate_staging_safety_gate(
    profile_path: Path | str | None = None, profile: dict[str, Any] | None = None,
    asm_path: Path | str = Path("rom/src/main.asm"), build_id: str | None = None,
    staging_root: Path | str = Path("staging/candidates"), lake_dir: Path | str = DEFAULT_LAKE_DIR,
    db_path: Path | str = DEFAULT_CATALOG_DB, sampler: str = "SOBOL", n_trials: int = 32,
    seed: int = 42, rev_limit_rpm: int | None = None, overboost_cut_kpa: float | None = None,
    verify_roundtrip: bool = True, write_parquet: bool = True,
) -> dict[str, Any]:
    """Run the 6-check ISA-101 staging safety gate and emit a safe manual flash command on PASS."""
    if err := _reject_bool_ints(
        n_trials=n_trials, seed=seed, rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa,
    ):
        return err

    def _run() -> dict[str, Any]:
        safe_asm = _validate_path_arg("asm_path", asm_path)
        res = _run_staging_safety_gate(
            profile_path=_opt_path("profile_path", profile_path), profile=profile, build_id=build_id,
            cleanroom_src_dir=safe_asm if safe_asm.is_dir() else safe_asm.parent,
            staging_root=_validate_staging_root(staging_root),
            lake_dir=_validate_path_arg("lake_dir", lake_dir, reject_protected=_PROTECTED_ROM_DIRS),
            db_path=_validate_path_arg("db_path", db_path, reject_protected=_PROTECTED_ROM_DIRS),
            sampler=sampler, n_trials=n_trials, seed=seed, rev_limit_rpm=rev_limit_rpm,
            overboost_cut_kpa=overboost_cut_kpa, verify_roundtrip=verify_roundtrip, write_parquet=write_parquet,
        )
        payload = res.as_dict()
        payload["matched"] = True
        payload["manual_flash_command"] = f"minipro -p AT28C256 -w {shlex.quote(display_repo_path(res.rom_path))}" if res.ok else None
        payload["programmer_instructions_suppressed"] = not res.ok
        return payload

    return _call_upstream(_run)


_LIMITER_PROPS: dict[str, Any] = {"rev_limit_rpm": {"type": "integer"}, "overboost_cut_kpa": {"type": "number"}}
_STORAGE_PROPS: dict[str, Any] = {
    "lake_dir": {"type": "string", "default": str(DEFAULT_LAKE_DIR)},
    "db_path": {"type": "string", "default": str(DEFAULT_CATALOG_DB)},
    "write_parquet": {"type": "boolean", "default": True},
}

TOOL_REGISTRY: dict[str, _ToolEntry] = {
    entry["name"]: entry
    for entry in (
        _ToolEntry(
            inspect_rom_manifest,
            "Inspect ROM manifest catalog, bounded ROM manifest summary, or strict SCI offset/symbol lookup.",
            _schema({
                "rom_id": {"type": "string"}, "offset": {"type": ["integer", "string"]},
                "var_name": {"type": "string"}, "sci_table": {"type": "string"},
                "strict": {"type": "boolean", "default": True},
                "catalog_path": {"type": "string", "default": str(DEFAULT_CATALOG_PATH)},
            }),
        ),
        _ToolEntry(
            verify_roundtrip_sha256,
            "Verify byte-for-byte SHA-256 equivalence between source ROM or assembly tree and its splayed build.",
            _schema({"rom_path": {"type": "string"}, "asm_path": {"type": "string", "default": "rom/src/main.asm"}, "splay_dir": {"type": "string"}}),
        ),
        _ToolEntry(
            run_twin_parity,
            "Validate ROM vectors and prove 0-LSB HC11-vs-Python-Twin parity across corner and sample cases.",
            _schema({
                "asm_path": {"type": "string", "default": "rom/src/main.asm"}, "rom_path": {"type": "string"},
                "profile_path": {"type": "string"}, "use_adc": {"type": "boolean", "default": False},
                **_LIMITER_PROPS, "sample_cases": {"type": "array", "items": {"type": "object"}},
            }),
        ),
        _ToolEntry(
            run_single_replay,
            "Replay fixture or operating-point frames across Python Twins and m68hc11, optionally writing Parquet.",
            _schema({
                "fixture_path": {"type": "string"}, "operating_points": {"type": "array", "items": {"type": "object"}},
                "profile_path": {"type": "string"}, "rom_path": {"type": "string"},
                "asm_path": {"type": "string", "default": "rom/src/main.asm"}, "car_id": {"type": "string"},
                "build_id": {"type": "string", "default": "STOCK"}, "session_id": {"type": "string"},
                **_LIMITER_PROPS, "use_adc": {"type": "boolean", "default": False},
                "max_frames": {"type": "integer", "minimum": 1, "default": 250}, **_STORAGE_PROPS,
            }),
        ),
        _ToolEntry(
            run_monte_carlo_sweep,
            "Run a deterministic Sobol, LHS, or Optuna sweep verifying Twin and HC11 invariants.",
            _schema({
                "profile_path": {"type": "string"}, "asm_path": {"type": "string", "default": "rom/src/main.asm"},
                "sampler": {"type": "string", "enum": list(MC_SAMPLERS), "default": "SOBOL"},
                "n_trials": {"type": "integer", "minimum": 1, "maximum": 2048, "default": 64},
                "seed": {"type": "integer", "default": 42}, "map_sensor_bar": {"type": "integer", "enum": [1, 2, 3], "default": 2},
                **_LIMITER_PROPS, "verify_hc11": {"type": "boolean", "default": True},
                "hc11_sample_limit": {"type": "integer", "minimum": 0, "default": 32},
                "build_id": {"type": "string", "default": "STOCK"}, "campaign_id": {"type": "string"}, **_STORAGE_PROPS,
            }),
        ),
        _ToolEntry(
            evaluate_staging_safety_gate,
            "Run the 6-check ISA-101 staging safety gate, write staging artifacts, and gate manual flash commands.",
            _schema({
                "profile_path": {"type": "string"}, "profile": {"type": "object"},
                "asm_path": {"type": "string", "default": "rom/src/main.asm"}, "build_id": {"type": "string"},
                "staging_root": {"type": "string", "default": "staging/candidates"},
                "sampler": {"type": "string", "enum": list(MC_SAMPLERS), "default": "SOBOL"},
                "n_trials": {"type": "integer", "minimum": 1, "maximum": 1024, "default": 32},
                "seed": {"type": "integer", "default": 42}, **_LIMITER_PROPS,
                "verify_roundtrip": {"type": "boolean", "default": True}, **_STORAGE_PROPS,
            }),
        ),
    )
}
TOOLS = TOOL_REGISTRY


def create_fastmcp_server() -> Any | None:
    """Build a FastMCP('hc11-emulator') instance if mcp/fastmcp is installed, else None."""
    return _create_fastmcp_server(SERVER_NAME, TOOL_REGISTRY)


def handle_jsonrpc_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an offline MCP JSON-RPC 2.0 request deterministically."""
    return _handle_jsonrpc_request(payload, server_name=SERVER_NAME, server_version=SERVER_VERSION, tool_registry=TOOL_REGISTRY, protocol_version=PROTOCOL_VERSION)


def serve_stdio(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Serve newline-delimited MCP JSON-RPC 2.0 requests over stdio."""
    return _serve_stdio(handle_jsonrpc_request, stdin=stdin, stdout=stdout)


def main(argv: list[str] | None = None) -> int:
    """Entrypoint supporting FastMCP when installed or offline stdio JSON-RPC fallback."""
    return run_mcp_server(SERVER_NAME, SERVER_VERSION, TOOL_REGISTRY, argv=argv)


__all__ = [
    "SERVER_NAME", "SERVER_VERSION", "TOOL_REGISTRY", "TOOLS", "create_fastmcp_server",
    "evaluate_staging_safety_gate", "handle_jsonrpc_request", "inspect_rom_manifest", "main",
    "run_monte_carlo_sweep", "run_single_replay", "run_twin_parity", "serve_stdio", "verify_roundtrip_sha256",
]

if __name__ == "__main__":
    raise SystemExit(main())
