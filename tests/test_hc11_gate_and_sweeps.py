"""End-to-end M3 tests for 68HC11 vector/loop gates, Monte Carlo sweeps, staging gate, and CLI."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from click.testing import CliRunner
import pytest

from logicaide.cli import main as cli_main
from logicaide.sim import (
    AssembledRom,
    assemble_source,
    assemble_tree,
    ensure_writable_sim_path,
    measure_main_loop_budget,
    run_monte_carlo_sweep,
    run_staging_safety_gate,
    validate_rom_vectors,
)
from logicaide.telemetry import query_load_cell_rollups, summarize_telemetry_lake
from rom.models import encode_4byte_table

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_ASM = REPO_ROOT / "rom" / "src" / "main.asm"
SHADOW_PROFILE = REPO_ROOT / "profiles" / "examples" / "shadow_90_sbec_t2.json"
CSX_PROFILE = REPO_ROOT / "profiles" / "examples" / "csx_89_neon_dohc.json"


@pytest.fixture(scope="module")
def baseline_rom() -> AssembledRom:
    """Assemble the cleanroom 68HC11 ROM tree once per module."""
    return assemble_tree(MAIN_ASM)


def _patch_rom_bytes(rom: AssembledRom, patches: dict[int, bytes]) -> AssembledRom:
    """Return a copy of ``rom`` with absolute-address byte patches applied."""
    buf = bytearray(rom.rom_bytes)
    for addr, payload in patches.items():
        off = addr - rom.base_address
        buf[off : off + len(payload)] = payload
    raw = bytes(buf)
    return replace(rom, rom_bytes=raw, sha256=hashlib.sha256(raw).hexdigest())


def _delay_loop_bytes(entry_addr: int) -> bytes:
    """Assemble a valid 68HC11 subroutine exceeding 22,000 E-cycles."""
    src = f"        .org    0x{entry_addr:04X}\nMAIN_LOOP:\n        LDX     #4000\nDL:\n        DEX\n        BNE     DL\n        RTS\n"
    frag = assemble_source(src, base_address=entry_addr, rom_size_bytes=32)
    return frag.rom_bytes[:7]


def _write_mutated_profile(tmp_path: Path, filename: str, mutator: Callable[[dict[str, Any]], None]) -> Path:
    """Clone ``shadow_90_sbec_t2.json`` into ``tmp_path`` after applying ``mutator``."""
    data = json.loads(SHADOW_PROFILE.read_text(encoding="utf-8"))
    mutator(data)
    out = tmp_path / filename
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out


def _query_sqlite(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    """Execute a read-only SQLite query against a test catalog database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_validate_rom_vectors_cleanroom_passes(baseline_rom: AssembledRom) -> None:
    """Verify cleanroom rom/src/main.asm passes $FFC0-$FFFF vector validation."""
    res = validate_rom_vectors(baseline_rom)
    assert res.ok is True
    assert res.error_code is None
    assert res.invalid_vectors == []
    assert res.reset_vector == baseline_rom.symbols["RESET_ENTRY"]
    assert len(res.checked_vectors) == 32


@pytest.mark.parametrize(
    ("vec_addr", "target_kind", "code_patch"),
    [
        (0xFFFE, "zero", None),
        (0xFFFE, "erased", None),
        (0xFFFE, "low_ram", None),
        (0xFFFE, "table", None),
        (0xFFD6, "zero", None),
        (0xFFE8, "low_ram", None),
        (0xFFFE, "bad_op_addr", b"\x18\x01"),
        (0xFFD6, "bad_op_addr", b"\xFF"),
    ],
)
def test_validate_rom_vectors_rejects_corrupt_targets(
    baseline_rom: AssembledRom, vec_addr: int, target_kind: str, code_patch: bytes | None
) -> None:
    """Verify $FFC0-$FFFF validation rejects null, erased, <$8000, table, and illegal-opcode vectors."""
    targets = {
        "zero": 0x0000,
        "erased": 0xFFFF,
        "low_ram": 0x0100,
        "table": baseline_rom.symbols["BATOFF_TBL"],
        "bad_op_addr": 0xFB00,
    }
    target = targets[target_kind]
    patches: dict[int, bytes] = {vec_addr: target.to_bytes(2, "big")}
    if code_patch is not None:
        patches[target] = code_patch
    mutated = _patch_rom_bytes(baseline_rom, patches)

    res = validate_rom_vectors(mutated)
    assert res.ok is False
    assert res.error_code == "INVALID_VECTOR"
    assert any(item["vector"] == f"0x{vec_addr:04X}" for item in res.invalid_vectors)


