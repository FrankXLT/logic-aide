"""Tests for Wave D heritage discovery, exclusions, SHA-256 vault dedup, and CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from click.testing import CliRunner
import pytest
import yaml

from logicaide.cli import main
from logicaide.heritage import bom_extractor, discovery, discovery_wave_d, doc_curator

REPO_ROOT = Path(__file__).resolve().parents[1]

_PII_SCAN_RE = re.compile(
    r"(/" + r"home/|/" + r"Users/|[A-Za-z]:\\" + r"Users\\|\b\S+@\S+\.\S+\b"
    r"|bu" + r"car|st" + r"eve|mc" + r"clurg|Fl" + r"ash\.frm|/dev/" + r"ttyUSB|ser" + r"ial\.Serial)",
    re.IGNORECASE,
)
_ALLOWED_CLEANROOM_DESTS = frozenset({
    "knowledge/wiring/",
    "knowledge/drivetrain/",
    "knowledge/hardware/",
    "knowledge/ecu/",
    "scripts/",
    "data/heritage_raw/discovery_queue/",
})


def test_committed_wave_d_leads_yaml_is_valid_and_cleanroom() -> None:
    """Validate committed knowledge/discovery_leads_wave_d.yaml structure, line cap, and hygiene."""
    leads_path = REPO_ROOT / "knowledge" / "discovery_leads_wave_d.yaml"
    assert leads_path.is_file()
    yaml_text = leads_path.read_text(encoding="utf-8")
    assert len(yaml_text.splitlines()) <= 500
    assert not _PII_SCAN_RE.search(yaml_text)
    bom_extractor.validate_cleanroom_payload(yaml_text, label=str(leads_path))
    doc_curator.assert_no_deadbolt_tokens(yaml_text, label=str(leads_path))

    doc = yaml.safe_load(yaml_text)
    assert doc["schema_version"] == "1.0"
    assert doc["wave"] == "D"
    leads = doc["leads"]
    assert len(leads) == len(discovery_wave_d.WAVE_D_CDX_SPECS) + len(discovery_wave_d.WAVE_D_FORGE_SPECS)
    assert len({lead["lead_id"] for lead in leads}) == len(leads)
    assert {"wayback_cdx", "code_forge"} <= {lead["source_kind"] for lead in leads}
    assert all(lead["target_destination"] in _ALLOWED_CLEANROOM_DESTS for lead in leads)


def test_wave_d_excludes_and_quarantine_never_hashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify excluded PDFs and QUARANTINE_PII_DO_NOT_INGEST files are never hashed or included."""
    priv_dir = tmp_path / "private_references"
    priv_dir.mkdir()
    for excluded_name in ("GUFB.pdf", "9101.pdf", "Imprezawiringdiagram.pdf", "SCR-Drift-02-2003.pdf"):
        (priv_dir / excluded_name).write_bytes(b"%PDF-1.4 excluded")
    (priv_dir / "SHO").mkdir()
    (priv_dir / "SHO" / "LM2907N.pdf").write_bytes(b"%PDF-1.4 sho excluded")
    (priv_dir / "QUARANTINE_PII_DO_NOT_INGEST").mkdir()
    (priv_dir / "QUARANTINE_PII_DO_NOT_INGEST" / "88 T2.pdf").write_bytes(b"%PDF-1.4 quarantined")
    (priv_dir / "QUARANTINE_PII_DO_NOT_INGEST" / "notes.txt").write_text("DRB-II Code 11-55", encoding="utf-8")
    (priv_dir / "PortMon").mkdir()
    (priv_dir / "PortMon" / "sci.txt").write_text("D20 D21", encoding="utf-8")

    clean_pdf = priv_dir / "ChEM2.pdf"
    clean_pdf.write_bytes(b"%PDF-1.4 clean ChEM2 fixture")
    clean_sha = hashlib.sha256(clean_pdf.read_bytes()).hexdigest()

    hashed_paths: list[Path] = []
    real_sha256 = discovery._sha256_file

    def _spy_sha256(path: Path) -> str:
        hashed_paths.append(path.resolve())
        return real_sha256(path)

    monkeypatch.setattr(discovery, "_sha256_file", _spy_sha256)

    qdir = tmp_path / "data" / "heritage_raw" / "discovery_queue"
    out_yaml = tmp_path / "wave_d.yaml"
    payload = discovery.run_discover(
        wave="D",
        private_root=priv_dir,
        manifest_path=None,
        queue_dir=qdir,
        output_yaml=out_yaml,
        fetch_cdx=False,
        rom_root=None,
    )
    hashed_names = {p.name for p in hashed_paths}
    assert "ChEM2.pdf" in hashed_names
    assert {"GUFB.pdf", "9101.pdf", "Imprezawiringdiagram.pdf", "SCR-Drift-02-2003.pdf", "88 T2.pdf", "LM2907N.pdf"}.isdisjoint(hashed_names)
    assert all("QUARANTINE_PII_DO_NOT_INGEST" not in p.parts for p in hashed_paths)

    by_fp = {lead["fingerprint"]: lead for lead in payload["leads"]}
    assert "Confirmed in read-only corpus scan." not in by_fp["88 T2.pdf"]["rationale"]
    assert "Confirmed in read-only corpus scan." not in by_fp["LM2907N"]["rationale"]
    assert "Confirmed in read-only corpus scan." in by_fp["ChEM2.pdf"]["rationale"]

    raw_records = {r["fingerprint"]: r for r in discovery_wave_d.extract_wave_d_fingerprints(priv_dir, None)}
    assert raw_records["ChEM2.pdf"]["local_sha256"] == clean_sha
    assert raw_records["88 T2.pdf"]["local_sha256"] == ""
    assert raw_records["LM2907N"]["local_sha256"] == ""


