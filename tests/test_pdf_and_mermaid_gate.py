"""Tests for Wave D PDF extraction, Mermaid Gate 4a/4b, and cleanroom digest curation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from logicaide.heritage import doc_curator, mermaid_gate, pdf_extractor
from logicaide.heritage.chunker import chunk_heritage_document

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_minimal_pdf(lines: list[str]) -> bytes:
    """Build a minimal spec-valid PDF 1.4 containing text lines extractable by pdftotext."""
    escaped_ops = []
    for idx, line in enumerate(lines):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        op = "72 720 Td" if idx == 0 else "0 -16 Td"
        escaped_ops.append(f"{op} ({safe}) Tj")
    stream_body = ("BT /F1 12 Tf " + " ".join(escaped_ops) + " ET").encode("latin-1")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        ),
        (
            f"4 0 obj\n<< /Length {len(stream_body)} >>\nstream\n".encode("latin-1")
            + stream_body
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(out)


def test_is_excluded_pdf_all_excluded_and_clean_cases(tmp_path: Path) -> None:
    """Verify all 14 excluded basenames, excluded dirs, quarantine paths, and clean Chrysler PDFs."""
    excluded_basenames = [
        "9101.pdf", "9102.pdf", "9103.pdf", "9104.pdf",
        "9105.pdf", "9106.pdf", "9107.pdf", "9108.pdf",
        "Imprezawiringdiagram.pdf", "GUFB.pdf", "EECTuner_FAQ.pdf",
        "MSTweak3000.pdf", "VEX Format V1.pdf", "SCR-Drift-02-2003.pdf",
    ]
    assert set(excluded_basenames) == set(pdf_extractor.EXCLUDE_PDF_BASENAMES)
    for name in excluded_basenames:
        assert pdf_extractor.is_excluded_pdf(tmp_path / name, private_root=tmp_path) is True
        assert pdf_extractor.is_excluded_pdf(tmp_path / name.lower(), private_root=tmp_path) is True

    for excluded_dir in ("SHO", "Subaru Turbo Research", "A9L", "MSnS-E"):
        assert pdf_extractor.is_excluded_pdf(tmp_path / excluded_dir / "diagram.pdf", private_root=tmp_path) is True
    assert pdf_extractor.is_excluded_pdf(tmp_path / "QUARANTINE_PII_DO_NOT_INGEST" / "88 T2.pdf") is True

    for clean_name in ("88 T2.pdf", "LM2907N.pdf", "ChEM2.pdf"):
        assert pdf_extractor.is_excluded_pdf(tmp_path / "oem" / clean_name, private_root=tmp_path) is False


def test_detect_pdf_backends_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify backend probing structure and deterministic preference order."""
    live = pdf_extractor.detect_pdf_backends()
    for key in ("pdftotext", "pdftoppm", "docling", "pdfplumber", "pypdf", "preferred_text_backend", "preferred_ocr_backend"):
        assert key in live

    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}" if cmd in {"pdftotext", "tesseract"} else None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda mod: object() if mod == "pypdf" else None)
    probed = pdf_extractor.detect_pdf_backends()
    assert probed["pdftotext"] is True
    assert probed["pypdf"] is True
    assert probed["tesseract"] is True
    assert probed["preferred_text_backend"] == "pypdf"
    assert probed["preferred_ocr_backend"] == "tesseract"


def test_classify_ocr_quality_states() -> None:
    """Verify clean_text, empty_scan, and ocr_corrupted classification thresholds."""
    assert pdf_extractor.classify_ocr_quality([]) == "empty_scan"
    assert pdf_extractor.classify_ocr_quality(["   ", "x"]) == "empty_scan"
    assert pdf_extractor.classify_ocr_quality(["Header Only Page"] * 3) == "empty_scan"
    assert pdf_extractor.classify_ocr_quality(["SMEC Pin 11 \ufffd\ufffd\ufffd corrupted OCR text block"]) == "ocr_corrupted"
    assert pdf_extractor.classify_ocr_quality(["a b c d e f g h i j k l m n o p q r s t u v"]) == "ocr_corrupted"
    clean = (
        "Chrysler 1988 Turbo II SMEC diagnostic tree verifies Pin 11 ground isolation "
        "and Z12 chassis return continuity before energizing injector drivers."
    )
    assert pdf_extractor.classify_ocr_quality([clean]) == "clean_text"


