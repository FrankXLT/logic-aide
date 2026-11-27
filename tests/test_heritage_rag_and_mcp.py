"""Tests for Wave D heritage chunker, hybrid SQLite RAG, CLI, and chrysler-heritage MCP."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from click.testing import CliRunner
import pytest

from logicaide.cli import main as cli_main
from logicaide.heritage.chunker import (
    TABLE_SLICE_SIZE,
    TABLE_SPLIT_THRESHOLD,
    chunk_heritage_document,
    format_scope_prefix,
    is_chrome_partial,
)
from logicaide.heritage.rag_index import build_heritage_index
from logicaide.heritage.rag_query import (
    NO_VERIFIED_SOURCE_MATCH,
    RRF_K,
    TIER_WEIGHTS,
    escape_fts5_query,
    get_provenance_card,
    get_verified_schematic,
    lookup_60way_pin,
    lookup_sci_offset_hybrid,
    search_heritage_docs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_KNOWLEDGE = REPO_ROOT / "knowledge"
REPO_WIKI = REPO_ROOT / "wiki"
REPO_CATALOG = REPO_ROOT / "rom" / "manifests" / "catalog.json"
MCP_SERVER_PATH = REPO_ROOT / "mcp_servers" / "chrysler_heritage.py"


def _load_mcp_module():
    spec = importlib.util.spec_from_file_location("chrysler_heritage_mcp", MCP_SERVER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_cleanroom_digest(
    *,
    doc_id: str,
    title: str,
    trust_tier: str,
    ecu_scope: list[str],
    body: str,
    mermaid_verification: str = "NONE",
    pins_referenced: list[str] | None = None,
    hex_symbols: list[str] | None = None,
    symbols_referenced: list[str] | None = None,
) -> str:
    ecus = json.dumps(ecu_scope)
    return (
        f"> **Scope**: Synthetic cleanroom digest for {doc_id}\n"
        f"> **Boundary**: Read-only test fixture for offline RAG validation only.\n\n"
        f"---\n"
        f'doc_id: "{doc_id}"\ntitle: "{title}"\ntrust_tier: "{trust_tier}"\n'
        f'source_type: "SYNTHETIC_TEST_DIGEST"\n'
        f'source_citation: "LogicAIDE Synthetic Test Fixture ({doc_id})"\n'
        f'warc_capture_date: "2026-11-26T00:00:00Z"\npage_ref: "Test Fixture p.1"\n'
        f"ecu_families: {ecus}\necu_scope: {ecus}\n"
        f'engine_scope: ["2.2L_K"]\n'
        f"hex_symbols: {json.dumps(hex_symbols or [])}\n"
        f"symbols_referenced: {json.dumps(symbols_referenced or [])}\n"
        f"pins_referenced: {json.dumps(pins_referenced or [])}\n"
        f'mermaid_verification: "{mermaid_verification}"\n'
        f'cleanroom_status: "DISTILLED_ATTRIBUTED"\nverified_by: "pytest_fixture"\n'
        f"---\n\n{body.strip()}\n"
    )


@pytest.fixture(scope="module")
def hybrid_corpus_and_db(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build an isolated heritage SQLite RAG DB combining repo docs and controlled fixtures."""
    root = tmp_path_factory.mktemp("heritage_rag")
    knowledge_dir, wiki_dir = root / "knowledge", root / "wiki"
    shutil.copytree(REPO_KNOWLEDGE, knowledge_dir)
    shutil.copytree(REPO_WIKI, wiki_dir)

    (knowledge_dir / "tier3_community" / "synthetic_tier3_speculative_pin.md").write_text(
        _make_cleanroom_digest(
            doc_id="synthetic-tier3-speculative-pin",
            title="Synthetic Tier 3 Speculative Pin 49 Notes",
            trust_tier="Tier 3",
            ecu_scope=["SYNTH_T3"],
            pins_referenced=["60-way Pin 49"],
            hex_symbols=["0xFE99"],
            symbols_referenced=["SPECULATIVE_CHANNEL_ZQX"],
            body=(
                "# Speculative Tier 3 Pin 49 Notes\n\n"
                "NEVER use Tier 3 community notes as a sole pinout source without FSM corroboration.\n\n"
                "## Speculative Bench Mapping\n\n"
                "Unverified note claims 60-way Pin 49 carries SPECULATIVE_CHANNEL_ZQX and "
                "RAM offset 0xFE99 on SYNTH_T3 units.\n"
            ),
        ),
        encoding="utf-8",
    )

    (knowledge_dir / "tier2_verified" / "synthetic_quarantined_diagram.md").write_text(
        _make_cleanroom_digest(
            doc_id="synthetic-quarantined-diagram",
            title="Synthetic Quarantined Diagram",
            trust_tier="Tier 2",
            ecu_scope=["SMEC_88_89"],
            mermaid_verification="NEEDS_HUMAN_REVIEW",
            body=(
                "# Quarantined SMEC Flow Probe\n\n## Quarantined Diagnostic Tree\n\n"
                "Pending human FSM review.\n\n```mermaid\nflowchart TD\n"
                '    qstart["Quarantined SMEC Check"] --> qgate["Manual Review Pending"]\n```\n'
            ),
        ),
        encoding="utf-8",
    )

    # Tier 3 sorts alphabetically first by doc_id so only Trust-Weighted RRF ranks Tier 1 > Tier 2 > Tier 3.
    rrf_body = "# RRF Weight Calibration\n\n## Shared Signal Note\n\nToken RRF_WEIGHT_SENTINEL_99 verifies tier weighting.\n"
    for tier, folder, did in (("Tier 3", "tier3_community", "rrf-a-tier3"), ("Tier 2", "tier2_verified", "rrf-b-tier2"), ("Tier 1", "tier1_oem", "rrf-c-tier1")):
        extra = "\nNEVER trust unverified pinouts without FSM check.\n" if tier == "Tier 3" else ""
        (knowledge_dir / folder / f"{did}.md").write_text(
            _make_cleanroom_digest(
                doc_id=did, title=f"RRF Calibration {tier}", trust_tier=tier,
                ecu_scope=["RRF_TEST"], symbols_referenced=["RRF_WEIGHT_SENTINEL_99"], body=rrf_body + extra,
            ),
            encoding="utf-8",
        )

    table_rows = [
        "| Pin 1 | SIG_1 | ROW1_HEADER_ANCHOR |" if idx == 1 else f"| Pin {idx} | SIG_{idx} | {'ROW41_HYDRATION_SENTINEL' if idx == 41 else 'Nominal'} |"
        for idx in range(1, 43)
    ]
    (knowledge_dir / "tier2_verified" / "synthetic_large_table_parent.md").write_text(
        _make_cleanroom_digest(
            doc_id="synthetic-large-table-parent",
            title="Synthetic Large Table Parent",
            trust_tier="Tier 2",
            ecu_scope=["TABLE_TEST"],
            body="# Large Table Hydration\n\n## Full Connector Matrix\n\n| Pin | Signal | Notes |\n| :--- | :--- | :--- |\n" + "\n".join(table_rows) + "\n",
        ),
        encoding="utf-8",
    )

    db_path = root / "heritage_index.sqlite"
    summary = build_heritage_index(knowledge_dir=knowledge_dir, wiki_dir=wiki_dir, db_path=db_path, verify_frontmatter=True)
    assert summary["parent_count"] > 0
    assert summary["quarantined_mermaid_count"] >= 1
    assert summary["verified_mermaid_count"] >= 1
    return {"root": root, "knowledge": knowledge_dir, "wiki": wiki_dir, "db": db_path}


