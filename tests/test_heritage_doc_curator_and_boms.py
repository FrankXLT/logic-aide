"""Tests for Wave C cleanroom BOM extraction, doc curation, and quarantine/Deadbolt gates."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from logicaide.heritage import bom_extractor, doc_curator

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_curate_cli_module() -> Any:
    """Load scripts/curate_heritage_web_docs.py cleanly without mutating sys.path."""
    script_path = REPO_ROOT / "scripts" / "curate_heritage_web_docs.py"
    spec = importlib.util.spec_from_file_location("curate_heritage_web_docs", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_synthetic_private_refs(root: Path) -> Path:
    """Create a minimal cleanroom synthetic private_references tree under tmp_path."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "eeprom.htm").write_text(
        "<html><body><table id='customerInfo'><tr><td>Order Header</td></tr></table>"
        "<table id='CartGrid'><tr><th>Line</th><th>Product Detail</th><th>Desc</th><th>Order Qty</th></tr>"
        "<tr><td>1</td><td>Mouser #: 804-27SF0207CPHE Mfr. #: SST27SF020-70-3C-PHE "
        "Desc.: Memory - MTP 256K X 8 70ns</td><td>Mem</td><td>5</td></tr>"
        "<tr><td>2</td><td>Mouser #: 526-NTE74LS193 Mfr. #: NTE74LS193 "
        "Desc.: SYNC BIN CNTR DIP-16</td><td>IC</td><td>5</td></tr></table></body></html>",
        encoding="utf-8",
    )
    (root / "Mouser1.htm").write_text(
        "<html><body><table class='gridItems'><tr><th>No</th><th>Product Detail</th><th>Stock</th><th>Qty</th></tr>"
        "<tr><td>1</td><td>Mouser #: 78-4N25 Mfr. #: 4N25 Desc.: Optocouplers Phototransistor Out</td>"
        "<td>Yes</td><td>10</td></tr></table></body></html>",
        encoding="utf-8",
    )
    (root / "Mouser2.htm").write_text(
        "<html><body><table id='gridItems'><tr><th>No</th><th>Product Detail</th><th>Stock</th><th>Qty</th></tr>"
        "<tr><td>1</td><td>Mouser #: 78-1N4733A Mfr. #: 1N4733A-TR Desc.: Zener Diodes 5.1V 1W</td>"
        "<td>Yes</td><td>10</td></tr></table></body></html>",
        encoding="utf-8",
    )
    kb_dir = root / "Knock_Board" / "Knock_Board"
    kb_dir.mkdir(parents=True)
    (kb_dir / "Knock_Board.pde").write_text(
        "#include <SoftwareSerial.h>\nconst int freq_volt = 66;\nconst float volt_lsb = 0.0000762939;\n"
        "const int ignEvent = 2;\nconst int knockDiscrete = 3;\nconst int clearKnock = 4;\n"
        "const int LEDPin = 13;\nconst int rpmPin = 0;\nconst int knockPin = 1;\n"
        "SoftwareSerial lcd = SoftwareSerial(5, 6);\n",
        encoding="utf-8",
    )
    dis_dir = root / "DIS_Adaptor"
    dis_dir.mkdir(parents=True)
    (dis_dir / "parts.rtf").write_text(
        r"{\rtf1\ansi{\colortbl;\red0\green0\blue0;}"
        r"1\tab 10\tab STP10NK80ZFP\tab MOSFET N-CH 800V 9A TO-220F\row "
        r"2\tab 5\tab PIC12F629-E/SN-ND\tab IC MCU CMOS 8BIT 1K FLASH\row}",
        encoding="utf-8",
    )
    (dis_dir / "f629tempo2.asm").write_text(
        "  list p=12F629\n  retlw d'3'\n  retlw d'1'\n  retlw d'4'\n  retlw d'2'\n"
        "  movlw 0x03 ; lock\n  subwf lockcnt, f\n  incf shortcount, f\n",
        encoding="utf-8",
    )
    dis_08 = root / "DIS_Adaptor_5-20-08"
    (dis_08 / "Hybrid").mkdir(parents=True)
    (dis_08 / "README.txt").write_text(
        "Pre-2003 2.4 engine base timing 9 BTDC\n2003+ NGC 9 BTDC\nVB921 driver note\n"
        "Hybrid apps do not need to populate R4\n"
        "1 -- Simulated HEP out Gray\n2 -- NC\n6 -- 1+4 Coil out\n7 -- 2+3 Coil out\n8 -- Ground\n",
        encoding="utf-8",
    )
    (dis_08 / "Hybrid" / "690Hybrid.asm").write_text(
        "  list p=16F690\n  retlw d'3'\n  retlw d'1'\n  retlw d'4'\n  retlw d'2'\n"
        "  movlw 0x03 ; lock\n  subwf lockcnt, f\n  incf shortl, f\n",
        encoding="utf-8",
    )
    dis_07 = root / "DIS_Adaptor11-9-07"
    dis_07.mkdir(parents=True)
    (dis_07 / "README.txt").write_text(
        "1 -- Simulated HEP out Gray\n2 -- +8 volt HEP supply (In)\n6 -- 1+4 Coil out\n7 -- 2+3 Coil out\n8 -- Ground\n",
        encoding="utf-8",
    )
    return root