def test_wave_d_dedup_targets_forges_pii_and_missing_root(tmp_path: Path) -> None:
    """Verify SHA-256 dedup, cleanroom destinations, CDX+forge leads, PII scrubbing, and missing root fallback."""
    priv_dir = tmp_path / "private_references"
    priv_dir.mkdir()
    t2_pdf = priv_dir / "88 T2.pdf"
    t2_bytes = b"%PDF-1.4 1988 Turbo II SMEC cleanroom fixture"
    t2_pdf.write_bytes(t2_bytes)
    t2_sha = hashlib.sha256(t2_bytes).hexdigest()

    raw_dir = tmp_path / "data" / "heritage_raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "88 T2.pdf").write_bytes(t2_bytes)

    manifest_sha = "e" * 64
    manifest = tmp_path / "_INDEX_AND_ROUTING_MANIFEST.md"
    manifest.write_text(f"- verified Wave D fixture `{manifest_sha}`\n", encoding="utf-8")

    sample_user = "sample" + "user"
    sample_email = "dev" + "@" + "example.org"
    win_path = "C:\\" + "Users\\" + sample_user + "\\docs"
    posix_path = "/" + "home/" + sample_user + "/docs"

    qdir = raw_dir / "discovery_queue"
    out_yaml = tmp_path / "wave_d.yaml"
    payload = discovery.run_discover(
        wave="D",
        private_root=priv_dir,
        manifest_path=manifest,
        queue_dir=qdir,
        output_yaml=out_yaml,
        fetch_cdx=False,
        raw_root=raw_dir,
        rom_root=None,
        extra_fingerprints=[
            {"fingerprint": "manifest-held-pdf", "sha256": manifest_sha, "query_pattern": "archive.invalid/a.pdf"},
            {
                "fingerprint": "pii-scrub-check",
                "query_pattern": f"{win_path}\\doc.pdf",
                "rationale": f"Collected from {posix_path} via {sample_email} on \\\\workstation\\share",
            },
        ],
    )
    by_fp: dict[str, dict[str, Any]] = {lead["fingerprint"]: lead for lead in payload["leads"]}
    for fp, expected_sha in (("88 T2.pdf", t2_sha), ("manifest-held-pdf", manifest_sha)):
        assert by_fp[fp]["already_in_vault"] is True
        assert by_fp[fp]["status"] == "already_covered"
        assert by_fp[fp]["vault_dedup_sha256_matches"] == [expected_sha]

    assert all(lead["target_destination"] in _ALLOWED_CLEANROOM_DESTS for lead in payload["leads"])
    forge_leads = [lead for lead in payload["leads"] if lead["source_kind"] == "code_forge"]
    cdx_leads = [lead for lead in payload["leads"] if lead["source_kind"] == "wayback_cdx"]
    assert len(cdx_leads) >= len(discovery_wave_d.WAVE_D_CDX_SPECS)
    assert {lead["forge"] for lead in forge_leads} == {"github", "gitlab", "sourceforge"}
    assert all(lead["code_forge_query_url"].startswith("https://") for lead in forge_leads)

    qdoc = json.loads((qdir / "wave_d_queue.json").read_text(encoding="utf-8"))
    assert qdoc["wave"] == "D"
    assert qdoc["skipped_already_in_vault_count"] == 2
    queued_fps = {item["fingerprint"] for item in qdoc["queue"]}
    assert {"88 T2.pdf", "manifest-held-pdf"}.isdisjoint(queued_fps)

    combined = out_yaml.read_text(encoding="utf-8") + (qdir / "wave_d_queue.json").read_text(encoding="utf-8")
    assert not _PII_SCAN_RE.search(combined)
    assert sample_user not in combined and "example.org" not in combined and "workstation" not in combined
    doc_curator.assert_no_deadbolt_tokens(combined, label="wave_d_outputs")

    # Graceful fallback when private_root does not exist on disk.
    missing_yaml = tmp_path / "missing_wave_d.yaml"
    missing_qdir = tmp_path / "missing_queue"
    fallback = discovery.run_discover(
        wave="D",
        private_root=tmp_path / "nonexistent_private_references",
        manifest_path=tmp_path / "nonexistent_manifest.md",
        queue_dir=missing_qdir,
        output_yaml=missing_yaml,
        fetch_cdx=False,
        rom_root=None,
    )
    assert fallback["wave"] == "D"
    assert len(fallback["leads"]) == len(discovery_wave_d.WAVE_D_CDX_SPECS) + len(discovery_wave_d.WAVE_D_FORGE_SPECS)
    assert (missing_qdir / "wave_d_queue.json").is_file()


def test_wave_d_cli_discover_into_tmp_path(tmp_path: Path) -> None:
    """Verify `logicaide heritage discover --wave D` CLI writes YAML and queue JSON cleanly."""
    priv_dir = tmp_path / "private_references"
    priv_dir.mkdir()
    (priv_dir / "LM2907N.pdf").write_bytes(b"%PDF-1.4 LM2907N")
    qdir = tmp_path / "queue"
    out_yaml = tmp_path / "cli_wave_d.yaml"

    result = CliRunner().invoke(
        main,
        [
            "heritage",
            "discover",
            "--wave",
            "D",
            "--private-root",
            str(priv_dir),
            "--queue-dir",
            str(qdir),
            "--output-yaml",
            str(out_yaml),
            "--no-fetch-cdx",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_yaml.is_file()
    assert (qdir / "wave_d_queue.json").is_file()
    doc = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))
    assert doc["wave"] == "D"
    by_fp = {lead["fingerprint"]: lead for lead in doc["leads"]}
    assert "Confirmed in read-only corpus scan." in by_fp["LM2907N"]["rationale"]
