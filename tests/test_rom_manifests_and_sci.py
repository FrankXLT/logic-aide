"""Unit, CLI, and cleanroom verification tests for partitioned ROM manifests and SCI lookup."""

from __future__ import annotations

import json
from pathlib import Path
import re

from click.testing import CliRunner
import pytest

from logicaide.cli import main as cli_main
from logicaide.rom.manifest import NO_VERIFIED_SOURCE_MATCH, lookup_sci, sync_manifests
from logicaide.rom.sci_xref import PROTOCOL_COLUMNS, load_mpr_sci_crossref, lookup_xref_variable

REPO_ROOT = Path(__file__).resolve().parents[1]
XREF_PATH = REPO_ROOT / "rom" / "sci" / "mpr_sci_crossref.json"
MANIFESTS_DIR = REPO_ROOT / "rom" / "manifests"
CATALOG_PATH = MANIFESTS_DIR / "catalog.json"

PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("local POSIX path", re.compile(r"/" + r"home/[A-Za-z0-9_.-]+")),
    ("local Windows path", re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+", re.IGNORECASE)),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("contributor token", re.compile(r"\b(?:bu" + r"car|ste" + r"ve|mcc" + r"lurg|bas" + r"tage)\b", re.IGNORECASE)),
)
DEADBOLT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("flash form token", re.compile(r"Flash" + r"\.frm", re.IGNORECASE)),
    ("ttyUSB device", re.compile(r"/dev/" + r"ttyUSB", re.IGNORECASE)),
    ("pyserial open", re.compile(r"\bserial\." + r"Serial\b")),
    ("flash write opcode", re.compile(r"\b(?:flash_ecu|program_eeprom|erase_sector)\b", re.IGNORECASE)),
)


def _load_manifest(rom_id: str) -> dict:
    """Load one generated partitioned ROM manifest from disk."""
    return json.loads((MANIFESTS_DIR / rom_id / "rom_manifest.json").read_text(encoding="utf-8"))


def test_mpr_sci_crossref_and_lookup() -> None:
    """Verify the 402-variable, 10-protocol MPR SCI cross-reference and query API."""
    xref = load_mpr_sci_crossref(XREF_PATH)
    assert xref["variable_count"] == 402
    assert xref["csv_nonempty_line_count"] == 403
    assert xref["protocol_count"] == 10
    assert tuple(xref["protocols"]) == PROTOCOL_COLUMNS
    assert len(PROTOCOL_COLUMNS) == 10

    expected_err_regs = {f"ERR{i}" for i in range(4)} | {f"ERS{i}" for i in range(4)}
    assert expected_err_regs.issubset(xref["error_registers"].keys())

    for func_id in ("12", "79", "392"):
        assert func_id in xref["function_sets"]
        assert xref["function_sets"][func_id]["points"]

    for query in ("0x63", "$63", 99):
        hits = lookup_xref_variable(xref, protocol="SMEC T1", offset=query)
        assert hits
        assert any(h["short_name"] == "MAP" for h in hits)


