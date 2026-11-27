"""Cleanroom Wave D heritage PDF extractor with backend probing, OCR quality gating, and table stitching."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from logicaide.heritage.bom_extractor import QuarantinedPathError, check_quarantine, is_quarantined_path
from logicaide.heritage.doc_curator import _format_gfm_pipe_table, _redact_deadbolt_tokens, assert_no_deadbolt_tokens, scrub_pii

EXCLUDE_PDF_BASENAMES: tuple[str, ...] = (
    "9101.pdf", "9102.pdf", "9103.pdf", "9104.pdf", "9105.pdf", "9106.pdf", "9107.pdf", "9108.pdf",
    "Imprezawiringdiagram.pdf", "GUFB.pdf", "EECTuner_FAQ.pdf", "MSTweak3000.pdf", "VEX Format V1.pdf", "SCR-Drift-02-2003.pdf",
)
_EXCLUDE_BASENAMES_LOWER: frozenset[str] = frozenset(name.lower() for name in EXCLUDE_PDF_BASENAMES)
EXCLUDE_PDF_DIR_PARTS: tuple[str, ...] = ("sho", "subaru turbo research", "a9l", "msns-e")
_EXCLUDE_DIR_PARTS_LOWER: frozenset[str] = frozenset(part.lower() for part in EXCLUDE_PDF_DIR_PARTS)
_CLI_BACKENDS: tuple[str, ...] = ("pdftotext", "pdftoppm", "pdfimages", "pdfinfo", "gs", "tesseract", "marker_single")
_PY_BACKENDS: tuple[str, ...] = ("docling", "marker", "surya_ocr", "pypdf", "pypdfium2", "pdfplumber", "PIL")
_CONT_MARKER_RE = re.compile(r"^\s*(?:[*_#\s]*)?(?:table\s+\d+\s*)?\(?\s*cont(?:inued)?\.?\s*\)?(?:[*_\s]*)$", re.I)
_MERMAID_HEADER_RE = re.compile(r"^(?:flowchart|graph|sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|erDiagram)\b")
_VALID_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)


def _run_cmd(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a local subprocess command with UTF-8 replacement decoding."""
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", check=False, timeout=timeout)


def is_excluded_pdf(pdf_path: Path | str, private_root: Path | None = None) -> bool:
    """Return True for non-Chrysler/club excluded PDFs or quarantined source paths."""
    path = Path(pdf_path)
    if is_quarantined_path(path) or path.name.lower() in _EXCLUDE_BASENAMES_LOWER:
        return True
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if is_quarantined_path(resolved) or resolved.name.lower() in _EXCLUDE_BASENAMES_LOWER:
        return True
    unresolved_parts, resolved_parts = path.parts[:-1], resolved.parts[:-1]
    if private_root is not None:
        if Path(private_root).is_symlink():
            return True
        try:
            root_res = Path(private_root).resolve()
        except OSError:
            return True
        if not resolved.is_relative_to(root_res) or is_quarantined_path(rel_res := resolved.relative_to(root_res)):
            return True
        resolved_parts = rel_res.parts[:-1]
        try:
            if is_quarantined_path(rel_raw := path.relative_to(private_root)):
                return True
            unresolved_parts = rel_raw.parts[:-1]
        except ValueError:
            pass
    return any(part.lower() in _EXCLUDE_DIR_PARTS_LOWER for part in (*unresolved_parts, *resolved_parts))


def detect_pdf_backends() -> dict[str, Any]:
    """Detect installed CLI tools and Python modules without downloading models or packages."""
    status: dict[str, Any] = {cmd: shutil.which(cmd) is not None for cmd in _CLI_BACKENDS}
    for mod in _PY_BACKENDS:
        specs = ("surya_ocr", "surya") if mod == "surya_ocr" else (mod,)
        status[mod] = any(importlib.util.find_spec(name) is not None for name in specs)
    status["preferred_text_backend"] = next((c for c in ("docling", "pdfplumber", "pypdf", "pdftotext") if status[c]), None)
    status["preferred_ocr_backend"] = "marker" if (status["marker_single"] or status["marker"]) else ("tesseract" if status["tesseract"] else None)
    return status