def test_chunker_strips_scope_boundary_injects_prefix_and_skips_chrome(tmp_path: Path) -> None:
    md_path = tmp_path / "knowledge" / "tier2_verified" / "sample_smec.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text(
        _make_cleanroom_digest(
            doc_id="synth-chunk-test",
            title="Synthetic SMEC Chunk Test",
            trust_tier="Tier 2",
            ecu_scope=["SMEC_88_89"],
            pins_referenced=["60-way Pin 11"],
            hex_symbols=["0xF2"],
            body="# Header\n\n## Pinout Overview\n\nInspect 60-way Pin 11 before 0xF2 telemetry.\n",
        ),
        encoding="utf-8",
    )
    parents, children = chunk_heritage_document(md_path, repo_root=tmp_path, verify_mermaid=False)
    assert len(parents) == 1 and len(children) == 1
    assert "> **Scope**:" not in parents[0]["body_markdown"]
    assert "> **Boundary**:" not in parents[0]["body_markdown"]
    expected_prefix = format_scope_prefix(
        {
            "doc_id": "synth-chunk-test", "trust_tier": "Tier 2", "ecu_scope": ["SMEC_88_89"],
            "engine_scope": ["2.2L_K"], "source_citation": "LogicAIDE Synthetic Test Fixture (synth-chunk-test)",
            "page_ref": "Test Fixture p.1",
        },
        "Pinout Overview",
        "NONE",
    )
    assert children[0]["prefixed_text"].startswith(expected_prefix)
    assert "> **Scope**:" not in children[0]["prefixed_text"]
    assert is_chrome_partial(tmp_path / "wiki" / "_Sidebar.md", repo_root=tmp_path)
    assert is_chrome_partial(tmp_path / "wiki" / "_Footer.md", repo_root=tmp_path)
    assert is_chrome_partial(tmp_path / "knowledge" / "README.md", repo_root=tmp_path)
    sidebar = tmp_path / "wiki" / "_Sidebar.md"
    sidebar.parent.mkdir(parents=True, exist_ok=True)
    sidebar.write_text("- [Home](Home.md)\n", encoding="utf-8")
    assert chunk_heritage_document(sidebar, repo_root=tmp_path) == ([], [])


