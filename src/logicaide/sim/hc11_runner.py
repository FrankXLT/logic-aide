"""Cleanroom Motorola 68HC11 hardware emulator harness, cycle timer, and twin parity runner."""

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
from pathlib import Path
import sys
from typing import Any

import m68hc11

_REPO_ROOT = Path(__file__).resolve().parents[3]
if importlib.util.find_spec("rom") is None and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from logicaide.sim.assembler import AssembledRom
from logicaide.sim.hc11_peripherals import (
    HC11_CLOCK_HZ,
    MAX_MAIN_LOOP_CYCLES,
    MAX_MAIN_LOOP_MS,
    RESET_VECTOR_ADDR,
    VECTOR_BASE,
    VECTOR_END,
    LoopTimingResult,
    TwinParityCase,
    TwinParitySummary,
    VectorCheckResult,
    create_hc11_cpu,
    validate_rom_vectors,
)
from logicaide.sim.splay import extract_calibrations_from_rom
from rom.models import (
    BoostCalibration,
    FuelCalibration,
    SparkCalibration,
    calc_fuel_pw,
    calc_spark_advance,
    calc_wastegate_and_limiters,
)

__all__ = [
    "HC11_CLOCK_HZ",
    "MAX_MAIN_LOOP_CYCLES",
    "MAX_MAIN_LOOP_MS",
    "RESET_VECTOR_ADDR",
    "VECTOR_BASE",
    "VECTOR_END",
    "LoopTimingResult",
    "TwinParityCase",
    "TwinParitySummary",
    "VectorCheckResult",
    "create_hc11_cpu",
    "execute_ram_case",
    "measure_main_loop_budget",
    "run_twin_parity",
    "validate_rom_vectors",
]

_DEFAULT_RAM_ADDRS: dict[str, int] = {
    "RAM_SIM_FLAGS": 0x001F, "RAM_RPM_HB": 0x0020, "RAM_MAP_RAW": 0x0021, "RAM_BARO_RAW": 0x0022,
    "RAM_BATT_RAW": 0x0023, "RAM_CTS_RAW": 0x0024, "RAM_ACT_RAW": 0x0025, "RAM_TPS_RAW": 0x0026,
    "RAM_KNOCK_RAW": 0x0027, "RAM_FUEL_TRIM_U8": 0x0028, "RAM_BASE_PW_HB": 0x0030, "RAM_PEF_U8": 0x0032,
    "RAM_BATOFF_U8": 0x0033, "RAM_FUEL_PW_HB": 0x0034, "RAM_DIST_PW_HB": 0x0036, "RAM_KNOCK_RETARD": 0x0038,
    "RAM_SPARK_ADV": 0x0039, "RAM_DWELL_HB": 0x003A, "RAM_WG_DUTY": 0x003C, "RAM_CUTOUT_FLAGS": 0x003D,
    "RAM_SCI_LAST_RX": 0x003E, "RAM_SCI_LAST_TX": 0x003F,
}
_DEFAULT_PARITY_CORNERS: tuple[tuple[str, int, int, int, int, int, int], ...] = (
    ("idle_warm", 28, 36, 101, 216, 0, 0), ("idle_low_batt", 28, 36, 101, 108, 0, 0),
    ("idle_high_batt", 28, 36, 101, 255, 0, 0), ("zero_rpm_min_map", 0, 15, 85, 255, 0, 0),
    ("zero_rpm_mid_map", 0, 128, 101, 194, 0, 0), ("cruise_low_load", 64, 75, 101, 216, 40, 0),
    ("cruise_alt_baro", 64, 75, 85, 216, 40, 10), ("cruise_high_baro", 80, 95, 127, 200, 60, 0),
    ("pef_bp_75", 75, 135, 101, 216, 80, 0), ("pef_bp_100", 100, 157, 101, 216, 100, 12),
    ("pef_bp_134", 134, 180, 101, 194, 120, 0), ("pef_bp_156", 156, 198, 101, 180, 120, 20),
    ("boost_198_no_knk", 120, 198, 101, 216, 120, 0), ("boost_206_knk_active", 120, 206, 101, 216, 200, 0),
    ("boost_215_knk_edge", 150, 215, 101, 205, 165, 15), ("boost_235_high_knk", 175, 235, 101, 195, 230, 20),
    ("boost_251_pre_ob", 190, 251, 101, 216, 0, 0), ("boost_251_pre_ob_knk", 190, 251, 101, 216, 220, 20),
    ("overboost_exact_252", 160, 252, 101, 216, 0, 0), ("overboost_max_255", 160, 255, 101, 216, 220, 20),
    ("rev_pre_limit_202", 202, 180, 101, 216, 0, 0), ("rev_exact_limit_203", 203, 180, 101, 216, 0, 0),
    ("rev_max_255_mid_map", 255, 180, 101, 216, 0, 0), ("both_cutouts_max", 255, 255, 127, 255, 255, 20),
    ("spark_underflow_clamp", 20, 245, 101, 216, 220, 0), ("spark_underflow_zero_rpm", 0, 250, 101, 160, 240, 0),
    ("low_batt_dwell_clamp", 96, 150, 101, 108, 0, 20), ("high_batt_dwell", 96, 150, 101, 245, 0, 0),
    ("pef_high_rpm_230", 230, 210, 101, 216, 180, 10), ("trim_heavy_sat_guard", 150, 250, 101, 108, 0, 200),
    ("floor_clamp_cold_batt", 48, 15, 101, 250, 0, 0), ("knk_below_thresh_120", 160, 220, 101, 216, 120, 0),
)


