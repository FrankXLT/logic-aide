"""Tests for Wave B heritage discovery, code-forge queries, and ROM/manifest SHA-256 dedup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

import pytest
import yaml

from logicaide.heritage import discovery, discovery_wave_b

REQUIRED_WAVE_B_FINGERPRINTS = (
    "DRBFunctionTable", "DRBMemoryTable", "FastDataOutputAddressTable", "XmitRequestedRamLocation",
    "SetupHiSpeedDataTransfer", "PEFTBL_PumpingEfficiency", "ChemAsmSMEC", "ChemAsmLM",
    "zChEM", "Blueberry60", "Ladybug60", "D-Cal_120", "tbl2calx", "DuaLogger.prc",
)


def test_wave_b_run_discover_yaml_queue_and_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit concise Wave B YAML <= 500 lines, wave_b_queue.json, and default output path."""
    out_yaml, qdir = tmp_path / "discovery_leads_wave_b.yaml", tmp_path / "queue"
    payload = discovery.run_discover(
        wave="B", private_root=None, manifest_path=None,
        queue_dir=qdir, output_yaml=out_yaml, fetch_cdx=False, rom_root=None,
    )
    yaml_text = out_yaml.read_text(encoding="utf-8")
    assert len(yaml_text.splitlines()) <= 500
    doc = yaml.safe_load(yaml_text)
    assert doc["schema_version"] == "1.0" and doc["wave"] == "B"
    assert len(payload["leads"]) == len(doc["leads"]) >= 18
    pairs = [(lead["category"], lead["fingerprint"]) for lead in doc["leads"]]
    assert len(pairs) == len(set(pairs))

    qdoc = json.loads((qdir / "wave_b_queue.json").read_text(encoding="utf-8"))
    assert qdoc["wave"] == "B" and qdoc["fetch_cdx"] is False and qdoc["queued_count"] == len(doc["leads"])

    monkeypatch.chdir(tmp_path)
    discovery.run_discover(
        wave="B", private_root=None, manifest_path=None,
        queue_dir=tmp_path / "q_default", fetch_cdx=False, rom_root=None,
    )
    assert (tmp_path / "knowledge" / "discovery_leads_wave_b.yaml").is_file()


def test_wave_b_required_fingerprints_and_archival_cdx_specs() -> None:
    """Verify all 14 required Wave B fingerprints and 6 CDX domain patterns are covered."""
    spec_fps = {s[1] for s in discovery_wave_b.WAVE_B_CDX_SPECS} | {s[1] for s in discovery_wave_b.WAVE_B_FORGE_SPECS}
    records = discovery_wave_b.extract_wave_b_fingerprints(private_root=None, manifest_path=None)
    emitted_fps = {rec["fingerprint"] for rec in records}
    emitted_patterns = {rec["query_pattern"] for rec in records}

    for token in REQUIRED_WAVE_B_FINGERPRINTS:
        assert token in spec_fps and token in emitted_fps

    for pattern in (
        "boostbutton.com/*", "mptune.com/*", "thedodgegarage.com/*ecu*",
        "minimopar.net/*sbec*", "chryslerccdsci.wordpress.com/*", "turbododge.com/*disassembly*",
    ):
        assert pattern in emitted_patterns

    for toolchain in ("as11", "blackfoot", "m68hc11", "Ghidra SLEIGH 68HC11 / 6803"):
        assert toolchain in emitted_fps