def test_partitioned_catalog_and_ecu_topology() -> None:
    """Verify catalog.json and strict separation across SMEC, SBEC, LM, and GLHS manifests."""
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert catalog["manifest_count"] == 4
    by_id = {entry["rom_id"]: entry for entry in catalog["manifests"]}
    assert set(by_id) == {"smec_88_89_v19", "sbec_90_93_v3_1", "lm_86_87_v13", "lm_86_glhs_v1"}

    expected_meta = {
        "smec_88_89_v19": ("SMEC_DUAL_BOARD", "68HC11", 32768),
        "sbec_90_93_v3_1": ("SBEC_SINGLE_BOARD", "68HC11", 32768),
        "lm_86_87_v13": ("LM_PM", "6803", 16384),
        "lm_86_glhs_v1": ("LM_PM", "6803", 8192),
    }
    manifests = {rid: _load_manifest(rid) for rid in expected_meta}
    for rid, (family, mcu, size_bytes) in expected_meta.items():
        m = manifests[rid]
        assert (m["ecu_family"], m["mcu"], m["rom_size_bytes"]) == (family, mcu, size_bytes)
        assert (by_id[rid]["ecu_family"], by_id[rid]["mcu"], by_id[rid]["rom_size_bytes"]) == (family, mcu, size_bytes)

    glhs = manifests["lm_86_glhs_v1"]
    assert glhs["split_chip_topology"] == "2x_8KB_2764"
    assert glhs["origins"]["named"]["LowerChipOrg"]["hex"] == "0xA000"

    smec = manifests["smec_88_89_v19"]
    assert smec["sci_tables"] == ["0xF2"]
    assert smec["fast_data_output_addresses"] == []
    assert not any(e.get("sci_table") == "0xF6" for e in smec["ram_watchlist"])

    sbec = manifests["sbec_90_93_v3_1"]
    assert sbec["sci_tables"] == ["0xF2", "0xF6"]
    assert len(sbec["fast_data_output_addresses"]) == 15
    assert sum(1 for e in sbec["ram_watchlist"] if e.get("sci_table") == "0xF6") == 15

    lm_v13 = manifests["lm_86_87_v13"]
    lm_io = [e for e in lm_v13["ram_watchlist"] if e.get("sci_table") == "LM_IO"]
    assert lm_io
    assert any(e["offset_hex"] == "0x8F" and e["symbol"] == "EngineRpm_HB" for e in lm_v13["ram_watchlist"])
    assert any(e["offset_hex"] == "0x94" and e["symbol"] == "MapValue" for e in lm_v13["ram_watchlist"])
    assert any(e["offset_hex"] == "0x81" and e["symbol"] == "EngineRpm_HB" for e in glhs["ram_watchlist"])
    assert any(e["offset_hex"] == "0x86" and e["symbol"] == "MapValue" for e in glhs["ram_watchlist"])


def test_calibration_tables_drb_and_reference_baselines() -> None:
    """Verify calibration tables, DRB metadata, and SHA-256 reference baselines."""
    smec = _load_manifest("smec_88_89_v19")
    sbec = _load_manifest("sbec_90_93_v3_1")
    lm_v13 = _load_manifest("lm_86_87_v13")
    glhs = _load_manifest("lm_86_glhs_v1")

    for manifest in (smec, sbec, lm_v13, glhs):
        symbols = {t["symbol"] for t in manifest["calibration_tables"]}
        for prefix in ("BATOFF", "PEFTBL", "WOTTBL"):
            assert any(sym.startswith(prefix) for sym in symbols), f"{manifest['rom_id']} missing {prefix}*"
        assert manifest["reference_baselines"]
        for base in manifest["reference_baselines"]:
            assert base["filename"]
            assert base["size_bytes"] == manifest["rom_size_bytes"]
            assert len(base["sha256"]) == 64
            assert base["baseline_present"] is False

    for hc11 in (smec, sbec):
        cal_syms = {t["symbol"] for t in hc11["calibration_tables"]}
        assert "DRBFunctionTable" in cal_syms
        assert "XmitRequestedRamLocation" in cal_syms
        assert hc11["drb_function_table"]
        assert hc11["drb_memory_table"]