def _execute_case(
    rom_bytes: bytes,
    symbols: Mapping[str, int],
    *,
    base_address: int = 0x8000,
    ram_inputs: Mapping[int, int] | None = None,
    adc_channels: tuple[int, int, int, int] | None = None,
    sci_rx_bytes: list[int] | None = None,
    entry_symbol: str = "MAIN_LOOP",
    entry_address: int | None = None,
    max_cycles: int = MAX_MAIN_LOOP_CYCLES,
    step_cap: int = 60_000,
) -> tuple[LoopTimingResult, m68hc11.HC11, dict[str, Any]]:
    """Execute one bounded subroutine invocation on HC11 and return timing, CPU, and I/O."""
    target_pc = entry_address if entry_address is not None else symbols[entry_symbol]
    cpu, periph = create_hc11_cpu(
        rom_bytes, base_address=base_address, ram_init=ram_inputs, adc_channels=adc_channels, sci_rx_bytes=sci_rx_bytes
    )
    cpu.mem[0x00FF], cpu.mem[0x00FE], cpu.sp = 0xFF, 0xFF, 0x00FD
    cpu.pc, cpu.stopped, cpu.waiting = target_pc & 0xFFFF, False, False

    cycles, steps, cycle_cap, halt_reason = 0, 0, max_cycles * 4, "RTS"
    while cpu.pc != 0xFFFF:
        if cycles > cycle_cap:
            halt_reason = "RUNAWAY_LOOP"
            break
        if steps >= step_cap:
            halt_reason = "STEP_CAP_EXCEEDED"
            break
        try:
            step = cpu.step()
        except m68hc11.IllegalOpcode:
            halt_reason = "ILLEGAL_OPCODE"
            break
        cycles += step.cycles
        steps += 1
        if getattr(cpu, "_timer_on", False):
            cpu._timer_advance(step.cycles)
        if cpu.stopped:
            halt_reason = "STOP"
            break
        if cpu.waiting:
            halt_reason = "WAI"
            break

    exceeded = cycles > max_cycles
    ok = (not exceeded) and (halt_reason == "RTS")
    error_code = "LOOP_BUDGET_EXCEEDED" if exceeded else (None if halt_reason == "RTS" else halt_reason)
    timing = LoopTimingResult(
        ok=ok,
        cycles=cycles,
        duration_ms=round((cycles / HC11_CLOCK_HZ) * 1000.0, 6),
        max_cycles=max_cycles,
        max_ms=round((max_cycles / HC11_CLOCK_HZ) * 1000.0, 6),
        exceeded=exceeded,
        error_code=error_code,
        halt_reason=halt_reason,
        final_pc=cpu.pc,
    )
    return timing, cpu, periph


def measure_main_loop_budget(
    rom_bytes: bytes,
    symbols: Mapping[str, int],
    *,
    ram_inputs: Mapping[int, int] | None = None,
    adc_channels: tuple[int, int, int, int] | None = None,
    sci_rx_bytes: list[int] | None = None,
    entry_symbol: str = "MAIN_LOOP",
    entry_address: int | None = None,
    max_cycles: int = MAX_MAIN_LOOP_CYCLES,
    step_cap: int = 60_000,
    base_address: int = 0x8000,
) -> LoopTimingResult:
    """Measure cycle count and ms budget for a single 68HC11 MAIN_LOOP invocation."""
    timing, _, _ = _execute_case(
        rom_bytes, symbols, base_address=base_address, ram_inputs=ram_inputs, adc_channels=adc_channels,
        sci_rx_bytes=sci_rx_bytes, entry_symbol=entry_symbol, entry_address=entry_address,
        max_cycles=max_cycles, step_cap=step_cap,
    )
    return timing