def test_measure_main_loop_budget_cleanroom_and_tight_cap(baseline_rom: AssembledRom) -> None:
    """Verify cleanroom MAIN_LOOP passes well below 22,000 cycles and fails tight max_cycles=100."""
    clean = measure_main_loop_budget(baseline_rom.rom_bytes, baseline_rom.symbols)
    assert clean.ok is True
    assert clean.exceeded is False
    assert clean.error_code is None
    assert 0 < clean.cycles < 22_000
    assert clean.duration_ms < 11.0

    tight = measure_main_loop_budget(baseline_rom.rom_bytes, baseline_rom.symbols, max_cycles=100)
    assert tight.ok is False
    assert tight.exceeded is True
    assert tight.error_code == "LOOP_BUDGET_EXCEEDED"


def test_measure_main_loop_budget_synthetic_overrun(baseline_rom: AssembledRom) -> None:
    """Verify a synthetic 68HC11 delay loop exceeding 22,000 E-cycles triggers LOOP_BUDGET_EXCEEDED."""
    main_addr = baseline_rom.symbols["MAIN_LOOP"]
    patched = _patch_rom_bytes(baseline_rom, {main_addr: _delay_loop_bytes(main_addr)})
    res = measure_main_loop_budget(patched.rom_bytes, patched.symbols, max_cycles=22_000)
    assert res.ok is False
    assert res.exceeded is True
    assert res.cycles > 22_000
    assert res.duration_ms > 11.0
    assert res.error_code == "LOOP_BUDGET_EXCEEDED"


@pytest.mark.parametrize("sampler", ["SOBOL", "LHS", "OPTUNA"])
def test_monte_carlo_samplers_deterministic_and_lake_ingestion(sampler: str, tmp_path: Path) -> None:
    """Verify SOBOL, LHS, and OPTUNA sweeps are deterministic, pass invariants, and ingest into DuckDB/SQLite."""
    lake_a, db_a = tmp_path / "drives", tmp_path / "catalog.sqlite"
    lake_b, db_b = tmp_path / "drives_b", tmp_path / "catalog_b.sqlite"

    res_a = run_monte_carlo_sweep(
        profile_path=SHADOW_PROFILE, sampler=sampler, n_trials=32, seed=42,
        lake_dir=lake_a, db_path=db_a, build_id=f"mc_{sampler.lower()}",
    )
    res_b = run_monte_carlo_sweep(
        profile_path=SHADOW_PROFILE, sampler=sampler, n_trials=32, seed=42,
        lake_dir=lake_b, db_path=db_b, build_id=f"mc_{sampler.lower()}",
    )

    assert res_a.ok is True
    assert res_a.overflow_count == 0
    assert res_a.wrap_count == 0
    assert res_a.max_loop_cycles <= 22_000
    assert res_a.violations == []
    assert res_a.issue_ids == []
    assert (res_a.best_objective, res_a.max_rpm_seen, res_a.max_map_kpa_seen, res_a.max_loop_cycles) == (
        res_b.best_objective, res_b.max_rpm_seen, res_b.max_map_kpa_seen, res_b.max_loop_cycles,
    )
    assert len(res_a.parquet_chunks) >= 1
    assert all(Path(p).is_file() and "MONTE_CARLO_SWEEP" in p for p in res_a.parquet_chunks)

    summary = summarize_telemetry_lake(lake_dir=lake_a, db_path=db_a)
    rollups = query_load_cell_rollups(lake_dir=lake_a)
    assert "MONTE_CARLO_SWEEP" in summary["run_kinds"]
    assert summary["active_rows"] == 32
    assert len(rollups) > 0
    assert all(row["run_kind"] == "MONTE_CARLO_SWEEP" for row in rollups)

    campaigns = _query_sqlite(db_a, "SELECT * FROM optimization_campaigns WHERE campaign_id = ?", (res_a.campaign_id,))
    assert len(campaigns) == 1
    assert campaigns[0]["sampler"] == sampler
    assert campaigns[0]["n_trials"] == 32
    assert campaigns[0]["status"] == "COMPLETED"


