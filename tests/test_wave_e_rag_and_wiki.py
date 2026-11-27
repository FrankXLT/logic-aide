"""Offline Wave E tests for discovery leads, cleanroom digests, wiki sync, and 28+ Q Mopar RAG."""

from __future__ import annotations

from pathlib import Path
import re

import pytest
import yaml

from logicaide.heritage.chunker import chunk_heritage_document
from logicaide.heritage.crawl4ai_client import HIJACKED_DOMAINS
from logicaide.heritage.doc_curator import validate_cleanroom_digest
from logicaide.heritage.rag_index import build_heritage_index
from logicaide.heritage.rag_query import (
    NO_VERIFIED_SOURCE_MATCH,
    lookup_60way_pin,
    lookup_sci_offset_hybrid,
    search_heritage_docs,
)
from logicaide.heritage.wiki_sync import sync_wiki, validate_wiki_tree

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_KNOWLEDGE = REPO_ROOT / "knowledge"
REPO_WIKI = REPO_ROOT / "wiki"

WAVE_E_DIGESTS: tuple[Path, ...] = (
    REPO_KNOWLEDGE / "tier2_verified" / "smec_sbec_lm_pm_harness_pinout_crossref.md",
    REPO_KNOWLEDGE / "tier2_verified" / "mopar_fwd_transaxles_and_turbo_specs.md",
    REPO_KNOWLEDGE / "tier2_verified" / "mopar_fault_codes_and_ignition_diag.md",
    REPO_KNOWLEDGE / "tier3_community" / "turbomopar_web_community_notes.md",
)

WAVE_E_WIKI_PAGES: tuple[Path, ...] = (
    REPO_WIKI / "ecu-families" / "lm-smec-sbec-connector-evolution.md",
    REPO_WIKI / "wiring-and-diagnostics" / "key-dance-fault-codes-and-ignition-bench.md",
    REPO_WIKI / "turbo-and-airflow" / "eight-valve-heads-camshafts-and-turbo-builds.md",
    REPO_WIKI / "drivetrain-and-swaps" / "a555-a568-a413-transaxles.md",
    REPO_WIKI / "turbo-and-airflow" / "oem-chrysler-turbos.md",
    REPO_WIKI / "Home.md",
    REPO_WIKI / "_Sidebar.md",
)

_DEADBOLT_FORBIDDEN_RE = re.compile(
    r"(?:\bminipro\b|\bflash_ecu\b|\bCRAWL4AI_API_TOKEN\b|Flash\.frm|/dev/ttyUSB|\b0x(?:41|51)\b)",
    re.IGNORECASE,
)
_RAW_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_OBFUSCATED_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+\s*(?:\[at\]|\(at\)|\bat\b)\s*[A-Za-z0-9.-]+\s*(?:\[dot\]|\(dot\)|\.)\s*[A-Za-z]{2,}\b",
    re.IGNORECASE,
)