def test_wave_b_code_forge_queries_skip_cdx_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify code-forge leads carry valid URLs, cdx_query_url=None, and never call CDX."""
    for forge in ("github", "gitlab", "sourceforge"):
        url = discovery_wave_b.build_code_forge_query_url(forge, "68HC11 as11")
        assert url.startswith("https://") and "68HC11" in url
    with pytest.raises(ValueError, match="Unsupported code forge"):
        discovery_wave_b.build_code_forge_query_url("bitbucket", "68hc11")
    with pytest.raises(ValueError, match="non-empty"):
        discovery_wave_b.build_code_forge_query_url("github", "   ")

    cdx_calls: list[str] = []
    monkeypatch.setattr(discovery, "fetch_cdx_snapshots", lambda url: cdx_calls.append(url) or [])
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)

    qdir = tmp_path / "queue"
    payload = discovery.run_discover(
        wave="B", private_root=None, manifest_path=None,
        queue_dir=qdir, output_yaml=tmp_path / "wave_b.yaml", fetch_cdx=True, rom_root=None,
    )
    forge_leads = [lead for lead in payload["leads"] if lead["source_kind"] == "code_forge"]
    cdx_leads = [lead for lead in payload["leads"] if lead["source_kind"] == "wayback_cdx"]
    assert {lead["forge"] for lead in forge_leads} == {"github", "gitlab", "sourceforge"}
    for lead in forge_leads:
        assert lead["code_forge_query_url"].startswith("https://")
        assert lead["cdx_query_url"] is None and lead["status"] == "queued_code_forge"

    assert len(cdx_calls) == len({lead["cdx_query_url"] for lead in cdx_leads})
    assert all(url is not None and url.startswith(discovery.CDX_BASE_URL) for url in cdx_calls)

    qdoc = json.loads((qdir / "wave_b_queue.json").read_text(encoding="utf-8"))
    queued_forge = [item for item in qdoc["queue"] if item.get("source_kind") == "code_forge"]
    assert len(queued_forge) == len(forge_leads)
    assert all(item["cdx_query_url"] is None and "cdx_snapshots" not in item for item in queued_forge)


def test_wave_b_cdx_query_params_and_vault_dedup(tmp_path: Path) -> None:
    """Verify CDX fl/collapse params, id_ memento template, and manifest + rom/ JSON SHA-256 dedup."""
    manifest_sha, rom_sha = "a" * 64, "b" * 64
    manifest = tmp_path / "_INDEX_AND_ROUTING_MANIFEST.md"
    manifest.write_text(f"- verified baseline `{manifest_sha}`\n", encoding="utf-8")

    rom_dir = tmp_path / "rom" / "sbec"
    rom_dir.mkdir(parents=True)
    (rom_dir / "rom_manifest.json").write_text(json.dumps({"baseline_sha256": rom_sha}), encoding="utf-8")

    priv_dir = tmp_path / "private_references"
    priv_dir.mkdir()
    asm_file = priv_dir / "Blueberry60.asm"
    asm_file.write_text("; Blueberry60 LM source\n", encoding="utf-8")
    asm_sha = hashlib.sha256(asm_file.read_bytes()).hexdigest()

    raw_dir = tmp_path / "data" / "heritage_raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "Blueberry60.asm").write_bytes(asm_file.read_bytes())

    qdir = raw_dir / "discovery_queue"
    payload = discovery.run_discover(
        wave="B", private_root=priv_dir, manifest_path=manifest, queue_dir=qdir,
        output_yaml=tmp_path / "wave_b.yaml", fetch_cdx=False, raw_root=raw_dir, rom_root=tmp_path / "rom",
        extra_fingerprints=[
            {"fingerprint": "manifest-held-asm", "sha256": manifest_sha, "query_pattern": "example.org/m.asm"},
            {"fingerprint": "rom-held-bin", "sha256": rom_sha, "query_pattern": "example.org/r.bin"},
        ],
    )
    by_fp: dict[str, dict[str, Any]] = {lead["fingerprint"]: lead for lead in payload["leads"]}

    cdx_sample = by_fp["ChemAsmLM"]
    assert cdx_sample["source_kind"] == "wayback_cdx"
    assert cdx_sample["sample_memento_url_template"] == "https://web.archive.org/web/{timestamp}id_/{original}"
    params = urllib.parse.parse_qs(urllib.parse.urlparse(cdx_sample["cdx_query_url"]).query)
    assert params["collapse"] == ["digest"]
    assert {"original", "mimetype", "timestamp", "digest"}.issubset(set(params["fl"][0].split(",")))

    for fp, expected_sha in (("Blueberry60", asm_sha), ("manifest-held-asm", manifest_sha), ("rom-held-bin", rom_sha)):
        assert by_fp[fp]["already_in_vault"] is True
        assert by_fp[fp]["status"] == "already_covered"
        assert by_fp[fp]["vault_dedup_sha256_matches"] == [expected_sha]

    qdoc = json.loads((qdir / "wave_b_queue.json").read_text(encoding="utf-8"))
    queued_fps = {item["fingerprint"] for item in qdoc["queue"]}
    assert qdoc["skipped_already_in_vault_count"] == 3
    assert {"Blueberry60", "manifest-held-asm", "rom-held-bin"}.isdisjoint(queued_fps)


def test_wave_b_pii_and_deadbolt_scrubbing(tmp_path: Path) -> None:
    """Verify Windows/Linux paths, emails, and contributor/deadbolt tokens never leak."""
    scrubbed_url = discovery_wave_b.build_code_forge_query_url(
        "github", r"68hc11 C:\Users\sampleuser\src /home/sampleuser/src dev@example.org"
    )
    assert "sampleuser" not in scrubbed_url and "example.org" not in scrubbed_url

    out_yaml, qdir = tmp_path / "wave_b.yaml", tmp_path / "queue"
    discovery.run_discover(
        wave="B", private_root=None, manifest_path=None,
        queue_dir=qdir, output_yaml=out_yaml, fetch_cdx=False, rom_root=None,
        extra_fingerprints=[{
            "fingerprint": "extra-clean",
            "query_pattern": r"C:\Users\sampleuser\asm\test.asm",
            "rationale": r"From /home/sampleuser/repo by dev@example.org on \\workstation\share",
        }],
    )
    combined = out_yaml.read_text(encoding="utf-8") + (qdir / "wave_b_queue.json").read_text(encoding="utf-8")
    assert not re.search(
        r"(/home/|/Users/|[A-Za-z]:\\Users\\|\b\S+@\S+\.\S+\b|bucar|steve|mcclurg|Flash\.frm|/dev/ttyUSB|serial\.Serial)",
        combined, re.IGNORECASE,
    )
