"""Tests for LogicAIDE Living Heritage Wiki validation and dual-publish sync."""

from __future__ import annotations

from pathlib import Path
import pytest

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
    assert "[Guide](guide.md#pinout)" in home_out
    assert "[Home](Home.md)" in guide_out
    assert "[Guide](guide.md)" in sidebar_out
    assert (out / "_Footer.md").read_text(encoding="utf-8") == "Cleanroom Heritage Footer\n"


def test_sync_wiki_static_site_preserves_subdirs_and_frontmatter(tmp_path: Path) -> None:
    wiki = _build_sample_wiki(tmp_path)
    out = tmp_path / "static_site"
    res = sync_wiki(wiki, target_dir=out, strip_frontmatter=False, flatten_for_github_wiki=False)

    assert res["synced_files"] == 4
    guide_out = (out / "topic" / "guide.md").read_text(encoding="utf-8")
    assert guide_out.startswith("---")
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