def test_chunker_table_slices_and_mermaid_decomposition(tmp_path: Path) -> None:
    row_count = TABLE_SPLIT_THRESHOLD + 7  # 42 rows (> 35 threshold)
    rows = "\n".join(f"| Pin {i} | C{i:02d} | Signal {i} |" for i in range(1, row_count + 1))
    body = (
        "# Chunker Structural Test\n\n## Large Pin Table\n\n| Pin | Circuit | Signal |\n| --- | --- | --- |\n"
        f"{rows}\n\n"
        '## Diagnostic Flowchart\n\n```mermaid\nflowchart TD\n    A["Ignition OFF"] -->|"Check DMM"| B["Probe Pin 11"]\n```\n\n'
        "## SCI Sequence\n\n```mermaid\nsequenceDiagram\n    participant H as Host\n    participant E as ECU\n"
        "    H->>E: Poll 0xF2\n    E-->>H: Telemetry Byte\n```\n"
    )
    md_path = tmp_path / "doc.md"
    md_path.write_text(
        _make_cleanroom_digest(doc_id="chunk-struct-test", title="Chunker Structural Test", trust_tier="Tier 1", ecu_scope=["SBEC_90_93"], body=body),
        encoding="utf-8",
    )
    parents, children = chunk_heritage_document(md_path, repo_root=tmp_path, verify_mermaid=True)
    assert len(parents) == 3

    table_slices = [c for c in children if c["chunk_kind"] == "TABLE_SLICE"]
    assert [c["row_range"] for c in table_slices] == [
        f"rows 1-{TABLE_SLICE_SIZE} of {row_count}",
        f"rows {TABLE_SLICE_SIZE + 1}-{2 * TABLE_SLICE_SIZE} of {row_count}",
        f"rows {2 * TABLE_SLICE_SIZE + 1}-{row_count} of {row_count}",
    ]
    assert all("| Pin | Circuit | Signal |" in slc["prefixed_text"] for slc in table_slices)
    assert any(c["chunk_kind"] == "TREE_BRANCH" and "A (Ignition OFF) --[Check DMM]--> B (Probe Pin 11)" in c["prefixed_text"] for c in children)
    seq_edges = [c for c in children if c["chunk_kind"] == "SCHEMATIC_EDGE"]
    assert any("H (Host) ->> E (ECU): Poll 0xF2" in c["prefixed_text"] for c in seq_edges)
    assert any("E (ECU) -->> H (Host): Telemetry Byte" in c["prefixed_text"] for c in seq_edges)


