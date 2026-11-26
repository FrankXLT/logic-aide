"""Validation tests for Wave A injector BATOFF and G-head flowbench presets."""

from __future__ import annotations

import json
from pathlib import Path
import re

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "profiles" / "schema" / "vehicle_build.schema.json"
INJECTORS_PATH = REPO_ROOT / "profiles" / "presets" / "injectors_batoff.json"
FLOWBENCH_JSON_PATH = REPO_ROOT / "profiles" / "presets" / "cylinder_head_flowbench.json"
FLOWBENCH_MD_PATH = REPO_ROOT / "knowledge" / "tier2_verified" / "g_head_flowbench.md"

_PII_OR_PATH_RE = re.compile(
    r"(?:\bSteve\b|/home/|/Users/|private_references|[A-Za-z]:\\|[\w.+-]+@[\w-]+\.[\w.-]+)",
    re.IGNORECASE,
)


def _load_batoff_subschema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["properties"]["fuel_system"]["properties"]["batoff_deadtime_ms"]


def test_injectors_batoff_presets_schema_and_monotonicity() -> None:
    """Validate all injector presets against vehicle_build.schema.json and monotonicity."""
    payload = json.loads(INJECTORS_PATH.read_text(encoding="utf-8"))
    presets = payload["presets"]
    assert payload["preset_count"] == len(presets)
    assert len(presets) >= 90

    batoff_schema = _load_batoff_subschema()
    by_id: dict[str, dict] = {}
    for preset in presets:
        by_id[preset["preset_id"]] = preset
        batoff = preset["batoff_deadtime_ms"]
        jsonschema.validate(instance=batoff, schema=batoff_schema)

        volts = batoff["voltage_points_v"]
        deadtimes = batoff["deadtime_ms"]
        assert len(volts) == len(deadtimes)
        assert min(volts) <= 8.0 and max(volts) >= 15.0
        assert all(volts[idx] < volts[idx + 1] for idx in range(len(volts) - 1))
        assert all(dt > 0.0 for dt in deadtimes)
        assert all(deadtimes[idx] >= deadtimes[idx + 1] for idx in range(len(deadtimes) - 1))

    holley = by_id["holley_delphi_75lb_788cc"]
    corrob = holley["corroboration"]
    assert corrob["matched_sources"] == 3
    assert len(corrob["source_tables"]) == 3
    assert corrob["exact_overlap_10v_15v_ms"] is True
    assert corrob["exact_full_6v_16v_ms"] is True


def test_cylinder_head_flowbench_json_nist_and_cleanroom() -> None:
    """Verify 28 inH2O / 6.97 kPa depression, exact dm3_s conversion, and zero PII."""
    raw_text = FLOWBENCH_JSON_PATH.read_text(encoding="utf-8")
    assert not _PII_OR_PATH_RE.search(raw_text)

    payload = json.loads(raw_text)
    assert payload["test_depression_inh2o"] == 28.0
    assert payload["test_depression_kpa"] == 6.97
    assert payload["nist_sp811_cfm_to_dm3_s"] == 0.47194745

    datasets = payload["flowbench_datasets"]
    assert len(datasets) >= 3
    for dataset in datasets:
        for port_key in ("intake", "exhaust"):
            port_map = dataset.get(port_key) or {}
            for config_label, points in port_map.items():
                assert not _PII_OR_PATH_RE.search(config_label)
                assert len(points) >= 4
                for point in points:
                    assert point["dm3_s"] == round(point["cfm"] * 0.47194745, 3)


def test_g_head_flowbench_markdown_structure_and_cleanroom() -> None:
    """Verify 2-line scope header, YAML frontmatter, GFM pipe tables, and zero PII."""
    text = FLOWBENCH_MD_PATH.read_text(encoding="utf-8")
    assert not _PII_OR_PATH_RE.search(text)

    lines = text.splitlines()
    assert lines[0].startswith("> **Scope**:")
    assert lines[1].startswith("> **Boundary**:")
    assert lines[2] == ""
    assert lines[3] == "---"

    end_fm = lines.index("---", 4)
    frontmatter = yaml.safe_load("\n".join(lines[4:end_fm]))
    assert isinstance(frontmatter, dict)
    assert frontmatter["doc_id"] == "g-head-flowbench-nist-sp811"
    assert frontmatter["trust_tier"] == "Tier 2"
    assert frontmatter["cleanroom_status"] == "DISTILLED_ATTRIBUTED"

    body = "\n".join(lines[end_fm + 1 :])
    pipe_rows = [ln for ln in body.splitlines() if ln.startswith("|") and ln.endswith("|")]
    separator_rows = [ln for ln in pipe_rows if ":---:" in ln or "---" in ln]
    assert len(pipe_rows) >= 20
    assert len(separator_rows) >= 5


def test_leak_path_regex_catches_single_backslash_windows_paths() -> None:
    """Ensure cleanroom path-leak regex rejects single-backslash C:\\ drive paths."""
    from logicaide.profiles.injector_presets import _LEAK_PATH_RE

    assert _LEAK_PATH_RE.search(r"C:\Users\demo\file.xls")
    assert _PII_OR_PATH_RE.search(r"C:\Users\demo\file.xls")