def classify_ocr_quality(page_texts: list[str]) -> str:
    """Classify extracted PDF page texts as clean_text, empty_scan, or ocr_corrupted."""
    if not page_texts:
        return "empty_scan"
    total_nws = sum(len(re.sub(r"\s+", "", page)) for page in page_texts)
    if total_nws < 15 or (len(page_texts) > 1 and (total_nws / len(page_texts)) < 30):
        return "empty_scan"
    unique_pages = {re.sub(r"\s+", " ", p).strip() for p in page_texts if p.strip()}
    if len(page_texts) >= 3 and len(unique_pages) == 1 and len(next(iter(unique_pages))) < 150:
        return "empty_scan"
    joined = "\n".join(page_texts)
    nws = re.sub(r"\s+", "", joined)
    bad_ctrl = sum(1 for ch in joined if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r\f"))
    if bad_ctrl / max(1, total_nws) > 0.01:
        return "ocr_corrupted"
    allowed_punct = frozenset(".,-()/:'\"%$#_")
    symbol_ratio = sum(1 for ch in nws if not ch.isalnum() and ch not in allowed_punct) / max(1, len(nws))
    non_alnum_ratio = sum(1 for ch in nws if not ch.isalnum()) / max(1, len(nws))
    tokens = joined.split()
    single_ratio = sum(1 for tok in tokens if len(tok) == 1 and not tok.isdigit()) / max(1, len(tokens))
    weird_tok_ratio = sum(1 for tok in tokens if re.search(r"[^A-Za-z0-9.,\-()/:'\"%$#_]", tok)) / max(1, len(tokens))
    if symbol_ratio > 0.05 or non_alnum_ratio > 0.35 or (len(tokens) >= 10 and single_ratio >= 0.25) or (len(tokens) >= 20 and single_ratio >= 0.11) or (len(tokens) >= 15 and weird_tok_ratio >= 0.05):
        return "ocr_corrupted"
    if len(page_texts) == 1 and total_nws < 80 and (single_ratio >= 0.12 or weird_tok_ratio >= 0.05 or symbol_ratio >= 0.01):
        return "empty_scan"
    return "clean_text"


def _is_pipe_row(line: str) -> bool:
    """Return True when a line looks like a Markdown pipe table row."""
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _split_pipe_cells(line: str) -> list[str]:
    """Split a Markdown pipe row into stripped cell strings."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    """Return True when a line is a GFM pipe table header separator row."""
    return _is_pipe_row(line) and bool(cells := _split_pipe_cells(line)) and all(re.match(r"^:?-{2,}:?$", c) for c in cells)


def _header_signature(cells: list[str]) -> tuple[str, ...]:
    """Normalize pipe-table header cells for cross-page duplicate header comparison."""
    return tuple(re.sub(r"\s+", " ", re.sub(r"\s*\(?\s*cont(?:inued)?\.?\s*\)?\s*", " ", c, flags=re.I)).strip().lower() for c in cells)


def _find_trailing_table(lines: list[str]) -> list[str] | None:
    """Return trailing Markdown pipe table lines at the end of accumulated lines."""
    idx = len(lines) - 1
    while idx >= 0 and not lines[idx].strip():
        idx -= 1
    if idx < 0 or not _is_pipe_row(lines[idx]):
        return None
    end = idx + 1
    while idx >= 0 and _is_pipe_row(lines[idx]):
        idx -= 1
    return tbl if len(tbl := lines[idx + 1 : end]) >= 2 and _is_separator_row(tbl[1]) else None


def _parse_leading_table(lines: list[str]) -> tuple[bool, list[str], int] | None:
    """Locate a leading Markdown pipe table (optionally preceded by a Cont. marker)."""
    idx = next((i for i, ln in enumerate(lines) if ln.strip()), len(lines))
    has_cont = idx < len(lines) and bool(_CONT_MARKER_RE.match(lines[idx]))
    if has_cont:
        idx = next((i for i in range(idx + 1, len(lines)) if lines[i].strip()), len(lines))
    if idx >= len(lines) or not _is_pipe_row(lines[idx]):
        return None
    start = idx
    while idx < len(lines) and _is_pipe_row(lines[idx]):
        idx += 1
    return has_cont, lines[start:idx], idx


def stitch_cross_page_tables(pages_md: list[str]) -> str:
    """Merge consecutive Markdown pipe tables split across page boundaries."""
    if not pages_md:
        return ""
    acc_lines: list[str] = pages_md[0].rstrip().splitlines()
    for page_md in pages_md[1:]:
        page_lines = page_md.splitlines()
        prev_table, leading = _find_trailing_table(acc_lines), _parse_leading_table(page_lines)
        if prev_table is None or leading is None:
            acc_lines.extend(["", *page_lines] if acc_lines and page_lines else page_lines)
            continue
        has_cont, next_table, next_end = leading
        prev_hdr, next_first = _split_pipe_cells(prev_table[0]), _split_pipe_cells(next_table[0])
        prev_sig, next_sig = _header_signature(prev_hdr), _header_signature(next_first)
        has_next_sep = len(next_table) >= 2 and _is_separator_row(next_table[1])
        header_has_cont = any(re.search(r"cont(?:inued)?\.?", c, re.I) for c in next_first)
        if not (has_cont or header_has_cont or prev_sig == next_sig or (not has_next_sep and len(prev_hdr) == len(next_first))):
            acc_lines.extend(["", *page_lines])
            continue
        while acc_lines and not acc_lines[-1].strip():
            acc_lines.pop()
        acc_lines.extend(next_table[2:] if (has_next_sep and (prev_sig == next_sig or header_has_cont)) else ([next_table[0], *next_table[2:]] if has_next_sep else next_table))
        rem = page_lines[next_end:]
        while rem and not rem[0].strip():
            rem = rem[1:]
        if rem:
            acc_lines.extend(["", *rem])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(acc_lines)).strip()


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file in streaming chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_pdf_page_count(pdf_path: Path, backends: dict[str, Any]) -> int | None:
    """Return PDF page count via pdfinfo or installed Python PDF library."""
    if backends.get("pdfinfo") and (proc := _run_cmd(["pdfinfo", str(pdf_path)], timeout=30)).returncode == 0 and (m := re.search(r"^Pages:\s*(\d+)", proc.stdout, re.MULTILINE)):
        return int(m.group(1))
    if backends.get("pypdf"):
        import pypdf  # type: ignore[import-not-found]
        return len(pypdf.PdfReader(str(pdf_path)).pages)
    if backends.get("pdfplumber"):
        import pdfplumber  # type: ignore[import-not-found]
        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    return None


def _layout_page_to_markdown(page_text: str) -> str:
    """Convert aligned multi-column blocks in layout text to GFM pipe tables."""
    lines, out, idx = page_text.splitlines(), [], 0
    while idx < len(lines):
        if _is_pipe_row(lines[idx]):
            out.append(lines[idx].rstrip())
            idx += 1
            continue
        block, scan = [], idx
        while scan < len(lines) and lines[scan].strip() and not _is_pipe_row(lines[scan]):
            cols = [c.strip() for c in re.split(r"\s{2,}", lines[scan].strip()) if c.strip()]
            if not (2 <= len(cols) <= 20):
                break
            block.append(cols)
            scan += 1
        if len(block) >= 3 and len({len(r) for r in block}) <= 2 and min(len(r) for r in block) >= 2:
            out.extend(_format_gfm_pipe_table(block))
            idx = scan
        else:
            out.append(lines[idx].rstrip())
            idx += 1
    return "\n".join(out).strip()


def _extract_pages_python(pdf_path: Path, max_pages: int | None, backends: dict[str, Any]) -> tuple[list[str], str]:
    """Extract per-page text via Docling, pdfplumber, pypdf, or Poppler pdftotext."""
    if backends.get("docling"):
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]
        return [DocumentConverter().convert(str(pdf_path)).document.export_to_markdown()], "docling"
    if backends.get("pdfplumber"):
        import pdfplumber  # type: ignore[import-not-found]
        with pdfplumber.open(str(pdf_path)) as pdf:
            return [p.extract_text() or "" for p in (pdf.pages[:max_pages] if max_pages is not None else pdf.pages)], "pdfplumber"
    if backends.get("pypdf"):
        import pypdf  # type: ignore[import-not-found]
        pages = pypdf.PdfReader(str(pdf_path)).pages
        return [p.extract_text() or "" for p in (pages[:max_pages] if max_pages is not None else pages)], "pypdf"
    if backends.get("pdftotext"):
        cmd = ["pdftotext", "-layout", *(["-f", "1", "-l", str(max_pages)] if max_pages is not None else []), str(pdf_path), "-"]
        if (proc := _run_cmd(cmd)).returncode != 0:
            raise RuntimeError(f"pdftotext failed on {pdf_path.name}: {proc.stderr.strip()}")
        return (pages[:-1] if (pages := proc.stdout.split("\f")) and pages[-1] == "" else pages), "pdftotext"
    raise RuntimeError("No PDF text extraction backend available (docling, pdfplumber, pypdf, pdftotext)")


def _validate_base_dir(path_val: Path | str, *, label: str) -> Path:
    """Reject NUL bytes and symlinked directory arguments."""
    if "\x00" in str(path_val):
        raise ValueError(f"Invalid {label}: NUL byte in path")
    raw_dir, cur = Path(path_val), Path()
    if not raw_dir.is_absolute():
        for part in raw_dir.parts:
            if (cur := cur / part).is_symlink():
                raise ValueError(f"Symlinked {label} component is not allowed: {cur}")
    probe = raw_dir
    while not probe.exists() and not probe.is_symlink() and probe.parent != probe:
        probe = probe.parent
    if probe.is_symlink():
        raise ValueError(f"Symlinked {label} is not allowed: {probe}")
    return raw_dir


def _assert_safe_subpath(base_dir: Path, target: Path, *, allow_equal: bool = False) -> Path:
    """Verify target has no symlinks at or below base_dir and resolves inside base_dir."""
    try:
        rel = target.relative_to(base_dir)
        if ".." in rel.parts:
            raise ValueError(f"Path escapes base directory {base_dir}: {target}")
        cur = base_dir
        for check_path in (base_dir, *((cur := cur / part) for part in rel.parts)):
            if check_path.is_symlink():
                raise ValueError(f"Symlinked path is not allowed: {check_path}")
        base_res, target_res = base_dir.resolve(), target.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"Invalid target path {target}: {exc}") from exc
    if not target_res.is_relative_to(base_res) or (not allow_equal and target_res == base_res):
        raise ValueError(f"Path escapes base directory {base_dir}: {target}")
    return target


def _resolve_checked(raw_path: Path, *, label: str) -> Path:
    """Resolve raw_path safely and enforce quarantine on the resolved target."""
    try:
        resolved = raw_path.resolve()
    except OSError as exc:
        raise ValueError(f"Invalid {label}: {raw_path}") from exc
    check_quarantine(resolved)
    return resolved


def _validate_slug_and_target(slug: str | None, stem: str, out_dir: Path, fig_dir: Path) -> tuple[str, Path, Path, Path]:
    """Validate slug and ensure md, meta, and figures paths stay strictly inside base dirs."""
    if slug is None:
        doc_slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_") or "document"
    elif not isinstance(slug, str) or not slug or slug != slug.strip() or "\x00" in slug or "/" in slug or "\\" in slug or slug in {".", ".."} or not _VALID_SLUG_RE.fullmatch(slug):
        raise ValueError(f"Invalid document slug: {slug!r}")
    else:
        doc_slug = slug
    return doc_slug, _assert_safe_subpath(out_dir, out_dir / f"{doc_slug}.md"), _assert_safe_subpath(out_dir, out_dir / f"{doc_slug}.meta.json"), _assert_safe_subpath(fig_dir, fig_dir / doc_slug)


def _safe_write_text(base_dir: Path, target: Path, text: str) -> None:
    """Verify target contains no symlinks before creating base_dir and writing UTF-8 text."""
    _assert_safe_subpath(base_dir, target)
    base_dir.mkdir(parents=True, exist_ok=True)
    _assert_safe_subpath(base_dir, target).write_text(text, encoding="utf-8")


def _load_sidecar_ocr(pdf_path: Path, figures_slug_dir: Path, max_pages: int | None) -> list[str] | None:
    """Load optional pre-extracted sidecar .ocr.txt files with symlink and containment checks."""
    for candidate in (Path(f"{pdf_path}.ocr.txt"), pdf_path.with_suffix(".ocr.txt")):
        if candidate.is_symlink() or candidate.is_file():
            safe = _resolve_checked(check_quarantine(_assert_safe_subpath(pdf_path.parent, candidate)), label="OCR sidecar")
            pages = safe.read_text(encoding="utf-8", errors="replace").split("\f")
            pages = pages[:-1] if pages and pages[-1] == "" else pages
            return pages[:max_pages] if max_pages is not None else pages
    if figures_slug_dir.is_symlink():
        raise ValueError(f"Symlinked figures directory is not allowed: {figures_slug_dir}")
    if figures_slug_dir.is_dir() and (sidecars := sorted(figures_slug_dir.glob("*.ocr.txt"))):
        for p in sidecars:
            _resolve_checked(check_quarantine(_assert_safe_subpath(figures_slug_dir, p)), label="OCR sidecar")
        return [p.read_text(encoding="utf-8", errors="replace") for p in (sidecars[:max_pages] if max_pages is not None else sidecars)]
    return None


def _render_pages_pdftoppm(pdf_path: Path, figures_slug_dir: Path, dpi: int, max_pages: int | None) -> list[Path]:
    """Render PDF pages to PNG at dpi via Poppler pdftoppm."""
    _assert_safe_subpath(figures_slug_dir.parent, figures_slug_dir)
    figures_slug_dir.mkdir(parents=True, exist_ok=True)
    for stale in figures_slug_dir.glob("page-*.png"):
        _assert_safe_subpath(figures_slug_dir, stale).unlink()
    cmd = ["pdftoppm", "-png", "-r", str(dpi), *(["-f", "1", "-l", str(max_pages)] if max_pages is not None else []), str(pdf_path), str(figures_slug_dir / "page")]
    if (proc := _run_cmd(cmd, timeout=300)).returncode != 0:
        raise RuntimeError(f"pdftoppm failed on {pdf_path.name}: {proc.stderr.strip()}")
    return [_assert_safe_subpath(figures_slug_dir, png) for png in sorted(figures_slug_dir.glob("page-*.png"))]


def _handle_ocr_track(
    *, resolved: Path, ocr_quality: str, raw_pages: list[str], backend_used: str, backends: dict[str, Any],
    figures_dir: Path, figures_slug_dir: Path, render_dpi: int, max_pages: int | None, render_figures: bool,
) -> tuple[list[str], str, str, list[Path]]:
    """Run sidecar/OCR fallback or 300 DPI PNG rendering for scanned/corrupted PDFs."""
    _assert_safe_subpath(figures_dir, figures_slug_dir)
    sidecar, rendered = _load_sidecar_ocr(resolved, figures_slug_dir, max_pages), []
    if render_figures and backends.get("pdftoppm"):
        try:
            rendered = _render_pages_pdftoppm(resolved, figures_slug_dir, render_dpi, max_pages)
        except RuntimeError:
            if sidecar is None:
                raise
    if sidecar is not None:
        return sidecar, f"{backend_used}+sidecar_ocr", "sidecar_ocr_loaded", rendered
    if backends.get("marker_single"):
        out_dir = _assert_safe_subpath(figures_slug_dir, figures_slug_dir / "marker_out")
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["marker_single", str(resolved), "--output_dir", str(out_dir), *(["--max_pages", str(max_pages)] if max_pages is not None else [])]
        if _run_cmd(cmd, timeout=300).returncode == 0 and (md_files := sorted(out_dir.rglob("*.md"))):
            return [_assert_safe_subpath(out_dir, md_files[0]).read_text(encoding="utf-8", errors="replace")], f"{backend_used}+marker", "marker_ocr", rendered
    if backends.get("tesseract") and rendered:
        ocr_pages: list[str] = []
        for png in rendered:
            if (proc := _run_cmd(["tesseract", str(_assert_safe_subpath(figures_slug_dir, png)), "stdout"])).returncode != 0:
                raise RuntimeError(f"tesseract failed on {png.name}: {proc.stderr.strip()}")
            ocr_pages.append(proc.stdout)
        return ocr_pages, f"{backend_used}+tesseract", "tesseract_ocr", rendered
    suffix = "rendered" if (render_figures and rendered) else "pending"
    prefix = "ocr_corrupted" if ocr_quality == "ocr_corrupted" else "ocr_fallback"
    return raw_pages, (f"{backend_used}+pdftoppm" if suffix == "rendered" else backend_used), f"{prefix}_{suffix}", rendered


def _check_mermaid_blocks(markdown: str, verify_mermaid: bool) -> str:
    """Perform lightweight structural validation of fenced Mermaid blocks when requested."""
    if not verify_mermaid:
        return "not_requested"
    if not (blocks := re.findall(r"```mermaid\s*\n(.*?)```", markdown, flags=re.DOTALL)):
        return "no_diagrams"
    return "needs_human_review" if any(not _MERMAID_HEADER_RE.match(next((ln.strip() for ln in b.splitlines() if ln.strip()), "")) for b in blocks) else "syntax_checked"


def _validate_positive_ints(**values: int | None) -> None:
    """Validate optional positive integer bounds, rejecting booleans and values < 1."""
    for name, value in values.items():
        if value is not None and (isinstance(value, bool) or value < 1):
            raise ValueError(f"{name} must be >= 1 (got {value!r})")


def _discover_pdf_candidates(target: Path, target_root: Path) -> list[Path]:
    """Collect non-symlink PDF files strictly contained under target_root."""
    candidates: list[Path] = []
    for p in ([target] if target.is_file() else sorted(target.rglob("*"))):
        if p.is_symlink() or not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        try:
            if _assert_safe_subpath(target, p, allow_equal=True).resolve().is_relative_to(target_root):
                candidates.append(p)
        except (OSError, ValueError):
            continue
    return candidates


def extract_pdf_document(
    pdf_path: Path | str, *, output_md_dir: Path = Path("data/heritage_raw/pdfs_md"),
    figures_dir: Path = Path("data/heritage_raw/figures"), render_dpi: int = 300,
    max_pages: int | None = None, render_figures: bool = True, verify_mermaid: bool = False,
    redact_deadbolt: bool = True, slug: str | None = None, private_root: Path | str | None = None,
) -> dict[str, Any]:
    """Extract PDF text/tables/figures cleanly with quarantine, OCR fallback, PII & Deadbolt gates."""
    _validate_positive_ints(render_dpi=render_dpi, max_pages=max_pages)
    out_dir, fig_dir = _validate_base_dir(output_md_dir, label="output_md_dir"), _validate_base_dir(figures_dir, label="figures_dir")
    resolved_private_root = (
        _resolve_checked(check_quarantine(_validate_base_dir(private_root, label="private_root")), label="private_root")
        if private_root is not None else None
    )
    raw_path = check_quarantine(Path(pdf_path))
    if raw_path.is_symlink():
        raise ValueError(f"Symlinked PDF path is not allowed: {raw_path}")
    if private_root is not None and (raw_abs := Path(os.path.abspath(raw_path))).is_relative_to(priv_abs := Path(os.path.abspath(Path(private_root)))):
        _assert_safe_subpath(priv_abs, raw_abs)
    resolved = _resolve_checked(raw_path, label="PDF path")
    if is_excluded_pdf(raw_path, private_root=resolved_private_root) or is_excluded_pdf(resolved, private_root=resolved_private_root):
        raise ValueError(f"Excluded non-Chrysler/club PDF blocked: {raw_path.name}")
    doc_slug, md_path, meta_path, figures_slug_dir = _validate_slug_and_target(slug, resolved.stem, out_dir, fig_dir)
    if not resolved.is_file():
        raise FileNotFoundError(f"PDF file not found: {raw_path}")
    backends, sha256 = detect_pdf_backends(), _sha256_file(resolved)
    raw_pages, backend_used = _extract_pages_python(resolved, max_pages, backends)
    page_count, ocr_quality = _get_pdf_page_count(resolved, backends) or len(raw_pages), classify_ocr_quality(raw_pages)
    if ocr_quality == "clean_text":
        track, ocr_status, final_pages, rendered_paths = "D1", "clean_text", raw_pages, []
    else:
        track = "D2"
        final_pages, backend_used, ocr_status, rendered_paths = _handle_ocr_track(
            resolved=resolved, ocr_quality=ocr_quality, raw_pages=raw_pages, backend_used=backend_used, backends=backends,
            figures_dir=fig_dir, figures_slug_dir=figures_slug_dir, render_dpi=render_dpi, max_pages=max_pages, render_figures=render_figures,
        )
    safe_md = scrub_pii(stitch_cross_page_tables([_layout_page_to_markdown(p) for p in final_pages]))
    if redact_deadbolt:
        safe_md = _redact_deadbolt_tokens(safe_md)
    else:
        assert_no_deadbolt_tokens(safe_md, label=resolved.name)
    _safe_write_text(out_dir, md_path, safe_md + ("\n" if safe_md else ""))
    meta: dict[str, Any] = {
        "slug": doc_slug, "source_filename": scrub_pii(resolved.name), "sha256": sha256, "page_count": page_count, "pages_processed": len(final_pages),
        "track": track, "backend_used": backend_used, "ocr_quality": ocr_quality, "ocr_status": ocr_status,
        "mermaid_verification": _check_mermaid_blocks(safe_md, verify_mermaid), "figure_paths": [p.as_posix() for p in rendered_paths],
        "md_path": md_path.as_posix(), "meta_path": meta_path.as_posix(), "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _safe_write_text(out_dir, meta_path, json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return meta


def run_parse_docs(
    input_path: Path | str = Path("private_references"), *, output_md_dir: Path = Path("data/heritage_raw/pdfs_md"),
    figures_dir: Path = Path("data/heritage_raw/figures"), render_dpi: int = 300,
    max_pages: int | None = None, render_figures: bool = True, verify_mermaid: bool = False, limit: int | None = None,
) -> dict[str, Any]:
    """Discover, deduplicate by SHA-256, extract PDFs, and write manifest.json."""
    _validate_positive_ints(render_dpi=render_dpi, max_pages=max_pages, limit=limit)
    target, out_dir, fig_dir = _validate_base_dir(input_path, label="input_path"), _validate_base_dir(output_md_dir, label="output_md_dir"), _validate_base_dir(figures_dir, label="figures_dir")
    manifest_path = _assert_safe_subpath(out_dir, out_dir / "manifest.json")
    if not target.exists():
        raise FileNotFoundError(f"PDF input path not found: {target}")
    target_root = target.resolve()
    candidates, root_for_excl = _discover_pdf_candidates(target, target_root), (target_root if target.is_dir() else None)
    excluded_count, duplicate_count, seen_sha256, used_slugs, selected = 0, 0, set(), set(), []
    for pdf in candidates:
        if is_excluded_pdf(pdf, private_root=root_for_excl):
            excluded_count += 1
        elif (sha := _sha256_file(pdf)) in seen_sha256:
            duplicate_count += 1
        else:
            seen_sha256.add(sha)
            base = re.sub(r"[^a-z0-9]+", "_", pdf.stem.lower()).strip("_") or "document"
            used_slugs.add(slug := base if base not in used_slugs else f"{base}_{sha[:8]}")
            selected.append((pdf, slug))
    documents = [
        extract_pdf_document(pdf, output_md_dir=out_dir, figures_dir=fig_dir, render_dpi=render_dpi, max_pages=max_pages, render_figures=render_figures, verify_mermaid=verify_mermaid, slug=slug, private_root=root_for_excl)
        for pdf, slug in (selected[:limit] if limit is not None else selected)
    ]
    manifest: dict[str, Any] = {
        "schema_version": "1.0", "generated_at_utc": datetime.now(timezone.utc).isoformat(), "input_path": scrub_pii(target.as_posix()), "backends": detect_pdf_backends(),
        "discovered_count": len(candidates), "excluded_count": excluded_count, "duplicate_count": duplicate_count, "processed_count": len(documents), "documents": documents,
    }
    _safe_write_text(out_dir, manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


__all__ = [
    "EXCLUDE_PDF_BASENAMES", "EXCLUDE_PDF_DIR_PARTS", "is_excluded_pdf", "detect_pdf_backends",
    "classify_ocr_quality", "stitch_cross_page_tables", "extract_pdf_document", "run_parse_docs",
]