def test_stitch_cross_page_tables_merges_and_preserves_headers() -> None:
    """Verify cross-page Markdown table continuations merge cleanly without duplicate headers."""
    page1 = (
        "## SMEC Pinout\n\n"
        "| Pin | Circuit | Function |\n"
        "| --- | --- | --- |\n"
        "| 11 | K22 | Fuel Pump Control |"
    )
    page2 = (
        "(Cont.)\n\n"
        "| Pin (Cont.) | Circuit | Function |\n"
        "| --- | --- | --- |\n"
        "| 12 | Z12 | Power Ground |\n\n"
        "Post-table verification note."
    )
    stitched = pdf_extractor.stitch_cross_page_tables([page1, page2])
    assert stitched.count("| Pin | Circuit | Function |") == 1
    assert "| Pin (Cont.) |" not in stitched
    assert "| 11 | K22 | Fuel Pump Control |" in stitched
    assert "| 12 | Z12 | Power Ground |" in stitched
    assert "Post-table verification note." in stitched

    page3 = (
        "| DB15 Pin | Signal |\n"
        "| --- | --- |\n"
        "| 9 | SCI TX |"
    )
    separate = pdf_extractor.stitch_cross_page_tables([page1, page3])
    assert "| Pin | Circuit | Function |" in separate
    assert "| DB15 Pin | Signal |" in separate


def test_run_parse_docs_dedup_exclude_and_deadbolt_scrubbing(tmp_path: Path) -> None:
    """Verify run_parse_docs extracts clean PDFs, deduplicates SHA-256, skips excluded, and scrubs PII/Deadbolt."""
    in_dir = tmp_path / "pdfs"
    in_dir.mkdir()
    email_tok = "dev@" + "example.org"
    write_tok = "0x" + "41"
    flash_tok = "Flash" + ".frm"
    pdf_bytes = _make_minimal_pdf([
        "Chrysler 1988 Turbo II SMEC Pin 11 diagnostic reference document.",
        "Verify Z12 chassis ground continuity and D20 D21 serial bus lines.",
        "Always confirm sixty way Pin 11 isolation before energizing drivers.",
        f"Archival contact {email_tok} with token {write_tok} and {flash_tok} redacted.",
    ])
    (in_dir / "88 T2.pdf").write_bytes(pdf_bytes)
    (in_dir / "88_T2_copy.pdf").write_bytes(pdf_bytes)
    (in_dir / "GUFB.pdf").write_bytes(_make_minimal_pdf(["Ford GUFB strategy excluded."]))

    out_md = tmp_path / "pdfs_md"
    fig_dir = tmp_path / "figures"
    manifest = pdf_extractor.run_parse_docs(
        in_dir,
        output_md_dir=out_md,
        figures_dir=fig_dir,
        render_figures=False,
        verify_mermaid=False,
    )
    assert manifest["discovered_count"] == 3
    assert manifest["excluded_count"] == 1
    assert manifest["duplicate_count"] == 1
    assert manifest["processed_count"] == 1
    assert (out_md / "manifest.json").is_file()

    doc_meta = manifest["documents"][0]
    assert doc_meta["track"] == "D1"
    assert doc_meta["ocr_quality"] == "clean_text"
    md_text = Path(doc_meta["md_path"]).read_text(encoding="utf-8")
    assert email_tok not in md_text
    assert flash_tok not in md_text
    assert "[REDACTED_EMAIL]" in md_text
    assert "[REDACTED_DEADBOLT]" in md_text
    doc_curator.assert_no_deadbolt_tokens(md_text, label="extracted_md")


def test_mermaid_gate4a_archetypes_repair_and_failures() -> None:
    """Verify Gate 4a passes all 4 Chrysler archetypes, auto-repairs quotes, and rejects broken syntax."""
    archetypes = [
        ("flowchart TD\n  A[Start] --> B[End]", "diagnostic_tree"),
        ("flowchart LR\n  A[MAP] --> B[SMEC]", "wiring_or_pneumatic"),
        ("sequenceDiagram\n  DRB->>SMEC: Request ID", "sci_timing"),
        ("stateDiagram-v2\n  [*] --> Idle", "sci_timing"),
    ]
    for block, expected_arch in archetypes:
        res = mermaid_gate.repair_and_validate_gate4a(block)
        assert res["valid"] is True
        assert res["archetype"] == expected_arch

    unquoted = "flowchart TD\n  N1[Sensor (5V/Ref)] --> N2[SMEC Pin 11]"
    with pytest.raises(mermaid_gate.MermaidGateError):
        mermaid_gate.check_mermaid_syntax_strict(unquoted)
    repaired = mermaid_gate.repair_and_validate_gate4a(unquoted, max_repairs=2)
    assert repaired["valid"] is True
    assert repaired["repair_attempts"] >= 1
    assert '"Sensor (5V/Ref)"' in repaired["repaired_block"]

    for broken in (
        "flowchart TD\n  A -->",
        "flowchart TD\n  A -> B",
        "flowchart TD\n  subgraph S1\n  A[One] --> B[Two]",
    ):
        with pytest.raises(mermaid_gate.MermaidGateError):
            mermaid_gate.check_mermaid_syntax_strict(broken)
        bad = mermaid_gate.repair_and_validate_gate4a(broken, max_repairs=2)
        assert bad["valid"] is False