def test_monte_carlo_fault_injection_persists_critical_issues(baseline_rom: AssembledRom, tmp_path: Path) -> None:
    """Verify calibration, spark-wrap, and ceiling fault injections persist CRITICAL tuning_issues."""
    db_path = tmp_path / "catalog.sqlite"
    lake_dir = tmp_path / "drives"

    # 1. Patch REV_LIMIT_U8=255 (8160 valid) while enforcing 6500 RPM target -> REV_LIMIT_VIOLATION
    rev_rom = _patch_rom_bytes(baseline_rom, {baseline_rom.symbols["REV_LIMIT_U8"]: b"\xFF"})
    rev_res = run_monte_carlo_sweep(
        rev_rom, profile_path=SHADOW_PROFILE, sampler="SOBOL", n_trials=32, seed=42,
        rev_limit_rpm=6500, lake_dir=lake_dir, db_path=db_path, write_parquet=False,
    )
    assert rev_res.ok is False

    # 2. Patch OVERBOOST_CUT_U8=255 while enforcing 200.0 kPa target -> OVERBOOST_CUT_VIOLATION
    ob_rom = _patch_rom_bytes(baseline_rom, {baseline_rom.symbols["OVERBOOST_CUT_U8"]: b"\xFF"})
    ob_res = run_monte_carlo_sweep(
        ob_rom, profile_path=SHADOW_PROFILE, sampler="SOBOL", n_trials=32, seed=42,
        overboost_cut_kpa=200.0, lake_dir=lake_dir, db_path=db_path, write_parquet=False,
    )
    assert ob_res.ok is False

    # 3. Patch BASE_SPK_TBL too low (underflow) AND patch CALC_SPARK_ADVANCE to wrap spark > base_adv
    low_base_tbl = encode_4byte_table([(0x20, 4), (0x40, 4), (0x64, 4), (0x96, 4), (0xC8, 4)])
    tbl_rom = _patch_rom_bytes(baseline_rom, {baseline_rom.symbols["BASE_SPK_TBL"]: low_base_tbl})
    tbl_res = run_monte_carlo_sweep(
        tbl_rom, sampler="SOBOL", n_trials=32, seed=42, lake_dir=lake_dir, db_path=db_path, write_parquet=False,
    )
    assert tbl_res.ok is False and tbl_res.wrap_count > 0

    # LDAA #0x78; STAA *RAM_SPARK_ADV; RTS -> HC11 outputs spark=120 > base_adv (max 80)
    brk_src = "        .org    0x9400\nCALC_SPARK_ADVANCE:\n        LDAA    #0x78\n        STAA    *0x39\n        RTS\n"
    brk_code = assemble_source(brk_src, base_address=0x9400, rom_size_bytes=16).rom_bytes[:5]
    brk_rom = _patch_rom_bytes(baseline_rom, {baseline_rom.symbols["CALC_SPARK_ADVANCE"]: brk_code})
    brk_res = run_monte_carlo_sweep(
        brk_rom, sampler="SOBOL", n_trials=32, seed=42, lake_dir=lake_dir, db_path=db_path, write_parquet=False,
    )
    assert brk_res.ok is False and brk_res.wrap_count > 0

    # 4. Explicit rev_limit_rpm=9000 and overboost_cut_kpa=250.0 (2-bar) violations
    hi_rev = run_monte_carlo_sweep(
        baseline_rom, profile_path=SHADOW_PROFILE, sampler="SOBOL", n_trials=16, seed=42,
        rev_limit_rpm=9000, lake_dir=lake_dir, db_path=db_path, write_parquet=False,
    )
    hi_ob = run_monte_carlo_sweep(
        baseline_rom, profile_path=SHADOW_PROFILE, sampler="SOBOL", n_trials=16, seed=42,
        overboost_cut_kpa=250.0, lake_dir=lake_dir, db_path=db_path, write_parquet=False,
    )
    assert hi_rev.ok is False and hi_ob.ok is False

    issues = _query_sqlite(db_path, "SELECT category, severity FROM tuning_issues")
    cats = {row["category"] for row in issues}
    assert all(row["severity"] == "CRITICAL" for row in issues)
    assert {
        "REV_LIMIT_VIOLATION", "OVERBOOST_CUT_VIOLATION", "SPARK_UNDERFLOW_WRAP", "REV_LIMIT_CEILING_EXCEEDED"
    }.issubset(cats)


