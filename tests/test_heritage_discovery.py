"""Tests for Wave A heritage discovery, vault SHA-256 deduplication, and PII scrubbing."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse

import pytest
import yaml

from logicaide.heritage import discovery


def test_build_cdx_query_and_memento_urls() -> None:
    """Verify canonical Wayback CDX query parameters and raw id_ memento URLs."""
    url = discovery.build_cdx_query_url("squirrelpf.com/turbocalc/*", limit=25)
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == discovery.CDX_BASE_URL
    assert params["url"] == ["squirrelpf.com/turbocalc/*"]
    assert params["output"] == ["json"]
    assert params["fl"] == ["timestamp,original,mimetype,statuscode,digest"]
    assert params["filter"] == ["statuscode:200"]
    assert params["collapse"] == ["digest"]
    assert params["limit"] == ["25"]
    assert discovery.build_memento_url("20150102030405", "http://example.org/map.gif") == (
        "https://web.archive.org/web/20150102030405id_/http://example.org/map.gif"
    )
    assert discovery.build_memento_url("20150102030405", "http://example.org/map.gif", raw=False) == (
        "https://web.archive.org/web/20150102030405/http://example.org/map.gif"
    )


def test_vault_sha256_dedup_and_queue_omission(tmp_path: Path) -> None:
    """Verify manifest/raw SHA-256 matches mark leads already_covered and omit from queue."""
    manifest_sha = "a" * 64
    manifest = tmp_path / "manifest.md"
    manifest.write_text(f"- sample_doc `{manifest_sha}`\n", encoding="utf-8")

    raw_dir = tmp_path / "data" / "heritage_raw"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "captured_map.bin"
    raw_file.write_bytes(b"chrysler-sbec-bench-capture")
    file_sha = hashlib.sha256(raw_file.read_bytes()).hexdigest()
    assert discovery.collect_vault_sha256(manifest, raw_dir) == {manifest_sha, file_sha}

    qdir = tmp_path / "data" / "heritage_raw" / "discovery_queue"
    payload = discovery.run_discover(
        wave="A",
        private_root=tmp_path / "nonexistent_private",
        manifest_path=manifest,
        queue_dir=qdir,
        output_yaml=tmp_path / "knowledge" / "discovery_leads.yaml",
        fetch_cdx=False,
        raw_root=raw_dir,
        profiles_turbo_dir=None,
        extra_fingerprints=[
            {"fingerprint": "held-manifest", "sha256": manifest_sha, "query_pattern": "example.org/held1"},
            {"fingerprint": "held-raw", "sha256": file_sha, "query_pattern": "example.org/held2"},
            {"fingerprint": "missing-item", "sha256": "f" * 64, "query_pattern": "example.org/missing"},
        ],
    )
    by_fp = {lead["fingerprint"]: lead for lead in payload["leads"]}
    assert by_fp["held-manifest"]["already_in_vault"] is True and by_fp["held-manifest"]["status"] == "already_covered"
    assert by_fp["held-manifest"]["vault_dedup_sha256_matches"] == [manifest_sha]
    assert by_fp["held-raw"]["already_in_vault"] is True and by_fp["held-raw"]["status"] == "already_covered"
    assert by_fp["missing-item"]["already_in_vault"] is False and by_fp["missing-item"]["status"] == "queued_cdx"

    queue_doc = json.loads((qdir / "wave_a_queue.json").read_text(encoding="utf-8"))
    queued_fps = {item["fingerprint"] for item in queue_doc["queue"]}
    assert queue_doc["skipped_already_in_vault_count"] == 2
    assert "missing-item" in queued_fps
    assert "held-manifest" not in queued_fps and "held-raw" not in queued_fps


def test_fetch_cdx_rate_limit_and_digest_dedup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify polite 1.5s delay between CDX calls and global digest deduplication."""
    monkeypatch.setattr(discovery, "WAVE_A_SEED_SPECS", ())
    sleeps: list[float] = []
    monkeypatch.setattr(discovery.time, "sleep", lambda secs: sleeps.append(secs))

    responses = [
        [["timestamp", "original", "mimetype", "statuscode", "digest"], ["20180101000000", "http://example.org/a1", "text/html", "200", "DIGEST_SHARED"], ["20180102000000", "http://example.org/a2", "text/html", "200", "DIGEST_UNIQUE_A"]],
        [["timestamp", "original", "mimetype", "statuscode", "digest"], ["20180103000000", "http://example.org/b1", "text/html", "200", "DIGEST_SHARED"], ["20180104000000", "http://example.org/b2", "text/html", "200", "DIGEST_UNIQUE_B"]],
    ]

    class _FakeResp(io.BytesIO):
        def __enter__(self) -> "_FakeResp":
            return self
        def __exit__(self, *_: Any) -> None:
            self.close()

    calls = 0

    def _fake_urlopen(req: Any, timeout: float = 20.0) -> _FakeResp:
        nonlocal calls
        body = json.dumps(responses[calls]).encode("utf-8")
        calls += 1
        return _FakeResp(body)

    monkeypatch.setattr(discovery.urllib.request, "urlopen", _fake_urlopen)
    qdir = tmp_path / "discovery_queue"
    discovery.run_discover(
        wave="A", private_root=None, manifest_path=None, queue_dir=qdir,
        output_yaml=tmp_path / "leads.yaml", fetch_cdx=True, profiles_turbo_dir=None,
        extra_fingerprints=[
            {"fingerprint": "alpha", "query_pattern": "example.org/a*"},
            {"fingerprint": "beta", "query_pattern": "example.org/b*"},
        ],
    )
    assert sleeps == [discovery.POLITE_REQUEST_DELAY_S]
    queue_doc = json.loads((qdir / "wave_a_queue.json").read_text(encoding="utf-8"))
    assert [s["digest"] for s in queue_doc["queue"][0]["cdx_snapshots"]] == ["DIGEST_SHARED", "DIGEST_UNIQUE_A"]
    assert [s["digest"] for s in queue_doc["queue"][1]["cdx_snapshots"]] == ["DIGEST_UNIQUE_B"]


