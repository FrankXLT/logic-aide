"""Cleanroom Wave C heritage document curator (.doc via antiword, .htm via lxml/BS4, YAML & Deadbolt gates)."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

from bs4 import BeautifulSoup
import yaml

from logicaide.heritage.bom_extractor import (
    QUARANTINED_PATH_PATTERNS,
    QuarantinedPathError,
    check_quarantine,
    is_quarantined_path,
)

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KNOWLEDGE_DIR = DEFAULT_REPO_ROOT / "knowledge"
MAX_DIGEST_LINES = 500

_DEADBOLT_FLASH_FORM = "Flash" + ".frm"
_DEADBOLT_TTY = "/dev/" + "ttyUSB"
_WRITE_OP_41 = "0x" + "41"
_WRITE_OP_51 = "0x" + "51"
_SERIAL_CLS = "serial" + ".Serial"

REQUIRED_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "doc_id",
    "title",
    "trust_tier",
    "source_type",
    "source_citation",
    "warc_capture_date",
    "page_ref",
    "ecu_families",
    "ecu_scope",
    "engine_scope",
    "hex_symbols",
    "symbols_referenced",
    "pins_referenced",
    "mermaid_verification",
    "cleanroom_status",
    "verified_by",
)
VALID_TRUST_TIERS = frozenset({"Tier 1", "Tier 2", "Tier 3"})

WAVE_C_DIGEST_RELPATHS: tuple[str, ...] = (
    "tier2_verified/lm_87_t2_red_blue_50way_pinout.md",
    "tier2_verified/lm_6803_spark_boost_and_table_math.md",
    "tier2_verified/injector_and_3bar_map_scaling.md",
    "tier2_verified/dohc_ecc_kblock_hybrid_machining.md",
    "tier2_verified/smec_28c256_readonly_hardware_notes.md",
    "tier3_community/sbecii_92_pinout_conversion_notes.md",
    "tier3_community/daytona_digital_dash_26pin.md",
)

_PII_SCRUB_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/" + r"home/[^\s/\"']+(?:/[^\s\"']*)*"), "[REDACTED_PATH]"),
    (re.compile(r"/" + r"Users/[^\s/\"']+(?:/[^\s\"']*)*"), "[REDACTED_PATH]"),
    (re.compile(r"[A-Za-z]:\\" + r"Users\\[^\s\\\"']+(?:\\[^\s\"']*)*", re.IGNORECASE), "[REDACTED_PATH]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\\\\[A-Za-z0-9_.-]+(?:\\[^\s\"']*)*"), "[REDACTED_HOST]"),
    (re.compile(r"\b(?:bucar|steve|mcclurg|scott|adam)\b", re.IGNORECASE), "[REDACTED_NAME]"),
)

_DEADBOLT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("flash-write opcode", re.compile(rf"\b(?:{re.escape(_WRITE_OP_41)}|{re.escape(_WRITE_OP_51)})\b", re.IGNORECASE)),
    ("flash form token", re.compile(re.escape(_DEADBOLT_FLASH_FORM), re.IGNORECASE)),
    ("serial device path", re.compile(re.escape(_DEADBOLT_TTY), re.IGNORECASE)),
    ("pyserial open call", re.compile(rf"\b{re.escape(_SERIAL_CLS)}\b")),
)

_FORBIDDEN_DIGEST_PII: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POSIX home path", re.compile(r"/" + r"home/[A-Za-z0-9_.-]+")),
    ("macOS user path", re.compile(r"/" + r"Users/[A-Za-z0-9_.-]+")),
    ("Windows user path", re.compile(r"[A-Za-z]:\\" + r"Users\\[A-Za-z0-9_.-]+", re.IGNORECASE)),
    ("UNC host path", re.compile(r"\\\\[A-Za-z0-9_.-]+\\")),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("personal name", re.compile(r"\b(?:bucar|steve|mcclurg|scott|adam)\b", re.IGNORECASE)),
)


class DeadboltViolationError(ValueError):
    """Raised when forbidden flash-write opcodes or hardware serial tokens appear."""


class DigestValidationError(ValueError):
    """Raised when a curated cleanroom markdown digest fails schema or safety checks."""


def scrub_pii(text: str) -> str:
    """Scrub local home paths, email addresses, UNC hosts, and personal names from text."""
    cleaned = text
    for pattern, replacement in _PII_SCRUB_RULES:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def assert_no_deadbolt_tokens(text: str, *, label: str = "document") -> None:
    """Reject flash-write opcodes, serial device paths, and Flash tool tokens."""
    for rule_name, pattern in _DEADBOLT_RULES:
        if match := pattern.search(text):
            raise DeadboltViolationError(f"Hardware Deadbolt violation ({rule_name}) in {label}: {match.group(0)!r}")


def _redact_deadbolt_tokens(text: str) -> str:
    """Redact flash-write opcodes and Deadbolt tokens when digesting raw archival docs."""
    cleaned = text
    for _, pattern in _DEADBOLT_RULES:
        cleaned = pattern.sub("[REDACTED_DEADBOLT]", cleaned)
    return cleaned


def extract_doc_text(
    doc_path: Path | str,
    *,
    antiword_bin: str = "antiword",
    redact_deadbolt: bool = False,
) -> str:
    """Extract cleanroom plain text from a legacy .doc via antiword with quarantine & Deadbolt guards."""
    path = check_quarantine(doc_path)
    resolved = path.resolve()
    check_quarantine(resolved)
    if not resolved.is_file():
        raise FileNotFoundError(f"Legacy .doc file not found: {path}")
    try:
        proc = subprocess.run(
            [antiword_bin, str(resolved)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Failed to invoke '{antiword_bin}' on {path.name}: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"antiword exited {proc.returncode} on {path.name}: {proc.stderr.strip()}")
    cleaned = scrub_pii(proc.stdout)
    if redact_deadbolt:
        return _redact_deadbolt_tokens(cleaned)
    assert_no_deadbolt_tokens(cleaned, label=path.name)
    return cleaned


def _format_gfm_pipe_table(rows: list[list[str]]) -> list[str]:
    """Format normalized 2D cell rows into a GitHub-Flavored Markdown pipe table."""
    if not rows:
        return []
    width = max(len(r) for r in rows)
    if width == 0:
        return []
    padded = [r + [""] * (width - len(r)) for r in rows]
    escaped = [[cell.replace("|", "\\|") for cell in r] for r in padded]
    header = "| " + " | ".join(escaped[0]) + " |"
    separator = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(r) + " |" for r in escaped[1:]]
    return [header, separator, *body]


def html_table_to_gfm(table_tag: Any) -> list[str]:
    """Convert a BeautifulSoup <table> element into GFM pipe table lines."""
    rows: list[list[str]] = []
    for tr in table_tag.find_all("tr"):
        if tr.find_parent("table") is not table_tag:
            continue
        row_cells: list[str] = []
        for cell in tr.find_all(["th", "td"], recursive=False):
            text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            colspan_raw = str(cell.get("colspan", "1") or "1").strip()
            colspan = min(int(colspan_raw), 64) if colspan_raw.isdigit() else 1
            row_cells.append(text)
            row_cells.extend([""] * max(0, colspan - 1))
        if any(c for c in row_cells):
            rows.append(row_cells)
    if len(rows) < 2 and not any(rows):
        return []
    return _format_gfm_pipe_table(rows)


def pre_block_to_gfm(pre_text: str) -> list[str]:
    """Normalize aligned/tabbed <pre> text into a GFM pipe table when tabular, else fenced block."""
    raw_lines = [ln.rstrip() for ln in pre_text.splitlines() if ln.strip()]
    if not raw_lines:
        return []
    split_rows = [re.split(r"\t+|\s{2,}", ln.strip()) for ln in raw_lines]
    col_counts = [len(r) for r in split_rows]
    if len(split_rows) >= 2 and min(col_counts) >= 2 and max(col_counts) <= 12:
        return _format_gfm_pipe_table(split_rows)
    return ["```text", *raw_lines, "```"]


def html_to_gfm_sections(
    source: Path | str,
    *,
    scrub: bool = True,
    check_deadbolt: bool = True,
) -> dict[str, Any]:
    """Parse .htm/.html via lxml/BeautifulSoup, normalizing <table> & <pre> blocks to GFM."""
    is_path = isinstance(source, Path) or (isinstance(source, str) and "<" not in source and Path(source).is_file())
    if is_path:
        path = check_quarantine(source)
        html_text = path.read_text(encoding="utf-8", errors="ignore")
        label = path.name
    else:
        html_text = str(source)
        label = "html_input"

    soup = BeautifulSoup(html_text, "lxml")
    for junk in soup(["script", "style"]):
        junk.decompose()
    title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)) if soup.title else ""

    tables: list[str] = []
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None:
            continue
        if gfm := html_table_to_gfm(table):
            table_md = "\n".join(gfm)
            tables.append(table_md)
            table.replace_with(soup.new_string(f"\n\n{table_md}\n\n"))

    pre_blocks: list[str] = []
    for pre in soup.find_all("pre"):
        if gfm := pre_block_to_gfm(pre.get_text("\n")):
            pre_md = "\n".join(gfm)
            pre_blocks.append(pre_md)
            pre.replace_with(soup.new_string(f"\n\n{pre_md}\n\n"))

    body_text = soup.body.get_text("\n") if soup.body else soup.get_text("\n")
    normalized_lines = [ln.strip() for ln in body_text.splitlines()]
    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(normalized_lines)).strip()
    if scrub:
        title = scrub_pii(title)
        tables = [scrub_pii(t) for t in tables]
        pre_blocks = [scrub_pii(p) for p in pre_blocks]
        markdown = scrub_pii(markdown)
    if check_deadbolt:
        assert_no_deadbolt_tokens(title, label=f"{label}:title")
        assert_no_deadbolt_tokens(markdown, label=label)
    return {"title": title, "tables": tables, "pre_blocks": pre_blocks, "markdown": markdown}


def validate_cleanroom_digest(
    md_source: Path | str,
    *,
    max_lines: int = MAX_DIGEST_LINES,
) -> dict[str, Any]:
    """Validate digest line cap, Scope/Boundary header, YAML frontmatter, PII, and Deadbolt rules."""
    if isinstance(md_source, Path) or (isinstance(md_source, str) and "\n" not in md_source and Path(md_source).is_file()):
        path = Path(md_source)
        label = path.name
        text = path.read_text(encoding="utf-8")
    else:
        label = "digest"
        text = str(md_source)

    lines = text.splitlines()
    if len(lines) > max_lines:
        raise DigestValidationError(f"{label}: exceeds {max_lines}-line cap ({len(lines)} lines)")
    if len(lines) < 6 or not lines[0].startswith("> **Scope**:") or not lines[1].startswith("> **Boundary**:"):
        raise DigestValidationError(f"{label}: missing 2-line Scope/Boundary header")
    if lines[2] != "" or lines[3] != "---":
        raise DigestValidationError(f"{label}: expected blank line then '---' YAML fence at line 4")
    try:
        end_idx = lines.index("---", 4)
    except ValueError as exc:
        raise DigestValidationError(f"{label}: unclosed YAML frontmatter fence") from exc

    fm = yaml.safe_load("\n".join(lines[4:end_idx]))
    if not isinstance(fm, dict):
        raise DigestValidationError(f"{label}: YAML frontmatter must be a mapping")
    missing = [k for k in REQUIRED_FRONTMATTER_FIELDS if k not in fm]
    if missing:
        raise DigestValidationError(f"{label}: missing frontmatter keys: {', '.join(missing)}")
    if fm["trust_tier"] not in VALID_TRUST_TIERS:
        raise DigestValidationError(f"{label}: invalid trust_tier {fm['trust_tier']!r}")

    for rule_name, pattern in _FORBIDDEN_DIGEST_PII:
        if match := pattern.search(text):
            raise DigestValidationError(f"{label}: forbidden {rule_name} detected ({match.group(0)!r})")
    assert_no_deadbolt_tokens(text, label=label)

    body = "\n".join(lines[end_idx + 1 :])
    if fm["doc_id"] == "smec-28c256-readonly-hardware-notes":
        if fm.get("ecu_scope") != ["SMEC_88_89"]:
            raise DigestValidationError(f"{label}: ecu_scope must be locked strictly to ['SMEC_88_89']")
        if "Z12" not in body or "Pin 11" not in body:
            raise DigestValidationError(f"{label}: missing mandatory Pin 11 Z12 ground-short warning")
    if fm["trust_tier"] == "Tier 3":
        if "NEVER" not in body or "FSM" not in body:
            raise DigestValidationError(f"{label}: Tier 3 digest missing NEVER sole pinout / FSM corroboration warning")
    return fm


def _probe_non_quarantined_sources(root: Path) -> list[str]:
    """Verify non-quarantined Wave C .htm/.doc sources parse cleanly when private_root exists."""
    probed: list[str] = []
    if not root.is_dir():
        return probed
    targets = ("Adding Table Points.doc", "SMEC_EEPROM_vs2.doc")
    for name in targets:
        matches = [p for p in sorted(root.rglob(name)) if p.is_file() and not is_quarantined_path(p)]
        if matches:
            extract_doc_text(matches[0], redact_deadbolt=True)
            probed.append(name)
    html_targets = ("injectors.php.htm", "map.php.htm", "92-t1-sbecii-pinouts-conversion-91-a.htm", "digital_dash.htm")
    for name in html_targets:
        matches = [p for p in sorted(root.rglob(name)) if p.is_file() and not is_quarantined_path(p)]
        if matches:
            html_to_gfm_sections(matches[0], scrub=True, check_deadbolt=True)
            probed.append(name)
    return probed


def curate_wave_c_knowledge(
    private_root: Path | str = Path("private_references"),
    knowledge_dir: Path | str = Path("knowledge"),
) -> dict[str, Any]:
    """Curate and validate all Wave C Tier 2 & Tier 3 cleanroom knowledge digests."""
    probed = _probe_non_quarantined_sources(Path(private_root))
    target_dir = Path(knowledge_dir)
    validated: list[dict[str, Any]] = []

    for rel in WAVE_C_DIGEST_RELPATHS:
        dest = target_dir / rel
        if not dest.is_file():
            canonical = DEFAULT_KNOWLEDGE_DIR / rel
            if not canonical.is_file():
                raise FileNotFoundError(f"Canonical cleanroom digest not found: {canonical}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
        fm = validate_cleanroom_digest(dest)
        validated.append({"path": rel, "doc_id": fm["doc_id"], "trust_tier": fm["trust_tier"], "lines": len(dest.read_text(encoding="utf-8").splitlines())})

    hw_bom_md = target_dir / "tier2_verified" / "hardware_adapter_boms.md"
    if hw_bom_md.is_file():
        fm = validate_cleanroom_digest(hw_bom_md)
        validated.append({"path": "tier2_verified/hardware_adapter_boms.md", "doc_id": fm["doc_id"], "trust_tier": fm["trust_tier"], "lines": len(hw_bom_md.read_text(encoding="utf-8").splitlines())})

    return {
        "probed_private_sources": probed,
        "curated_count": len(validated),
        "digests": validated,
    }


__all__ = [
    "QUARANTINED_PATH_PATTERNS",
    "QuarantinedPathError",
    "DeadboltViolationError",
    "DigestValidationError",
    "is_quarantined_path",
    "check_quarantine",
    "scrub_pii",
    "assert_no_deadbolt_tokens",
    "extract_doc_text",
    "html_table_to_gfm",
    "pre_block_to_gfm",
    "html_to_gfm_sections",
    "validate_cleanroom_digest",
    "curate_wave_c_knowledge",
]
