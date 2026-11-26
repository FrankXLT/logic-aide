"""Unit and integration tests for TurboCalc v5 importer and NIST SP 811 math."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

from click.testing import CliRunner
import pytest

from logicaide.cli import main
from logicaide.profiles.turbo_importer import (
    CFM_TO_LB_MIN,
    KG_S_TO_LB_MIN,
    LB_MIN_TO_KG_S,
    M3_MIN_TO_LB_MIN,
    M3_S_TO_LB_MIN,
    NIST_298K_VOLUMETRIC_FACTOR,
    compute_affine_transform,
    convert_flow_to_lb_min,
    pixel_to_flow_pr,
    run_import_turbocalc,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TURBO_DIR = REPO_ROOT / "profiles" / "turbo"


def _build_synthetic_docx(path: Path) -> None:
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body/></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)


def _build_synthetic_sql(path: Path) -> None:
    sql = "\n".join(
        [
            "INSERT INTO `turbocalc_company` (`company_id`, `company_name`) VALUES",
            "(1, 'Garrett/Honeywell');",
            "INSERT INTO `turbocalc_family` (`family_id`, `family_name`) VALUES",
            "(10, 'T04E Series'),",
            "(11, 'T3 Series');",
            (
                "INSERT INTO `turbocalc_data` (`product_id`, `company_id`, `family_id`, "
                "`product_name`, `map_path`, `airflow_unit`, `airflow_min`, `airflow_max`, "
                "`pr_min`, `pr_max`, `pixel_x_start`, `pixel_y_start`, `pixel_width`, `pixel_height`, "
                "`envelope_0_airflow`, `envelope_0_pr`, `envelope_1_airflow`, `envelope_1_pr`, "
                "`envelope_2_airflow`, `envelope_2_pr`, `envelope_3_airflow`, `envelope_3_pr`, "
                "`surge_1_airflow`, `surge_1_pr`, `eff_line_1_airflow`, `eff_line_1_pr`, `notes`) VALUES"
            ),
            (
                "(101, 1, 10, '50 Trim', 'maps/garrett/t04e_50.gif', 'lb/min', 0.0, 50.0, "
                "1.0, 3.5, 40, 20, 400, 300, 8.0, 1.2, 24.0, 3.1, 44.0, 2.7, 28.0, 1.2, "
                "8.0, 1.2, 22.0, 2.1, 'Synthetic T04E 50 Trim'),"
            ),
            (
                "(102, 1, 11, '60 Trim - Super60', 'maps/garrett/t3_s60.gif', 'kg/sec', 0.0, 0.30, "
                "1.0, 3.0, 50, 30, 300, 250, 0.05, 1.2, 0.18, 2.8, 0.27, 2.5, 0.16, 1.2, "
                "0.05, 1.2, 0.14, 1.9, 'Synthetic T3 Super60');"
            ),
        ]
    )
    path.write_text(sql, encoding="utf-8")


def test_nist_sp811_unit_conversions() -> None:
    """Verify NIST SP 811 dry-air (298.15 K, 101.325 kPa) airflow conversions."""
    assert NIST_298K_VOLUMETRIC_FACTOR == 0.073915
    assert convert_flow_to_lb_min(25.0, "lb/min") == pytest.approx(25.0, rel=1e-9)
    assert KG_S_TO_LB_MIN == pytest.approx(132.277357, rel=1e-6)
    assert LB_MIN_TO_KG_S == pytest.approx(1.0 / 132.277357, rel=1e-6)
    assert convert_flow_to_lb_min(1.0, "kg/sec") == pytest.approx(132.277357, rel=1e-6)
    assert convert_flow_to_lb_min(1.0, "kg/s") * LB_MIN_TO_KG_S == pytest.approx(1.0, rel=1e-9)
    assert M3_S_TO_LB_MIN == pytest.approx(156.616391, rel=1e-6)
    assert convert_flow_to_lb_min(1.0, "m^3/sec") == pytest.approx(156.616391, rel=1e-6)
    assert M3_MIN_TO_LB_MIN == pytest.approx(2.610273, rel=1e-6)
    assert convert_flow_to_lb_min(1.0, "m^3/min") == pytest.approx(2.610273, rel=1e-6)
    assert CFM_TO_LB_MIN == 0.069
    assert convert_flow_to_lb_min(100.0, "cfm") == pytest.approx(6.9, rel=1e-9)
    assert convert_flow_to_lb_min(100.0, "cfmc") == pytest.approx(6.9, rel=1e-9)
    with pytest.raises(ValueError, match="Unsupported airflow unit"):
        convert_flow_to_lb_min(10.0, "slug/hr")


def test_affine_transform_corners_and_bounds_validation() -> None:
    """Verify 4-point affine recovery within 1e-4 and invalid pixel bound rejection."""
    x0, y0, width, height = 44.0, 39.0, 388.0, 503.0
    m_min, m_max, pr_min, pr_max = 5.0, 65.0, 1.0, 4.5
    matrix, corners = compute_affine_transform(
        x0, y0, width, height, m_min, m_max, pr_min, pr_max
    )
    spec = {
        "turbo_id": "synthetic_affine",
        "axis_calibration_pending": False,
        "affine_matrix_2x3": matrix,
    }

    expected_corners = {
        "top_left": (x0, y0, m_min, pr_max),
        "top_right": (x0 + width, y0, m_max, pr_max),
        "bottom_left": (x0, y0 + height, m_min, pr_min),
        "bottom_right": (x0 + width, y0 + height, m_max, pr_min),
    }
    for name, (px, py, exp_flow, exp_pr) in expected_corners.items():
        flow, pr = pixel_to_flow_pr(spec, px, py)
        assert flow == pytest.approx(exp_flow, abs=1e-4)
        assert pr == pytest.approx(exp_pr, abs=1e-4)
        assert corners[name]["flow_lb_min"] == pytest.approx(exp_flow, abs=1e-4)
        assert corners[name]["pressure_ratio"] == pytest.approx(exp_pr, abs=1e-4)

    for bad_w, bad_h in ((0.0, 100.0), (-5.0, 100.0), (100.0, 0.0), (100.0, -1.0)):
        with pytest.raises(ValueError, match="Invalid calibration pixel bounds"):
            compute_affine_transform(x0, y0, bad_w, bad_h, m_min, m_max, pr_min, pr_max)

    with pytest.raises(ValueError, match="not axis-calibrated"):
        pixel_to_flow_pr({"turbo_id": "pending", "axis_calibration_pending": True}, 10.0, 10.0)


def test_live_turbo_catalog_and_generated_specs() -> None:
    """Validate live Wave A catalog counts, GT3076R geometry/curves, and aliases."""
    catalog = json.loads((TURBO_DIR / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["calibrated_count"] == 145
    assert catalog["company_count"] == 6
    assert catalog["family_count"] == 21
    assert catalog["stub_count"] == 13
    assert catalog["unique_figure_count"] == 157
    assert catalog["alias_count"] == 2

    gt_spec = json.loads((TURBO_DIR / "garrett_gt3076r" / "turbo_spec.json").read_text(encoding="utf-8"))
    assert gt_spec["calibrated"] is True
    assert gt_spec["nist_sp811_normalization"]["nist_298k_volumetric_factor"] == 0.073915
    for field in ("envelope_points", "surge_line", "choke_boundary", "efficiency_spine_points"):
        assert len(gt_spec[field]) >= 2, f"garrett_gt3076r missing points in {field}"

    for corner in gt_spec["calibration_corners"].values():
        px, py = corner["pixel"]
        flow, pr = pixel_to_flow_pr(gt_spec, px, py)
        assert flow == pytest.approx(corner["flow_lb_min"], abs=1e-4)
        assert pr == pytest.approx(corner["pressure_ratio"], abs=1e-4)

    for alias_id in ("garrett_t3_t4_50trim", "garrett_t3_super60_t2"):
        assert alias_id in catalog["aliases"]
        alias_spec = json.loads((TURBO_DIR / alias_id / "turbo_spec.json").read_text(encoding="utf-8"))
        assert alias_spec["is_alias"] is True
        assert alias_spec["alias_of"] == catalog["aliases"][alias_id]
        assert (REPO_ROOT / alias_spec["canonical_spec_ref"]).is_file()


def test_synthetic_sql_path_round_trip(tmp_path: Path) -> None:
    """Run synthetic --sql-path import through CLI and direct API into tmp_path."""
    source_dir = tmp_path / "source"
    maps_dir = source_dir / "maps" / "garrett"
    maps_dir.mkdir(parents=True)
    (maps_dir / "t04e_50.gif").write_bytes(b"GIF89a-50trim-map")
    (maps_dir / "t3_s60.gif").write_bytes(b"GIF89a-super60-map")
    (maps_dir / "t3_s60_dup.gif").write_bytes(b"GIF89a-super60-map")
    (maps_dir / "unmatched_stub.gif").write_bytes(b"GIF89a-unmatched-stub")

    sql_path = tmp_path / "synthetic_tools.sql"
    _build_synthetic_sql(sql_path)
    _build_synthetic_docx(source_dir / "turbodata_2010_10_27.docx")
    (source_dir / "turbocalc.xml").write_text("<TurboCalc/>", encoding="utf-8")

    out_profiles = tmp_path / "out_profiles"
    out_figures = tmp_path / "out_figures"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "turbo",
            "import-turbocalc",
            "--source-dir", str(source_dir),
            "--sql-path", str(sql_path),
            "--output-dir", str(out_profiles),
            "--figures-dir", str(out_figures),
            "--no-presets",
        ],
    )
    assert result.exit_code == 0, result.output

    catalog = json.loads((out_profiles / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["calibrated_count"] == 2
    assert catalog["stub_count"] == 1
    assert catalog["unique_figure_count"] == 3
    assert catalog["source_figure_count"] == 4
    assert catalog["alias_count"] == 2

    s60 = json.loads((out_profiles / "garrett_t3_60_trim_super60" / "turbo_spec.json").read_text(encoding="utf-8"))
    assert s60["source_bounds"]["airflow_max_lb_min"] == pytest.approx(0.30 * KG_S_TO_LB_MIN, abs=1e-4)
    flow_tr, pr_tr = pixel_to_flow_pr(s60, 350, 30)
    assert flow_tr == pytest.approx(0.30 * KG_S_TO_LB_MIN, abs=1e-4)
    assert pr_tr == pytest.approx(3.0, abs=1e-4)

    direct = run_import_turbocalc(
        source_dir=source_dir,
        sql_path=sql_path,
        profiles_dir=tmp_path / "direct_profiles",
        figures_dir=tmp_path / "direct_figures",
        examples_dir=tmp_path / "empty_examples",
    )
    assert direct["calibrated_count"] == 2
    assert direct["stub_count"] == 1


def test_cli_import_turbocalc_decouples_output_dir_from_presets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify --output-dir does not overwrite --presets-dir when --include-presets is enabled."""
    import logicaide.profiles.injector_presets as ip_mod
    import logicaide.profiles.turbo_importer as ti_mod

    captured: dict[str, Any] = {}
    monkeypatch.setattr(ti_mod, "run_import_turbocalc", lambda **kw: captured.setdefault("turbo", kw) or {})
    monkeypatch.setattr(ip_mod, "run_extract_presets", lambda **kw: captured.setdefault("presets", kw) or {})

    turbo_dir = tmp_path / "turbo"
    presets_dir = tmp_path / "presets"
    priv_root = tmp_path / "priv"
    res = CliRunner().invoke(
        main,
        [
            "turbo", "import-turbocalc",
            "--output-dir", str(turbo_dir),
            "--presets-dir", str(presets_dir),
            "--private-root", str(priv_root),
            "--include-presets",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["turbo"] == {"output_dir": turbo_dir}
    assert captured["presets"] == {"private_root": priv_root, "output_presets_dir": presets_dir}
