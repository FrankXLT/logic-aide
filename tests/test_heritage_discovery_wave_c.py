"""Tests for Wave C heritage discovery, hardware/forge leads, quarantine, and vault dedup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

import pytest
import yaml

from logicaide.heritage import bom_extractor, discovery, discovery_wave_c

REPO_ROOT = Path(__file__).resolve().parents[1]

_PII_SCAN_RE = re.compile(
    r"(/" + r"home/|/" + r"Users/|[A-Za-z]:\\" + r"Users\\|\b\S+@\S+\.\S+\b"
    r"|bu" + r"car|st" + r"eve|mc" + r"clurg|Fl" + r"ash\.frm|/dev/" + r"ttyUSB|ser" + r"ial\.Serial)",
    re.IGNORECASE,
)


def test_committed_wave_c_leads_yaml_is_complete_and_cleanroom() -> None:
    """Validate committed knowledge/discovery_leads_wave_c.yaml line cap, leads, and hygiene."""
    leads_path = REPO_ROOT / "knowledge" / "discovery_leads_wave_c.yaml"
    yaml_text = leads_path.read_text(encoding="utf-8")
    assert len(yaml_text.splitlines()) <= 500
    assert not _PII_SCAN_RE.search(yaml_text)
    bom_extractor.validate_cleanroom_payload(yaml_text, label=str(leads_path))

    doc = yaml.safe_load(yaml_text)
    assert doc["schema_version"] == "1.0" and doc["wave"] == "C"
    leads = doc["leads"]
    assert len(leads) == len(discovery_wave_c.WAVE_C_CDX_SPECS) + len(discovery_wave_c.WAVE_C_FORGE_SPECS)

    pairs = [(lead["category"], lead["fingerprint"]) for lead in leads]
    lead_ids = [lead["lead_id"] for lead in leads]
    assert len(pairs) == len(set(pairs)) and len(lead_ids) == len(set(lead_ids))

    fps = {lead["fingerprint"] for lead in leads}
    for required_fp in discovery_wave_c.WAVE_C_REQUIRED_FINGERPRINTS:
        assert required_fp in fps
    assert {"mpscandroid", "Chrysler SCI hardware"}.issubset(fps)


def test_wave_c_run_discover_yaml_queue_and_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit concise Wave C YAML <= 500 lines, wave_c_queue.json, and default output path."""
    out_yaml, qdir = tmp_path / "discovery_leads_wave_c.yaml", tmp_path / "queue"
    payload = discovery.run_discover(
        wave="C",
        private_root=None,
        manifest_path=None,
        queue_dir=qdir,
        output_yaml=out_yaml,
        fetch_cdx=False,
        rom_root=None,
    )
    yaml_text = out_yaml.read_text(encoding="utf-8")
    assert len(yaml_text.splitlines()) <= 500
    doc = yaml.safe_load(yaml_text)
    assert doc["schema_version"] == "1.0" and doc["wave"] == "C"
    assert len(payload["leads"]) == len(doc["leads"]) >= 21

    qdoc = json.loads((qdir / "wave_c_queue.json").read_text(encoding="utf-8"))
    assert qdoc["wave"] == "C" and qdoc["fetch_cdx"] is False and qdoc["queued_count"] == len(doc["leads"])

    monkeypatch.chdir(tmp_path)
    discovery.run_discover(
        wave="C",
        private_root=None,
        manifest_path=None,
        queue_dir=tmp_path / "q_default",
        fetch_cdx=False,
        rom_root=None,
    )
    assert (tmp_path / "knowledge" / "discovery_leads_wave_c.yaml").is_file()


