"""Tests for LogicAIDE Living Heritage Wiki validation and dual-publish sync."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from logicaide.heritage.wiki_sync import (
    WikiValidationError,
    run_sync_wiki,
    sync_wiki,
    validate_mermaid_block,
    validate_wiki_tree,
)

REPO_WIKI = Path(__file__).resolve().parents[1] / "wiki"
WAVE_A_PAGES = (
    "Home.md",
    "turbo-and-airflow/turbo-and-airflow-overview.md",
    "turbo-and-airflow/oem-chrysler-turbos.md",
    "turbo-and-airflow/turbocalc-compressor-catalog.md",
    "turbo-and-airflow/cylinder-head-flowbench-atlas.md",
)


def _frontmatter_block(**overrides: object) -> str:
    data = {
        "title": "Sample Page",
        "slug": "sample-page",
        "category": "turbo-and-airflow",
        "tier": "Tier 2",
        "provenance": "Cleanroom verification",
        "ecu_scope": ["SMEC_88_89"],
        "engine_scope": ["2.2L_K"],
        "status": "verified",
        "last_verified": "2026-11-26",
        "tags": ["wave-a"],
    }
    data.update(overrides)
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            items = ", ".join(f'"{item}"' for item in value)
            lines.append(f"{key}: [{items}]")
        else:
            lines.append(f'{key}: "{value}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _build_sample_wiki(root: Path) -> Path:
    wiki = root / "wiki"
    (wiki / "topic").mkdir(parents=True)
    (wiki / "_Sidebar.md").write_text("- [Home](Home.md)\n- [Guide](topic/guide.md)\n", encoding="utf-8")
    (wiki / "_Footer.md").write_text("Cleanroom Heritage Footer\n", encoding="utf-8")
    (wiki / "Home.md").write_text(
        _frontmatter_block(title="Home", slug="home", category="portal")
        + "# Home\n\nSee [Guide](topic/guide.md#pinout).\n",
        encoding="utf-8",
    )
    (wiki / "topic" / "guide.md").write_text(
        _frontmatter_block(title="Guide", slug="guide")
        + "## Pinout\n\nBack to [Home](../Home.md).\n\n"
        + '```mermaid\nflowchart LR\n    A["Pin 22"] -->|"0.5V"| B["SMEC"]\n```\n',
        encoding="utf-8",
    )
    return wiki


def test_live_wiki_tree_valid_and_wave_a_clean() -> None:
    result = validate_wiki_tree(REPO_WIKI)
    assert result["validated_files"] >= 12
    assert result["mermaid_blocks_checked"] >= 4
    warnings_text = "\n".join(result["warnings"])
    for page in WAVE_A_PAGES:
        assert page not in warnings_text


@pytest.mark.parametrize(
    "block",
    [
        'flowchart LR\n    A["Sensor"] -->|"5V Ref"| B["ECU"]',
        "sequenceDiagram\n    Tool->>ECU: Mode Request\n    ECU-->>Tool: Frame",
    ],
)
def test_validate_mermaid_block_accepts_valid_diagrams(block: str) -> None:
    validate_mermaid_block(block)


@pytest.mark.parametrize(
    "bad_block",
    [
        "",
        "pie title Unsupported\n    \"A\" : 10",
        "flowchart LR\n    A[Unclosed --> B[Ok]",
        "flowchart LR\n    A -> B",
        "flowchart LR\n    A -->",
    ],
)
def test_validate_mermaid_block_rejects_malformed_diagrams(bad_block: str) -> None:
    with pytest.raises(WikiValidationError):
        validate_mermaid_block(bad_block)


def test_dry_run_and_validate_only_do_not_write_or_mutate(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    stage = tmp_path / "stage_out"
    before = {p.relative_to(wiki): p.read_text(encoding="utf-8") for p in wiki.rglob("*.md")}

    dry_res = run_sync_wiki(wiki, target_dir=stage, dry_run=True)
    val_res = run_sync_wiki(wiki, target_dir=stage, validate_only=True)

    assert dry_res["validated_files"] == 4 and dry_res["synced_files"] == 0
    assert val_res["validate_only"] is True and val_res["synced_files"] == 0
    assert not stage.exists()
    assert {p.relative_to(wiki): p.read_text(encoding="utf-8") for p in wiki.rglob("*.md")} == before


def test_sync_wiki_github_flat_strips_frontmatter_and_rewrites_links(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    out = tmp_path / "github_wiki"
    res = sync_wiki(wiki, target_dir=out, strip_frontmatter=True, flatten_for_github_wiki=True)

    assert res["synced_files"] == 4
    home_out = (out / "Home.md").read_text(encoding="utf-8")
    guide_out = (out / "guide.md").read_text(encoding="utf-8")
    sidebar_out = (out / "_Sidebar.md").read_text(encoding="utf-8")

    assert not home_out.startswith("---")
    assert "[Guide](guide#pinout)" in home_out
    assert "[Home](Home)" in guide_out
    assert "[Guide](guide)" in sidebar_out
    assert ".md)" not in home_out
    assert ".md)" not in guide_out
    assert ".md)" not in sidebar_out
    assert (out / "_Footer.md").read_text(encoding="utf-8") == "Cleanroom Heritage Footer\n"


def test_sync_wiki_static_site_preserves_subdirs_and_frontmatter(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    out = tmp_path / "static_site"
    res = sync_wiki(wiki, target_dir=out, strip_frontmatter=False, flatten_for_github_wiki=False)

    assert res["synced_files"] == 4
    home_out = (out / "Home.md").read_text(encoding="utf-8")
    guide_out = (out / "topic" / "guide.md").read_text(encoding="utf-8")
    assert guide_out.startswith("---")
    assert "[Guide](topic/guide.md#pinout)" in home_out
    assert "[Home](../Home.md)" in guide_out


def test_sync_wiki_rejects_flattened_filename_collision(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    (wiki / "other").mkdir()
    (wiki / "other" / "guide.md").write_text(_frontmatter_block(slug="other-guide") + "# Dup\n", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="collision"):
        sync_wiki(wiki, target_dir=tmp_path / "out", flatten_for_github_wiki=True)


def test_validate_wiki_tree_rejects_bad_frontmatter_broken_links_and_forbidden_tokens(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    (wiki / "bad_meta.md").write_text("---\ntitle: \"Only Title\"\n---\n# Bad\n", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="missing frontmatter fields"):
        validate_wiki_tree(wiki)
    (wiki / "bad_meta.md").unlink()

    (wiki / "broken_link.md").write_text(_frontmatter_block(slug="broken") + "[Missing](nope.md)\n", encoding="utf-8")
    with pytest.raises(WikiValidationError, match="broken or out-of-tree link"):
        validate_wiki_tree(wiki)
    (wiki / "broken_link.md").unlink()

    for token in ("C:" + "\\Users\\" + "demo", "/dev/" + "ttyUSB0"):
        bad_file = wiki / "forbidden.md"
        bad_file.write_text(_frontmatter_block(slug="forbidden") + f"Forbidden: {token}\n", encoding="utf-8")
        with pytest.raises(WikiValidationError, match="forbidden"):
            validate_wiki_tree(wiki)
        bad_file.unlink()


def test_wave_b_ecu_and_sci_pages_verified() -> None:
    overviews = ("ecu-families/ecu-families-overview.md", "rom-and-sci/rom-and-sci-overview.md")
    wave_b = (
        "ecu-families/lm-pm-6803.md", "ecu-families/smec-68hc11.md", "ecu-families/sbec-68hc11.md",
        "ecu-families/pin-11-safety-matrix.md", "rom-and-sci/sci-7812-and-62500-protocol.md",
        "rom-and-sci/drb-ii-commands-and-prompts.md", "rom-and-sci/fuel-and-spark-math-peftbl-wottbl.md",
    )
    for rel in (*overviews, *wave_b):
        text = (REPO_WIKI / rel).read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        assert meta["status"] == "verified", f"{rel} expected verified, got {meta.get('status')!r}"
    home_text = (REPO_WIKI / "Home.md").read_text(encoding="utf-8")
    for ov in overviews:
        assert ov in home_text
    sidebar_text = (REPO_WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    for page in wave_b:
        assert page in sidebar_text


def test_wave_c_drivetrain_and_hardware_pages_verified() -> None:
    overviews = (
        "drivetrain-and-swaps/drivetrain-and-swaps-overview.md",
        "hardware-bench/hardware-bench-overview.md",
    )
    wave_c = (
        "drivetrain-and-swaps/dohc-ecc-hybrid-swap-guide.md",
        "drivetrain-and-swaps/a555-a568-a413-transaxles.md",
        "hardware-bench/smec-28c256-read-only-socket-notes.md",
        "hardware-bench/knock-and-dis-conditioning.md",
    )
    for rel in (*overviews, *wave_c):
        text = (REPO_WIKI / rel).read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        assert meta["status"] == "verified", f"{rel} expected verified, got {meta.get('status')!r}"
        if rel == "hardware-bench/smec-28c256-read-only-socket-notes.md":
            assert meta["ecu_scope"] == ["SMEC_88_89"]

    home_text = (REPO_WIKI / "Home.md").read_text(encoding="utf-8")
    assert "| **Drivetrain & Swaps** | `verified` |" in home_text
    assert "| **Hardware Bench** | `verified` |" in home_text
    for rel in (*overviews, *wave_c):
        assert rel in home_text

    sidebar_text = (REPO_WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    for page in wave_c:
        assert page in sidebar_text


def test_wave_d_wiring_and_diagnostics_verified_and_zero_stubs() -> None:
    wave_d = (
        "wiring-and-diagnostics/wiring-and-diagnostics-overview.md",
        "wiring-and-diagnostics/60-way-connector-atlas.md",
        "wiring-and-diagnostics/50-way-bulkhead-atlas.md",
        "wiring-and-diagnostics/drb-ii-fault-trees.md",
    )
    for rel in wave_d:
        text = (REPO_WIKI / rel).read_text(encoding="utf-8")
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        assert meta["status"] == "verified", f"{rel} expected verified, got {meta.get('status')!r}"

    home_text = (REPO_WIKI / "Home.md").read_text(encoding="utf-8")
    assert "| **Wiring & Diagnostics** | `verified` |" in home_text
    assert home_text.count("| `stub` |") == 0
    for rel in wave_d:
        assert rel in home_text

    sidebar_text = (REPO_WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    for rel in wave_d:
        assert rel in sidebar_text

    assert validate_wiki_tree(REPO_WIKI)["warnings"] == []


def test_validate_wiki_tree_rejects_out_of_tree_symlink(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    outside = tmp_path / "outside_secret.md"
    outside.write_text(_frontmatter_block(slug="outside") + "# Secret\n", encoding="utf-8")
    (wiki / "escape.md").symlink_to(outside)

    with pytest.raises(WikiValidationError):
        validate_wiki_tree(wiki)


def test_sync_wiki_rejects_target_symlink(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    out = tmp_path / "github_wiki"
    out.mkdir()
    outside_target = tmp_path / "outside_target.md"
    outside_target.write_text("original\n", encoding="utf-8")
    (out / "Home.md").symlink_to(outside_target)

    with pytest.raises(WikiValidationError):
        sync_wiki(wiki, target_dir=out, dry_run=False)
    assert outside_target.read_text(encoding="utf-8") == "original\n"


def test_sync_wiki_flattens_backtick_labels_and_preserves_inline_code_spans(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    (wiki / "Home.md").write_text(
        _frontmatter_block(title="Home", slug="home", category="portal")
        + "# Home\n\n"
        + "See [Guide](topic/guide.md#pinout), [`60-Way` `SMEC`](topic/guide.md#pinout), and [`_Sidebar.md`](_Sidebar.md).\n\n"
        + "Inline examples `[not-a-link](topic/missing.md)` and `[keep-me](topic/guide.md)` must stay literal.\n",
        encoding="utf-8",
    )

    validated = validate_wiki_tree(wiki)
    assert validated["validated_files"] == 4

    flat_out = tmp_path / "flat_wiki"
    sync_wiki(wiki, target_dir=flat_out, strip_frontmatter=True, flatten_for_github_wiki=True)
    flat_home = (flat_out / "Home.md").read_text(encoding="utf-8")
    assert "[Guide](guide#pinout)" in flat_home
    assert "[`60-Way` `SMEC`](guide#pinout)" in flat_home
    assert "[`_Sidebar.md`](_Sidebar)" in flat_home
    assert "`[not-a-link](topic/missing.md)`" in flat_home
    assert "`[keep-me](topic/guide.md)`" in flat_home

    unflat_out = tmp_path / "unflat_wiki"
    sync_wiki(wiki, target_dir=unflat_out, strip_frontmatter=False, flatten_for_github_wiki=False)
    unflat_home = (unflat_out / "Home.md").read_text(encoding="utf-8")
    unflat_guide = (unflat_out / "topic" / "guide.md").read_text(encoding="utf-8")
    assert "[`60-Way` `SMEC`](topic/guide.md#pinout)" in unflat_home
    assert "[`_Sidebar.md`](_Sidebar.md)" in unflat_home
    assert "`[not-a-link](topic/missing.md)`" in unflat_home
    assert "[Home](../Home.md)" in unflat_guide


def test_sync_wiki_repo_wiki_flattens_links_and_backtick_labels(tmp_path: Path) -> None:
    out = tmp_path / "repo_github_wiki"
    res = sync_wiki(REPO_WIKI, target_dir=out, strip_frontmatter=True, flatten_for_github_wiki=True)
    assert res["synced_files"] >= 12

    home_out = (out / "Home.md").read_text(encoding="utf-8")
    sidebar_out = (out / "_Sidebar.md").read_text(encoding="utf-8")

    assert ".md)" not in home_out
    assert ".md)" not in sidebar_out
    assert "[`60-Way` `SMEC` & `SBEC` Connector Atlas](60-way-connector-atlas)" in sidebar_out
    assert "[`60-Way` `SMEC` & `SBEC` Connector Atlas (`1988–1993`)](60-way-connector-atlas)" in home_out