def test_missing_private_root_and_turbo_stub_key_compat(tmp_path: Path) -> None:
    """Verify legacy_map_path/figure_path/source_image_relpath compatibility without phantom duplicates."""
    turbo_dir = tmp_path / "profiles" / "turbo"
    specs = {
        "garrett_703457": {
            "turbo_id": "garrett_703457_2comp_e", "axis_calibration_pending": True, "company_name": "garrett",
            "legacy_map_path": "maps/garrett/703457-2comp_e.jpg",
            "figure_path": "data/heritage_raw/figures/turbo_maps/garrett/703457-2comp_e.jpg", "figure_sha256": "b" * 64,
        },
        "holset_hx40": {
            "turbo_id": "holset_hx40", "axis_calibration_pending": True,
            "source_image_relpath": "maps/holset/hx40.jpg", "source_image_sha256": "c" * 64,
        },
        "garrett_gt1241": {
            "turbo_id": "garrett_gt1241", "axis_calibration_pending": False,
            "legacy_map_path": "maps/garrett/GT1241.gif", "figure_sha256": "d" * 64,
        },
    }
    for sub, payload in specs.items():
        (turbo_dir / sub).mkdir(parents=True)
        (turbo_dir / sub / "turbo_spec.json").write_text(json.dumps(payload), encoding="utf-8")

    calibrated, stubs = discovery._load_turbo_profile_state(turbo_dir)
    assert calibrated == {"gt1241.gif"}
    assert [(s["brand"], s["basename"], s["sha256"]) for s in stubs] == [
        ("garrett", "703457-2comp_e.jpg", "b" * 64),
        ("holset", "hx40.jpg", "c" * 64),
    ]

    priv_maps = tmp_path / "priv" / "turbocalc v5 beta 4 - canvas" / "maps" / "garrett"
    priv_maps.mkdir(parents=True)
    (priv_maps / "703457-2comp_e.jpg").write_bytes(b"stub")
    (priv_maps / "GT1241.gif").write_bytes(b"cal")
    records = discovery._collect_uncalibrated_maps(tmp_path / "priv", calibrated, stubs)
    assert [r["fingerprint"] for r in records] == ["garrett/703457-2comp_e.jpg", "holset/hx40.jpg"]

    res = discovery.run_discover(
        wave="A", private_root=tmp_path / "absent_private", manifest_path=None,
        queue_dir=tmp_path / "q", output_yaml=tmp_path / "out.yaml", profiles_turbo_dir=turbo_dir,
    )
    assert len(res["leads"]) >= 2 + len(discovery.WAVE_A_SEED_SPECS)


def test_live_discovery_leads_and_pii_scrubbing() -> None:
    """Validate scrub_pii and live knowledge/discovery_leads.yaml schema and cleanliness."""
    scrubbed = discovery.scrub_pii(r"/home/demo/work C:\Users\demo\doc.txt contact@example.org")
    assert "/home/" not in scrubbed and "C:\\Users\\" not in scrubbed and "contact@example.org" not in scrubbed

    raw_text = Path("knowledge/discovery_leads.yaml").read_text(encoding="utf-8")
    assert not re.search(r"(/home/|/Users/|[A-Za-z]:\\Users\\|\b\S+@\S+\.\S+\b)", raw_text)
    doc = yaml.safe_load(raw_text)
    assert doc["schema_version"] == "1.0"
    assert doc["wave"] == "A"
    assert doc["vault_sha256_count"] >= 157
    leads = doc["leads"]
    assert len(leads) == 27
    pairs = [(lead["category"], lead["fingerprint"]) for lead in leads]
    assert len(pairs) == len(set(pairs))


def test_wave_traversal_and_invalid_memento_rejected(tmp_path: Path) -> None:
    """Reject path-traversal wave identifiers and malformed Wayback memento arguments."""
    with pytest.raises(ValueError, match="Invalid wave identifier"):
        discovery.run_discover(wave="../../outside", private_root=None, manifest_path=None, queue_dir=tmp_path / "q", output_yaml=tmp_path / "out.yaml", profiles_turbo_dir=None)
    with pytest.raises(ValueError, match="Invalid Wayback memento timestamp"):
        discovery.build_memento_url("not-a-ts", "http://example.org/a")
    with pytest.raises(ValueError, match="Invalid memento target URL scheme"):
        discovery.build_memento_url("20200101000000", "javascript:alert(1)")