def test_escape_fts5_query_executes_without_sqlite_errors(hybrid_corpus_and_db: dict[str, Path]) -> None:
    conn = sqlite3.connect(hybrid_corpus_and_db["db"])
    try:
        for raw in ("$8000", "0xF2", "Pin-11", "60-way", "#782", '"AND OR NOT"'):
            escaped = escape_fts5_query(raw)
            assert escaped
            rows = conn.execute("SELECT child_id FROM heritage_fts WHERE heritage_fts MATCH ?", (escaped,)).fetchall()
            assert isinstance(rows, list)
    finally:
        conn.close()
    pin_escaped = escape_fts5_query("Pin-11")
    assert '"Pin-11"' in pin_escaped and '"Pin"' in pin_escaped and '"11"' in pin_escaped


def test_trust_weighted_rrf_parent_hydration_and_filters(hybrid_corpus_and_db: dict[str, Path]) -> None:
    db = hybrid_corpus_and_db["db"]
    assert RRF_K == 60
    assert TIER_WEIGHTS == {"Tier 1": 1.25, "Tier 2": 1.10, "Tier 3": 0.90}

    ranked = search_heritage_docs("RRF_WEIGHT_SENTINEL_99", db_path=db, ecu="RRF_TEST", min_tier=3)
    assert ranked["matched"] is True
    assert [r["trust_tier"] for r in ranked["results"]] == ["Tier 1", "Tier 2", "Tier 3"]
    scores = [r["score"] for r in ranked["results"]]
    assert scores[0] > scores[1] > scores[2]

    tier2_only = search_heritage_docs("RRF_WEIGHT_SENTINEL_99", db_path=db, ecu="RRF_TEST", min_tier=2)
    assert [r["trust_tier"] for r in tier2_only["results"]] == ["Tier 1", "Tier 2"]

    hydrated = search_heritage_docs("ROW41_HYDRATION_SENTINEL", db_path=db, ecu="TABLE_TEST")
    assert hydrated["matched"] is True
    top = hydrated["results"][0]
    assert top["chunk_kind"] == "TABLE_SLICE"
    assert top["row_range"] == "rows 41-42 of 42"
    assert "ROW1_HEADER_ANCHOR" in top["body_markdown"] and "ROW41_HYDRATION_SENTINEL" in top["body_markdown"]

    smec_only = search_heritage_docs("Pin 11", db_path=db, ecu="SMEC_88_89")
    assert smec_only["matched"] is True
    assert all("SMEC_88_89" in r["ecu_scope"] for r in smec_only["results"])


