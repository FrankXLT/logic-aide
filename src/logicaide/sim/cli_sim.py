"""Click CLI subcommands for M3 68HC11 simulation, twin parity, Monte Carlo sweeps, and staging."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import click

from logicaide.sim.assembler import AssembledRom, AssemblyError, assemble_tree
from logicaide.sim.hc11_runner import TwinParitySummary, run_twin_parity
from logicaide.sim.monte_carlo import SweepSummary, run_monte_carlo_sweep
from logicaide.sim.safety_gate import StagingGateResult, run_staging_safety_gate
from logicaide.sim.splay import RoundtripVerificationResult, build_candidate_rom_bytes, verify_roundtrip_sha256

_ROM_SIZE_BYTES = 32768
_BASE_ADDRESS = 0x8000
_HANDLED_ERRORS = (AssemblyError, FileNotFoundError, KeyError, ValueError, OSError, RuntimeError, sqlite3.Error)


def _compact_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Return only keyword arguments whose values are not ``None``."""
    return {key: value for key, value in kwargs.items() if value is not None}


def _to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, Paths, and bytes into JSON-safe primitives."""
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _emit_json(payload: Any) -> None:
    """Emit structured JSON output to stdout."""
    click.echo(json.dumps(_to_jsonable(payload), indent=2, sort_keys=True))


def _assemble_reference_layout(asm_path: Path) -> tuple[dict[str, int], tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    """Extract reference symbols and memory ranges from ``asm_path`` if it exists."""
    if not asm_path.is_file():
        return {}, (), ()
    ref = assemble_tree(asm_path)
    return dict(ref.symbols), ref.table_ranges, ref.code_ranges


def _rom_from_bytes(rom_bytes: bytes, source_label: str, asm_path: Path) -> AssembledRom:
    """Wrap raw 32 KB ROM bytes into an ``AssembledRom`` using ``asm_path`` layout if present."""
    if len(rom_bytes) != _ROM_SIZE_BYTES:
        raise ValueError(f"Expected {_ROM_SIZE_BYTES}-byte ROM binary, got {len(rom_bytes)} bytes ({source_label})")
    symbols, table_ranges, code_ranges = _assemble_reference_layout(asm_path)
    return AssembledRom(
        rom_bytes=rom_bytes,
        base_address=_BASE_ADDRESS,
        rom_size_bytes=_ROM_SIZE_BYTES,
        sha256=hashlib.sha256(rom_bytes).hexdigest(),
        symbols=symbols,
        written_ranges=((_BASE_ADDRESS, _BASE_ADDRESS + _ROM_SIZE_BYTES),),
        table_ranges=table_ranges,
        code_ranges=code_ranges,
        source_files=(source_label,),
        backend="binary",
    )


def _resolve_parity_rom(
    *,
    rom_path: Path | None,
    asm_path: Path,
    profile_path: Path | None,
    rev_limit_rpm: int | None,
    overboost_cut_kpa: float | None,
) -> AssembledRom:
    """Load an explicit ``.bin`` ROM or build a candidate ROM from cleanroom assembly."""
    if rom_path is not None:
        if not rom_path.is_file():
            raise FileNotFoundError(f"ROM binary not found: {rom_path}")
        return _rom_from_bytes(rom_path.read_bytes(), str(rom_path), asm_path)

    built = build_candidate_rom_bytes(
        profile_path,
        cleanroom_src_dir=asm_path.parent,
        **_compact_kwargs(rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa),
    )
    if isinstance(built, tuple) and len(built) == 2 and isinstance(built[1], AssembledRom):
        return built[1]
    if isinstance(built, AssembledRom):
        return built
    if isinstance(built, (bytes, bytearray)):
        return _rom_from_bytes(bytes(built), str(asm_path), asm_path)
    raise TypeError(f"Unexpected candidate ROM result type: {type(built).__name__}")


def _print_parity_summary(summary: TwinParitySummary) -> None:
    """Print a concise human-readable parity summary."""
    status = "PASS" if summary.ok else "FAIL"
    passed = getattr(summary, "passed_cases", getattr(summary, "pass_count", "?"))
    total = getattr(summary, "total_cases", getattr(summary, "case_count", "?"))
    vec_badge = "PASS" if getattr(summary, "vectors_ok", False) else "FAIL"
    click.echo(
        f"Twin Parity: {status} | passed={passed}/{total} "
        f"| max_cycles={summary.max_loop_cycles} ({summary.max_loop_ms}ms) | vectors={vec_badge}"
    )
    failures = getattr(summary, "failures", ()) or ()
    for item in failures[:5]:
        click.echo(f"  - {item}")


def _print_sweep_summary(summary: SweepSummary) -> None:
    """Print a concise human-readable Monte Carlo sweep summary."""
    status = "PASS" if summary.ok else "FAIL"
    sampler = getattr(summary, "sampler", "?")
    trials = getattr(summary, "n_trials", getattr(summary, "trial_count", "?"))
    build_id = getattr(summary, "build_id", "STOCK")
    click.echo(f"Monte Carlo Sweep ({sampler}): {status} | trials={trials} | build_id={build_id}")
    chunks = getattr(summary, "parquet_chunks", ()) or ()
    if chunks:
        click.echo(f"Parquet chunks: {', '.join(str(c) for c in chunks[:3])}")
    issues = getattr(summary, "violations", getattr(summary, "failures", ())) or ()
    for item in issues[:5]:
        click.echo(f"  - {item}")


def _print_stage_summary(result: StagingGateResult) -> None:
    """Print staging safety gate status, artifact paths, and SHA-256."""
    click.echo(
        f"Staging Gate: {result.status} | sha256={result.candidate_sha256 or 'n/a'} "
        f"| rom={result.rom_path or 'none'} | report={result.report_path or 'none'}"
    )
    for check in getattr(result, "checks", ()) or ():
        if isinstance(check, dict):
            name, passed, detail = check.get("name", "?"), check.get("passed"), check.get("detail", "")
        else:
            name = getattr(check, "name", str(check))
            passed = getattr(check, "ok", getattr(check, "passed", None))
            detail = getattr(check, "detail", getattr(check, "message", ""))
        badge = "PASS" if passed else "FAIL"
        click.echo(f"  [{badge}] {name}: {detail}" if detail else f"  [{badge}] {name}")


def _print_verify_summary(result: RoundtripVerificationResult) -> None:
    """Print SHA-256 splay/reassemble roundtrip verification output."""
    status = "PASS" if result.ok else "FAIL"
    src_sha = getattr(result, "source_sha256", getattr(result, "expected_sha256", ""))
    out_sha = getattr(result, "reassembled_sha256", getattr(result, "actual_sha256", ""))
    click.echo(
        f"Roundtrip SHA-256: {status} "
        f"| source={src_sha[:12] + '...' if src_sha else 'n/a'} "
        f"| reassembled={out_sha[:12] + '...' if out_sha else 'n/a'}"
    )


@click.command(name="parity")
@click.option("--asm", "asm_path", type=click.Path(path_type=Path), default=Path("rom/src/main.asm"), show_default=True, help="Entry 68HC11 assembly file.")
@click.option("--rom", "rom_path", type=click.Path(path_type=Path), default=None, help="Optional 32 KB .bin ROM overriding cleanroom assembly.")
@click.option("--profile", "profile_path", type=click.Path(path_type=Path), default=None, help="Optional vehicle build JSON profile.")
@click.option("--use-adc", is_flag=True, default=False, help="Drive 68HC11 ADC registers instead of direct zero-page RAM injection.")
@click.option("--rev-limit-rpm", type=int, default=None, help="Optional rev-limit override in RPM.")
@click.option("--overboost-cut-kpa", type=float, default=None, help="Optional overboost fuel-cut threshold in kPa.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit parity report as JSON.")
@click.pass_context
def sim_parity(
    ctx: click.Context, asm_path: Path, rom_path: Path | None, profile_path: Path | None,
    use_adc: bool, rev_limit_rpm: int | None, overboost_cut_kpa: float | None, output_json: bool,
) -> None:
    """Verify bit-exact parity between 68HC11 execution and Python behavioral twins."""
    try:
        assembled = _resolve_parity_rom(
            rom_path=rom_path, asm_path=asm_path, profile_path=profile_path,
            rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa,
        )
        summary = run_twin_parity(
            assembled, profile_path=profile_path, use_adc=use_adc,
            **_compact_kwargs(rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa),
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    _emit_json(summary) if output_json else _print_parity_summary(summary)
    if not summary.ok:
        ctx.exit(2)


@click.command(name="sweep")
@click.option("--profile", "profile_path", type=click.Path(path_type=Path), default=None, help="Optional vehicle build JSON profile.")
@click.option("--asm", "asm_path", type=click.Path(path_type=Path), default=Path("rom/src/main.asm"), show_default=True, help="Entry 68HC11 assembly file.")
@click.option("--sampler", type=click.Choice(["SOBOL", "LHS", "OPTUNA"], case_sensitive=False), default="SOBOL", show_default=True, help="Monte Carlo sampling strategy.")
@click.option("--n-trials", type=click.IntRange(1, 2048), default=64, show_default=True, help="Number of Monte Carlo sweep trials (1..2048).")
@click.option("--seed", type=int, default=42, show_default=True, help="Deterministic QMC/PRNG seed.")
@click.option("--lake-dir", type=click.Path(path_type=Path), default=Path("data/drives"), show_default=True, help="Parquet telemetry lake root directory.")
@click.option("--db-path", type=click.Path(path_type=Path), default=Path("data/drives/catalog.sqlite"), show_default=True, help="SQLite catalog database path.")
@click.option("--build-id", default="STOCK", show_default=True, help="Calibration build identifier.")
@click.option("--twins-only", is_flag=True, default=False, help="Evaluate Python twins only without HC11 CPU verification.")
@click.option("--no-parquet", is_flag=True, default=False, help="Skip writing sweep outputs to Parquet.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit Monte Carlo sweep summary as JSON.")
@click.pass_context
def sim_sweep(
    ctx: click.Context, profile_path: Path | None, asm_path: Path, sampler: str, n_trials: int,
    seed: int, lake_dir: Path, db_path: Path, build_id: str, twins_only: bool, no_parquet: bool,
    output_json: bool,
) -> None:
    """Run a deterministic Sobol, LHS, or Optuna Monte Carlo calibration sweep."""
    try:
        summary = run_monte_carlo_sweep(
            profile_path=profile_path, cleanroom_src_dir=asm_path.parent, sampler=sampler.upper(),
            n_trials=n_trials, seed=seed, lake_dir=lake_dir, db_path=db_path, build_id=build_id,
            verify_hc11=not twins_only, write_parquet=not no_parquet,
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    _emit_json(summary) if output_json else _print_sweep_summary(summary)
    if not summary.ok:
        ctx.exit(2)


@click.command(name="stage")
@click.option("--profile", "profile_path", type=click.Path(path_type=Path), default=None, help="Optional vehicle build JSON profile.")
@click.option("--asm", "asm_path", type=click.Path(path_type=Path), default=Path("rom/src/main.asm"), show_default=True, help="Entry 68HC11 assembly file.")
@click.option("--build-id", default=None, help="Candidate build identifier.")
@click.option("--staging-dir", type=click.Path(path_type=Path), default=Path("staging/candidates"), show_default=True, help="Destination directory for staged candidate ROM builds.")
@click.option("--lake-dir", type=click.Path(path_type=Path), default=Path("data/drives"), show_default=True, help="Parquet telemetry lake root directory.")
@click.option("--db-path", type=click.Path(path_type=Path), default=Path("data/drives/catalog.sqlite"), show_default=True, help="SQLite catalog database path.")
@click.option("--sampler", type=click.Choice(["SOBOL", "LHS", "OPTUNA"], case_sensitive=False), default="SOBOL", show_default=True, help="Monte Carlo sampling strategy.")
@click.option("--n-trials", type=click.IntRange(1, 1024), default=32, show_default=True, help="Number of Monte Carlo gate trials (1..1024).")
@click.option("--seed", type=int, default=42, show_default=True, help="Deterministic QMC/PRNG seed.")
@click.option("--rev-limit-rpm", type=int, default=None, help="Optional rev-limit override in RPM.")
@click.option("--overboost-cut-kpa", type=float, default=None, help="Optional overboost fuel-cut threshold in kPa.")
@click.option("--no-parquet", is_flag=True, default=False, help="Skip writing sweep outputs to Parquet.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit staging safety gate report as JSON.")
@click.pass_context
def sim_stage(
    ctx: click.Context, profile_path: Path | None, asm_path: Path, build_id: str | None,
    staging_dir: Path, lake_dir: Path, db_path: Path, sampler: str, n_trials: int, seed: int,
    rev_limit_rpm: int | None, overboost_cut_kpa: float | None, no_parquet: bool, output_json: bool,
) -> None:
    """Run the full M3 safety gate and write verified ROM artifacts to staging/candidates/."""
    try:
        result = run_staging_safety_gate(
            profile_path=profile_path, cleanroom_src_dir=asm_path.parent, build_id=build_id,
            staging_dir=staging_dir, lake_dir=lake_dir, db_path=db_path, sampler=sampler.upper(),
            n_trials=n_trials, seed=seed, write_parquet=not no_parquet,
            **_compact_kwargs(rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa),
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    _emit_json(result) if output_json else _print_stage_summary(result)
    if result.status == "BLOCKED":
        ctx.exit(2)


@click.command(name="verify-sha256")
@click.option("--rom", "rom_path", type=click.Path(path_type=Path), default=None, help="Optional 32 KB .bin ROM to splay and roundtrip-verify.")
@click.option("--asm", "asm_path", type=click.Path(path_type=Path), default=Path("rom/src/main.asm"), show_default=True, help="Entry 68HC11 assembly file.")
@click.option("--splay-dir", type=click.Path(path_type=Path), default=None, help="Optional output directory for splayed .asm modules.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Emit roundtrip SHA-256 verification report as JSON.")
@click.pass_context
def sim_verify_sha256(
    ctx: click.Context, rom_path: Path | None, asm_path: Path, splay_dir: Path | None, output_json: bool,
) -> None:
    """Verify bit-exact SHA-256 splay and reassembly roundtrip parity."""
    try:
        result = verify_roundtrip_sha256(rom_path=rom_path, asm_path=asm_path, splay_dir=splay_dir)
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    _emit_json(result) if output_json else _print_verify_summary(result)
    if not result.ok:
        ctx.exit(2)


def register_sim_commands(sim_group: click.Group) -> None:
    """Register M3 simulation, parity, sweep, staging, and roundtrip commands onto ``sim``."""
    sim_group.add_command(sim_parity, name="parity")
    sim_group.add_command(sim_sweep, name="sweep")
    sim_group.add_command(sim_stage, name="stage")
    sim_group.add_command(sim_verify_sha256, name="verify-sha256")
