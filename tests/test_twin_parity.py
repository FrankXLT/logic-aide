"""Sequential unit and integration tests for 68HC11 twins, assembler, splay, and parity."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from logicaide.cli import main
from logicaide.sim.assembler import AssemblyError, assemble_source, assemble_tree, format_s19
from logicaide.sim.hc11_peripherals import MAX_MAIN_LOOP_CYCLES, create_hc11_cpu, validate_rom_vectors
from logicaide.sim.hc11_runner import execute_ram_case, run_twin_parity
from logicaide.sim.splay import build_candidate_rom_bytes, splay_rom_binary, verify_roundtrip_sha256
from logicaide.telemetry import export_stimulus_fixture
from rom.models import (
    DEFAULT_MIN_PW_TICKS,
    SparkCalibration,
    calc_anti_dwell_ticks,
    calc_dwell_ticks,
    calc_fuel_pw,
    calc_spark_advance,
    calc_wastegate_and_limiters,
    check_unclamped_spark_wrap,
    default_boost_calibration,
    default_fuel_calibration,
    default_spark_calibration,
    encode_4byte_table,
    encode_5byte_table,
    hc11_fdiv,
    hc11_idiv,
    hc11_mul,
    kpa_to_raw_map,
    lookup_4byte_table,
    lookup_5byte_table,
    raw_map_to_kpa,
    scale_map_adc,
    scale_x_by_b_adding_a,
    scale_y_by_a,
    validate_overboost_cut_kpa,
    validate_rev_limit_rpm,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_ASM = REPO_ROOT / "rom" / "src" / "main.asm"
SHADOW_PROFILE = REPO_ROOT / "profiles" / "examples" / "shadow_90_sbec_t2.json"
CSX_PROFILE = REPO_ROOT / "profiles" / "examples" / "csx_89_neon_dohc.json"
TELEMETRY_SLICE = REPO_ROOT / "tests" / "fixtures" / "telemetry" / "ksmec_mpN7D7P6_slice.csv"


def _flipped_first_byte_asm(asm_text: str) -> str:
    """Toggle the first 0xNN literal inside a .byte directive in splayed assembly text."""
    lines = asm_text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(".byte"):
            prefix, rest = line.split("0x", 1)
            old_val = int(rest[:2], 16)
            new_val = old_val ^ 0x01
            lines[idx] = f"{prefix}0x{new_val:02X}{rest[2:]}"
            return "\n".join(lines) + "\n"
    raise AssertionError("No .byte 0xNN line found to mutate")


def test_hc11_arithmetic_primitives() -> None:
    """Verify exact 68HC11 MUL, IDIV, FDIV, and Q8 scaling with bit-7 rounding."""
    assert hc11_mul(0x10, 0x08) == (0x0080, True)
    assert hc11_mul(0x10, 0x04) == (0x0040, False)
    assert hc11_idiv(1000, 3) == (333, 1, False)
    assert hc11_idiv(0x1234, 0) == (0xFFFF, 0x1234, True)
    assert hc11_fdiv(1, 2) == (0x8000, 0, False, False)
    assert hc11_fdiv(4, 2) == (0x0000, 0, True, False)
    assert hc11_fdiv(7, 0) == (0xFFFF, 7, True, True)

    assert scale_y_by_a(0x80, 0x0100) == 0x0080
    assert scale_y_by_a(0x80, 0x0101) == 0x0081  # bit-7 carry rounding
    assert scale_y_by_a(0x40, 0x0102) == 0x0041

    assert scale_x_by_b_adding_a(0, 0x80, 1000) == (500, False)
    assert scale_x_by_b_adding_a(1, 0x80, 1000) == (1500, False)
    assert scale_x_by_b_adding_a(1, 0x80, 0xC000) == (0xFFFF, True)


def test_4byte_and_5byte_table_lookup_primitives() -> None:
    """Verify 4-byte half-bit carry rounding and 5-byte 0xFF sentinel clamping."""
    tbl4 = encode_4byte_table([(10, 20), (14, 22)])
    assert tbl4[0] == 2
    assert lookup_4byte_table(5, tbl4) == 20
    assert lookup_4byte_table(10, tbl4) == 20
    assert lookup_4byte_table(11, tbl4) == 21  # delta=1 * slope=0x0080 rounds up +1
    assert lookup_4byte_table(50, tbl4) == 22

    tbl5 = encode_5byte_table([(10, 100), (20, 300)])
    assert tbl5[-1] == 0xFF
    assert len(tbl5) == 11
    assert lookup_5byte_table(5, tbl5) == 100
    assert lookup_5byte_table(15, tbl5) == 200
    assert lookup_5byte_table(30, tbl5) == 300


def test_fuel_twin_floor_saturation_and_map_scaling() -> None:
    """Verify #0x0060 PW floor, 0xFFFF carry clamp, and 1/2/3-bar MAP scaling."""
    cal_2bar = default_fuel_calibration(2)
    low = calc_fuel_pw(28, 0, 250, cal_2bar)
    assert low.floor_clamped is True
    assert low.pw_ticks_u16 == DEFAULT_MIN_PW_TICKS == 0x0060

    high_wottbl = encode_5byte_table([(0x00, 0x0000), (0xFF, 0xF000)])
    sat_cal = default_fuel_calibration(2).__class__(
        batoff_table=cal_2bar.batoff_table,
        peftbl_table=cal_2bar.peftbl_table,
        wottbl_table=high_wottbl,
        map_mult=cal_2bar.map_mult,
        map_add=cal_2bar.map_add,
        map_sensor_bar=2,
    )
    sat = calc_fuel_pw(0x71, 0xFF, 0x59, sat_cal, fuel_trim_u8=0x80)
    assert sat.saturated_u16 is True
    assert sat.pw_ticks_u16 == 0xFFFF

    for bar, kpa in ((1, 95.0), (2, 180.0), (3, 275.0)):
        raw = kpa_to_raw_map(kpa, bar)
        assert 0 < raw <= 255
        assert abs(raw_map_to_kpa(raw, bar) - kpa) < 1.5
    scaled_2bar, _ = scale_map_adc(0x80, cal_2bar.map_mult, cal_2bar.map_add)
    scaled_3bar, _ = scale_map_adc(0x80, default_fuel_calibration(3).map_mult, default_fuel_calibration(3).map_add)
    assert scaled_2bar != scaled_3bar
    assert scale_map_adc(0xFF, 0x7F, 0x8000) == (0xFF, True)