def test_zero_hallucination_abstention_and_tier3_gate(hybrid_corpus_and_db: dict[str, Path]) -> None:
    db = hybrid_corpus_and_db["db"]
    nonsense = search_heritage_docs("xyzzy_plugh_nonexistent_token_998811", db_path=db)
    assert nonsense["matched"] is False and nonsense["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert NO_VERIFIED_SOURCE_MATCH in nonsense["message"] and nonsense["results"] == []

    tier3_sci_blocked = search_heritage_docs("0xFE99", db_path=db, require_verified_for_pin_or_sci=True)
    assert tier3_sci_blocked["matched"] is False and NO_VERIFIED_SOURCE_MATCH in tier3_sci_blocked["message"]

    tier3_sci_unblocked = search_heritage_docs("0xFE99", db_path=db, require_verified_for_pin_or_sci=False)
    assert tier3_sci_unblocked["matched"] is True and tier3_sci_unblocked["results"][0]["trust_tier"] == "Tier 3"

    tier3_pin_blocked = lookup_60way_pin(49, ecu="SYNTH_T3", db_path=db)
    assert tier3_pin_blocked["matched"] is False and NO_VERIFIED_SOURCE_MATCH in tier3_pin_blocked["message"]


def test_get_verified_schematic_quarantine_gate(hybrid_corpus_and_db: dict[str, Path]) -> None:
    db = hybrid_corpus_and_db["db"]
    blocked = get_verified_schematic(doc_id="synthetic-quarantined-diagram", pending_review=False, db_path=db)
    assert blocked["matched"] is False and blocked["status"] == "QUARANTINED_NEEDS_HUMAN_REVIEW"

    unblocked = get_verified_schematic(doc_id="synthetic-quarantined-diagram", pending_review=True, db_path=db)
    assert unblocked["matched"] is True
    assert unblocked["diagrams"][0]["quarantined"] is True
    assert unblocked["diagrams"][0]["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"

    verified = get_verified_schematic(doc_id="pin-11-safety-matrix", pending_review=False, db_path=db)
    assert verified["matched"] is True
    assert verified["diagrams"][0]["quarantined"] is False
    assert verified["diagrams"][0]["mermaid_verification"] == "VERIFIED"


def test_pin11_sci_and_provenance_card(hybrid_corpus_and_db: dict[str, Path]) -> None:
    db = hybrid_corpus_and_db["db"]
    pin11 = lookup_60way_pin(11, db_path=db)
    assert pin11["matched"] is True and pin11["pin"] == 11
    assert any("Z12" in note and "SBEC" in note for note in pin11["safety_notes"])

    sci_f2 = lookup_sci_offset_hybrid("0xF2", catalog_path=REPO_CATALOG, db_path=db)
    assert sci_f2["matched"] is True and sci_f2["heritage_results"]

    sci_5e = lookup_sci_offset_hybrid("0x5E", rom_id="smec_88_89_v19", catalog_path=REPO_CATALOG, db_path=db)
    assert sci_5e["matched"] is True and any(m.get("var_name") == "EngineRpm_LB" for m in sci_5e["manifest_matches"])

    sci_rpm = lookup_sci_offset_hybrid("EngineRpm_HB", catalog_path=REPO_CATALOG, db_path=db)
    assert sci_rpm["matched"] is True and any(m.get("var_name") == "EngineRpm_HB" for m in sci_rpm["manifest_matches"])

    prov = get_provenance_card("smec-28c256-readonly-hardware-notes", db_path=db)
    assert prov["matched"] is True and prov["trust_tier"] == "Tier 2" and prov["source_citation"]
    assert prov["sections"] and all(len(sec["sha256"]) == 64 for sec in prov["sections"])


def test_cli_heritage_subcommands(hybrid_corpus_and_db: dict[str, Path], tmp_path: Path) -> None:
    runner, cli_db = CliRunner(), tmp_path / "cli_heritage.sqlite"
    for cmd_name in ("build-index", "index"):
        res = runner.invoke(cli_main, [
            "heritage", cmd_name, "--knowledge-dir", str(hybrid_corpus_and_db["knowledge"]),
            "--wiki-dir", str(hybrid_corpus_and_db["wiki"]), "--db-path", str(cli_db), "--json",
        ])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["parent_count"] > 0 and payload["child_count"] > 0

    cases = (
        (["heritage", "query", "Pin 11 Z12", "--db-path", str(cli_db), "--json"], lambda p: p["matched"] and p["results"]),
        (["heritage", "query", "--pin", "11", "--db-path", str(cli_db), "--json"], lambda p: p["pin"] == 11 and any("Z12" in n for n in p["safety_notes"])),
        (["heritage", "query", "--sci", "EngineRpm_HB", "--catalog", str(REPO_CATALOG), "--db-path", str(cli_db), "--json"], lambda p: p["matched"] and p["manifest_matches"]),
        (["heritage", "query", "--provenance", "smec-28c256-readonly-hardware-notes", "--db-path", str(cli_db), "--json"], lambda p: p["matched"] and len(p["sections"]) >= 1),
        (["heritage", "diagram", "--doc", "pin-11-safety-matrix", "--db-path", str(cli_db), "--json"], lambda p: p["matched"] and p["diagrams"][0]["quarantined"] is False),
        (["heritage", "diagram", "--doc", "synthetic-quarantined-diagram", "--db-path", str(cli_db), "--json"], lambda p: p["matched"] is False and p["status"] == "QUARANTINED_NEEDS_HUMAN_REVIEW"),
        (["heritage", "diagram", "--doc", "synthetic-quarantined-diagram", "--pending-review", "--db-path", str(cli_db), "--json"], lambda p: p["matched"] is True and p["diagrams"][0]["quarantined"] is True),
    )
    for args, check in cases:
        res = runner.invoke(cli_main, args)
        assert res.exit_code == 0, res.output
        assert check(json.loads(res.output))


def test_mcp_direct_wrappers_and_stdio_jsonrpc(hybrid_corpus_and_db: dict[str, Path], tmp_path: Path) -> None:
    mcp_mod, db = _load_mcp_module(), hybrid_corpus_and_db["db"]
    expected_tools = {
        "search_heritage_docs", "get_verified_schematic", "lookup_60way_pin",
        "lookup_sci_offset", "get_provenance_card", "discover_heritage_leads", "sync_heritage_wiki",
    }
    assert set(mcp_mod.TOOL_REGISTRY) == expected_tools

    assert mcp_mod.search_heritage_docs("Pin 11 Z12", db_path=db)["matched"] is True
    assert mcp_mod.get_verified_schematic(doc_id="pin-11-safety-matrix", db_path=db)["matched"] is True
    assert mcp_mod.lookup_60way_pin(11, db_path=db)["pin"] == 11
    assert mcp_mod.lookup_sci_offset("EngineRpm_HB", catalog_path=REPO_CATALOG, db_path=db)["matched"] is True
    assert mcp_mod.get_provenance_card("smec-28c256-readonly-hardware-notes", db_path=db)["matched"] is True

    leads = mcp_mod.discover_heritage_leads(
        wave="D", fetch_cdx=False, private_root=tmp_path / "empty_priv",
        manifest_path=tmp_path / "missing_manifest.md", queue_dir=tmp_path / "queue", output_yaml=tmp_path / "leads.yaml",
    )
    assert leads["wave"] == "D" and isinstance(leads["leads"], list)

    wiki_sync_res = mcp_mod.sync_heritage_wiki(wiki_dir=REPO_WIKI, target_dir=tmp_path / "wiki_mirror", dry_run=True)
    assert wiki_sync_res["dry_run"] is True and wiki_sync_res["validated_files"] > 0

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lookup_60way_pin", "arguments": {"pin": 11, "db_path": str(db)}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "sync_heritage_wiki", "arguments": {"wiki_dir": str(REPO_WIKI), "target_dir": str(tmp_path / "wiki_e2e"), "dry_run": True}}},
    ]
    proc = subprocess.run(
        [sys.executable, str(MCP_SERVER_PATH), "--stdio"],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        text=True, capture_output=True, check=True, timeout=30,
    )
    responses = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["serverInfo"]["name"] == "chrysler-heritage"
    assert {t["name"] for t in responses[1]["result"]["tools"]} == expected_tools
    assert responses[2]["result"]["isError"] is False and responses[2]["result"]["structuredContent"]["pin"] == 11
    assert any("Z12" in note for note in responses[2]["result"]["structuredContent"]["safety_notes"])
    assert responses[3]["result"]["isError"] is False and responses[3]["result"]["structuredContent"]["dry_run"] is True
    assert responses[3]["result"]["structuredContent"]["validated_files"] > 0


def test_hardening_60way_bounds_corrupt_db_and_jsonrpc_validation(hybrid_corpus_and_db: dict[str, Path], tmp_path: Path) -> None:
    from logicaide.heritage import rag_query

    mcp_mod, db = _load_mcp_module(), hybrid_corpus_and_db["db"]
    assert rag_query.lookup_60way_pin(14, db_path=db)["matched"] is False
    assert rag_query.lookup_60way_pin(18, db_path=db)["matched"] is True

    empty_db = tmp_path / "empty.sqlite"
    empty_db.write_bytes(b"")
    with pytest.raises(ValueError, match="Invalid or corrupt"):
        rag_query.search_heritage_docs("SMEC Pin 11", db_path=empty_db)
    assert mcp_mod.search_heritage_docs("SMEC Pin 11", db_path=empty_db)["status"] == "NO_VERIFIED_SOURCE_MATCH"

    cli_err = CliRunner().invoke(cli_main, ["heritage", "query", "Pin 11", "--db-path", str(empty_db)])
    assert cli_err.exit_code != 0

    bad_arr = mcp_mod.handle_jsonrpc_request(
        {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "lookup_60way_pin", "arguments": []}}
    )
    bad_bool = mcp_mod.handle_jsonrpc_request(
        {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "search_heritage_docs", "arguments": {"query": "SMEC", "min_tier": True}}}
    )
    assert bad_arr["error"]["code"] == -32602
    assert bad_bool["error"]["code"] == -32602