def execute_ram_case(
    assembled: AssembledRom,
    *,
    rpm_hb: int,
    map_raw: int,
    baro_raw: int,
    batt_raw: int,
    knock_raw: int,
    fuel_trim_u8: int = 0,
    use_adc: bool = False,
    dist_pw_u16: int = 0x0100,
    sci_rx_bytes: list[int] | None = None,
    max_cycles: int = MAX_MAIN_LOOP_CYCLES,
) -> dict[str, Any]:
    """Execute one MAIN_LOOP operating point via RAM injection or ADC hooks."""
    syms = assembled.symbols
    def _addr(name: str) -> int:
        return syms.get(name, _DEFAULT_RAM_ADDRS[name])

    dist_addr = _addr("RAM_DIST_PW_HB")
    ram_init: dict[int, int] = {
        _addr("RAM_SIM_FLAGS"): 1 if use_adc else 0,
        _addr("RAM_RPM_HB"): rpm_hb & 0xFF,
        _addr("RAM_MAP_RAW"): 0 if use_adc else (map_raw & 0xFF),
        _addr("RAM_BARO_RAW"): baro_raw & 0xFF,
        _addr("RAM_BATT_RAW"): 0 if use_adc else (batt_raw & 0xFF),
        _addr("RAM_CTS_RAW"): 0x80,
        _addr("RAM_ACT_RAW"): 0x80,
        _addr("RAM_TPS_RAW"): 0x80,
        _addr("RAM_KNOCK_RAW"): knock_raw & 0xFF,
        _addr("RAM_FUEL_TRIM_U8"): fuel_trim_u8 & 0xFF,
        dist_addr: (dist_pw_u16 >> 8) & 0xFF,
        dist_addr + 1: dist_pw_u16 & 0xFF,
    }
    timing, cpu, periph = _execute_case(
        assembled.rom_bytes, syms, base_address=assembled.base_address, ram_inputs=ram_init,
        adc_channels=(map_raw & 0xFF, batt_raw & 0xFF, 0x80, 0x80),
        sci_rx_bytes=sci_rx_bytes, max_cycles=max_cycles,
    )
    return {
        "pw_ticks_u16": cpu.read16(_addr("RAM_FUEL_PW_HB")),
        "base_pw_u16": cpu.read16(_addr("RAM_BASE_PW_HB")),
        "pef_u8": cpu.read8(_addr("RAM_PEF_U8")),
        "batoff_u8": cpu.read8(_addr("RAM_BATOFF_U8")),
        "spark_u8": cpu.read8(_addr("RAM_SPARK_ADV")),
        "knock_retard_u8": cpu.read8(_addr("RAM_KNOCK_RETARD")),
        "dwell_ticks_u16": cpu.read16(_addr("RAM_DWELL_HB")),
        "wg_duty_u8": cpu.read8(_addr("RAM_WG_DUTY")),
        "cutout_flags_u8": cpu.read8(_addr("RAM_CUTOUT_FLAGS")),
        "sci_last_rx": cpu.read8(_addr("RAM_SCI_LAST_RX")),
        "sci_last_tx": cpu.read8(_addr("RAM_SCI_LAST_TX")),
        "sci_tx_bytes": list(periph["sci"]["tx_bytes"]),
        "cycles": timing.cycles,
        "duration_ms": timing.duration_ms,
        "ok": timing.ok,
        "error_code": timing.error_code,
    }


def _evaluate_parity_case(
    assembled: AssembledRom,
    corner: tuple[str, int, int, int, int, int, int],
    fuel_cal: FuelCalibration,
    spark_cal: SparkCalibration,
    boost_cal: BoostCalibration,
    *,
    use_adc: bool,
    max_cycles: int,
) -> tuple[TwinParityCase, bool]:
    """Compare one operating point between Python Twins and HC11 ROM execution."""
    case_id, rpm_hb, map_raw, baro_raw, batt_raw, knock_raw, fuel_trim_u8 = corner
    fuel_res = calc_fuel_pw(rpm_hb, map_raw, batt_raw, fuel_cal, fuel_trim_u8=fuel_trim_u8)
    spark_res = calc_spark_advance(rpm_hb, map_raw, knock_raw, spark_cal, batt_raw_u8=batt_raw)
    boost_res = calc_wastegate_and_limiters(rpm_hb, map_raw, baro_raw, spark_res.knock_retard_u8, boost_cal)

    twin_pw = 0 if boost_res.fuel_cut else fuel_res.pw_ticks_u16
    twin_wg = 0 if boost_res.fuel_cut else boost_res.wg_duty_u8
    hc11_out = execute_ram_case(
        assembled, rpm_hb=rpm_hb, map_raw=map_raw, baro_raw=baro_raw, batt_raw=batt_raw,
        knock_raw=knock_raw, fuel_trim_u8=fuel_trim_u8, use_adc=use_adc, max_cycles=max_cycles,
    )
    max_diff = max(
        abs(twin_pw - hc11_out["pw_ticks_u16"]),
        abs(spark_res.spark_u8 - hc11_out["spark_u8"]),
        abs(twin_wg - hc11_out["wg_duty_u8"]),
        abs(boost_res.cutout_flags_u8 - hc11_out["cutout_flags_u8"]),
        abs(spark_res.dwell_ticks_u16 - hc11_out["dwell_ticks_u16"]),
    )
    loop_ok = bool(hc11_out["ok"]) and (hc11_out["cycles"] <= max_cycles)
    record = TwinParityCase(
        case_id=case_id, rpm_hb=rpm_hb & 0xFF, map_raw=map_raw & 0xFF, baro_raw=baro_raw & 0xFF,
        batt_raw=batt_raw & 0xFF, knock_raw=knock_raw & 0xFF, fuel_trim_u8=fuel_trim_u8 & 0xFF,
        twin_pw_u16=twin_pw, hc11_pw_u16=hc11_out["pw_ticks_u16"],
        twin_spark_u8=spark_res.spark_u8, hc11_spark_u8=hc11_out["spark_u8"],
        twin_wg_u8=twin_wg, hc11_wg_u8=hc11_out["wg_duty_u8"],
        twin_cutout_u8=boost_res.cutout_flags_u8, hc11_cutout_u8=hc11_out["cutout_flags_u8"],
        twin_dwell_u16=spark_res.dwell_ticks_u16, hc11_dwell_u16=hc11_out["dwell_ticks_u16"],
        loop_cycles=hc11_out["cycles"], passed=loop_ok and (max_diff == 0), max_lsb_diff=max_diff,
    )
    return record, loop_ok