def test_spark_twin_wrap_guard_and_dwell() -> None:
    """Verify borrow clamp prevents 0xFF wrap and dwell/anti-dwell stay bounded."""
    assert check_unclamped_spark_wrap(10, 25) is True
    assert check_unclamped_spark_wrap(25, 25) is False

    cal = default_spark_calibration()
    underflow = calc_spark_advance(rpm_hb_u8=0, map_raw_u8=250, knock_raw_u8=240, cal=cal)
    assert underflow.underflow_clamped is True
    assert underflow.wrap_detected is True
    assert underflow.spark_u8 == 0  # clamped to 0 instead of 0xFF wrap

    # Trigger knock-stage underflow specifically (after_map < knk_ret)
    tight_cal = SparkCalibration(
        base_spk_table=encode_4byte_table([(0x20, 12), (0xC8, 12)]),
        map_retard_table=encode_4byte_table([(0x20, 4), (0xC8, 4)]),
        dltknk_table=cal.dltknk_table,
        retmax_table=encode_4byte_table([(0x20, 16), (0xC8, 16)]),
        spklmt_table=cal.spklmt_table,
        dwell_table=cal.dwell_table,
    )
    knk_wrap = calc_spark_advance(0x60, 0x80, 0xF0, tight_cal)
    assert knk_wrap.underflow_clamped is True
    assert knk_wrap.spark_u8 == 0

    dwell = calc_dwell_ticks(0xD8, cal)
    assert dwell == 2500
    assert calc_anti_dwell_ticks(0xD8, 1000, cal) == cal.min_adwell_ticks
    assert calc_anti_dwell_ticks(0xD8, 3500, cal) == 3500 - dwell