def test_regression_60way_pin13_lm_exclusion_negative_pin_and_tier_clamp(
    hybrid_corpus_and_db: dict[str, Path], tmp_path: Path
) -> None:
    db = hybrid_corpus_and_db["db"]

    pin13_lm = lookup_60way_pin(13, ecu="LM_6803", db_path=db)
    assert pin13_lm["matched"] is False
    pin13_all = lookup_60way_pin(13, db_path=db)
    for item in pin13_all.get("results", []):
        assert item["doc_id"] != "sci-7812-and-62500-protocol"
        assert "Red Conn. Pin 13" not in item["body_markdown"]

    with pytest.raises(ValueError):
        lookup_60way_pin("-5", db_path=db)
    with pytest.raises(ValueError):
        search_heritage_docs("SMEC", db_path=db, min_tier=True)

    iso_k, iso_w = tmp_path / "iso_k", tmp_path / "iso_w"
    (iso_k / "tier2_verified").mkdir(parents=True)
    (iso_k / "tier3_community").mkdir(parents=True)
    iso_w.mkdir()
    (iso_k / "tier2_verified" / "lm_red_conn.md").write_text(
        _make_cleanroom_digest(
            doc_id="lm-red-conn-only",
            title="LM 50-way Red Conn SCI",
            trust_tier="Tier 2",
            ecu_scope=["LM_6803", "SMEC_88_89"],
            body=(
                "# LM 6803 SCI\n\n## Harness Pinout\n\n"
                "| Controller Family | SCI TX | SCI RX |\n| :--- | :--- | :--- |\n"
                "| **LM (6803, 1984–87)** | Red Conn. Pin 11 | Red Conn. Pin 13 |\n"
            ),
        ),
        encoding="utf-8",
    )
    (iso_w / "_Footer.md").write_text("# Isolated Fixture Footer\n\nMinimal wiki root.\n", encoding="utf-8")
    iso_db = tmp_path / "iso.sqlite"
    build_heritage_index(knowledge_dir=iso_k, wiki_dir=iso_w, db_path=iso_db, verify_frontmatter=True)
    assert lookup_60way_pin(13, db_path=iso_db)["matched"] is False

    t3_k = tmp_path / "t3_k"
    (t3_k / "tier3_community").mkdir(parents=True)
    (t3_k / "tier3_community" / "t3_40way_sci.md").write_text(
        _make_cleanroom_digest(
            doc_id="t3-40way-sci",
            title="Tier 3 40-way pin and SCI TX notes",
            trust_tier="Tier 3",
            ecu_scope=["SMEC_88_89"],
            body=(
                "# Community Notes\n\n> NEVER use as a sole pinout or SCI map; corroborate with a Tier 1 OEM FSM.\n\n"
                "## Connector & SCI\n\nUnverified 40-way pin, connector pin, SCI RX, and SCI TX wire note.\n"
            ),
        ),
        encoding="utf-8",
    )
    t3_db = tmp_path / "t3_only.sqlite"
    build_heritage_index(knowledge_dir=t3_k, wiki_dir=iso_w, db_path=t3_db, verify_frontmatter=True)

    for q in ("40-way pin", "connector pin", "SCI TX", "SCI RX"):
        clamped = search_heritage_docs(q, db_path=t3_db, min_tier=3, require_verified_for_pin_or_sci=True)
        assert clamped["matched"] is False
        unclamped = search_heritage_docs(q, db_path=t3_db, min_tier=3, require_verified_for_pin_or_sci=False)
        assert unclamped["matched"] is True and unclamped["results"][0]["trust_tier"] == "Tier 3"

    live_sci_tx = search_heritage_docs("SCI TX", db_path=db, min_tier=3, require_verified_for_pin_or_sci=True)
    assert live_sci_tx["matched"] is True
    assert all(r["trust_tier"] in {"Tier 1", "Tier 2"} for r in live_sci_tx["results"])


