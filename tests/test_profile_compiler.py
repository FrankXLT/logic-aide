"""Unit tests for the WS-1 Vehicle Build Profile Compiler and SQLite persistence."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sqlite3
from typing import Any

import pytest

from logicaide.catalog.sqlite_store import get_vehicle_and_latest_revision
from logicaide.profiles.compiler import (
    SG_REF,
    ProfileCompilationError,
    compile_profile,
    crosscheck_injector_batoff,
    evaluate_mopar_compatibility,
    resolve_rom_manifest,
    resolve_turbo_spec,
    validate_profile_schema,
    verify_fuel_scaling,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_rel(src_root: Path, dst_root: Path, rel_path: str) -> Path:
    src, dst = src_root / rel_path, dst_root / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


@pytest.fixture()
def isolated_repo(tmp_path: Path) -> Path:
    """Create a deterministic repo tree with schema, presets, turbos, and ROM manifests."""
    repo = tmp_path / "repo"
    for rel in (
        "profiles/schema/vehicle_build.schema.json",
        "profiles/presets/injectors_batoff.json",
        "profiles/turbo/catalog.json",
        "profiles/turbo/garrett_t3_t4_50trim/turbo_spec.json",
        "profiles/turbo/garrett_t3_super60_t2/turbo_spec.json",
        "profiles/examples/csx_89_neon_dohc.json",
        "profiles/examples/shadow_90_sbec_t2.json",
    ):
        _copy_rel(REPO_ROOT, repo, rel)

    manifests_dir = repo / "rom" / "manifests"
    (manifests_dir / "smec_88_89_v19").mkdir(parents=True, exist_ok=True)
    (manifests_dir / "sbec_90_93_v3_1").mkdir(parents=True, exist_ok=True)

    smec_entry = {
        "rom_id": "smec_88_89_v19",
        "ecu_family": "SMEC_DUAL_BOARD",
        "mcu": "MC68HC11A8",
        "rom_size_bytes": 32768,
        "base_address": "0x8000",
        "manifest_ref": "rom/manifests/smec_88_89_v19/rom_manifest.json",
    }
    sbec_entry = {
        "rom_id": "sbec_90_93_v3_1",
        "ecu_family": "SBEC_SINGLE_BOARD",
        "mcu": "MC68HC11E9",
        "rom_size_bytes": 32768,
        "base_address": "0x8000",
        "manifest_ref": "rom/manifests/sbec_90_93_v3_1/rom_manifest.json",
    }
    (manifests_dir / "catalog.json").write_text(
        json.dumps({"manifests": [smec_entry, sbec_entry]}, indent=2), encoding="utf-8"
    )
    (repo / smec_entry["manifest_ref"]).write_text(json.dumps(smec_entry, indent=2), encoding="utf-8")
    (repo / sbec_entry["manifest_ref"]).write_text(json.dumps(sbec_entry, indent=2), encoding="utf-8")
    return repo


def _load_example(repo: Path, name: str) -> dict[str, Any]:
    return json.loads((repo / "profiles" / "examples" / name).read_text(encoding="utf-8"))


def test_compile_csx_89_hybrid_dohc_and_idempotent_persistence(
    isolated_repo: Path, tmp_path: Path
) -> None:
    """Compile csx_89_neon_dohc, verify all gates, and assert idempotent SQLite persistence."""
    db_path = tmp_path / "catalog.sqlite"
    profile_path = isolated_repo / "profiles/examples/csx_89_neon_dohc.json"

    report = compile_profile(profile_path, repo_root=isolated_repo, db_path=db_path, persist=True)
    assert report["car_id"] == "csx_89_neon_dohc"
    assert report["compatibility_tier"] == "HYBRID_MACHINING_REQUIRED"
    assert len(report["swap_checklist"]) == 7
    assert all(item["severity"] == "AMBER" and item["verified"] for item in report["swap_checklist"])
    assert "piston_valve_reliefs" in {item["key"] for item in report["swap_checklist"]}

    fuel = report["fuel_scaling"]
    assert fuel["verified"] is True
    assert fuel["declared_effective_flow_lb_hr"] == pytest.approx(89.96, abs=0.01)
    assert fuel["expected_effective_flow_lb_hr"] == pytest.approx(80.0 * math.sqrt(55.0 / 43.5), abs=1e-4)

    assert report["turbo"]["turbo_id"] == "garrett_t3_t4_50trim"
    assert report["turbo"]["canonical_turbo_id"] == "garrett_t04e_50_trim"
    assert report["turbo"]["is_alias"] is True
    assert report["injector_batoff"]["curve_valid"] is True
    assert report["injector_batoff"]["nearest_preset"]["preset_id"]

    rom = report["rom_manifest"]
    assert rom["resolved"] is True
    assert rom["resolved_via"] == "partitioned_catalog_bridge"
    assert rom["rom_id"] == "smec_88_89_v19"
    assert rom["ecu_family"] == "SMEC_DUAL_BOARD"
    assert rom["rom_size_bytes"] == 32768

    first_rec = report["catalog_record"]
    assert first_rec["created"] is True
    stored = get_vehicle_and_latest_revision("csx_89_neon_dohc", db_path=db_path)
    assert stored is not None
    assert stored["vehicle"]["ecu_family"] == "SMEC_DUAL_BOARD"
    assert stored["latest_revision"]["compatibility_tier"] == "HYBRID_MACHINING_REQUIRED"

    second = compile_profile(profile_path, repo_root=isolated_repo, catalog_db_path=db_path, persist=True)
    assert second["catalog_record"]["created"] is False
    assert second["catalog_record"]["revision_id"] == first_rec["revision_id"]

    with sqlite3.connect(db_path) as conn:
        v_count = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        r_count = conn.execute("SELECT COUNT(*) FROM vehicle_profile_revisions").fetchone()[0]
    assert (v_count, r_count) == (1, 1)


def test_compile_shadow_90_bolt_on_native(isolated_repo: Path, tmp_path: Path) -> None:
    """Compile shadow_90_sbec_t2 as BOLT_ON_NATIVE with Super60 alias and SBEC ROM bridge."""
    db_path = tmp_path / "catalog.sqlite"
    profile_path = isolated_repo / "profiles/examples/shadow_90_sbec_t2.json"

    report = compile_profile(profile_path, repo_root=isolated_repo, db_path=db_path, persist=True)
    assert report["car_id"] == "shadow_90_sbec_t2"
    assert report["compatibility_tier"] == "BOLT_ON_NATIVE"
    assert report["swap_checklist"] == []
    assert report["fuel_scaling"]["declared_effective_flow_lb_hr"] == pytest.approx(51.72, abs=0.01)
    assert report["turbo"]["turbo_id"] == "garrett_t3_super60_t2"
    assert report["turbo"]["canonical_turbo_id"] == "garrett_t3_60_trim_super60"
    assert report["turbo"]["is_alias"] is True
    assert report["injector_batoff"]["curve_valid"] is True
    assert report["rom_manifest"]["rom_id"] == "sbec_90_93_v3_1"
    assert report["rom_manifest"]["ecu_family"] == "SBEC_SINGLE_BOARD"
    assert report["rom_manifest"]["rom_size_bytes"] == 32768
    assert report["catalog_record"]["created"] is True


def test_incompatible_edz_8v_rejected_by_schema_and_evaluator(isolated_repo: Path) -> None:
    """Reject INCOMPATIBLE 2.4L_EDZ + 8V_782_SWIRL in both schema and compatibility stages."""
    profile = _load_example(isolated_repo, "shadow_90_sbec_t2.json")
    profile["engine_bottom_end"]["block_code"] = "2.4L_EDZ"

    with pytest.raises(ProfileCompilationError) as schema_exc:
        validate_profile_schema(profile, isolated_repo / "profiles/schema/vehicle_build.schema.json")
    assert schema_exc.value.stage == "schema"

    with pytest.raises(ProfileCompilationError) as compat_exc:
        evaluate_mopar_compatibility(profile)
    assert compat_exc.value.stage == "compatibility"


def test_missing_piston_valve_reliefs_and_tier_mismatch_rejected(isolated_repo: Path) -> None:
    """Reject missing piston_valve_reliefs=true or declared_tier mismatch on K+DOHC hybrid."""
    profile = _load_example(isolated_repo, "csx_89_neon_dohc.json")
    profile["engine_bottom_end"]["piston_valve_reliefs"] = False

    with pytest.raises(ProfileCompilationError) as schema_exc:
        validate_profile_schema(profile, isolated_repo / "profiles/schema/vehicle_build.schema.json")
    assert schema_exc.value.stage == "schema"

    profile["cylinder_head_and_cam"]["head_code"] = "ECC_20_DOHC"
    with pytest.raises(ProfileCompilationError) as compat_exc:
        evaluate_mopar_compatibility(profile)
    assert compat_exc.value.stage == "compatibility"

    profile["engine_bottom_end"]["piston_valve_reliefs"] = True
    profile["cylinder_head_and_cam"]["compatibility_tier"] = "BOLT_ON_NATIVE"
    with pytest.raises(ProfileCompilationError) as tier_exc:
        evaluate_mopar_compatibility(profile)
    assert tier_exc.value.stage == "compatibility"


def test_fuel_scaling_mismatch_and_e85_gravity_scaling(isolated_repo: Path) -> None:
    """Detect effective_flow_lb_hr mismatch and verify SG_REF scaling for E85."""
    profile = _load_example(isolated_repo, "shadow_90_sbec_t2.json")
    fuel = dict(profile["fuel_system"])

    fuel["effective_flow_lb_hr"] = 75.0
    with pytest.raises(ProfileCompilationError) as exc_info:
        verify_fuel_scaling(fuel)
    assert exc_info.value.stage == "fuel_scaling"

    sg_e85 = 0.785
    fuel["specific_gravity"] = sg_e85
    expected = 46.0 * math.sqrt(55.0 / 43.5) * math.sqrt(sg_e85 / SG_REF)
    fuel["effective_flow_lb_hr"] = round(expected, 3)
    res = verify_fuel_scaling(fuel)
    assert res["verified"] is True
    assert res["sg_ref"] == SG_REF


@pytest.mark.parametrize(
    "mutator",
    [
        lambda b: b["deadtime_ms"].__setitem__(4, 9.99),
        lambda b: b["voltage_points_v"].__setitem__(2, 7.5),
        lambda b: b.__setitem__("voltage_points_v", [9, 10, 11, 12, 13, 14, 15, 16]),
        lambda b: b.__setitem__("min_linear_pw_ms", 0.5),
    ],
)
def test_batoff_curve_rejects_invalid_and_accepts_custom_curve(
    isolated_repo: Path, mutator: Any
) -> None:
    """Reject non-monotonic/short BATOFF curves while allowing valid custom curves."""
    presets_path = isolated_repo / "profiles/presets/injectors_batoff.json"
    profile = _load_example(isolated_repo, "csx_89_neon_dohc.json")
    custom_res = crosscheck_injector_batoff(profile["fuel_system"], presets_path)
    assert custom_res["curve_valid"] is True
    assert custom_res["is_custom_curve"] is True
    assert custom_res["nearest_preset"] is not None

    bad_fuel = json.loads(json.dumps(profile["fuel_system"]))
    mutator(bad_fuel["batoff_deadtime_ms"])
    with pytest.raises(ProfileCompilationError) as exc_info:
        crosscheck_injector_batoff(bad_fuel, presets_path)
    assert exc_info.value.stage == "injector_batoff"


def test_path_traversal_blocked_for_turbo_and_rom_refs(isolated_repo: Path) -> None:
    """Block '..' traversal and absolute escapes in turbo_spec_ref and rom_manifest_ref."""
    for bad_ref in ("../outside.json", "profiles/turbo/../../etc/passwd", "/etc/passwd"):
        with pytest.raises(ProfileCompilationError) as turbo_exc:
            resolve_turbo_spec({"turbo_id": "garrett_t3_t4_50trim", "turbo_spec_ref": bad_ref}, isolated_repo)
        assert turbo_exc.value.stage == "turbo"

        with pytest.raises(ProfileCompilationError) as rom_exc:
            resolve_rom_manifest(
                {"ecu_family": "SMEC_DUAL_BOARD", "eprom_size_bytes": 32768, "rom_manifest_ref": bad_ref},
                isolated_repo,
            )
        assert rom_exc.value.stage == "rom_manifest"


def test_explicit_rom_manifest_mismatch_and_match(isolated_repo: Path) -> None:
    """Enforce strict (ecu_family, rom_size_bytes) parity on explicit ROM manifests."""
    smec_ref = "rom/manifests/smec_88_89_v19/rom_manifest.json"
    ok = resolve_rom_manifest(
        {"ecu_family": "SMEC_DUAL_BOARD", "eprom_size_bytes": 32768, "rom_manifest_ref": smec_ref},
        isolated_repo,
    )
    assert ok["resolved_via"] == "explicit_file"
    assert ok["rom_id"] == "smec_88_89_v19"

    with pytest.raises(ProfileCompilationError) as exc_info:
        resolve_rom_manifest(
            {"ecu_family": "SBEC_SINGLE_BOARD", "eprom_size_bytes": 32768, "rom_manifest_ref": smec_ref},
            isolated_repo,
        )
    assert exc_info.value.stage == "rom_manifest"


def test_null_byte_paths_blocked_for_turbo_and_rom_refs(isolated_repo: Path) -> None:
    """Reject embedded null bytes in turbo_spec_ref and rom_manifest_ref."""
    with pytest.raises(ProfileCompilationError) as turbo_exc:
        resolve_turbo_spec({"turbo_id": "garrett_t3_t4_50trim", "turbo_spec_ref": "profiles/turbo/\x00bad.json"}, isolated_repo)
    assert turbo_exc.value.stage == "turbo"

    with pytest.raises(ProfileCompilationError) as rom_exc:
        resolve_rom_manifest(
            {"ecu_family": "SMEC_DUAL_BOARD", "eprom_size_bytes": 32768, "rom_manifest_ref": "rom/\x00rom_manifest.json"},
            isolated_repo,
        )
    assert rom_exc.value.stage == "rom_manifest"


def test_missing_partitioned_manifest_target_rejected(isolated_repo: Path) -> None:
    """Reject missing partitioned manifest_ref target when require_rom_manifest=True."""
    (isolated_repo / "rom/manifests/smec_88_89_v19/rom_manifest.json").unlink()
    with pytest.raises(ProfileCompilationError) as exc_info:
        resolve_rom_manifest(
            {"ecu_family": "SMEC_DUAL_BOARD", "eprom_size_bytes": 32768, "rom_manifest_ref": "rom/rom_manifest.json"},
            isolated_repo,
            require_rom_manifest=True,
        )
    assert exc_info.value.stage == "rom_manifest"


def test_duplicate_vin_hash_raises_profile_compilation_error(isolated_repo: Path, tmp_path: Path) -> None:
    """Wrap duplicate vin_hash IntegrityError as ProfileCompilationError('catalog')."""
    db_path = tmp_path / "catalog.sqlite"
    compile_profile(isolated_repo / "profiles/examples/csx_89_neon_dohc.json", repo_root=isolated_repo, db_path=db_path)

    dup_profile = _load_example(isolated_repo, "shadow_90_sbec_t2.json")
    dup_profile["identity_and_chassis"]["car_id"] = "shadow_90_dup_vin"
    dup_profile["identity_and_chassis"]["vin_hash"] = _load_example(isolated_repo, "csx_89_neon_dohc.json")["identity_and_chassis"]["vin_hash"]
    dup_path = tmp_path / "shadow_dup_vin.json"
    dup_path.write_text(json.dumps(dup_profile), encoding="utf-8")

    with pytest.raises(ProfileCompilationError) as exc_info:
        compile_profile(dup_path, repo_root=isolated_repo, db_path=db_path)
    assert exc_info.value.stage == "catalog"