def test_boost_twin_limiters_and_ceilings() -> None:
    """Verify rev-limit <= 8160 RPM and 1/2/3-bar overboost cut ceilings."""
    assert validate_rev_limit_rpm(8160) == 255
    with pytest.raises(ValueError):
        validate_rev_limit_rpm(8161)

    for bar, max_kpa in ((1, 105.0), (2, 205.0), (3, 315.0)):
        assert validate_overboost_cut_kpa(max_kpa, bar) == max_kpa
        with pytest.raises(ValueError):
            validate_overboost_cut_kpa(max_kpa + 0.1, bar)

    cal = default_boost_calibration(2, rev_limit_rpm=6496, overboost_cut_kpa=200.0)
    cut = calc_wastegate_and_limiters(cal.rev_limit_u8, cal.overboost_cut_u8, 101, 0, cal)
    assert cut.overboost_cut is True
    assert cut.rev_limit_cut is True
    assert cut.fuel_cut is True
    assert cut.cutout_flags_u8 == 0x07


def test_cleanroom_assembler_s19_and_error_bounds() -> None:
    """Verify 2-pass assembly of main.asm, S19 formatting, and overlap/bounds rejection."""
    assembled = assemble_tree(MAIN_ASM)
    assert assembled.base_address == 0x8000
    assert assembled.rom_size_bytes == 32768
    assert len(assembled.rom_bytes) == 32768
    assert validate_rom_vectors(assembled).ok is True

    s19_lines = format_s19(assembled.rom_bytes, 0x8000).splitlines()
    assert s19_lines[0].startswith("S0")
    assert s19_lines[-1].startswith("S9")
    assert any(line.startswith("S1") for line in s19_lines[1:-1])

    with pytest.raises(AssemblyError, match="Overlapping"):
        assemble_source(" .org 0x8000\n .byte 0x01\n .org 0x8000\n .byte 0x02\n")
    with pytest.raises(AssemblyError, match="outside ROM"):
        assemble_source(" .org 0x7FFF\n .byte 0x01\n")


def test_splay_roundtrip_sha256_and_mutation_detection(tmp_path: Path) -> None:
    """Verify cleanroom ROM splays/reassembles bit-identically and detects 1-byte edits."""
    assembled = assemble_tree(MAIN_ASM)
    splay_dir = tmp_path / "splay"
    tree = splay_rom_binary(assembled.rom_bytes, splay_dir, symbols=assembled.symbols)
    assert (splay_dir / "main.asm").is_file()
    assert len(tree.files) == 6

    ok_res = verify_roundtrip_sha256(assembled.rom_bytes, splay_dir)
    assert ok_res.ok is True
    assert ok_res.error_code is None
    assert ok_res.expected_sha256 == ok_res.actual_sha256 == assembled.sha256
    assert ok_res.mismatch_count == 0

    fuel_asm = splay_dir / "fuel_tables.asm"
    fuel_asm.write_text(_flipped_first_byte_asm(fuel_asm.read_text(encoding="utf-8")), encoding="utf-8")
    bad_res = verify_roundtrip_sha256(assembled.rom_bytes, splay_dir)
    assert bad_res.ok is False
    assert bad_res.error_code == "ROUNDTRIP_SHA256_MISMATCH"
    assert bad_res.mismatch_count == 1


def test_hc11_peripheral_bus_ports_sci_adc_and_timer() -> None:
    """Verify $1000..$100A, $102B..$102F SCI, $1030..$1034 ADC, and timer advance."""
    prog = assemble_source(
        " .org 0x8000\n"
        "START:\n"
        " LDAA #0xA5\n"
        " STAA 0x1000\n"
        " LDAA 0x1031\n"
        " STAA 0x0040\n"
        " LDAA 0x1032\n"
        " STAA 0x0041\n"
        " LDAA 0x102E\n"
        " STAA 0x0042\n"
        " LDAA 0x102F\n"
        " STAA 0x0043\n"
        " STAA 0x102F\n"
        " RTS\n"
    )
    cpu, periph = create_hc11_cpu(
        prog.rom_bytes, adc_channels=(0x42, 0xD8, 0x80, 0x99), sci_rx_bytes=[0x5A]
    )
    cpu.mem[0x00FF], cpu.mem[0x00FE], cpu.sp, cpu.pc = 0xFF, 0xFF, 0x00FD, 0x8000
    tcnt_start = cpu.read16(0x100E)
    total_cycles = 0
    while cpu.pc != 0xFFFF:
        step = cpu.step()
        total_cycles += step.cycles
        cpu._timer_advance(step.cycles)

    assert cpu.read8(0x1000) == 0xA5
    assert cpu.read8(0x0040) == 0x42  # ADR1 not shadowed by timer
    assert cpu.read8(0x0041) == 0xD8  # ADR2 not shadowed by timer
    assert (cpu.read8(0x0042) & 0x20) == 0x20  # SCSR RDRF set
    assert cpu.read8(0x0043) == 0x5A  # SCDR RX byte
    assert periph["sci"]["tx_bytes"] == [0x5A]
    assert cpu.read16(0x100E) == (tcnt_start + total_cycles) & 0xFFFF