def test_regression_quarantined_mermaid_stripped_from_search(
    hybrid_corpus_and_db: dict[str, Path]
) -> None:
    db = hybrid_corpus_and_db["db"]
    res = search_heritage_docs("Quarantined Diagnostic Tree", ecu="SMEC_88_89", db_path=db)
    assert res["matched"] is True
    hits = [r for r in res["results"] if r["doc_id"] == "synthetic-quarantined-diagram"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"
    assert hit["mermaid_source"] is None
    assert "```mermaid" not in hit["body_markdown"]
    assert "Quarantined SMEC Check" not in hit["body_markdown"]
    assert "Pending human FSM review." in hit["body_markdown"]


def test_mcp_wrapper_bool_and_path_guards(
    hybrid_corpus_and_db: dict[str, Path], tmp_path: Path
) -> None:
    mcp_mod, db = _load_mcp_module(), hybrid_corpus_and_db["db"]

    assert mcp_mod.search_heritage_docs("SMEC", db_path=db, min_tier=True)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.search_heritage_docs("SMEC", db_path=db, limit=True)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.get_verified_schematic(min_tier=True, db_path=db)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.lookup_60way_pin(True, db_path=db)["status"] == "NO_VERIFIED_SOURCE_MATCH"

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    sym_wiki = tmp_path / "sym_wiki"
    sym_wiki.symlink_to(REPO_WIKI)
    sym_target = tmp_path / "sym_target"
    sym_target.symlink_to(outside_dir)
    sym_yaml = tmp_path / "sym_leads.yaml"
    sym_yaml.symlink_to(outside_dir / "leads.yaml")

    assert mcp_mod.sync_heritage_wiki(wiki_dir=sym_wiki, target_dir=tmp_path / "out", dry_run=True)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.sync_heritage_wiki(wiki_dir=REPO_WIKI, target_dir=sym_target, dry_run=True)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.sync_heritage_wiki(wiki_dir="../outside_wiki", target_dir=tmp_path / "out", dry_run=True)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.discover_heritage_leads(wave="D", queue_dir=tmp_path / "q", output_yaml=sym_yaml)["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert mcp_mod.discover_heritage_leads(wave="D", queue_dir="../escape_q", output_yaml=tmp_path / "leads.yaml")["status"] == "NO_VERIFIED_SOURCE_MATCH"