def _sample_digest(
    *,
    doc_id: str = "sample-cleanroom-digest",
    trust_tier: str = "Tier 2",
    ecu_scope: str = '["SMEC_88_89"]',
    body: str = "## Summary\n\nVerified cleanroom notes.",
) -> str:
    """Build a valid cleanroom knowledge digest markdown string."""
    return "\n".join([
        "> **Scope**: Cleanroom test digest",
        "> **Boundary**: Distilled hardware/pinout facts only.",
        "",
        "---",
        f'doc_id: "{doc_id}"',
        'title: "Sample Cleanroom Digest"',
        f'trust_tier: "{trust_tier}"',
        'source_type: "TEST_DIGEST"',
        'source_citation: "Cleanroom Test Fixture"',
        'warc_capture_date: "2026-11-26T00:00:00Z"',
        'page_ref: "Test Fixture"',
        'ecu_families: ["SMEC_DUAL_BOARD"]',
        f"ecu_scope: {ecu_scope}",
        'engine_scope: ["2.2L_K"]',
        "hex_symbols: []",
        "symbols_referenced: []",
        'pins_referenced: ["Pin 11"]',
        'mermaid_verification: "NONE"',
        'cleanroom_status: "DISTILLED_ATTRIBUTED"',
        'verified_by: "human_maintainer"',
        "---",
        "",
        body,
    ])


def test_committed_hardware_boms_and_wave_c_digests_are_cleanroom(tmp_path: Path) -> None:
    """Validate committed hardware_boms.json and all 8 Wave C knowledge digests."""
    bom_path = REPO_ROOT / "profiles" / "presets" / "hardware_boms.json"
    bom_text = bom_path.read_text(encoding="utf-8")
    bom_extractor.validate_cleanroom_payload(bom_text, label=str(bom_path))
    bom_doc = json.loads(bom_text)

    assert bom_doc["schema_version"] == "1.0"
    assert bom_doc["preset_count"] == 3
    by_id: dict[str, dict[str, Any]] = {p["preset_id"]: p for p in bom_doc["presets"]}
    assert set(by_id) == {
        "sst27sf020_74ls193_socket_adapter",
        "knock_sci_4n25_1n4733a_lm2917_board",
        "dis_adaptor_pic_vb921_waste_spark",
    }
    ee_pns = {item["part_number"] for item in by_id["sst27sf020_74ls193_socket_adapter"]["bom_items"]}
    assert {"SST27SF020-70-3C-PHE", "NTE74LS193"}.issubset(ee_pns)
    kb = by_id["knock_sci_4n25_1n4733a_lm2917_board"]
    assert {"4N25", "1N4733A-TR"}.issubset({item["part_number"] for item in kb["bom_items"]})
    assert kb["firmware"]["eeprom_size_bytes"] == 34 and kb["firmware"]["freq_volt_hz_per_v"] == 66
    dis = by_id["dis_adaptor_pic_vb921_waste_spark"]
    assert len(dis["db15_pinout"]) == 15
    assert {v["mcu"] for v in dis["firmware_variants"]} == {"PIC12F629", "PIC16F690"}

    summary = doc_curator.curate_wave_c_knowledge(
        private_root=tmp_path / "absent_private_refs",
        knowledge_dir=REPO_ROOT / "knowledge",
    )
    assert summary["probed_private_sources"] == []
    assert summary["curated_count"] == 8
    for item in summary["digests"]:
        assert item["lines"] <= doc_curator.MAX_DIGEST_LINES
        digest_text = (REPO_ROOT / "knowledge" / item["path"]).read_text(encoding="utf-8")
        bom_extractor.validate_cleanroom_payload(digest_text, label=item["path"])