def test_lookup_sci_strict_gate_and_no_verified_source_match() -> None:
    """Verify strict source-verification enforcement and cross-ECU SCI resolution."""
    smec_f6 = lookup_sci("smec_88_89_v19", sci_table="0xF6", offset="0x02", catalog_path=CATALOG_PATH)
    assert smec_f6["matched"] is False
    assert smec_f6["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert smec_f6["message"] == NO_VERIFIED_SOURCE_MATCH

    smec_unverified = lookup_sci("smec_88_89_v19", var_name="CCDFlags1", catalog_path=CATALOG_PATH)
    assert smec_unverified["matched"] is False
    assert smec_unverified["status"] == "NO_VERIFIED_SOURCE_MATCH"

    for kwargs in ({"var_name": "MapValue"}, {"offset": "0x63"}, {"offset": "$63"}):
        smec_map = lookup_sci("smec_88_89_v19", catalog_path=CATALOG_PATH, **kwargs)
        assert smec_map["matched"] is True
        assert smec_map["sci_table"] == "0xF2"
        assert smec_map["offset"] == "0x63"
        assert smec_map["entry"]["source_verified"] is True

    sbec_f6_map = lookup_sci("sbec_90_93_v3_1", sci_table="0xF6", offset="0x02", catalog_path=CATALOG_PATH)
    assert sbec_f6_map["matched"] is True
    assert sbec_f6_map["var_name"] == "MapValue"
    assert sbec_f6_map["entry"]["target_address"] == "0x0082"

    lm_bad = lookup_sci("lm_86_87_v13", offset="0xF0", catalog_path=CATALOG_PATH)
    assert lm_bad["matched"] is False
    assert lm_bad["status"] == "NO_VERIFIED_SOURCE_MATCH"


def test_sync_manifests_generates_sorted_ctags_with_src_and_twins(tmp_path: Path) -> None:
    """Verify sync_manifests emits sorted Exuberant CTAGS combining manifests, ASM, and twins."""
    rom_dir = tmp_path / "rom"
    (rom_dir / "src").mkdir(parents=True)
    (rom_dir / "models").mkdir(parents=True)
    (rom_dir / "src" / "sample.asm").write_text(
        "; synthetic test asm\nSampleFuelLoop:\n    ldaa #$10\n",
        encoding="utf-8",
    )
    (rom_dir / "models" / "smec_twin.py").write_text(
        "class SmecTwin:\n    pass\n\ndef compute_peftbl(rpm: int) -> int:\n    return rpm\n",
        encoding="utf-8",
    )

    tags_path = tmp_path / "tags"
    summary = sync_manifests(catalog_path=CATALOG_PATH, tags_path=tags_path, rom_dir=rom_dir)
    assert summary["tag_count"] > 0
    assert len(summary["rom_ids"]) == 4

    lines = tags_path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("!_TAG_FILE_FORMAT\t2")
    assert lines[1].startswith("!_TAG_FILE_SORTED\t1")
    body = [line for line in lines if not line.startswith("!_TAG_")]
    assert body == sorted(body)
    joined = "\n".join(body)
    for expected in ("MapValue\t", "DRBFunctionTable\t", "SampleFuelLoop\t", "SmecTwin\t", "compute_peftbl\t"):
        assert expected in joined


def test_cli_profile_compile_and_sim_commands(tmp_path: Path) -> None:
    """Exercise profile compile, sim lookup-sci, and sim manifest-sync via Click CliRunner."""
    runner = CliRunner()
    db_path = tmp_path / "catalog.sqlite"

    res_hybrid = runner.invoke(
        cli_main,
        ["profile", "compile", str(REPO_ROOT / "profiles/examples/csx_89_neon_dohc.json"), "--db-path", str(db_path), "--json"],
    )
    assert res_hybrid.exit_code == 0, res_hybrid.output
    hybrid_data = json.loads(res_hybrid.output)
    assert hybrid_data["compatibility_tier"] == "HYBRID_MACHINING_REQUIRED"
    assert len(hybrid_data["swap_checklist"]) >= 7
    assert hybrid_data["rom_manifest"]["rom_id"] == "smec_88_89_v19"

    res_native = runner.invoke(
        cli_main,
        ["profile", "compile", str(REPO_ROOT / "profiles/examples/shadow_90_sbec_t2.json"), "--db-path", str(db_path), "--json"],
    )
    assert res_native.exit_code == 0, res_native.output
    native_data = json.loads(res_native.output)
    assert native_data["compatibility_tier"] == "BOLT_ON_NATIVE"
    assert native_data["rom_manifest"]["rom_id"] == "sbec_90_93_v3_1"

    res_lookup = runner.invoke(
        cli_main,
        ["sim", "lookup-sci", "--rom-id", "smec_88_89_v19", "--sci-table", "0xF6", "--offset", "0x10"],
    )
    assert res_lookup.exit_code == 0, res_lookup.output
    assert res_lookup.output.strip() == NO_VERIFIED_SOURCE_MATCH

    tags_out = tmp_path / "tags"
    res_sync = runner.invoke(
        cli_main,
        ["sim", "manifest-sync", "--tags-path", str(tags_out), "--json"],
    )
    assert res_sync.exit_code == 0, res_sync.output
    sync_data = json.loads(res_sync.output)
    assert sync_data["tag_count"] > 0
    assert tags_out.is_file()


def test_generated_rom_cleanroom_and_baseline_immutability() -> None:
    """Assert generated rom/ JSONs are PII/deadbolt-free and baseline dirs hold only README.md."""
    json_files = sorted((REPO_ROOT / "rom" / "sci").glob("*.json")) + sorted(MANIFESTS_DIR.rglob("*.json"))
    assert len(json_files) >= 6

    violations: list[str] = []
    for jf in json_files:
        text = jf.read_text(encoding="utf-8")
        rel = jf.relative_to(REPO_ROOT).as_posix()
        for label, pat in (*PII_PATTERNS, *DEADBOLT_PATTERNS):
            if pat.search(text):
                violations.append(f"{rel}: matched {label}")
    assert not violations, "Cleanroom or deadbolt violation in generated ROM JSONs:\n" + "\n".join(violations)

    for rel_dir in ("rom/inputs", "rom/stock_baselines"):
        entries = sorted(p.name for p in (REPO_ROOT / rel_dir).iterdir())
        assert entries == ["README.md"], f"{rel_dir} must contain only README.md, found {entries}"


def test_glhs_rejects_mismatched_xref_and_clears_short_names() -> None:
    """Verify GLHS does not leak mismatched mprxref short_names onto verified RAM equates."""
    for bad_var in ("PREVMAP1", "CELL0"):
        res = lookup_sci("lm_86_glhs_v1", var_name=bad_var, catalog_path=CATALOG_PATH)
        assert res["matched"] is False
        assert res["status"] == "NO_VERIFIED_SOURCE_MATCH"

    glhs = _load_manifest("lm_86_glhs_v1")
    by_hex = {e["offset_hex"]: e for e in glhs["ram_watchlist"]}
    assert by_hex["0x86"]["symbol"] == "MapValue"
    assert by_hex["0x86"]["short_name"] is None
    assert by_hex["0x50"]["symbol"] == "AutoCalCell10"
    assert by_hex["0x50"]["short_name"] is None


def test_lookup_sci_offset_parsing_and_validation() -> None:
    """Verify decimal '99' == 0x63, hex formats, negative rejection, and empty var rejection."""
    for raw_offset in ("99", "0x63", "$63"):
        res = lookup_sci("smec_88_89_v19", offset=raw_offset, catalog_path=CATALOG_PATH)
        assert res["matched"] is True
        assert res["offset"] == "0x63"
        assert res["var_name"] == "MapValue"

    with pytest.raises(ValueError):
        lookup_sci("smec_88_89_v19", offset=-1, catalog_path=CATALOG_PATH)

    with pytest.raises(ValueError):
        lookup_sci("smec_88_89_v19", var_name="   ", catalog_path=CATALOG_PATH)


def test_cli_and_sync_error_handling_boundaries(tmp_path: Path) -> None:
    """Verify clean CLI error exits and non-destructive sync_manifests failure on unknown rom_id."""
    tags_file = tmp_path / "tags"
    sentinel = "!_SENTINEL_KEEP_ME\n"
    tags_file.write_text(sentinel, encoding="utf-8")

    with pytest.raises(KeyError):
        sync_manifests(catalog_path=CATALOG_PATH, rom_id="unknown_id", tags_path=tags_file)
    assert tags_file.read_text(encoding="utf-8") == sentinel

    runner = CliRunner()
    for args in (
        ["sim", "lookup-sci", "--rom-id", "smec_88_89_v19", "--var", "   "],
        ["sim", "manifest-sync", "--rom-id", "unknown_id", "--tags-path", str(tags_file)],
        ["heritage", "discover", "--wave", "Z"],
    ):
        res = runner.invoke(cli_main, args)
        assert res.exit_code != 0
        assert res.exception is None or isinstance(res.exception, SystemExit)
        assert "Traceback" not in res.output
    assert tags_file.read_text(encoding="utf-8") == sentinel