def run_twin_parity(
    assembled: AssembledRom,
    *,
    map_sensor_bar: int = 2,
    rev_limit_rpm: int | None = None,
    overboost_cut_kpa: float | None = None,
    fuel_cal: FuelCalibration | None = None,
    spark_cal: SparkCalibration | None = None,
    boost_cal: BoostCalibration | None = None,
    sample_cases: list[Mapping[str, int]] | None = None,
    use_adc: bool = False,
    max_cycles: int = MAX_MAIN_LOOP_CYCLES,
    profile_path: Path | str | None = None,
) -> TwinParitySummary:
    """Validate vectors, extract ROM calibrations, and prove 0-LSB HC11-vs-Twin parity."""
    if profile_path is not None and map_sensor_bar == 2:
        import json as _json
        prof = _json.loads(Path(profile_path).read_text(encoding="utf-8"))
        if not isinstance(prof, Mapping):
            raise ValueError(f"Profile {profile_path} must decode to a JSON object.")
        map_sensor_bar = int((prof.get("ecu_and_sensors") or {}).get("map_sensor_bar", map_sensor_bar))
    vec_res = validate_rom_vectors(assembled)
    if fuel_cal is None or spark_cal is None or boost_cal is None:
        ext_fuel, ext_spark, ext_boost = extract_calibrations_from_rom(
            assembled, map_sensor_bar=map_sensor_bar, rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa
        )
        fuel_cal = fuel_cal or ext_fuel
        spark_cal = spark_cal or ext_spark
        boost_cal = boost_cal or ext_boost

    corners = list(_DEFAULT_PARITY_CORNERS)
    for idx, sc in enumerate(sample_cases or ()):
        corners.append((
            str(sc.get("case_id", f"sample_{idx}")),
            int(sc["rpm_hb"]) & 0xFF, int(sc["map_raw"]) & 0xFF, int(sc.get("baro_raw", 101)) & 0xFF,
            int(sc["batt_raw"]) & 0xFF, int(sc.get("knock_raw", 0)) & 0xFF, int(sc.get("fuel_trim_u8", 0)) & 0xFF,
        ))

    cases: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    loop_budget_ok = True
    for corner in corners:
        rec, case_loop_ok = _evaluate_parity_case(
            assembled, corner, fuel_cal, spark_cal, boost_cal, use_adc=use_adc, max_cycles=max_cycles
        )
        loop_budget_ok = loop_budget_ok and case_loop_ok
        rec_dict = rec.as_dict()
        cases.append(rec_dict)
        if not rec.passed:
            failures.append(rec_dict)

    max_lsb_diff = max((c["max_lsb_diff"] for c in cases), default=0)
    max_loop_cycles = max((c["loop_cycles"] for c in cases), default=0)
    return TwinParitySummary(
        ok=vec_res.ok and loop_budget_ok and len(failures) == 0 and len(cases) > 0,
        total_cases=len(cases),
        passed_cases=len(cases) - len(failures),
        failed_cases=len(failures),
        max_lsb_diff=max_lsb_diff,
        max_loop_cycles=max_loop_cycles,
        max_loop_ms=round((max_loop_cycles / HC11_CLOCK_HZ) * 1000.0, 6),
        loop_budget_ok=loop_budget_ok,
        vectors_ok=vec_res.ok,
        vector_check=vec_res.as_dict(),
        cases=cases,
        failures=failures,
    )