def test_quarantine_and_cleanroom_payload_gates() -> None:
    """Verify hard quarantine patterns and PII/Deadbolt payload rejection without raw literals."""
    blocked_paths = (
        "archive/page_files/logo.gif",
        "PortMon/capture.log",
        "tools/reg code.txt",
        "tools/serial.txt",
        "tools/password.txt",
        "tools/DD.txt",
        "tools/Config.cfg",
        "legacy/" + "Flash" + ".frm",
        "Mo_Parts " + "Ad" + "am/contact.txt",
        "Mc" + "Clurg_s contact.txt",
    )
    for bad in blocked_paths:
        assert bom_extractor.is_quarantined_path(bad) is True
        with pytest.raises(bom_extractor.QuarantinedPathError):
            bom_extractor.check_quarantine(bad)

    assert bom_extractor.is_quarantined_path("DIS_Adaptor/parts.rtf") is False

    bad_payloads = (
        "/" + "home/" + "sample" + "user/notes.txt",
        "C:\\" + "Users\\" + "sample" + "user\\notes.txt",
        "dev" + "@" + "example.org",
        "author " + "bu" + "car",
        "author " + "st" + "eve",
        "author " + "mc" + "clurg",
        "opcode " + "0x" + "41",
        "opcode " + "0x" + "51",
        "tool " + "Flash" + ".frm",
        "port " + "/" + "dev/" + "ttyUSB0",
    )
    for payload in bad_payloads:
        with pytest.raises(ValueError, match="Cleanroom policy violation"):
            bom_extractor.validate_cleanroom_payload(payload, label="unit-test")