@pytest.fixture(scope="module")
def wave_e_index_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build an isolated offline heritage SQLite RAG index for the Wave E benchmark."""
    db_path = tmp_path_factory.mktemp("wave_e_rag") / "heritage_index.sqlite"
    stats = build_heritage_index(
        knowledge_dir=REPO_KNOWLEDGE,
        wiki_dir=REPO_WIKI,
        db_path=db_path,
        verify_frontmatter=True,
    )
    assert stats["parent_count"] > 0 and stats["child_count"] > 0
    assert stats["quarantined_mermaid_count"] == 0
    return db_path


def test_wave_e_discovery_leads_manifest_gate() -> None:
    """Validate knowledge/discovery_leads_wave_e.yaml line cap, 28 leads, CDX guards, and zero hijacks."""
    manifest_path = REPO_KNOWLEDGE / "discovery_leads_wave_e.yaml"
    assert manifest_path.is_file()
    raw_text = manifest_path.read_text(encoding="utf-8")
    assert len(raw_text.splitlines()) <= 500
    for hijacked in HIJACKED_DOMAINS:
        assert hijacked not in raw_text.lower()

    payload = yaml.safe_load(raw_text)
    leads = payload["leads"]
    assert len(leads) == 28
    sites = {lead["source_site"] for lead in leads}
    assert {"allpar.com", "turbo-mopar.com", "sites.google.com", "minimopar.net", "thedodgegarage.com"} <= sites

    for lead in leads:
        cdx_url = str(lead["cdx_query_url"])
        if lead["source_site"] == "allpar.com":
            assert str(lead.get("to_timestamp")) == "20170531"
            assert "to=20170531" in cdx_url and "!original:.*/forums/.*" in cdx_url
        elif lead["source_site"] == "turbo-mopar.com":
            assert str(lead.get("to_timestamp")) == "20161231"
            assert "to=20161231" in cdx_url


def test_wave_e_cleanroom_digests_and_safety_gates() -> None:
    """Validate all 4 Wave E cleanroom digests, chunker/Mermaid verification, Tier 3 guards, and PII/Deadbolt."""
    for digest_path in WAVE_E_DIGESTS:
        assert digest_path.is_file()
        text = digest_path.read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 500
        assert not _RAW_EMAIL_RE.search(text)
        assert not _OBFUSCATED_EMAIL_RE.search(text)
        assert not _DEADBOLT_FORBIDDEN_RE.search(text)

        fm = validate_cleanroom_digest(digest_path)
        parents, children = chunk_heritage_document(digest_path, repo_root=REPO_ROOT, verify_mermaid=True)
        assert parents and children
        assert all(p["mermaid_verification"] != "NEEDS_HUMAN_REVIEW" for p in parents)

        if digest_path.name == "turbomopar_web_community_notes.md":
            assert fm["trust_tier"] == "Tier 3"
            for required_tok in ("NEVER", "FSM", "28C256", "Pin 1", "27C256", "Pin 27", "A14"):
                assert required_tok in text


def test_wave_e_wiki_and_flattened_mirror_gate(tmp_path: Path) -> None:
    """Validate Wave E wiki pages <= 500 lines, clean validation warnings, and flattened mirror sync."""
    for page_path in WAVE_E_WIKI_PAGES:
        assert page_path.is_file()
        assert len(page_path.read_text(encoding="utf-8").splitlines()) <= 500

    val_summary = validate_wiki_tree(REPO_WIKI)
    assert val_summary["warnings"] == []

    mirror_dir = tmp_path / "wiki_mirror"
    sync_res = sync_wiki(REPO_WIKI, target_dir=mirror_dir, dry_run=False, flatten_for_github_wiki=True)
    assert sync_res["warnings"] == []
    assert sync_res["synced_files"] == sync_res["validated_files"]

    mirrored_files = list(mirror_dir.glob("*.md"))
    assert len(mirrored_files) == sync_res["synced_files"]
    for mf in mirrored_files:
        content = mf.read_text(encoding="utf-8")
        assert content.strip()
        assert ".md)" not in content


POSITIVE_BENCHMARK_CASES: tuple[tuple[str, set[str]], ...] = (
    # MiniMopar (6)
    ("LM 6803 Power Module 10-way 12-way connector", {"smec-sbec-lm-pm-harness-pinout-crossref", "lm-smec-sbec-connector-evolution", "lm-pm-6803"}),
    ("SMEC 60-way 14-way dual board 27C128", {"smec-sbec-lm-pm-harness-pinout-crossref", "lm-smec-sbec-connector-evolution", "smec-68hc11"}),
    ("1990 1993 TBI SBEC Pin 11 Z12 ground", {"smec-sbec-lm-pm-harness-pinout-crossref", "lm-smec-sbec-connector-evolution", "pin-11-safety-matrix", "60-way-connector-atlas"}),
    ("key-dance Code 12 Code 53 Code 55 MIL", {"mopar-fault-codes-and-ignition-diag", "key-dance-fault-codes-and-ignition-bench", "drb-ii-fault-trees"}),
    ("DRB-II DRBMemoryTable fault registers", {"drb-ii-commands-and-prompts", "drb-ii-fault-trees", "mopar-fault-codes-and-ignition-diag"}),
    ("SMEC 60-way Pin 14 R31 voltage regulation", {"smec-sbec-lm-pm-harness-pinout-crossref", "60-way-connector-atlas"}),
    # DodgeGarage (8)
    ("A555 A568 Getrag 4-pinion transaxle", {"mopar-fwd-transaxles-and-turbo-specs", "a555-a568-a413-transaxles"}),
    ("A413 3-speed automatic pump flats big axles", {"mopar-fwd-transaxles-and-turbo-specs", "a555-a568-a413-transaxles"}),
    ("Garrett TO3 TE04H VNT25 TBO3 turbocharger", {"mopar-fwd-transaxles-and-turbo-specs", "oem-chrysler-turbos"}),
    ("4105782 4105445 8V cylinder head 114 mm valves", {"mopar-fwd-transaxles-and-turbo-specs", "eight-valve-heads-camshafts-and-turbo-builds", "cylinder-head-flowbench-atlas"}),
    ("OSJ oversize cam journal oil pressure", {"mopar-fwd-transaxles-and-turbo-specs", "eight-valve-heads-camshafts-and-turbo-builds"}),
    ("Gus Mahon MeanMini Acclaim spark plug salt pepper", {"mopar-fault-codes-and-ignition-diag", "key-dance-fault-codes-and-ignition-bench", "eight-valve-heads-camshafts-and-turbo-builds"}),
    ("MSD BTM6 double retard solid-core plug wires", {"mopar-fault-codes-and-ignition-diag", "key-dance-fault-codes-and-ignition-bench"}),
    ("Grainger valve wastegate boost M4 nickel", {"oem-chrysler-turbos", "mopar-fwd-transaxles-and-turbo-specs"}),
    # Pre-2017 Allpar (4)
    ("A515 EDF A522 EDS 2.2L bore stroke", {"mopar-fwd-transaxles-and-turbo-specs", "eight-valve-heads-camshafts-and-turbo-builds"}),
    ("4323808 Allpar 2.2L", {"mopar-fwd-transaxles-and-turbo-specs"}),
    ("1991 1993 Lotus 16V DOHC Turbo III 224 hp", {"mopar-fwd-transaxles-and-turbo-specs", "oem-chrysler-turbos", "eight-valve-heads-camshafts-and-turbo-builds"}),
    ("Turbo I II III IV forged crank intercooled", {"mopar-fwd-transaxles-and-turbo-specs", "oem-chrysler-turbos"}),
    # MPSciLink / Turbo-Mopar (6)
    ("MPSciLink Arduino Mega 74HC04 SCI bridge", {"lm-smec-sbec-connector-evolution", "turbomopar-web-community-notes"}),
    ("28C256 Pin 1 A14 27C256 Pin 27 dual-calibration", {"turbomopar-web-community-notes", "smec-28c256-read-only-socket-notes"}),
    ("Code 53 D-Cal checksum internal ECM stalling", {"turbomopar-web-community-notes", "key-dance-fault-codes-and-ignition-bench"}),
    ("MPScandroid Nextion Module Settings Bluetooth", {"turbomopar-web-community-notes", "lm-smec-sbec-connector-evolution"}),
    ("CEL flash fuel cut HEP 8V sensor feed", {"turbomopar-web-community-notes"}),
    ("FastDataOutputAddressTable 0xF2 SCI RAM", {"sci-7812-and-62500-protocol", "drb-ii-commands-and-prompts", "smec-68hc11", "sbec-68hc11"}),
)


@pytest.mark.parametrize(("query", "expected_doc_ids"), POSITIVE_BENCHMARK_CASES)
def test_golden_mopar_positive_benchmark(
    wave_e_index_db: Path, query: str, expected_doc_ids: set[str]
) -> None:
    """24 positive benchmark queries across all 4 sources must match expected doc_ids in top 3."""
    res = search_heritage_docs(query, db_path=wave_e_index_db, limit=5, min_tier=3)
    assert res["matched"] is True, f"Expected match for {query!r}"
    top3_doc_ids = {item["doc_id"] for item in res["results"][:3]}
    assert top3_doc_ids & expected_doc_ids, f"Top-3 {top3_doc_ids} missed {expected_doc_ids} for {query!r}"


def test_golden_mopar_pin_sci_and_tier_hierarchy(wave_e_index_db: Path) -> None:
    """Verify lookup_60way_pin(14), lookup_sci_offset_hybrid, and Tier 1/2 > Tier 3 enforcement."""
    pin14 = lookup_60way_pin(14, db_path=wave_e_index_db)
    assert pin14["matched"] is True
    assert any("R31" in item["body_markdown"] for item in pin14["results"][:3])

    sci_res = lookup_sci_offset_hybrid("0xF2", db_path=wave_e_index_db)
    assert sci_res["matched"] is True
    assert sci_res["heritage_results"]

    for contested in ("50-way bulkhead connector pinout", "60-way SMEC SBEC Pin 11 Z12", "SCI TX Pin 25 Pin 51 D21"):
        res = search_heritage_docs(
            contested, db_path=wave_e_index_db, limit=5, min_tier=3, require_verified_for_pin_or_sci=True
        )
        assert res["matched"] is True
        assert res["results"][0]["trust_tier"] in {"Tier 1", "Tier 2"}
        assert all(item["trust_tier"] in {"Tier 1", "Tier 2"} for item in res["results"])


@pytest.mark.parametrize(
    ("kind", "query"),
    [
        ("search", "CAN-FD ISO 15765 OBD-II diagnostic arbitration"),
        ("search", "BMW Motronic DME M1.3 fuel table hex offset"),
        ("search", "Tesla Model 3 inverter IGBT gate driver"),
        ("search", "Subaru EJ257 AVCS cam timing table"),
        ("pin60", 44),
        ("sci", "0xZZ"),
    ],
)
def test_golden_mopar_trap_abstentions(wave_e_index_db: Path, kind: str, query: str | int) -> None:
    """Out-of-scope, unused 60-way cavity 44, and invalid SCI offset traps must strictly abstain."""
    if kind == "pin60":
        res = lookup_60way_pin(query, db_path=wave_e_index_db)
    elif kind == "sci":
        res = lookup_sci_offset_hybrid(str(query), db_path=wave_e_index_db)
    else:
        res = search_heritage_docs(str(query), db_path=wave_e_index_db, min_tier=3)
    assert res["matched"] is False
    assert NO_VERIFIED_SOURCE_MATCH in res["message"]