def test_cleanroom_twin_parity_ram_and_adc() -> None:
    """Prove 0-LSB parity and <= 22,000 cycles across RAM injection and ADC hooks."""
    assembled = assemble_tree(MAIN_ASM)
    for use_adc in (False, True):
        summary = run_twin_parity(assembled, use_adc=use_adc)
        assert summary.ok is True
        assert summary.passed_cases == summary.total_cases >= 32
        assert summary.max_lsb_diff == 0
        assert summary.vectors_ok is True
        assert summary.loop_budget_ok is True
        assert summary.max_loop_cycles <= MAX_MAIN_LOOP_CYCLES

    sci_out = execute_ram_case(
        assembled, rpm_hb=64, map_raw=0x88, baro_raw=101, batt_raw=0xD8,
        knock_raw=0, use_adc=True, sci_rx_bytes=[0xF2],
    )
    assert sci_out["ok"] is True
    assert sci_out["sci_last_rx"] == 0xF2
    assert sci_out["sci_last_tx"] == 0x88
    assert sci_out["sci_tx_bytes"] == [0x88]


@pytest.mark.parametrize("profile_path", [SHADOW_PROFILE, CSX_PROFILE], ids=["shadow_2bar", "csx_3bar"])
def test_profile_twin_parity_shadow_and_csx(profile_path: Path) -> None:
    """Prove 0-LSB parity on 2-bar Shadow and 3-bar CSX candidate ROM builds."""
    _, candidate = build_candidate_rom_bytes(profile_path)
    summary = run_twin_parity(candidate, profile_path=profile_path)
    assert summary.ok is True
    assert summary.passed_cases == summary.total_cases
    assert summary.max_lsb_diff == 0
    assert summary.vectors_ok is True
    assert summary.max_loop_cycles <= MAX_MAIN_LOOP_CYCLES


def test_telemetry_fixture_corner_parity(tmp_path: Path) -> None:
    """Prove 0-LSB parity on GPS-stripped real telemetry corner samples."""
    stim_path = tmp_path / "stim.csv"
    export_res = export_stimulus_fixture(TELEMETRY_SLICE, stim_path, max_rows=10)
    assert export_res["gps_stripped"] is True

    samples: list[dict[str, int]] = []
    with stim_path.open(encoding="utf-8") as fh:
        for idx, row in enumerate(csv.DictReader(fh)):
            rpm_hb = max(0, min(255, round(float(row["RPM"]) / 32.0)))
            map_kpa = 101.325 + float(row["MAP3BAR"]) * 6.894757
            samples.append({
                "case_id": f"telem_{idx}",
                "rpm_hb": rpm_hb,
                "map_raw": kpa_to_raw_map(map_kpa, map_sensor_bar=2),
                "baro_raw": 101,
                "batt_raw": 0xD8,
                "knock_raw": 0,
                "fuel_trim_u8": 0,
            })

    summary = run_twin_parity(assemble_tree(MAIN_ASM), sample_cases=samples)
    assert summary.ok is True
    assert summary.passed_cases == summary.total_cases
    assert summary.max_lsb_diff == 0


def test_cli_manifest_sync_writes_asm_and_twin_tags(tmp_path: Path) -> None:
    """Verify sim manifest-sync emits rom/src/*.asm and rom/models/*_twin.py CTAGS."""
    tags_out = tmp_path / "tags"
    res = CliRunner().invoke(main, ["sim", "manifest-sync", "--tags-path", str(tags_out), "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["tag_count"] > 0
    tags_text = tags_out.read_text(encoding="utf-8")
    assert "rom/src/main.asm" in tags_text
    assert "rom/models/fuel_twin.py" in tags_text
    assert "MAIN_LOOP\trom/src/main.asm" in tags_text
    assert "calc_fuel_pw\trom/models/fuel_twin.py" in tags_text
    assert "calc_spark_advance\trom/models/spark_twin.py" in tags_text
    assert "calc_wastegate_and_limiters\trom/models/boost_twin.py" in tags_text