def test_synthetic_extract_hardware_boms_and_cli_wrapper(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify extract_hardware_boms and curate_heritage_web_docs CLI on synthetic tmp_path data."""
    priv = _make_synthetic_private_refs(tmp_path / "private_refs")
    presets_dir = tmp_path / "presets"
    knowledge_dir = tmp_path / "knowledge"
    out_json = presets_dir / "hardware_boms.json"
    out_md = knowledge_dir / "tier2_verified" / "hardware_adapter_boms.md"

    payload = bom_extractor.extract_hardware_boms(private_root=priv, output_json=out_json, output_md=out_md)
    assert payload["preset_count"] == 3
    assert out_json.is_file() and out_md.is_file()
    assert len(out_md.read_text(encoding="utf-8").splitlines()) <= 500
    fm = doc_curator.validate_cleanroom_digest(out_md)
    assert fm["doc_id"] == "hardware-adapter-boms-cleanroom"

    cli_mod = _load_curate_cli_module()
    rc = cli_mod.main([
        "--private-root", str(priv),
        "--knowledge-dir", str(knowledge_dir),
        "--presets-dir", str(presets_dir),
        "--json",
    ])
    assert rc == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["hardware_boms"]["preset_count"] == 3
    assert cli_out["knowledge"]["curated_count"] == 8


def test_doc_curator_extract_doc_html_and_digest_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test antiword .doc Deadbolt/PII scrubbing, HTML-to-GFM conversion, and digest checks."""
    doc_file = tmp_path / "SMEC_EEPROM_vs2.doc"
    doc_file.write_bytes(b"fake-doc")

    leaked = (
        "Path /" + "home/" + "sample" + "user/a.asm by dev" + "@" + "example.org "
        "with " + "0x" + "41 write and " + "serial" + ".Serial"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(args=a[0], returncode=0, stdout=leaked, stderr=""),
    )
    with pytest.raises(doc_curator.DeadboltViolationError):
        doc_curator.extract_doc_text(doc_file, redact_deadbolt=False)

    cleaned = doc_curator.extract_doc_text(doc_file, redact_deadbolt=True)
    assert "[REDACTED_PATH]" in cleaned and "[REDACTED_EMAIL]" in cleaned and "[REDACTED_DEADBOLT]" in cleaned

    with pytest.raises(doc_curator.QuarantinedPathError):
        doc_curator.extract_doc_text(tmp_path / "page_files" / "bad.doc")

    html_src = (
        "<html><head><title>Pinout /" + "home/" + "sample" + "user</title><style>body{}</style></head>"
        "<body><table><tr><th>Pin</th><th colspan='2'>Signal</th></tr>"
        "<tr><td>11</td><td>Z12</td><td>Bootstrap</td></tr></table>"
        "<pre>ColA\tColB\n1\t2</pre><pre>Single non-tabular line</pre>"
        "<script>bad()</script></body></html>"
    )
    sections = doc_curator.html_to_gfm_sections(html_src)
    assert "[REDACTED_PATH]" in sections["title"]
    assert "| Pin | Signal |  |" in sections["tables"][0]
    assert "| ColA | ColB |" in sections["pre_blocks"][0]
    assert sections["pre_blocks"][1].startswith("```text")
    assert "bad()" not in sections["markdown"]

    with pytest.raises(doc_curator.DigestValidationError, match="Scope/Boundary"):
        doc_curator.validate_cleanroom_digest("# Missing header\n")
    with pytest.raises(doc_curator.DigestValidationError, match="Z12"):
        doc_curator.validate_cleanroom_digest(_sample_digest(doc_id="smec-28c256-readonly-hardware-notes"))
    with pytest.raises(doc_curator.DigestValidationError, match="SMEC_88_89"):
        doc_curator.validate_cleanroom_digest(
            _sample_digest(
                doc_id="smec-28c256-readonly-hardware-notes",
                ecu_scope='["SBEC_90_93"]',
                body="Pin 11 Z12 warning",
            )
        )
    with pytest.raises(doc_curator.DigestValidationError, match="FSM"):
        doc_curator.validate_cleanroom_digest(_sample_digest(trust_tier="Tier 3", body="No warning"))
    with pytest.raises(doc_curator.DigestValidationError, match="UNC host"):
        doc_curator.validate_cleanroom_digest(_sample_digest(body="Share \\\\fileserver\\archive"))
    with pytest.raises(doc_curator.DigestValidationError, match="500-line cap"):
        doc_curator.validate_cleanroom_digest(_sample_digest(body="\n".join(["line"] * 505)))


def test_case_insensitive_nested_and_missing_quarantine_enforcement(tmp_path: Path) -> None:
    """Verify case-insensitive, nested, and missing quarantined paths are blocked."""
    quarantined_variants = (
        "tools/config.cfg",
        "tools/dd.txt",
        "portmon/capture.log",
        "nested/QUARANTINE_PII_DO_NOT_INGEST/raw.htm",
        "archive/Page_FILES/x.doc",
    )
    for item in quarantined_variants:
        assert bom_extractor.is_quarantined_path(item) is True
        assert doc_curator.is_quarantined_path(item) is True
        with pytest.raises(bom_extractor.QuarantinedPathError):
            bom_extractor.check_quarantine(item)

    with pytest.raises(bom_extractor.QuarantinedPathError):
        bom_extractor.parse_mouser_bom_html("PortMon/missing.log")
    with pytest.raises(bom_extractor.QuarantinedPathError):
        bom_extractor.parse_dis_readme("PortMon/missing.log")
    with pytest.raises(doc_curator.QuarantinedPathError):
        doc_curator.html_to_gfm_sections(tmp_path / "QUARANTINE_PII_DO_NOT_INGEST" / "raw.htm")
    with pytest.raises(doc_curator.QuarantinedPathError):
        doc_curator.extract_doc_text(tmp_path / "Page_FILES" / "x.doc")

    with pytest.raises(ValueError, match="UNC host"):
        bom_extractor.validate_cleanroom_payload("From \\\\fileserver\\share", label="unit-test")


def test_mouser_thead_tbody_preserves_first_item_and_asymmetric_db15_pinout() -> None:
    """Verify thead+tbody Mouser HTML keeps first item and asymmetric DB15 renders N/A."""
    html = (
        "<html><body><table id='CartGrid'>"
        "<thead><tr><th>Line</th><th>Product Detail</th><th>Stock</th><th>Order Qty</th></tr></thead>"
        "<tbody>"
        "<tr><td>1</td><td>Mouser #: 78-4N25 Mfr. #: 4N25 Desc.: Optocoupler</td><td>Yes</td><td>4</td></tr>"
        "<tr><td>2</td><td>Mouser #: 78-1N4733A Mfr. #: 1N4733A-TR Desc.: Zener 5.1V</td><td>Yes</td><td>8</td></tr>"
        "</tbody></table></body></html>"
    )
    items = bom_extractor.parse_mouser_bom_html(html)
    assert len(items) == 2
    assert items[0] == {
        "qty": 4,
        "part_number": "4N25",
        "description": "Optocoupler",
        "mouser_part_number": "78-4N25",
    }
    assert items[1]["part_number"] == "1N4733A-TR"

    payload = {
        "presets": [
            {"preset_id": "sst27sf020_74ls193_socket_adapter", "bom_items": []},
            {
                "preset_id": "knock_sci_4n25_1n4733a_lm2917_board",
                "bom_items": [],
                "firmware": {
                    "freq_volt_hz_per_v": 66,
                    "serial_baud": 115200,
                    "lcd_baud": 9600,
                    "pinout": {
                        "ignEvent": "D2", "knockDiscrete": "D3", "clearKnock": "D4",
                        "LEDPin": "D13", "rpmPin": "A0", "knockPin": "A1",
                        "lcdSoftwareSerial": "D5 (RX), D6 (TX)",
                    },
                    "eeprom_map": [],
                },
            },
            {
                "preset_id": "dis_adaptor_pic_vb921_waste_spark",
                "bom_items": [],
                "db15_pinout": {"1": "HEP Gray", "8": "Ground"},
                "db15_pinout_11_9_07": {"1": "HEP Gray"},
            },
        ]
    }
    md = bom_extractor._render_hardware_boms_markdown(payload)
    assert "| 1 | HEP Gray | Same |" in md
    assert "| 8 | Ground | N/A |" in md


def test_doc_curator_colspan_cap_and_deadbolt_in_title() -> None:
    """Verify html_table_to_gfm caps huge colspan <= 64 and title rejects Deadbolt tokens."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(
        "<table><tr><th>A</th><th>B</th></tr>"
        "<tr><td colspan='1000000000'>Wide</td></tr></table>",
        "lxml",
    )
    gfm = doc_curator.html_table_to_gfm(soup.find("table"))
    assert len(gfm) == 3
    col_count = gfm[1].count("---")
    assert 2 <= col_count <= 64

    bad_title_html = "<html><head><title>Tool " + "Fl" + "ash.frm</title></head><body><p>OK</p></body></html>"
    with pytest.raises(doc_curator.DeadboltViolationError):
        doc_curator.html_to_gfm_sections(bad_title_html)