def test_staging_safety_gate_pass_shadow_and_amber_csx(tmp_path: Path) -> None:
    """Verify PASS staging artifacts for BOLT_ON_NATIVE and 7 [AMBER] warnings for HYBRID_MACHINING_REQUIRED."""
    staging_root = tmp_path / "staging"
    lake_dir, db_path = tmp_path / "drives", tmp_path / "catalog.sqlite"

    shadow_res = run_staging_safety_gate(
        profile_path=SHADOW_PROFILE, build_id="shadow_pass", staging_root=staging_root,
        lake_dir=lake_dir, db_path=db_path, n_trials=32, seed=42,
    )
    assert shadow_res.ok is True
    assert shadow_res.status == "PASS"
    assert shadow_res.warnings == []
    bin_path = staging_root / "shadow_pass" / "shadow_pass.bin"
    assert bin_path.is_file() and bin_path.stat().st_size == 32_768
    assert Path(shadow_res.summary_path).is_file()
    report_txt = Path(shadow_res.report_path).read_text(encoding="utf-8")
    for token in (
        shadow_res.candidate_sha256, "22000", "11.0 ms", "vectors_ok=True",
        "Monte Carlo Sweep", "Human-Only Manual Verification", "Pin 11", "minipro",
    ):
        assert token in report_txt

    builds = _query_sqlite(db_path, "SELECT safety_gate_status FROM candidate_builds WHERE build_id = ?", ("shadow_pass",))
    assert len(builds) == 1 and builds[0]["safety_gate_status"] == "PASS"

    csx_res = run_staging_safety_gate(
        profile_path=CSX_PROFILE, build_id="csx_amber_pass", staging_root=staging_root,
        lake_dir=lake_dir, db_path=db_path, n_trials=32, seed=42,
    )
    assert csx_res.ok is True
    assert csx_res.status == "PASS"
    assert len(csx_res.warnings) == 7
    assert all("[AMBER]" in w for w in csx_res.warnings)
    assert "Advisory Warnings (AMBER)" in Path(csx_res.report_path).read_text(encoding="utf-8")


