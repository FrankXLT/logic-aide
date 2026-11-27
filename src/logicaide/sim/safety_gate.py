"""ISA-101 6-check ROM safety gate, staging artifact writer, and SQLite candidate registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any
import uuid

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from logicaide.catalog.sqlite_store import connect_catalog, upsert_vehicle  # noqa: E402
from logicaide.profiles.compiler import (  # noqa: E402
    DEFAULT_SCHEMA_REL,
    ProfileCompilationError,
    compile_profile,
    evaluate_mopar_compatibility,
    validate_profile_schema,
    verify_fuel_scaling,
)
from logicaide.sim.assembler import AssembledRom, assemble_tree  # noqa: E402
from logicaide.sim.hc11_runner import (  # noqa: E402
    MAX_MAIN_LOOP_CYCLES,
    MAX_MAIN_LOOP_MS,
    measure_main_loop_budget,
    run_twin_parity,
    validate_rom_vectors,
)
from logicaide.sim.monte_carlo import BAR_OVERBOOST_CEILINGS, REV_LIMIT_CEILING_RPM, SweepSummary, run_monte_carlo_sweep  # noqa: E402
from logicaide.sim.splay import build_candidate_rom_bytes, ensure_writable_sim_path, verify_roundtrip_sha256  # noqa: E402


@dataclass(frozen=True)
class SafetyCheckItem:
    """Single ISA-101 staging safety check outcome and supporting metrics."""

    name: str
    passed: bool
    detail: str
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Serialize check item to a JSON-ready dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class StagingGateResult:
    """Full result of the 6-check ROM safety gate and staging pen output."""

    ok: bool
    status: str
    build_id: str
    car_id: str
    staging_dir: str
    rom_path: str
    report_path: str
    summary_path: str
    baseline_sha256: str
    candidate_sha256: str
    vectors_ok: bool
    max_loop_cycles: int
    max_loop_ms: float
    twin_parity_ok: bool
    roundtrip_ok: bool
    sweep_overflows: int
    sweep_wraps: int
    checks: list[dict[str, Any]]
    blocking_reasons: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, Any]:
        """Serialize staging gate result into a JSON-ready dictionary."""
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _validate_build_id(build_id: str | None) -> str:
    if build_id is None:
        return f"build_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
    cleaned = str(build_id).strip()
    if not _BUILD_ID_RE.fullmatch(cleaned) or ".." in cleaned:
        raise ValueError(f"Unsafe build_id {build_id!r}")
    return cleaned


def _validate_staging_dir(staging_root: Path | str, build_id: str) -> Path:
    root_safe = ensure_writable_sim_path(staging_root)
    for forbidden in ("rom/inputs", "rom/stock_baselines", "rom/src"):
        bad = (_REPO_ROOT / forbidden).resolve()
        if root_safe == bad or root_safe.is_relative_to(bad):
            raise ValueError(f"Refusing to write staging outputs inside protected directory {bad}")
    candidate = root_safe / build_id
    if candidate.is_symlink():
        raise ValueError(f"Refusing symlinked staging target: {candidate}")
    target = ensure_writable_sim_path(candidate)
    if target == root_safe or not target.is_relative_to(root_safe):
        raise ValueError(f"Staging target {target} escapes staging root {root_safe}")
    return target


def _load_and_compile_profile(
    profile_path: Path | str | None, profile: Mapping[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    errors, compiled = [], None
    profile_dict = dict(profile) if profile is not None else None
    if profile_path is not None:
        p = Path(profile_path)
        if not p.is_file():
            raise FileNotFoundError(f"Vehicle profile not found: {p}")
        if profile_dict is None:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"Profile {p} must decode to a JSON object.")
            profile_dict = raw
        try:
            compiled = compile_profile(p, persist=False, require_rom_manifest=False)
        except ProfileCompilationError as exc:
            errors.append(f"[{exc.stage}] " + "; ".join(exc.details))
    elif profile_dict is not None:
        try:
            validate_profile_schema(profile_dict, schema_path=_REPO_ROOT / DEFAULT_SCHEMA_REL)
            compat = evaluate_mopar_compatibility(profile_dict)
            verify_fuel_scaling(profile_dict["fuel_system"])
            compiled = {"compatibility_tier": compat["compatibility_tier"], "swap_checklist": compat["swap_checklist"]}
        except ProfileCompilationError as exc:
            errors.append(f"[{exc.stage}] " + "; ".join(exc.details))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"[schema] Invalid in-memory profile: {exc}")
    return profile_dict, compiled, errors


def _extract_gate_meta(profile_dict: dict[str, Any] | None) -> dict[str, Any]:
    p = profile_dict or {}
    ident, bottom = p.get("identity_and_chassis") or {}, p.get("engine_bottom_end") or {}
    head, trans = p.get("cylinder_head_and_cam") or {}, p.get("transaxle_and_gearing") or {}
    turbo, ecu = p.get("turbo_and_intercooler") or {}, p.get("ecu_and_sensors") or {}
    block, hcode = bottom.get("block_code"), head.get("head_code")
    raw_year = ident.get("year")
    year = raw_year if isinstance(raw_year, int) and not isinstance(raw_year, bool) else 1991
    raw_bar = ecu.get("map_sensor_bar")
    map_bar = int(raw_bar) if raw_bar is not None and not isinstance(raw_bar, bool) else 2
    return {
        "car_id": str(ident.get("car_id") or "stock_sim"), "year": year,
        "model": str(ident.get("model") or "SMEC/SBEC Sim"), "ecu_family": str(ecu.get("ecu_family") or "SBEC_SINGLE_BOARD"),
        "engine_profile_id": f"{block}+{hcode}" if block and hcode else str(block or "2.5L_K+8V_782_SWIRL"),
        "transaxle_code": str(trans.get("transaxle_code") or "A555"), "turbo_spec_id": turbo.get("turbo_id"),
        "map_sensor_bar": map_bar, "rev_limit_rpm": ecu.get("rev_limit_rpm"),
        "overboost_fuel_cut_kpa": ecu.get("overboost_fuel_cut_kpa"),
        "pin11_continuity_verified": ecu.get("pin11_continuity_verified"),
        "pin2_3_25_45_verified": ecu.get("pin2_3_25_45_verified"),
    }


def _resolve_roms(
    *, profile_arg: dict[str, Any] | None, cleanroom_src_dir: Path | str | None,
    candidate_rom: AssembledRom | None, baseline_rom: AssembledRom | None,
    safe_rev: int | None, safe_ob: float | None, table_overrides: Mapping[str, bytes] | None,
    patch_bytes: Mapping[int, bytes] | None, profile_errors: list[str],
) -> tuple[AssembledRom, AssembledRom]:
    if candidate_rom is not None:
        if baseline_rom is None:
            src_path = Path(cleanroom_src_dir) if cleanroom_src_dir is not None else (_REPO_ROOT / "rom" / "src")
            entry = (src_path / "main.asm") if src_path.is_dir() else src_path
            baseline_rom = assemble_tree(entry)
        return baseline_rom, candidate_rom
    try:
        built_base, built_cand = build_candidate_rom_bytes(
            profile_or_path=profile_arg, cleanroom_src_dir=cleanroom_src_dir, rev_limit_rpm=safe_rev,
            overboost_cut_kpa=safe_ob, table_overrides=table_overrides, patch_bytes=patch_bytes,
        )
    except ValueError as exc:
        profile_errors.append(f"ROM calibration build rejected profile parameters: {exc}")
        built_base, built_cand = build_candidate_rom_bytes(
            profile_or_path=None, cleanroom_src_dir=cleanroom_src_dir, rev_limit_rpm=safe_rev,
            overboost_cut_kpa=safe_ob, table_overrides=table_overrides, patch_bytes=patch_bytes,
        )
    return (baseline_rom or built_base), built_cand


def _evaluate_drivetrain_and_deadbolt(
    meta: dict[str, Any], compiled: dict[str, Any] | None, profile_dict: dict[str, Any] | None,
    profile_errors: list[str], warnings: list[str],
) -> SafetyCheckItem:
    reasons = list(profile_errors)
    p11, p_vars = meta["pin11_continuity_verified"], meta["pin2_3_25_45_verified"]
    if profile_dict is not None:
        if p11 is not True:
            reasons.append("SECURITY.md Deadbolt: ecu_and_sensors.pin11_continuity_verified must be True.")
        if p_vars is not True:
            reasons.append("SECURITY.md Deadbolt: ecu_and_sensors.pin2_3_25_45_verified must be True.")
    else:
        warnings.append("No vehicle profile supplied; verify Pin 11 continuity and Pins 2/3/25/45 before bench power.")

    tier = (compiled or {}).get("compatibility_tier") or ((profile_dict or {}).get("cylinder_head_and_cam") or {}).get("compatibility_tier") or "BOLT_ON_NATIVE"
    if tier == "HYBRID_MACHINING_REQUIRED":
        checklist = (compiled or {}).get("swap_checklist") or []
        for item in checklist:
            warnings.append(f"[AMBER] {item.get('title') or item.get('key') or 'Swap mod'}: {item.get('description') or ''}".strip())
        if not checklist:
            warnings.append("[AMBER] HYBRID_MACHINING_REQUIRED build: complete physical swap checklist before startup.")

    passed = len(reasons) == 0
    return SafetyCheckItem(
        name="DRIVETRAIN_AND_DEADBOLT", passed=passed,
        detail="Drivetrain compatibility and pinout deadbolt checks passed." if passed else "; ".join(reasons),
        metrics={"compatibility_tier": tier, "pin11_verified": p11, "pin2_3_25_45_verified": p_vars, "reasons": reasons},
    )


def _run_parity_and_sweep(
    *, candidate_rom: AssembledRom, profile_dict: dict[str, Any] | None, eff_bar: int,
    req_rev: int | None, req_ob: float | None, safe_rev: int | None, safe_ob: float | None,
    sampler: str, n_trials: int, seed: int, write_parquet: bool, lake_dir: Path | str, db_path: Path | str, build_id: str,
) -> tuple[SafetyCheckItem, SafetyCheckItem, SweepSummary | None, bool, int, float]:
    bar_arg = eff_bar if eff_bar in BAR_OVERBOOST_CEILINGS else 2
    try:
        parity = run_twin_parity(
            candidate_rom, map_sensor_bar=bar_arg, rev_limit_rpm=safe_rev, overboost_cut_kpa=safe_ob,
            use_adc=False, max_cycles=MAX_MAIN_LOOP_CYCLES,
        )
        p_ok, p_cyc, p_ms = bool(parity.ok), parity.max_loop_cycles, parity.max_loop_ms
        p_item = SafetyCheckItem("TWIN_HC11_PARITY", p_ok, f"passed={parity.passed_cases}/{parity.total_cases}, max_lsb_diff={parity.max_lsb_diff}", parity.as_dict())
    except Exception as exc:
        p_ok, p_cyc, p_ms = False, 0, 0.0
        p_item = SafetyCheckItem("TWIN_HC11_PARITY", False, f"Twin parity error: {exc}", {"error": str(exc)})

    import math
    rev_ok = req_rev is None or (1000 <= int(req_rev) <= REV_LIMIT_CEILING_RPM)
    ob_ok = (eff_bar in BAR_OVERBOOST_CEILINGS) and (req_ob is None or (math.isfinite(float(req_ob)) and 0.0 < float(req_ob) <= BAR_OVERBOOST_CEILINGS[eff_bar]))
    sweep: SweepSummary | None = None
    try:
        sweep = run_monte_carlo_sweep(
            candidate_rom, profile=profile_dict, sampler=sampler, n_trials=n_trials, seed=seed, map_sensor_bar=bar_arg,
            rev_limit_rpm=int(req_rev) if req_rev is not None else None, overboost_cut_kpa=float(req_ob) if req_ob is not None else None,
            verify_hc11=True, lake_dir=lake_dir, db_path=db_path, build_id=build_id, write_parquet=write_parquet,
        )
        sw_ok = rev_ok and ob_ok and sweep.ok
        sw_det = f"sampler={sweep.sampler}, n_trials={sweep.n_trials}, overflows={sweep.overflow_count}, wraps={sweep.wrap_count}, violations={len(sweep.violations)}"
        s_item = SafetyCheckItem("CALIBRATION_AND_SWEEP", sw_ok, sw_det, sweep.as_dict())
    except Exception as exc:
        s_item = SafetyCheckItem("CALIBRATION_AND_SWEEP", False, f"Sweep execution error: {exc}", {"error": str(exc)})
    return p_item, s_item, sweep, p_ok, max(p_cyc, sweep.max_loop_cycles if sweep else 0), max(p_ms, sweep.max_loop_ms if sweep else 0.0)


def _run_isa101_checks(
    *, baseline_rom: AssembledRom, candidate_rom: AssembledRom, profile_dict: dict[str, Any] | None,
    compiled: dict[str, Any] | None, meta: dict[str, Any], profile_errors: list[str], eff_bar: int,
    req_rev: int | None, req_ob: float | None, safe_rev: int | None, safe_ob: float | None,
    sampler: str, n_trials: int, seed: int, verify_roundtrip: bool, write_parquet: bool,
    lake_dir: Path | str, db_path: Path | str, build_id: str,
) -> tuple[list[SafetyCheckItem], list[str], dict[str, Any]]:
    warnings: list[str] = []
    cand_bytes = candidate_rom.rom_bytes
    cand_sha = hashlib.sha256(cand_bytes).hexdigest()
    base_sha = baseline_rom.sha256 or hashlib.sha256(baseline_rom.rom_bytes).hexdigest()

    rt_res = verify_roundtrip_sha256(cand_bytes) if verify_roundtrip else None
    rt_ok = True if rt_res is None else bool(rt_res.ok)
    c1 = SafetyCheckItem(
        "CHECKSUM_AND_ROUNDTRIP", len(cand_bytes) == 32768 and rt_ok,
        f"len={len(cand_bytes)} bytes, sha256={cand_sha[:12]}..., roundtrip_ok={rt_ok}",
        {"rom_size_bytes": len(cand_bytes), "baseline_sha256": base_sha, "candidate_sha256": cand_sha,
         "checksum_8bit": sum(cand_bytes) & 0xFF, "checksum_16bit": sum(cand_bytes) & 0xFFFF,
         "roundtrip_ok": rt_ok, "roundtrip_error": None if rt_res is None else rt_res.error_code},
    )
    vec_res = validate_rom_vectors(candidate_rom)
    c2 = SafetyCheckItem("VECTOR_INTEGRITY", bool(vec_res.ok), f"reset_vector=0x{vec_res.reset_vector:04X}, invalid={len(vec_res.invalid_vectors)}", vec_res.as_dict())
    loop_res = measure_main_loop_budget(cand_bytes, candidate_rom.symbols, max_cycles=MAX_MAIN_LOOP_CYCLES)
    loop_ok = bool(loop_res.ok) and not bool(loop_res.exceeded) and loop_res.cycles <= MAX_MAIN_LOOP_CYCLES and loop_res.duration_ms <= MAX_MAIN_LOOP_MS
    c3 = SafetyCheckItem(
        "LOOP_CYCLE_BUDGET", loop_ok, f"cycles={loop_res.cycles}/{MAX_MAIN_LOOP_CYCLES}, duration_ms={loop_res.duration_ms:.4f}/{MAX_MAIN_LOOP_MS}",
        {"cycles": loop_res.cycles, "duration_ms": loop_res.duration_ms, "exceeded": loop_res.exceeded, "error_code": loop_res.error_code},
    )
    c4, c5, sweep, parity_ok, ps_cyc, ps_ms = _run_parity_and_sweep(
        candidate_rom=candidate_rom, profile_dict=profile_dict, eff_bar=eff_bar, req_rev=req_rev, req_ob=req_ob,
        safe_rev=safe_rev, safe_ob=safe_ob, sampler=sampler, n_trials=n_trials, seed=seed,
        write_parquet=write_parquet, lake_dir=lake_dir, db_path=db_path, build_id=build_id,
    )
    c6 = _evaluate_drivetrain_and_deadbolt(meta, compiled, profile_dict, profile_errors, warnings)
    agg = {
        "baseline_sha256": base_sha, "candidate_sha256": cand_sha, "roundtrip_ok": rt_ok, "vectors_ok": bool(vec_res.ok),
        "twin_parity_ok": parity_ok, "max_loop_cycles": max(loop_res.cycles, ps_cyc),
        "max_loop_ms": max(loop_res.duration_ms, ps_ms), "sweep": sweep,
    }
    return [c1, c2, c3, c4, c5, c6], warnings, agg


def _render_report_md(result: StagingGateResult, agg: dict[str, Any]) -> str:
    sweep: SweepSummary | None = agg["sweep"]
    lines = [f"# LogicAIDE Staging Safety Report — {result.build_id}", "\nSTATUS: PASS\n"] if result.ok else [
        "# SAFETY GATE BLOCKED — DO NOT FLASH", f"\nSTATUS: BLOCKED ({result.build_id})\n", "## Blocking Reasons",
        *(f"- {r}" for r in result.blocking_reasons), "",
    ]
    lines.extend([
        "## Build & Checksum Summary",
        f"- Build ID: `{result.build_id}` | Vehicle: `{result.car_id}`",
        f"- Baseline SHA-256: `{result.baseline_sha256}`",
        f"- Candidate SHA-256: `{result.candidate_sha256}`",
        f"- Loop Budget Proof: `{result.max_loop_cycles} / {MAX_MAIN_LOOP_CYCLES}` cycles (`{result.max_loop_ms:.4f} ms <= {MAX_MAIN_LOOP_MS:.1f} ms`)",
        f"- `$FFC0-$FFFF` Vector Proof: `vectors_ok={result.vectors_ok}`",
        f"- Twin 0-LSB Parity Proof: `twin_parity_ok={result.twin_parity_ok}`",
        f"- Monte Carlo Sweep: `overflows={result.sweep_overflows}`, `wraps={result.sweep_wraps}`, `campaign_id={sweep.campaign_id if sweep else 'N/A'}`",
        "", "## ISA-101 Gate Checks",
        *(f"- [{'PASS' if c['passed'] else 'FAIL'}] `{c['name']}`: {c['detail']}" for c in result.checks),
    ])
    if result.warnings:
        lines.extend(["", "## Advisory Warnings (AMBER)", *(f"- {w}" for w in result.warnings)])
    if result.ok:
        lines.extend([
            "", "## Human-Only Manual Verification & Flash Bench Instructions",
            "Per `SECURITY.md`, automated scripts never touch physical ECU hardware. Before programming:",
            "1. Visually confirm ECU hardware generation (`SMEC` vs `SBEC`) and part number.",
            "2. Verify 60-way **Pin 11 continuity** with a multimeter (open on SMEC; grounded on SBEC) before applying +12V/+21V.",
            "3. Verify Pins 2, 3, 25, and 45 against the exact model-year FSM and confirm 12V/21V optical/diode FTDI isolation.",
            "4. Inspect the staged binary checksum above, then copy/execute the manual bench programmer command:",
            f"   ```sh\n   minipro -p AT28C256 -w {shlex.quote(result.rom_path)}\n   ```",
        ])
    return "\n".join(lines) + "\n"


def _persist_candidate_build(
    *, db_path: Path | str, result: StagingGateResult, meta: dict[str, Any], campaign_id: str | None
) -> None:
    sql = (
        "INSERT INTO candidate_builds (build_id, car_id, campaign_id, baseline_sha256, candidate_sha256, staging_dir, "
        "vectors_ok, max_loop_cycles, twin_parity_ok, sweep_overflows, safety_gate_status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(build_id) DO UPDATE SET "
        "car_id=excluded.car_id, campaign_id=excluded.campaign_id, baseline_sha256=excluded.baseline_sha256, "
        "candidate_sha256=excluded.candidate_sha256, staging_dir=excluded.staging_dir, vectors_ok=excluded.vectors_ok, "
        "max_loop_cycles=excluded.max_loop_cycles, twin_parity_ok=excluded.twin_parity_ok, "
        "sweep_overflows=excluded.sweep_overflows, safety_gate_status=excluded.safety_gate_status, created_at=excluded.created_at"
    )
    valid_ecus = ("LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD")
    db_year = meta["year"] if isinstance(meta["year"], int) and 1984 <= meta["year"] <= 1995 else 1991
    db_ecu = meta["ecu_family"] if meta["ecu_family"] in valid_ecus else "SBEC_SINGLE_BOARD"
    conn = connect_catalog(db_path)
    try:
        with conn:
            upsert_vehicle(conn, {
                "car_id": result.car_id, "year": db_year, "model": meta["model"], "ecu_family": db_ecu,
                "engine_profile_id": meta["engine_profile_id"], "transaxle_code": meta["transaxle_code"],
                "turbo_spec_id": meta["turbo_spec_id"],
                "pinout_verified": bool(meta["pin11_continuity_verified"] is True and meta["pin2_3_25_45_verified"] is True),
            })
            valid_cid = campaign_id
            if valid_cid is not None and conn.execute(
                "SELECT 1 FROM optimization_campaigns WHERE campaign_id = ?", (valid_cid,)
            ).fetchone() is None:
                valid_cid = None
            conn.execute(sql, (
                result.build_id, result.car_id, valid_cid, result.baseline_sha256, result.candidate_sha256,
                result.staging_dir, int(result.vectors_ok), result.max_loop_cycles, int(result.twin_parity_ok),
                result.sweep_overflows, result.status, _utc_now_iso(),
            ))
    finally:
        conn.close()


def run_staging_safety_gate(
    *, profile_path: Path | str | None = None, profile: Mapping[str, Any] | None = None,
    build_id: str | None = None, cleanroom_src_dir: Path | str | None = None,
    candidate_rom: AssembledRom | None = None, baseline_rom: AssembledRom | None = None,
    staging_root: Path | str = Path("staging/candidates"), staging_dir: Path | str | None = None,
    lake_dir: Path | str = Path("data/drives"), db_path: Path | str = Path("data/drives/catalog.sqlite"),
    rev_limit_rpm: int | None = None, overboost_cut_kpa: float | None = None,
    table_overrides: Mapping[str, bytes] | None = None, patch_bytes: Mapping[int, bytes] | None = None,
    sampler: str = "SOBOL", n_trials: int = 32, seed: int = 42,
    verify_roundtrip: bool = True, write_parquet: bool = True,
) -> StagingGateResult:
    """Run the 6-check ISA-101 safety gate, write staging artifacts, and register in SQLite."""
    import math
    eff_build_id = _validate_build_id(build_id)
    eff_root = staging_dir if staging_dir is not None else staging_root
    target_dir = _validate_staging_dir(eff_root, eff_build_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    profile_dict, compiled, profile_errors = _load_and_compile_profile(profile_path, profile)
    meta = _extract_gate_meta(profile_dict)
    eff_bar = meta["map_sensor_bar"]
    req_rev = rev_limit_rpm if rev_limit_rpm is not None else meta["rev_limit_rpm"]
    req_ob = overboost_cut_kpa if overboost_cut_kpa is not None else meta["overboost_fuel_cut_kpa"]
    req_rev_int = int(req_rev) if isinstance(req_rev, (int, float)) and not isinstance(req_rev, bool) and math.isfinite(float(req_rev)) else None
    req_ob_flt = float(req_ob) if isinstance(req_ob, (int, float)) and not isinstance(req_ob, bool) else None
    safe_rev = req_rev_int if req_rev_int is not None and 1000 <= req_rev_int <= REV_LIMIT_CEILING_RPM else None
    safe_ob = req_ob_flt if (eff_bar in BAR_OVERBOOST_CEILINGS and req_ob_flt is not None and math.isfinite(req_ob_flt) and 0.0 < req_ob_flt <= BAR_OVERBOOST_CEILINGS[eff_bar]) else None

    base_rom, cand_rom = _resolve_roms(
        profile_arg=profile_dict if not profile_errors else None,
        cleanroom_src_dir=cleanroom_src_dir, candidate_rom=candidate_rom, baseline_rom=baseline_rom,
        safe_rev=safe_rev, safe_ob=safe_ob, table_overrides=table_overrides,
        patch_bytes=patch_bytes, profile_errors=profile_errors,
    )
    checks, warnings, agg = _run_isa101_checks(
        baseline_rom=base_rom, candidate_rom=cand_rom, profile_dict=profile_dict, compiled=compiled,
        meta=meta, profile_errors=profile_errors, eff_bar=eff_bar, req_rev=req_rev_int,
        req_ob=req_ob_flt, safe_rev=safe_rev, safe_ob=safe_ob,
        sampler=sampler, n_trials=n_trials, seed=seed, verify_roundtrip=verify_roundtrip,
        write_parquet=write_parquet, lake_dir=lake_dir, db_path=db_path, build_id=eff_build_id,
    )
    blocking_reasons = [f"{c.name}: {c.detail}" for c in checks if not c.passed]
    ok = len(blocking_reasons) == 0
    sweep: SweepSummary | None = agg["sweep"]
    rom_path, report_path, summary_path = target_dir / f"{eff_build_id}.bin", target_dir / "REPORT.md", target_dir / "gate_summary.json"
    result = StagingGateResult(
        ok=ok, status="PASS" if ok else "BLOCKED", build_id=eff_build_id, car_id=meta["car_id"],
        staging_dir=str(target_dir), rom_path=str(rom_path), report_path=str(report_path), summary_path=str(summary_path),
        baseline_sha256=agg["baseline_sha256"], candidate_sha256=agg["candidate_sha256"],
        vectors_ok=agg["vectors_ok"], max_loop_cycles=agg["max_loop_cycles"], max_loop_ms=agg["max_loop_ms"],
        twin_parity_ok=agg["twin_parity_ok"], roundtrip_ok=agg["roundtrip_ok"],
        sweep_overflows=sweep.overflow_count if sweep else 0, sweep_wraps=sweep.wrap_count if sweep else 0,
        checks=[c.as_dict() for c in checks], blocking_reasons=blocking_reasons, warnings=warnings,
    )
    rom_path.write_bytes(cand_rom.rom_bytes)
    summary_path.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
    report_path.write_text(_render_report_md(result, agg), encoding="utf-8")
    _persist_candidate_build(db_path=db_path, result=result, meta=meta, campaign_id=sweep.campaign_id if sweep else None)
    return result
