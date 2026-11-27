"""Parent-Child structural Markdown chunker for LogicAIDE cleanroom heritage docs."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import importlib
import json
from pathlib import Path
import re
from typing import Any, Sequence

import yaml

from logicaide.heritage.wiki_sync import validate_mermaid_block

try:
    from logicaide.heritage import mermaid_gate
except ImportError:  # Optional peer module; resolved dynamically if added later.
    mermaid_gate = None

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]
CHROME_BASENAMES = frozenset({"_Sidebar.md", "_Footer.md"})
VALID_TRUST_TIERS = frozenset({"Tier 1", "Tier 2", "Tier 3"})
VALID_MERMAID_STATES = frozenset({"VERIFIED", "NEEDS_HUMAN_REVIEW", "NONE"})
TABLE_SPLIT_THRESHOLD, TABLE_SLICE_SIZE, PROSE_MAX_WORDS = 35, 20, 350

_FENCE_OPEN_RE = re.compile(r"^([`~]{3,})(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SCOPE_BOUNDARY_RE = re.compile(r"^>\s*\*\*(?:Scope|Boundary)\*\*:", re.IGNORECASE)
_SEP_CELL_RE = re.compile(r"^:?-{1,}:?$")
_PIN_SINGLE_RE = re.compile(r"\b(?:Pin|Cavity|Cav)[\s_]+(\d{1,2})\b", re.IGNORECASE)
_PIN_UNDERSCORE_RE = re.compile(r"\bPin_(\d{1,2})\b", re.IGNORECASE)
_PIN_MULTI_RE = re.compile(r"\bPins?\s+(\d{1,2}(?:\s*[,/&]\s*\d{1,2})+)\b", re.IGNORECASE)
_PIN_CONN_RE = re.compile(r"\b(Red|Blue|DB15)\s+(?:Pin\s+)?(\d{1,2})\b", re.IGNORECASE)
_HEX_LITERAL_RE = re.compile(r"(?:(?<!\w)\$[0-9A-Fa-f]{2,4}\b|\b0x[0-9A-Fa-f]{2,4}\b)")
_PART_OR_CAST_RE = re.compile(r"(?:(?<!\w)#\d{3,4}\b|\bP\d{7}\b)")
_CIRCUIT_CODE_RE = re.compile(r"\b(?:(?:DK|[ACDGJKNRSTVXYZ])\d{1,3}|PIA[12]|ERR[0-3]|ERS[0-3]|IPL[12]|SW[12])\b")
_ROUTINE_SYM_RE = re.compile(
    r"\b(?:[A-Z][A-Z0-9_]{3,}|[A-Z][a-zA-Z0-9]+(?:_[A-Za-z0-9]+)+|[A-Z]{2,}[a-z]+[A-Z][A-Za-z0-9]*)\b"
)
_NOISE_CAPS = frozenset({"NOTE", "TIER", "ECU", "ENGINE", "SECTION", "SOURCE", "MERMAID", "HARD", "STOP"})
_FLOW_HEADER_RE = re.compile(r"^(flowchart|graph)(?:\s+(TD|TB|BT|RL|LR))?\b", re.IGNORECASE)
_SEQ_HEADER_RE = re.compile(r"^sequenceDiagram\b", re.IGNORECASE)
_STATE_HEADER_RE = re.compile(r"^stateDiagram(?:-v2)?\b", re.IGNORECASE)
_NODE_DEF_RE = re.compile(
    r"\b([A-Za-z0-9_.-]+)\s*(?:\(\[([^\]]+)\]\)|\[\[([^\]]+)\]\]|\(\(([^)]+)\)\)|\[([^\]]+)\]|\{([^}]+)\}|\(([^)]+)\))"
)
_FLOW_EDGE_RE = re.compile(r"\s*(?:<-->|<->|<--|-->|-\.->|-\.-|==>|===|---|--x|--o|o--o|x--x)\s*(?:\|([^|]+)\|)?\s*")
_SEQ_PARTICIPANT_RE = re.compile(r"^(?:participant|actor)\s+([A-Za-z0-9_.-]+)(?:\s+as\s+(.+))?$", re.IGNORECASE)
_SEQ_MSG_RE = re.compile(r"^(.+?)\s*(-->>|->>|-->|->|--x|-x|--\)|-\))\s*([^:]+?)\s*:\s*(.+)$")
_STATE_TRANS_RE = re.compile(r"^(.+?)\s*-->\s*([^:]+?)(?:\s*:\s*(.+))?$")


def _resolve_relpath(path: Path, repo_root: Path | None) -> str:
    resolved = path.resolve()
    candidates = ([Path(repo_root).resolve()] if repo_root is not None else []) + [DEFAULT_REPO_ROOT.resolve()]
    for root in candidates:
        if resolved.is_relative_to(root):
            return resolved.relative_to(root).as_posix()
    return path.as_posix()


def is_chrome_partial(md_path: Path | str, *, repo_root: Path | None = None) -> bool:
    """Return True for wiki chrome partials (_Sidebar.md, _Footer.md) and knowledge/README.md."""
    path = Path(md_path)
    if path.name in CHROME_BASENAMES:
        return True
    return _resolve_relpath(path, repo_root) == "knowledge/README.md" or (
        path.name == "README.md" and path.parent.name == "knowledge"
    )


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [s for item in value if (s := str(item).strip())]
    return [str(value).strip()] if str(value).strip() else []


def _extract_frontmatter_and_body(text: str) -> tuple[dict[str, Any], str]:
    lines = text.replace("\r\n", "\n").splitlines()
    idx = 0
    while idx < len(lines) and (not lines[idx].strip() or _SCOPE_BOUNDARY_RE.match(lines[idx].strip())):
        idx += 1
    if idx < len(lines) and lines[idx].strip() == "---":
        try:
            end_idx = lines.index("---", idx + 1)
        except ValueError as exc:
            raise ValueError("Unclosed YAML frontmatter fence") from exc
        parsed = yaml.safe_load("\n".join(lines[idx + 1 : end_idx])) or {}
        if not isinstance(parsed, dict):
            raise ValueError("YAML frontmatter must be a mapping")
        return parsed, "\n".join(lines[end_idx + 1 :]).strip("\n")
    return {}, "\n".join(lines).strip("\n")


def parse_heritage_document(md_path: Path | str, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Parse and normalize knowledge/**/*.md or wiki/**/*.md frontmatter; return {} for chrome."""
    path = Path(md_path)
    if is_chrome_partial(path, repo_root=repo_root):
        return {}
    raw_text = path.read_text(encoding="utf-8")
    fm, body_markdown = _extract_frontmatter_and_body(raw_text)
    relpath = _resolve_relpath(path, repo_root)
    doc_id = str(fm.get("doc_id") or fm.get("slug") or path.stem).strip()
    trust_tier = str(fm.get("trust_tier") or fm.get("tier") or "Tier 3").strip()
    trust_tier = trust_tier if trust_tier in VALID_TRUST_TIERS else "Tier 3"
    raw_date = fm.get("warc_capture_date") or fm.get("last_verified")
    warc_date = raw_date.isoformat() if isinstance(raw_date, (datetime, date)) else (str(raw_date).strip() or None if raw_date is not None else None)
    explicit_mmd = str(fm.get("mermaid_verification") or "").strip().upper()
    mmd_status = explicit_mmd if explicit_mmd in VALID_MERMAID_STATES else (
        "NEEDS_HUMAN_REVIEW" if str(fm.get("status") or "").strip().lower() == "needs_human_review" else "NONE"
    )
    return {
        "doc_id": doc_id, "title": str(fm.get("title") or doc_id).strip(), "trust_tier": trust_tier,
        "source_citation": str(fm.get("source_citation") or fm.get("provenance") or relpath).strip(),
        "page_ref": str(fm.get("page_ref") or relpath).strip(), "relpath": relpath, "warc_capture_date": warc_date,
        "ecu_scope": _coerce_str_list(fm.get("ecu_scope")), "engine_scope": _coerce_str_list(fm.get("engine_scope")),
        "hex_symbols": _coerce_str_list(fm.get("hex_symbols")),
        "symbols_referenced": _coerce_str_list(fm.get("symbols_referenced")),
        "pins_referenced": _coerce_str_list(fm.get("pins_referenced")),
        "mermaid_verification": mmd_status, "ecu_families": _coerce_str_list(fm.get("ecu_families")),
        "tags": _coerce_str_list(fm.get("tags")),
        "status": str(fm.get("status") or fm.get("cleanroom_status") or "").strip(),
        "body_markdown": body_markdown, "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    }


def format_scope_prefix(meta: dict[str, Any], section_path: str, mermaid_status: str) -> str:
    """Emit exact 2-line scope header eliminating cross-year ECU contamination."""
    clean = lambda v: re.sub(r"\s+", " ", str(v)).strip()
    doc_id = clean(meta.get("doc_id") or meta.get("slug") or "unknown")
    tier = clean(meta.get("trust_tier") or meta.get("tier") or "Tier 3")
    ecu_joined = ", ".join(_coerce_str_list(meta.get("ecu_scope"))) or "ALL"
    engine_joined = ", ".join(_coerce_str_list(meta.get("engine_scope"))) or "ALL"
    section = clean(section_path or meta.get("title") or "Overview")
    source = clean(meta.get("source_citation") or meta.get("provenance") or "unspecified")
    ref = clean(meta.get("page_ref") or meta.get("warc_capture_date") or meta.get("relpath") or "unspecified")
    status = clean(mermaid_status or "NONE").upper()
    status = status if status in VALID_MERMAID_STATES else "NONE"
    return (
        f"[DOC: {doc_id} | TIER: {tier} | ECU: {ecu_joined} | ENGINE: {engine_joined} | SECTION: {section}]\n"
        f"[SOURCE: {source} | REF: {ref} | MERMAID: {status}]"
    )


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|") or "-" not in stripped:
        return False
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def extract_pins_from_text(text: str, doc_pins: Sequence[str] = ()) -> list[str]:
    """Extract normalized pin/cavity references from chunk text, tables, and doc metadata."""
    found: set[str] = set()
    lines = text.splitlines()
    for idx in range(len(lines) - 2):
        hdr, sep = lines[idx].strip(), lines[idx + 1].strip()
        if hdr.startswith("|") and _is_table_separator(sep):
            cols = [c.strip().lower() for c in hdr.strip("|").split("|")]
            if cols and any(tok in cols[0] for tok in ("pin", "cavity")):
                pfx = "DB15 Pin" if "db15" in cols[0] else "Pin"
                r = idx + 2
                while r < len(lines) and lines[r].strip().startswith("|"):
                    c0 = lines[r].strip().strip("|").split("|")[0].strip("`* ")
                    if re.fullmatch(r"\d{1,2}", c0):
                        found.add(f"{pfx} {int(c0)}")
                    r += 1
    for m in _PIN_SINGLE_RE.finditer(text):
        found.add(f"Pin {int(m.group(1))}")
    for m in _PIN_UNDERSCORE_RE.finditer(text):
        found.add(f"Pin_{int(m.group(1))}")
    for m in _PIN_MULTI_RE.finditer(text):
        found.update(f"Pin {int(n)}" for n in re.findall(r"\d{1,2}", m.group(1)))
    found.update(f"{m.group(1)} {int(m.group(2))}" for m in _PIN_CONN_RE.finditer(text))
    found.update(p for p in doc_pins if p and p in text)
    return sorted(found)


def extract_symbols_from_text(
    text: str, doc_symbols: Sequence[str] = (), doc_hex: Sequence[str] = ()
) -> list[str]:
    """Extract hex literals, circuit codes, part numbers, and ROM symbols from chunk text."""
    found: set[str] = set()
    for pattern in (_HEX_LITERAL_RE, _PART_OR_CAST_RE, _CIRCUIT_CODE_RE, _ROUTINE_SYM_RE):
        for match in pattern.finditer(text):
            if (tok := match.group(0)) not in _NOISE_CAPS:
                found.add(tok)
    found.update(s for s in (*doc_symbols, *doc_hex) if s and s in text)
    return sorted(found)


def _consume_fence(lines: list[str], start: int) -> tuple[list[str], list[str], str, int]:
    match = _FENCE_OPEN_RE.match(lines[start].strip())
    assert match is not None
    marker, info = match.group(1), match.group(2).strip().lower()
    close_re = re.compile(rf"^{re.escape(marker[0])}{{{len(marker)},}}\s*$")
    raw, inner, idx = [lines[start]], [], start + 1
    while idx < len(lines):
        raw.append(lines[idx])
        if close_re.match(lines[idx].strip()):
            return raw, inner, (info.split()[0] if info else ""), idx + 1
        inner.append(lines[idx])
        idx += 1
    return raw, inner, (info.split()[0] if info else ""), idx


def _split_into_sections(body_markdown: str, doc_title: str) -> list[dict[str, Any]]:
    """Split markdown body by ## and ### headings while keeping fenced blocks atomic."""
    lines, sections, h2_title, h3_title, buf, idx = body_markdown.splitlines(), [], None, None, [], 0
    has_text = lambda buf_lines: any(ln.strip() and ln.strip() != "---" for ln in buf_lines)

    def _flush() -> None:
        if has_text(buf):
            path = f"{h2_title} > {h3_title}" if (h2_title and h3_title) else (h2_title or h3_title or doc_title or "Overview")
            sections.append({"section_path": path, "markdown": "\n".join(buf).strip()})
        buf.clear()

    while idx < len(lines):
        stripped = lines[idx].strip()
        if _FENCE_OPEN_RE.match(stripped):
            raw_fence, _, _, idx = _consume_fence(lines, idx)
            buf.extend(raw_fence)
            continue
        if h_match := _HEADING_RE.match(stripped):
            level, heading = len(h_match.group(1)), h_match.group(2).strip("` ").strip()
            if level == 1 and not h2_title and not h3_title and not has_text(buf):
                idx += 1
                continue
            if level in (2, 3):
                _flush()
                if level == 2:
                    h2_title, h3_title = heading, None
                else:
                    h3_title = heading
                idx += 1
                continue
        buf.append(lines[idx])
        idx += 1
    _flush()
    return sections or ([{"section_path": doc_title or "Overview", "markdown": body_markdown.strip()}] if body_markdown.strip() else [])


def _parse_section_blocks(section_markdown: str) -> list[dict[str, Any]]:
    """Decompose a parent section into atomic mermaid, table, and prose blocks."""
    lines, blocks, prose_buf, idx = section_markdown.splitlines(), [], [], 0

    def _flush_prose() -> None:
        if any(ln.strip() and ln.strip() != "---" for ln in prose_buf):
            blocks.append({"kind": "prose", "lines": list(prose_buf)})
        prose_buf.clear()

    while idx < len(lines):
        stripped = lines[idx].strip()
        if _FENCE_OPEN_RE.match(stripped):
            raw_fence, inner, lang, idx = _consume_fence(lines, idx)
            if lang == "mermaid":
                _flush_prose()
                blocks.append({"kind": "mermaid", "content": "\n".join(inner).strip()})
            else:
                prose_buf.extend(raw_fence)
        elif idx + 1 < len(lines) and stripped.startswith("|") and _is_table_separator(lines[idx + 1]):
            _flush_prose()
            header_line, sep_line, rows, idx = lines[idx], lines[idx + 1], [], idx + 2
            while idx < len(lines) and lines[idx].strip().startswith("|"):
                rows.append(lines[idx])
                idx += 1
            blocks.append({"kind": "table", "header": header_line, "separator": sep_line, "rows": rows})
        else:
            prose_buf.append(lines[idx])
            idx += 1
    _flush_prose()
    return blocks


def _normalize_gate_result(result: Any) -> str:
    if isinstance(result, str):
        return (norm := result.strip().upper()) if norm in VALID_MERMAID_STATES else "NEEDS_HUMAN_REVIEW"
    if isinstance(result, bool):
        return "VERIFIED" if result else "NEEDS_HUMAN_REVIEW"
    if isinstance(result, dict):
        for key in ("mermaid_verification", "status", "verdict"):
            if isinstance(val := result.get(key), str) and val.strip().upper() in VALID_MERMAID_STATES:
                return val.strip().upper()
        if "verified" in result:
            return "VERIFIED" if result["verified"] else "NEEDS_HUMAN_REVIEW"
    if isinstance(result, (tuple, list)) and result:
        return _normalize_gate_result(result[0])
    for attr in ("mermaid_verification", "status", "verified"):
        if hasattr(result, attr):
            return _normalize_gate_result(getattr(result, attr))
    return "NEEDS_HUMAN_REVIEW"


def _call_mermaid_gate(block: str, context: str) -> str:
    gate_mod = mermaid_gate
    if gate_mod is None:
        try:
            gate_mod = importlib.import_module("logicaide.heritage.mermaid_gate")
        except ImportError:
            gate_mod = None
    try:
        if gate_mod is not None and hasattr(gate_mod, "verify_mermaid_gate"):
            return _normalize_gate_result(gate_mod.verify_mermaid_gate(block, context))
        validate_mermaid_block(block, use_external_linter=False)
        return "VERIFIED"
    except (ValueError, RuntimeError):
        return "NEEDS_HUMAN_REVIEW"


def _extract_flowchart_items(body: list[str]) -> list[str]:
    labels: dict[str, str] = {}
    for line in body:
        for match in _NODE_DEF_RE.finditer(line):
            if lbl := next((g for g in match.groups()[1:] if g is not None), "").strip().strip("\"'"):
                labels[match.group(1)] = lbl
    subgraphs, items = [], []
    for line in body:
        low = line.lower()
        if low.startswith("subgraph "):
            subgraphs.append(line[9:].strip().strip('"'))
        elif low == "end" and subgraphs:
            subgraphs.pop()
        elif edge_match := _FLOW_EDGE_RE.search(line):
            strip_lbl = lambda expr: re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", "", re.sub(r'"(?:\\.|[^"\\])*"', "", expr))
            lhs_ids = [m.group(1) for p in strip_lbl(line[: edge_match.start()]).split("&") if (m := re.match(r"^\s*([A-Za-z0-9_.-]+)", p))]
            rhs_ids = [m.group(1) for p in strip_lbl(line[edge_match.end() :]).split("&") if (m := re.match(r"^\s*([A-Za-z0-9_.-]+)", p))]
            edge_lbl = (edge_match.group(1) or "").strip().strip('"')
            arrow = f"--[{edge_lbl}]-->" if edge_lbl else "-->"
            prefix = f"[Subgraph: {subgraphs[-1]}] " if subgraphs else ""
            for s in lhs_ids or [line[: edge_match.start()].strip()]:
                for d in rhs_ids or [line[edge_match.end() :].strip()]:
                    items.append(f"{prefix}{f'{s} ({labels[s]})' if s in labels else s} {arrow} {f'{d} ({labels[d]})' if d in labels else d}")
    return items


def _extract_sequence_items(body: list[str]) -> list[str]:
    aliases, stack, items = {}, [], []
    for line in body:
        if p_match := _SEQ_PARTICIPANT_RE.match(line):
            aliases[p_match.group(1)] = (p_match.group(2) or p_match.group(1)).strip()
            continue
        low = line.lower()
        if low.startswith("else") and stack:
            stack[-1] = line
        elif low.startswith(("alt ", "loop ", "opt ", "par ", "critical ")):
            stack.append(line)
        elif low == "end" and stack:
            stack.pop()
        elif m_match := _SEQ_MSG_RE.match(line):
            src, arrow, dst, msg = (m_match.group(i).strip() for i in range(1, 5))
            s_txt = f"{src} ({aliases[src]})" if aliases.get(src, src) != src else src
            d_txt = f"{dst} ({aliases[dst]})" if aliases.get(dst, dst) != dst else dst
            items.append(f"{f'[{stack[-1]}] ' if stack else ''}{s_txt} {arrow} {d_txt}: {msg}")
    return items


def _extract_mermaid_items(block: str) -> tuple[str, list[str]]:
    lines = [ln for raw in block.splitlines() if (ln := raw.split("%%", 1)[0].strip())]
    if not lines:
        return "SCHEMATIC_EDGE", [block]
    header, body = lines[0], lines[1:]
    if f_match := _FLOW_HEADER_RE.match(header):
        kind = "TREE_BRANCH" if (f_match.group(2) or "TD").upper() in {"TD", "TB"} else "SCHEMATIC_EDGE"
        return kind, (_extract_flowchart_items(body) or [block])
    if _SEQ_HEADER_RE.match(header):
        return "SCHEMATIC_EDGE", (_extract_sequence_items(body) or [block])
    if _STATE_HEADER_RE.match(header):
        items = [
            f"{m.group(1).strip()} --> {m.group(2).strip()}" + (f": {m.group(3).strip()}" if m.group(3) else "")
            for line in body if (m := _STATE_TRANS_RE.match(line))
        ]
        return "SCHEMATIC_EDGE", (items or [block])
    return "SCHEMATIC_EDGE", [block]


def _slice_table_block(block: dict[str, Any]) -> list[tuple[str, str]]:
    header, sep, rows = block["header"], block["separator"], block["rows"]
    total = len(rows)
    if total == 0:
        return [(f"{header}\n{sep}", "rows 0-0 of 0")]
    if total <= TABLE_SPLIT_THRESHOLD:
        return [("\n".join([header, sep, *rows]), f"rows 1-{total} of {total}")]
    return [
        ("\n".join([header, sep, *rows[s : min(s + TABLE_SLICE_SIZE, total)]]), f"rows {s + 1}-{min(s + TABLE_SLICE_SIZE, total)} of {total}")
        for s in range(0, total, TABLE_SLICE_SIZE)
    ]


def _split_prose_chunks(lines: list[str]) -> list[str]:
    raw = "\n".join(lines).strip()
    if not raw or len(raw.split()) <= PROSE_MAX_WORDS:
        return [raw] if raw else []
    chunks, cur, idx = [], [], 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if _FENCE_OPEN_RE.match(stripped):
            raw_fence, _, _, idx = _consume_fence(lines, idx)
            cur.extend(raw_fence)
            continue
        if cur and len(" ".join(cur).split()) >= PROSE_MAX_WORDS // 2 and (
            not stripped or stripped.startswith("#### ") or bool(_HEX_LITERAL_RE.match(stripped))
        ):
            if txt := "\n".join(cur).strip():
                chunks.append(txt)
            cur = []
            if not stripped:
                idx += 1
                continue
        cur.append(lines[idx])
        idx += 1
    if txt := "\n".join(cur).strip():
        chunks.append(txt)
    return chunks


def _build_child_row(
    meta: dict[str, Any], parent_id: str, child_idx: int, section_path: str,
    mermaid_status: str, chunk_kind: str, body_text: str, row_range: str | None = None,
) -> dict[str, Any]:
    prefix = format_scope_prefix(meta, section_path, mermaid_status)
    return {
        "child_id": f"{parent_id}::c{child_idx:03d}", "parent_id": parent_id, "doc_id": meta["doc_id"],
        "title": meta["title"], "chunk_kind": chunk_kind, "row_range": row_range,
        "prefixed_text": f"{prefix}\n\n{body_text.strip()}",
        "symbols_referenced_json": json.dumps(extract_symbols_from_text(body_text, meta["symbols_referenced"], meta["hex_symbols"])),
        "pins_referenced_json": json.dumps(extract_pins_from_text(body_text, meta["pins_referenced"])),
    }


def chunk_heritage_document(
    md_path: Path | str, *, repo_root: Path | None = None, verify_mermaid: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a heritage doc into parent sections and scope-prefixed child chunks."""
    meta = parse_heritage_document(md_path, repo_root=repo_root)
    if not meta:
        return [], []
    body = meta["body_markdown"]
    stripped_body = "\n".join(
        ln for b in _parse_section_blocks(body)
        for ln in (b["lines"] if b["kind"] == "prose" else ([b["header"], b["separator"], *b["rows"]] if b["kind"] == "table" else []))
    )
    doc_ctx = (
        f"{meta['title']} {' '.join(meta['ecu_scope'])} {' '.join(meta['engine_scope'])} "
        f"{' '.join(meta['hex_symbols'])} {' '.join(meta['symbols_referenced'])} {' '.join(meta['pins_referenced'])}\n{stripped_body}"
    )
    parents, children = [], []
    for sec_idx, sec in enumerate(_split_into_sections(body, meta["title"]), start=1):
        section_path, section_md = sec["section_path"], sec["markdown"]
        parent_id = f"{meta['doc_id']}::p{sec_idx:03d}"
        blocks = _parse_section_blocks(section_md)
        mmd_blocks = [b["content"] for b in blocks if b["kind"] == "mermaid"]
        mmd_src = "\n\n".join(mmd_blocks) if mmd_blocks else None
        if not mmd_blocks:
            mmd_status = "NONE"
        elif meta["mermaid_verification"] == "NEEDS_HUMAN_REVIEW":
            mmd_status = "NEEDS_HUMAN_REVIEW"
        elif verify_mermaid:
            sec_prose = "\n".join("\n".join(b["lines"]) for b in blocks if b["kind"] == "prose")
            statuses = [_call_mermaid_gate(blk, f"{doc_ctx}\n{section_path}\n{sec_prose}") for blk in mmd_blocks]
            mmd_status = "NEEDS_HUMAN_REVIEW" if any(s != "VERIFIED" for s in statuses) else "VERIFIED"
        else:
            mmd_status = meta["mermaid_verification"]
        parents.append({
            "parent_id": parent_id, "doc_id": meta["doc_id"], "title": meta["title"],
            "trust_tier": meta["trust_tier"], "ecu_scope": json.dumps(meta["ecu_scope"]),
            "engine_scope": json.dumps(meta["engine_scope"]), "section_path": section_path,
            "source_citation": meta["source_citation"], "page_ref": meta["page_ref"],
            "warc_capture_date": meta["warc_capture_date"], "body_markdown": section_md,
            "mermaid_source": mmd_src, "mermaid_verification": mmd_status,
            "token_count": max(1, len(section_md.split())),
            "sha256": hashlib.sha256(section_md.encode("utf-8")).hexdigest(),
        })
        child_items: list[tuple[str, str, str | None]] = []
        for blk in blocks:
            if blk["kind"] == "table":
                child_items.extend(("TABLE_SLICE", md, rr) for md, rr in _slice_table_block(blk))
            elif blk["kind"] == "mermaid":
                kind, items = _extract_mermaid_items(blk["content"])
                child_items.extend((kind, item, None) for item in items)
            else:
                child_items.extend(("NOTE_ROUTINE", txt, None) for txt in _split_prose_chunks(blk["lines"]))
        for c_idx, (kind, txt, rr) in enumerate(child_items or [("NOTE_ROUTINE", section_md, None)], start=1):
            children.append(_build_child_row(meta, parent_id, c_idx, section_path, mmd_status, kind, txt, rr))
    return parents, children


__all__ = [
    "DEFAULT_REPO_ROOT", "CHROME_BASENAMES", "TABLE_SPLIT_THRESHOLD", "TABLE_SLICE_SIZE",
    "is_chrome_partial", "parse_heritage_document", "format_scope_prefix",
    "extract_pins_from_text", "extract_symbols_from_text", "chunk_heritage_document",
]