def _build_blocked_cases(baseline_rom: AssembledRom, tmp_path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Construct the 6 mandatory BLOCKED safety-gate test scenarios."""
    pin11_false = _write_mutated_profile(
        tmp_path, "pin11_false.json", lambda d: d["ecu_and_sensors"].__setitem__("pin11_continuity_verified", False)
    )
    def _edz_8v(d: dict[str, Any]) -> None:
        d["engine_bottom_end"]["block_code"] = "2.4L_EDZ"
        d["cylinder_head_and_cam"]["head_code"] = "8V_445_BATHTUB"
    incompat_prof = _write_mutated_profile(tmp_path, "edz_8v.json", _edz_8v)
    main_addr = baseline_rom.symbols["MAIN_LOOP"]
    return [
        ("blk_rev9000", {"profile_path": SHADOW_PROFILE, "rev_limit_rpm": 9000}),
        ("blk_ob260", {"profile_path": SHADOW_PROFILE, "overboost_cut_kpa": 260.0}),
        ("blk_pin11", {"profile_path": pin11_false}),
        ("blk_edz8v", {"profile_path": incompat_prof}),
        ("blk_vec", {"profile_path": SHADOW_PROFILE, "patch_bytes": {0xFFFE: b"\x01\x00"}}),
        ("blk_loop", {"profile_path": SHADOW_PROFILE, "patch_bytes": {main_addr: _delay_loop_bytes(main_addr)}}),
    ]


def test_staging_safety_gate_blocked_cases(baseline_rom: AssembledRom, tmp_path: Path) -> None:
    """Verify all 6 fault cases return BLOCKED, suppress programmer commands, and persist BLOCKED status."""
    staging_root = tmp_path / "staging"
    lake_dir, db_path = tmp_path / "drives", tmp_path / "catalog.sqlite"

    for build_id, kwargs in _build_blocked_cases(baseline_rom, tmp_path):
        res = run_staging_safety_gate(
            build_id=build_id, staging_root=staging_root, lake_dir=lake_dir, db_path=db_path,
            n_trials=16, seed=42, write_parquet=False, **kwargs,
        )
        assert res.ok is False
        assert res.status == "BLOCKED"
        report_txt = Path(res.report_path).read_text(encoding="utf-8")
        assert "# SAFETY GATE BLOCKED — DO NOT FLASH" in report_txt
        assert "minipro" not in report_txt.lower()
        assert "ostrich" not in report_txt.lower()
        rows = _query_sqlite(db_path, "SELECT safety_gate_status FROM candidate_builds WHERE build_id = ?", (build_id,))
        assert len(rows) == 1 and rows[0]["safety_gate_status"] == "BLOCKED"


def test_hardware_deadbolt_protected_directories() -> None:
    """Verify ensure_writable_sim_path blocks immutable ROM zones and they remain untouched."""
    for zone in ("inputs", "stock_baselines"):
        protected = REPO_ROOT / "rom" / zone
        assert sorted(p.name for p in protected.iterdir()) == ["README.md"]
        with pytest.raises(ValueError, match="SECURITY.md"):
            ensure_writable_sim_path(protected)
        with pytest.raises(ValueError, match="SECURITY.md"):
            ensure_writable_sim_path(protected / "probe.bin")


def test_cli_sim_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise sim parity, sweep, stage (PASS + BLOCKED exit 2), and verify-sha256 via Click CliRunner."""
    monkeypatch.chdir(REPO_ROOT)
    runner = CliRunner()
    lake_dir = tmp_path / "cli_drives"
    db_path = lake_dir / "catalog.sqlite"
    staging_dir = tmp_path / "cli_staging"

    parity = runner.invoke(cli_main, ["sim", "parity", "--profile", "profiles/examples/shadow_90_sbec_t2.json", "--json"])
    assert parity.exit_code == 0, parity.output
    parity_json = json.loads(parity.output)
    assert parity_json["ok"] is True and parity_json["max_lsb_diff"] == 0

    sweep = runner.invoke(cli_main, [
        "sim", "sweep", "--profile", "profiles/examples/shadow_90_sbec_t2.json",
        "--sampler", "SOBOL", "--n-trials", "16", "--seed", "42",
        "--lake-dir", str(lake_dir), "--db-path", str(db_path), "--json",
    ])
    assert sweep.exit_code == 0, sweep.output
    assert json.loads(sweep.output)["ok"] is True

    stage_ok = runner.invoke(cli_main, [
        "sim", "stage", "--profile", "profiles/examples/shadow_90_sbec_t2.json",
        "--build-id", "cli_pass", "--n-trials", "16",
        "--staging-dir", str(staging_dir), "--lake-dir", str(lake_dir), "--db-path", str(db_path), "--json",
    ])
    assert stage_ok.exit_code == 0, stage_ok.output
    assert json.loads(stage_ok.output)["status"] == "PASS"

    stage_blk = runner.invoke(cli_main, [
        "sim", "stage", "--profile", "profiles/examples/shadow_90_sbec_t2.json",
        "--build-id", "cli_blocked", "--n-trials", "16", "--rev-limit-rpm", "9000",
        "--staging-dir", str(staging_dir), "--lake-dir", str(lake_dir), "--db-path", str(db_path), "--json",
    ])
    assert stage_blk.exit_code == 2, stage_blk.output
    blk_json = json.loads(stage_blk.output)
    assert blk_json["status"] == "BLOCKED"
    assert "# SAFETY GATE BLOCKED — DO NOT FLASH" in Path(blk_json["report_path"]).read_text(encoding="utf-8")

    verify = runner.invoke(cli_main, ["sim", "verify-sha256", "--json"])
    assert verify.exit_code == 0, verify.output
    assert json.loads(verify.output)["ok"] is True


def test_staging_build_id_symlink_escape_and_invalid_profile_guards(tmp_path: Path) -> None:
    """Reject unsafe build_ids and symlinks, block missing in-memory pinouts, and persist schema-invalid BLOCKED rows."""
    staging_root = tmp_path / "staging"
    outside_dir = tmp_path / "outside"
    staging_root.mkdir()
    outside_dir.mkdir()
    lake_dir, db_path = tmp_path / "drives", tmp_path / "catalog.sqlite"

    for bad_id in (".", "..", "bad id;echo", "a/b"):
        with pytest.raises(ValueError, match="Unsafe build_id"):
            run_staging_safety_gate(build_id=bad_id, staging_root=staging_root, lake_dir=lake_dir, db_path=db_path, n_trials=8)

    (staging_root / "sym_escape").symlink_to(outside_dir)
    with pytest.raises(ValueError, match="symlinked staging target"):
        run_staging_safety_gate(build_id="sym_escape", staging_root=staging_root, lake_dir=lake_dir, db_path=db_path, n_trials=8)

    inmem = json.loads((REPO_ROOT / "profiles/examples/shadow_90_sbec_t2.json").read_text(encoding="utf-8"))
    del inmem["ecu_and_sensors"]["pin11_continuity_verified"]
    blk_mem = run_staging_safety_gate(
        profile=inmem, build_id="blk_mem_nopin", staging_root=staging_root, lake_dir=lake_dir, db_path=db_path, n_trials=8, write_parquet=False,
    )
    assert blk_mem.status == "BLOCKED" and any("pin11" in r for r in blk_mem.blocking_reasons)

    bad_schema = json.loads((REPO_ROOT / "profiles/examples/shadow_90_sbec_t2.json").read_text(encoding="utf-8"))
    bad_schema["identity_and_chassis"]["year"] = 1999
    bad_schema["ecu_and_sensors"]["ecu_family"] = "BAD_ECU"
    bad_path = tmp_path / "bad_schema.json"
    bad_path.write_text(json.dumps(bad_schema), encoding="utf-8")
    blk_schema = run_staging_safety_gate(
        profile_path=bad_path, build_id="blk_schema", staging_root=staging_root, lake_dir=lake_dir, db_path=db_path, n_trials=8,
    )
    assert blk_schema.status == "BLOCKED"
    assert _query_sqlite(db_path, "SELECT safety_gate_status FROM candidate_builds WHERE build_id = ?", ("blk_schema",))[0]["safety_gate_status"] == "BLOCKED"


def test_nan_overboost_sweep_and_long_equ_symbol_splay(tmp_path: Path) -> None:
    """Reject NaN overboost thresholds in Monte Carlo sweeps and reassemble >=18-char .equ symbols."""
    from logicaide.sim.assembler import assemble_tree
    from logicaide.sim.splay import splay_rom_binary, verify_roundtrip_sha256

    stock = assemble_tree(REPO_ROOT / "rom/src/main.asm")
    nan_sweep = run_monte_carlo_sweep(
        stock, overboost_cut_kpa=float("nan"), n_trials=8, seed=7, verify_hc11=False, write_parquet=False,
        lake_dir=tmp_path / "drives", db_path=tmp_path / "catalog.sqlite",
    )
    assert nan_sweep.ok is False and any(v["category"] == "OVERBOOST_CUT_VIOLATION" for v in nan_sweep.violations)

    long_syms = dict(stock.symbols)
    long_syms["VERY_LONG_SYMBOL_NAME_GE18"] = 0x1234
    splay_dir = tmp_path / "splay_long"
    splay_rom_binary(stock.rom_bytes, splay_dir, symbols=long_syms)
    rt = verify_roundtrip_sha256(stock.rom_bytes, splay_dir)
    assert rt.ok is True and rt.error_code is None