def test_wave_c_code_forge_queries_skip_cdx_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Wave C code-forge leads cover all 3 forges and never invoke CDX fetch."""
    for forge in ("github", "gitlab", "sourceforge"):
        url = discovery_wave_c.build_code_forge_query_url(forge, "DIS_Adaptor PIC16F690")
        assert url.startswith("https://") and "DIS_Adaptor" in url

    cdx_calls: list[str] = []
    monkeypatch.setattr(discovery, "fetch_cdx_snapshots", lambda url: cdx_calls.append(url) or [])
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)

    qdir = tmp_path / "queue"
    payload = discovery.run_discover(
        wave="C",
        private_root=None,
        manifest_path=None,
        queue_dir=qdir,
        output_yaml=tmp_path / "wave_c.yaml",
        fetch_cdx=True,
        rom_root=None,
    )
    forge_leads = [lead for lead in payload["leads"] if lead["source_kind"] == "code_forge"]
    cdx_leads = [lead for lead in payload["leads"] if lead["source_kind"] == "wayback_cdx"]
    assert {lead["forge"] for lead in forge_leads} == {"github", "gitlab", "sourceforge"}
    for lead in forge_leads:
        assert lead["code_forge_query_url"].startswith("https://")
        assert lead["cdx_query_url"] is None and lead["status"] == "queued_code_forge"

    assert len(cdx_calls) == len({lead["cdx_query_url"] for lead in cdx_leads})
    assert all(url.startswith(discovery.CDX_BASE_URL) for url in cdx_calls)

    qdoc = json.loads((qdir / "wave_c_queue.json").read_text(encoding="utf-8"))
    queued_forge = [item for item in qdoc["queue"] if item.get("source_kind") == "code_forge"]
    assert len(queued_forge) == len(forge_leads)
    assert all(item["cdx_query_url"] is None and "cdx_snapshots" not in item for item in queued_forge)


def test_wave_c_quarantine_skip_and_vault_sha256_dedup(tmp_path: Path) -> None:
    """Verify quarantined files are ignored while clean corpus + manifest + rom SHA-256 dedup works."""
    manifest_sha, rom_sha = "c" * 64, "d" * 64
    manifest = tmp_path / "_INDEX_AND_ROUTING_MANIFEST.md"
    manifest.write_text(f"- verified Wave C baseline `{manifest_sha}`\n", encoding="utf-8")

    rom_dir = tmp_path / "rom" / "smec"
    rom_dir.mkdir(parents=True)
    (rom_dir / "rom_manifest.json").write_text(json.dumps({"baseline_sha256": rom_sha}), encoding="utf-8")

    priv_dir = tmp_path / "private_references"
    (priv_dir / "Knock_Board").mkdir(parents=True)
    kb_file = priv_dir / "Knock_Board" / "Knock_Board.pde"
    kb_file.write_text("// Knock_Board conditioning cleanroom fixture\n", encoding="utf-8")
    kb_sha = hashlib.sha256(kb_file.read_bytes()).hexdigest()

    # Quarantined directories & filenames must be skipped completely during corpus scan.
    (priv_dir / "page_files").mkdir()
    (priv_dir / "page_files" / "hidden.htm").write_text("26-pin digital dash", encoding="utf-8")
    (priv_dir / "QUARANTINE_PII_DO_NOT_INGEST").mkdir()
    (priv_dir / "QUARANTINE_PII_DO_NOT_INGEST" / "notes.htm").write_text("2275 EGT", encoding="utf-8")
    (priv_dir / "PortMon").mkdir()
    (priv_dir / "PortMon" / "sci.log").write_text("run6.dlg", encoding="utf-8")
    (priv_dir / ("Fl" + "ash.frm")).write_text("ECC DOHC -12AN 0.098", encoding="utf-8")
    (priv_dir / "reg code.txt").write_text("26-pin digital dash", encoding="utf-8")

    raw_dir = tmp_path / "data" / "heritage_raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "Knock_Board.pde").write_bytes(kb_file.read_bytes())

    raw_records = {r["fingerprint"]: r for r in discovery_wave_c.extract_wave_c_fingerprints(priv_dir, manifest)}
    assert raw_records["26-pin digital dash"]["local_sha256"] == ""
    assert raw_records["2275 EGT"]["local_sha256"] == ""
    assert raw_records["ECC DOHC -12AN 0.098"]["local_sha256"] == ""
    assert raw_records["Knock_Board"]["local_sha256"] == kb_sha

    qdir = raw_dir / "discovery_queue"
    payload = discovery.run_discover(
        wave="C",
        private_root=priv_dir,
        manifest_path=manifest,
        queue_dir=qdir,
        output_yaml=tmp_path / "wave_c.yaml",
        fetch_cdx=False,
        raw_root=raw_dir,
        rom_root=tmp_path / "rom",
        extra_fingerprints=[
            {"fingerprint": "manifest-held-bom", "sha256": manifest_sha, "query_pattern": "example.org/m.htm"},
            {"fingerprint": "rom-held-fw", "sha256": rom_sha, "query_pattern": "example.org/r.asm"},
        ],
    )
    by_fp: dict[str, dict[str, Any]] = {lead["fingerprint"]: lead for lead in payload["leads"]}

    assert "Confirmed in read-only corpus scan." not in by_fp["26-pin digital dash"]["rationale"]
    assert "Confirmed in read-only corpus scan." not in by_fp["2275 EGT"]["rationale"]
    assert "Confirmed in read-only corpus scan." not in by_fp["ECC DOHC -12AN 0.098"]["rationale"]
    assert "Confirmed in read-only corpus scan." in by_fp["Knock_Board"]["rationale"]

    cdx_sample = by_fp["4N25"]
    params = urllib.parse.parse_qs(urllib.parse.urlparse(cdx_sample["cdx_query_url"]).query)
    assert params["collapse"] == ["digest"]
    assert {"original", "mimetype", "timestamp", "digest"}.issubset(set(params["fl"][0].split(",")))

    for fp, expected_sha in (("Knock_Board", kb_sha), ("manifest-held-bom", manifest_sha), ("rom-held-fw", rom_sha)):
        assert by_fp[fp]["already_in_vault"] is True
        assert by_fp[fp]["status"] == "already_covered"
        assert by_fp[fp]["vault_dedup_sha256_matches"] == [expected_sha]

    qdoc = json.loads((qdir / "wave_c_queue.json").read_text(encoding="utf-8"))
    queued_fps = {item["fingerprint"] for item in qdoc["queue"]}
    assert qdoc["skipped_already_in_vault_count"] == 3
    assert {"Knock_Board", "manifest-held-bom", "rom-held-fw"}.isdisjoint(queued_fps)


def test_wave_c_pii_and_deadbolt_scrubbing(tmp_path: Path) -> None:
    """Verify Windows/Linux paths, emails, UNC hosts, and Deadbolt tokens never leak."""
    sample_user = "sample" + "user"
    sample_email = "dev" + "@" + "example.org"
    win_path = "C:\\" + "Users\\" + sample_user + "\\src"
    posix_path = "/" + "home/" + sample_user + "/src"

    scrubbed_url = discovery_wave_c.build_code_forge_query_url(
        "github", f"DIS_Adaptor {win_path} {posix_path} {sample_email}"
    )
    assert sample_user not in scrubbed_url and "example.org" not in scrubbed_url

    out_yaml, qdir = tmp_path / "wave_c.yaml", tmp_path / "queue"
    discovery.run_discover(
        wave="C",
        private_root=None,
        manifest_path=None,
        queue_dir=qdir,
        output_yaml=out_yaml,
        fetch_cdx=False,
        rom_root=None,
        extra_fingerprints=[{
            "fingerprint": "extra-clean-wave-c",
            "query_pattern": win_path + "\\bom.htm",
            "rationale": f"From {posix_path} by {sample_email} on \\\\workstation\\share",
        }],
    )
    combined = out_yaml.read_text(encoding="utf-8") + (qdir / "wave_c_queue.json").read_text(encoding="utf-8")
    assert not _PII_SCAN_RE.search(combined)
    assert "workstation" not in combined