def test_mermaid_gate4b_grounding_and_connector_false_positive_guard() -> None:
    """Verify Gate 4b grounds pins, table columns, circuits, hex, fault codes, and avoids 50-Way false match."""
    diagram = (
        "flowchart LR\n"
        '  A["Pin 11 K22 / Pin 12 Z12"] --> B["Pin 9 D20 / D21"]\n'
        '  B --> C["ROM $C000 Code 53"]'
    )
    backing = (
        "Verify Pin 11 and Pin 12 continuity on circuits K22, Z12, D20, and D21.\n"
        "Boot vector sits at 0xC000; DRB-II reports Code 53.\n\n"
        "| DB15 Pin | Signal |\n"
        "| --- | --- |\n"
        "| **`9`** | SCI TX |\n"
    )
    verified = mermaid_gate.verify_mermaid_gate(diagram, backing)
    assert verified["mermaid_verification"] == "VERIFIED"
    assert verified["unmatched_tokens"] == []
    assert {"Pin 11", "Pin 12", "Pin 9"}.issubset(set(verified["extracted_tokens"]["pins"]))
    assert {"K22", "Z12", "D20", "D21"}.issubset(set(verified["extracted_tokens"]["circuits"]))
    assert "$C000" in verified["extracted_tokens"]["hex_literals"]
    assert "Code 53" in verified["extracted_tokens"]["fault_codes"]

    unbacked = mermaid_gate.verify_mermaid_gate(
        "flowchart TD\n"
        '  X["Pin 44 Z99 0xDEAD Code 88"] --> Y["Done"]',
        "Pin 11 Z12 0xC000 Code 53",
    )
    assert unbacked["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"
    assert set(unbacked["unmatched_tokens"]) == {"Pin 44", "Z99", "0xDEAD", "Code 88"}

    way_tokens = mermaid_gate.extract_mermaid_grounding_tokens(
        'flowchart LR\n  C1["Red/Blue 50-Way Bulkhead"] --> C2["SMEC 60-Way"]'
    )
    assert way_tokens["pins"] == []
    assert "Blue 50" not in way_tokens["pins"]


def test_all_wiki_and_knowledge_files_have_zero_quarantined_mermaid() -> None:
    """Assert 0 quarantined Mermaid diagrams across all wiki/ and knowledge/ markdown docs."""
    md_files = sorted((REPO_ROOT / "wiki").rglob("*.md")) + sorted((REPO_ROOT / "knowledge").rglob("*.md"))
    assert len(md_files) > 0
    quarantined: list[str] = []
    verified_count = 0
    for md_path in md_files:
        parents, _ = chunk_heritage_document(md_path, repo_root=REPO_ROOT, verify_mermaid=True)
        for parent in parents:
            if parent["mermaid_verification"] == "NEEDS_HUMAN_REVIEW":
                quarantined.append(f"{md_path.relative_to(REPO_ROOT)}::{parent['parent_id']}")
            elif parent["mermaid_verification"] == "VERIFIED":
                verified_count += 1
    assert quarantined == []
    assert verified_count > 0


def test_curate_heritage_pdfs_validate_only_json() -> None:
    """Verify curate_heritage_pdfs.py --validate-only --json validates all 16 cleanroom digests."""
    script = REPO_ROOT / "scripts" / "curate_heritage_pdfs.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--validate-only", "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    kv = payload["knowledge_validation"]
    digests = kv["digests"]
    assert kv["validated_count"] == 16
    assert sum(1 for d in digests if d["trust_tier"] == "Tier 1") == 4
    assert sum(1 for d in digests if d["trust_tier"] in {"Tier 2", "Tier 3"}) == 12
    assert all(d["lines"] <= 500 for d in digests)

    chem2_fm = doc_curator.validate_cleanroom_digest(
        REPO_ROOT / "knowledge" / "tier2_verified" / "chem2_and_smec_add_a_pin_notes.md"
    )
    assert chem2_fm["ecu_scope"] == ["SMEC_88_89"]


def test_symlink_exclusion_and_positive_bounds(tmp_path: Path) -> None:
    """Verify symlinked excluded/external PDFs are rejected and invalid <=0 bounds raise ValueError."""
    in_dir = tmp_path / "priv"
    outside = tmp_path / "outside.pdf"
    in_dir.mkdir()
    outside.write_bytes(_make_minimal_pdf(["External PDF"]))
    gufb = in_dir / "GUFB.pdf"
    gufb.write_bytes(_make_minimal_pdf(["Excluded GUFB"]))
    alias = in_dir / "alias_clean.pdf"
    alias.symlink_to(gufb)
    escape = in_dir / "escape.pdf"
    escape.symlink_to(outside)

    assert pdf_extractor.is_excluded_pdf(alias, private_root=in_dir) is True
    assert pdf_extractor.is_excluded_pdf(escape, private_root=in_dir) is True

    manifest = pdf_extractor.run_parse_docs(
        in_dir, output_md_dir=tmp_path / "md", figures_dir=tmp_path / "fig", render_figures=False,
    )
    assert manifest["processed_count"] == 0
    with pytest.raises(ValueError):
        pdf_extractor.run_parse_docs(in_dir, output_md_dir=tmp_path / "md2", limit=0, render_figures=False)


def test_mermaid_gate4b_plural_multipin_and_connector_pair_guards() -> None:
    """Verify Gate 4b extracts all plural/delimited pins while excluding connector-pin pairs like 2-9."""
    res_slash = mermaid_gate.verify_mermaid_gate(
        'flowchart LR\n  A["Pin 25/99"] --> B["SCI"]',
        "Verify Pin 25 SCI TX continuity.",
    )
    assert res_slash["extracted_tokens"]["pins"] == ["Pin 25", "Pin 99"]
    assert res_slash["unmatched_tokens"] == ["Pin 99"]
    assert res_slash["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"

    res_multi = mermaid_gate.verify_mermaid_gate(
        'flowchart LR\n  A["Pins 1/20/22/99"] --> B["EPROM"]',
        "Pins 1, 20, 22 are isolated.",
    )
    assert res_multi["extracted_tokens"]["pins"] == ["Pin 1", "Pin 20", "Pin 22", "Pin 99"]
    assert res_multi["unmatched_tokens"] == ["Pin 99"]
    assert res_multi["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"

    res_amp_bad = mermaid_gate.verify_mermaid_gate(
        'flowchart LR\n  A["Pins 6 & 99"] --> B["Coil"]',
        "DB15 Pin 6 feeds coil driver.",
    )
    assert res_amp_bad["extracted_tokens"]["pins"] == ["Pin 6", "Pin 99"]
    assert res_amp_bad["unmatched_tokens"] == ["Pin 99"]
    assert res_amp_bad["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"

    res_amp_ok = mermaid_gate.verify_mermaid_gate(
        'flowchart LR\n  A["Pins 6 & 13"] --> B["Coil 1+4"]',
        "| DB15 Pin | Signal |\n| --- | --- |\n| **`6` & `13`** | Coil 1+4 |",
    )
    assert res_amp_ok["extracted_tokens"]["pins"] == ["Pin 6", "Pin 13"]
    assert res_amp_ok["unmatched_tokens"] == []
    assert res_amp_ok["mermaid_verification"] == "VERIFIED"

    res_cavs = mermaid_gate.verify_mermaid_gate(
        'flowchart LR\n  A["Cavs 33, 42"] --> B["Bulkhead"]',
        "Cavity 33 carries tachometer signal.",
    )
    assert res_cavs["extracted_tokens"]["pins"] == ["Cav 33", "Cav 42"]
    assert res_cavs["unmatched_tokens"] == ["Cav 42"]
    assert res_cavs["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"

    conn_pair_diag = 'flowchart LR\n  A["14-Way Pins 2-9 (Y11), 2-10 (Y12), 2-12 (J5)"] --> B["Pins 1-59, 2-14"]'
    pair_tokens = mermaid_gate.extract_mermaid_grounding_tokens(conn_pair_diag)
    assert pair_tokens["pins"] == []
    assert mermaid_gate.verify_mermaid_gate(conn_pair_diag, "Verify Y11, Y12, and J5.")["mermaid_verification"] == "VERIFIED"


def test_extract_pdf_document_rejects_unsafe_slugs(tmp_path: Path) -> None:
    """Verify path-traversal slugs are rejected with ValueError without writing outside output dirs."""
    in_dir = tmp_path / "in"
    out_md = tmp_path / "out_md"
    fig_dir = tmp_path / "fig"
    in_dir.mkdir()
    pdf_path = in_dir / "clean.pdf"
    pdf_path.write_bytes(
        _make_minimal_pdf([
            "Chrysler 1988 Turbo II SMEC Pin 11 diagnostic reference document.",
            "Verify Z12 chassis ground continuity before energizing injector drivers.",
        ])
    )
    for bad_slug in ("../outside", "bad/slug"):
        with pytest.raises(ValueError):
            pdf_extractor.extract_pdf_document(
                pdf_path, output_md_dir=out_md, figures_dir=fig_dir, render_figures=False, slug=bad_slug,
            )
    assert not (tmp_path / "outside.md").exists()
    assert not (tmp_path / "outside.meta.json").exists()
    assert not (out_md / "bad").exists()
    assert not (fig_dir / "bad").exists()


def test_extract_pdf_document_rejects_symlinks_and_sidecar(tmp_path: Path) -> None:
    """Verify symlink PDFs, symlink .ocr.txt sidecars, and symlink output targets are blocked."""
    in_dir = tmp_path / "in"
    out_md = tmp_path / "out_md"
    fig_dir = tmp_path / "fig"
    in_dir.mkdir()
    out_md.mkdir()

    clean_pdf = in_dir / "clean.pdf"
    clean_pdf.write_bytes(
        _make_minimal_pdf([
            "Chrysler 1988 Turbo II SMEC Pin 11 diagnostic reference document.",
            "Verify Z12 chassis ground continuity before energizing injector drivers.",
        ])
    )
    sym_pdf = in_dir / "sym_clean.pdf"
    sym_pdf.symlink_to(clean_pdf)
    with pytest.raises(ValueError):
        pdf_extractor.extract_pdf_document(
            sym_pdf, output_md_dir=out_md, figures_dir=fig_dir, render_figures=False,
        )

    secret_sidecar = tmp_path / "secret_sidecar.txt"
    secret_sidecar.write_text("SYNTHETIC_SIDECAR_SENTINEL Pin 11 Z12", encoding="utf-8")
    scan_pdf = in_dir / "scan.pdf"
    scan_pdf.write_bytes(_make_minimal_pdf(["x"]))
    adj_sidecar = in_dir / "scan.ocr.txt"
    adj_sidecar.symlink_to(secret_sidecar)
    with pytest.raises(ValueError):
        pdf_extractor.extract_pdf_document(
            scan_pdf, output_md_dir=out_md, figures_dir=fig_dir, render_figures=False, slug="scan_test",
        )
    adj_sidecar.unlink()
    fig_slug = fig_dir / "scan_test"
    fig_slug.mkdir(parents=True)
    (fig_slug / "page-1.ocr.txt").symlink_to(secret_sidecar)
    with pytest.raises(ValueError):
        pdf_extractor.extract_pdf_document(
            scan_pdf, output_md_dir=out_md, figures_dir=fig_dir, render_figures=False, slug="scan_test",
        )

    victim = tmp_path / "victim.md"
    victim.write_text("SENTINEL_UNTOUCHED\n", encoding="utf-8")
    (out_md / "target_doc.md").symlink_to(victim)
    with pytest.raises(ValueError):
        pdf_extractor.extract_pdf_document(
            clean_pdf, output_md_dir=out_md, figures_dir=fig_dir, render_figures=False, slug="target_doc",
        )
    assert victim.read_text(encoding="utf-8") == "SENTINEL_UNTOUCHED\n"


def test_layout_page_to_markdown_preserves_padded_gfm_pipe_table() -> None:
    """Verify space-padded GFM pipe tables are preserved without double-wrapping pipes."""
    padded_table = (
        "SMEC Connector Summary\n\n"
        "| ColA   | ColB   |\n"
        "| ---    | ---    |\n"
        "| 11     | K22    |\n"
        "| 12     | Z12    |"
    )
    rendered = pdf_extractor._layout_page_to_markdown(padded_table)
    assert "| | ColA" not in rendered
    assert "| \\| ColA" not in rendered
    assert "| ColA   | ColB   |" in rendered
    assert "| 11     | K22    |" in rendered
