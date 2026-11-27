"""Mermaid Gate 4a (syntax + quote repair) and Gate 4b (OCR token grounding) for Chrysler heritage diagrams."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

CHRYSLER_ARCHETYPES: dict[str, str] = {
    "flowchart TD": "diagnostic_tree",
    "graph TD": "diagnostic_tree",
    "flowchart LR": "wiring_or_pneumatic",
    "graph LR": "wiring_or_pneumatic",
    "sequenceDiagram": "sci_timing",
    "stateDiagram-v2": "sci_timing",
}

_FLOW_TYPES = frozenset({"flowchart", "graph"})
_SEQ_BLOCK_KEYWORDS = frozenset({"alt", "loop", "opt", "par", "critical", "break", "rect", "box"})
_SEQ_LABEL_KEYWORDS = frozenset({"alt", "else", "opt", "loop", "par", "and", "critical", "option", "break"})
_FLOW_DIRECTIVE_PREFIXES = frozenset(
    {"flowchart", "graph", "end", "style", "classDef", "linkStyle", "class", "direction", "click", "accTitle", "accDescr"}
)
_HAZARDOUS_LABEL_CHARS = frozenset("()/$+#")
_DELIM_PAIRS = {")": "(", "]": "[", "}": "{"}
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_TWO_CHAR_WRAPPERS = frozenset({("(", "["), ("[", "("), ("[", "["), ("(", "("), ("{", "{")})

_FLOW_HEADER_RE = re.compile(r"^(flowchart|graph)(?:\s+(TD|TB|BT|RL|LR))?$")
_SINGLE_HEADER_RE = re.compile(r"^(sequenceDiagram|stateDiagram-v2|erDiagram|classDiagram)$")
_ER_REL_RE = re.compile(r"[|}o]{1,2}(?:--|\.\.)[|{o]{1,2}")
_FLOW_BAD_EDGE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*(?:-->|---|==>|<--|-\.->|-\.-|--x|--o|<-->|o--o|x--x)"), "Dangling Mermaid edge start"),
    (re.compile(r"(?:--|==|-\.|<--|-->|---|==>|<->|<-->|o--o|x--x|-\.->)\s*(?:\|[^|]*\|)?\s*$"), "Dangling Mermaid edge end"),
    (re.compile(r"(?<![-.])->(?![->])"), "Invalid single-dash arrow '->'"),
    (re.compile(r"(?:-->|---|==>|<-->|-\.->)\s*(?:-->|---|==>|<-->|-\.->)"), "Empty node between chained Mermaid arrows"),
)
_SEQ_ARROW_RE = re.compile(r"(?:-->>|->>|-->|->|--x|-x|--\)|-\))")
_CLASS_ARROW_RE = re.compile(r"(?:<\|--|--\|>|\*--|o--|\.\.\|>|\.\.>|<--|-->|--)")
_INLINE_ARROW_END_RE = re.compile(r"\s+(?:-->|---|-\.->|==>|--o|--x)")
_QUOTED_INNER_RE = re.compile(r'"((?:\\.|[^"\\])*)"')

_PIN_LIST_PAT = r"(\d{1,3}(?:-\d{1,3})?(?:\s*(?:/|&|,)\s*\d{1,3}(?:-\d{1,3})?)*)(?!\.\.|[-\s]?way\b|-\d|[A-Za-z0-9_%])"
_PIN_EXTRACT_RE = re.compile(rf"\b(?:(red|blue)\s+(?:pins?[\s_]+)?|(pins?|cavs?|cavity|cavities)(?:\.?\s+|_)){_PIN_LIST_PAT}", re.I)
_PIN_BACKING_RE = re.compile(rf"\b(?:(red|blue)\s+(?:pins?[\s_]+)?|(pins?|cavs?|cavity|cavities)\.?\s*[\s_]*){_PIN_LIST_PAT}", re.I)
_CIRCUIT_RE = re.compile(r"(?<![A-Za-z0-9_$])(DK|[ACDGJKNRSTXYZ])(\d{1,3})(?![A-Za-z0-9_])")
_CIRCUIT_BACKING_RE = re.compile(r"(?<![A-Za-z0-9_$])(DK|[ACDGJKNRSTXYZ])0*(\d{1,3})(?![A-Za-z0-9_])", re.I)
_NON_CIRCUIT_TOKENS = frozenset({"T1", "T2", "T3", "T4", "J1", "J2", "X1", "X2", "A413", "A525", "A555", "A568", "A604"})
_HEX_RE = re.compile(r"(?<![A-Za-z0-9_])(\$[0-9A-Fa-f]{2,4}|0[xX][0-9A-Fa-f]{2,4})(?![A-Za-z0-9_])")
_FAULT_CODE_RE = re.compile(r"\bCodes?\s+(\d{1,2}(?:\s*(?:/|&|,)\s*\d{1,2})*)(?![A-Za-z0-9_])", re.I)


class MermaidGateError(ValueError):
    """Raised when Mermaid syntax or structural validation fails."""


def _strip_mermaid_comment(raw_line: str) -> str:
    """Strip trailing %% comments outside double-quoted strings and trim whitespace."""
    in_quotes, escaped = False, False
    for idx, ch in enumerate(raw_line):
        if escaped:
            escaped = False
        elif ch == "\\" and in_quotes:
            escaped = True
        elif ch == '"':
            in_quotes = not in_quotes
        elif not in_quotes and raw_line[idx : idx + 2] == "%%":
            return raw_line[:idx].strip()
    return raw_line.strip()


def _match_header_line(line: str) -> tuple[str, str] | None:
    """Return (diagram_type, archetype) for a header line, or None if invalid."""
    header_norm = " ".join(line.split())
    match = _FLOW_HEADER_RE.match(header_norm) or _SINGLE_HEADER_RE.match(header_norm)
    return (match.group(1), CHRYSLER_ARCHETYPES.get(header_norm, "general")) if match else None


def _try_parse_header_metadata(block: str) -> tuple[str, str]:
    """Best-effort extraction of (diagram_type, archetype) from the first non-comment line."""
    if not isinstance(block, str):
        return "", "unknown"
    for raw in block.splitlines():
        if cleaned := _strip_mermaid_comment(raw):
            return _match_header_line(cleaned) or ("", "unknown")
    return "", "unknown"


def _skip_quoted(text: str, start: int) -> int:
    """Return index immediately after closing double quote from start, or len(text)."""
    escaped = False
    for idx in range(start + 1, len(text)):
        if escaped:
            escaped = False
        elif text[idx] == "\\":
            escaped = True
        elif text[idx] == '"':
            return idx + 1
    return len(text)


def _find_matching_delimiter(text: str, open_idx: int) -> int | None:
    """Find matching closing delimiter on the same line, respecting double quotes."""
    stack, idx = [], open_idx
    while idx < len(text):
        ch = text[idx]
        if ch == '"':
            idx = _skip_quoted(text, idx)
            continue
        if ch in _OPEN_TO_CLOSE:
            stack.append(ch)
        elif ch in _DELIM_PAIRS:
            if not stack or stack[-1] != _DELIM_PAIRS[ch]:
                return None
            stack.pop()
            if not stack:
                return idx
        idx += 1
    return None


def _is_quoted_label(inner: str) -> bool:
    """Return True when inner text is a single properly closed double-quoted string."""
    trimmed = inner.strip()
    return len(trimmed) >= 2 and trimmed.startswith('"') and trimmed.endswith('"') and _skip_quoted(trimmed, 0) == len(trimmed)


def _unwrap_node_shape(raw_span: str) -> tuple[str, str, str]:
    """Split a balanced node delimiter span into (prefix_delim, inner_label, suffix_delim)."""
    open_ch, close_ch, body = raw_span[0], raw_span[-1], raw_span[1:-1]
    if len(body) >= 2 and (open_ch, body[0]) in _TWO_CHAR_WRAPPERS and body[-1] == _OPEN_TO_CLOSE.get(body[0]):
        if _find_matching_delimiter(body, 0) == len(body) - 1:
            return open_ch + body[0], body[1:-1], body[-1] + close_ch
    return open_ch, body, close_ch


def _has_node_prefix(line: str, idx: int) -> bool:
    """Return True if idx immediately follows a flowchart node ID (or subgraph ID)."""
    if idx <= 0:
        return False
    if line[idx - 1].isalnum() or line[idx - 1] == "_":
        return True
    if line[idx - 1].isspace() and line.lstrip().startswith("subgraph "):
        parts = line[:idx].rstrip().split()
        return len(parts) == 2 and parts[0] == "subgraph" and (parts[1][-1].isalnum() or parts[1][-1] == "_")
    return False


def _scan_flowchart_line(line: str) -> tuple[list[tuple[int, int, str, str, str]], list[str]]:
    """Extract balanced node spans and edge labels from a single flowchart line."""
    node_spans, edge_labels = [], []
    stripped = line.lstrip()
    if not stripped or stripped.startswith("%%"):
        return node_spans, edge_labels
    first_tok = stripped.split()[0]
    if first_tok in _FLOW_DIRECTIVE_PREFIXES and not (first_tok == "subgraph" and "[" in stripped):
        return node_spans, edge_labels

    idx, length = 0, len(line)
    while idx < length:
        ch = line[idx]
        if line[idx : idx + 2] == "%%":
            break
        if ch == '"':
            idx = _skip_quoted(line, idx)
        elif ch == "|":
            end_pipe = idx + 1
            while end_pipe < length and line[end_pipe] != "|":
                end_pipe = _skip_quoted(line, end_pipe) if line[end_pipe] == '"' else end_pipe + 1
            if end_pipe < length:
                edge_labels.append(line[idx + 1 : end_pipe])
            idx = end_pipe + 1 if end_pipe < length else idx + 1
        elif line[idx : idx + 3] in {"-- ", "-. ", "== "} and (idx == 0 or line[idx - 1].isspace() or line[idx - 1] in '])}"|'):
            if arrow_end := _INLINE_ARROW_END_RE.search(line, idx + 3):
                edge_labels.append(line[idx + 3 : arrow_end.start()])
                idx = arrow_end.end()
            else:
                idx += 3
        elif ch in _OPEN_TO_CLOSE and _has_node_prefix(line, idx) and (close_idx := _find_matching_delimiter(line, idx)) is not None:
            prefix, inner, suffix = _unwrap_node_shape(line[idx : close_idx + 1])
            node_spans.append((idx, close_idx + 1, prefix, inner, suffix))
            idx = close_idx + 1
        else:
            idx += 1
    return node_spans, edge_labels


def _check_mermaid_delimiters(body_lines: list[str], diagram_type: str) -> None:
    """Verify balanced quotes, brackets/parens/braces, and block end keywords."""
    stack: list[str] = []
    in_quotes, block_depth = False, 0
    for line in body_lines:
        scan_line = _ER_REL_RE.sub(" ", line) if diagram_type == "erDiagram" else line
        escaped = False
        for ch in scan_line:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = not in_quotes
            elif not in_quotes and ch in _OPEN_TO_CLOSE:
                stack.append(ch)
            elif not in_quotes and ch in _DELIM_PAIRS:
                if not stack or stack[-1] != _DELIM_PAIRS[ch]:
                    raise MermaidGateError(f"Unbalanced Mermaid delimiter {ch!r} in line: {line!r}")
                stack.pop()
        if in_quotes:
            raise MermaidGateError(f"Unclosed double quote in Mermaid line: {line!r}")
        first_tok = line.split()[0]
        if (diagram_type in _FLOW_TYPES and first_tok == "subgraph") or (diagram_type == "sequenceDiagram" and first_tok in _SEQ_BLOCK_KEYWORDS):
            block_depth += 1
        elif line == "end":
            block_depth -= 1
            if block_depth < 0:
                raise MermaidGateError("Unexpected 'end' in Mermaid block")
    if stack:
        raise MermaidGateError(f"Unclosed Mermaid delimiter(s): {''.join(stack)!r}")
    if block_depth != 0:
        raise MermaidGateError("Unclosed Mermaid block (missing 'end')")


def _check_mermaid_edges(body_lines: list[str], diagram_type: str) -> None:
    """Run diagram-specific edge syntax and hazardous flowchart label checks."""
    for raw_line in body_lines:
        line = _QUOTED_INNER_RE.sub('"STR"', raw_line)
        if diagram_type in _FLOW_TYPES:
            for pattern, err_msg in _FLOW_BAD_EDGE_RULES:
                if pattern.search(line):
                    raise MermaidGateError(f"{err_msg} in {diagram_type}: {raw_line!r}")
            if line.count("|") % 2 != 0:
                raise MermaidGateError(f"Unclosed edge label pipe '|' in Mermaid line: {raw_line!r}")
            for _, _, _, inner, _ in _scan_flowchart_line(raw_line)[0]:
                if not _is_quoted_label(inner) and any(ch in _HAZARDOUS_LABEL_CHARS for ch in inner):
                    raise MermaidGateError(f"Unquoted hazardous character in Mermaid node label: {inner!r}")
        elif diagram_type == "sequenceDiagram":
            if (m := _SEQ_ARROW_RE.search(line)) and (not line[: m.start()].strip() or not line[m.end() :].strip() or line[m.end() :].lstrip().startswith(":")):
                raise MermaidGateError(f"Malformed sequenceDiagram arrow in line: {raw_line!r}")
        elif diagram_type == "stateDiagram-v2" and "-->" in line:
            lhs, rhs = line.split("-->", 1)
            if not lhs.strip() or not rhs.strip() or rhs.strip().startswith(":"):
                raise MermaidGateError(f"Malformed stateDiagram-v2 transition: {raw_line!r}")
        elif diagram_type == "erDiagram" and ("--" in line or ".." in line):
            if not _ER_REL_RE.search(line) or ":" not in line:
                raise MermaidGateError(f"Malformed erDiagram relationship: {raw_line!r}")
        elif diagram_type == "classDiagram":
            if (m := _CLASS_ARROW_RE.search(line)) and (not line[: m.start()].strip() or not line[m.end() :].strip()):
                raise MermaidGateError(f"Malformed classDiagram relationship: {raw_line!r}")


def _resolve_external_linter() -> tuple[str, str] | None:
    """Return (tool_name, binary_path) for installed mmdlint or mmdc, else None."""
    return next(((name, path) for name in ("mmdlint", "mmdc") if (path := shutil.which(name))), None)


def _run_external_mermaid_linter(block: str) -> str | None:
    """Invoke mmdlint or mmdc CLI when installed; return linter name or None."""
    if not (resolved := _resolve_external_linter()):
        return None
    tool_name, binary_path = resolved
    with tempfile.TemporaryDirectory() as tmp_dir:
        in_file = Path(tmp_dir) / "diagram.mmd"
        in_file.write_text(block, encoding="utf-8")
        cmd = [binary_path, str(in_file)] if tool_name == "mmdlint" else [binary_path, "-i", str(in_file), "-o", str(Path(tmp_dir) / "diagram.svg")]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=15)
        except subprocess.TimeoutExpired as exc:
            raise MermaidGateError(f"{tool_name} timed out after 15s") from exc
        if proc.returncode != 0:
            raise MermaidGateError(f"{tool_name} rejected Mermaid block: {(proc.stderr or proc.stdout).strip() or proc.returncode}")
    return tool_name


def check_mermaid_syntax_strict(block: str, *, use_external_linter: bool = False) -> str:
    """Validate Mermaid syntax deterministically (and optionally via mmdlint/mmdc); return diagram_type."""
    if not isinstance(block, str) or not (lines := [c for raw in block.splitlines() if (c := _strip_mermaid_comment(raw))]):
        raise MermaidGateError("Mermaid block must be a non-empty string")
    if not (parsed_header := _match_header_line(lines[0])):
        raise MermaidGateError(f"Unsupported or invalid Mermaid diagram header: {lines[0]!r}")
    diagram_type, _ = parsed_header
    if len(lines) < 2:
        raise MermaidGateError(f"Mermaid '{diagram_type}' block has no statements after header")
    _check_mermaid_delimiters(lines[1:], diagram_type)
    _check_mermaid_edges(lines[1:], diagram_type)
    if use_external_linter:
        _run_external_mermaid_linter(block)
    return diagram_type


def repair_mermaid_quotes(block: str) -> str:
    """Auto-quote unquoted hazardous flowchart labels containing (), /, $, +, # inside [], (), {}."""
    if not isinstance(block, str) or not block.strip() or _try_parse_header_metadata(block)[0] not in _FLOW_TYPES:
        return block
    out_lines: list[str] = []
    header_seen = False
    for line in block.splitlines(keepends=True):
        if not header_seen:
            header_seen = bool(_strip_mermaid_comment(line))
            out_lines.append(line)
            continue
        node_spans, _ = _scan_flowchart_line(line)
        if not node_spans:
            out_lines.append(line)
            continue
        chunks, cursor = [], 0
        for start, end, prefix, inner, suffix in node_spans:
            chunks.append(line[cursor:start])
            if not _is_quoted_label(inner) and any(ch in _HAZARDOUS_LABEL_CHARS for ch in inner):
                escaped = re.sub(r'(?<!\\)"', lambda _: '\\"', inner.strip())
                chunks.append(f'{prefix}"{escaped}"{suffix}')
            else:
                chunks.append(line[start:end])
            cursor = end
        chunks.append(line[cursor:])
        out_lines.append("".join(chunks))
    return "".join(out_lines)


def repair_and_validate_gate4a(
    block: str, *, max_repairs: int = 2, use_external_linter: bool = False
) -> dict[str, Any]:
    """Run Gate 4a strict syntax validation with up to max_repairs quote-repair passes."""
    budget = max(0, max_repairs)
    current = block if isinstance(block, str) else ""
    errors: list[str] = []
    attempts, valid = 0, False
    linter_name = (_resolve_external_linter() or (None,))[0] if use_external_linter else None

    for attempt in range(budget + 1):
        try:
            diagram_type = check_mermaid_syntax_strict(current, use_external_linter=use_external_linter)
            valid, errors, attempts = True, [], attempt
            break
        except MermaidGateError as exc:
            if (msg := str(exc)) not in errors:
                errors.append(msg)
            attempts = attempt
            if attempt >= budget:
                break
            repaired = repair_mermaid_quotes(current)
            if repaired == current:
                break
            current, attempts = repaired, attempt + 1

    parsed_type, archetype = _try_parse_header_metadata(current)
    return {
        "valid": valid, "diagram_type": diagram_type if valid else parsed_type, "archetype": archetype,
        "repaired_block": current, "repair_attempts": attempts, "errors": errors, "external_linter": linter_name,
    }


def _unquote_label(text: str) -> str:
    """Strip surrounding double quotes and unescape inner quotes from a label."""
    trimmed = text.strip()
    return trimmed[1:-1].replace('\\"', '"').strip() if _is_quoted_label(trimmed) else trimmed


def _extract_quoted_strings(line: str) -> list[str]:
    """Extract unescaped text from all double-quoted substrings on a line."""
    return [m.replace('\\"', '"') for m in _QUOTED_INNER_RE.findall(line)]


def _extract_diagram_labels(block: str) -> list[str]:
    """Extract only node, edge, and message labels (excluding bare IDs and comments)."""
    diagram_type, _ = _try_parse_header_metadata(block)
    if not diagram_type:
        return []
    labels: list[str] = []
    header_seen = False
    for raw in block.splitlines():
        if not (line := _strip_mermaid_comment(raw)):
            continue
        if not header_seen:
            header_seen = True
            continue
        if diagram_type in _FLOW_TYPES:
            node_spans, edge_labels = _scan_flowchart_line(line)
            labels.extend(_unquote_label(inner) for _, _, _, inner, _ in node_spans)
            labels.extend(_unquote_label(edge) for edge in edge_labels)
            if line.startswith("subgraph ") and not node_spans and '"' in line:
                labels.extend(_extract_quoted_strings(line))
        elif diagram_type == "sequenceDiagram":
            first_tok = line.split()[0]
            if first_tok in _SEQ_LABEL_KEYWORDS:
                labels.append(_unquote_label(line[len(first_tok) :]))
            elif first_tok == "participant" and " as " in line:
                labels.append(_unquote_label(line.split(" as ", 1)[1]))
            elif ":" in line and (_SEQ_ARROW_RE.search(line) or line.lower().startswith("note ")):
                labels.append(_unquote_label(line.split(":", 1)[1]))
        elif diagram_type == "stateDiagram-v2":
            if "-->" in line and ":" in line.split("-->", 1)[1]:
                labels.append(_unquote_label(line.split("-->", 1)[1].split(":", 1)[1]))
            elif line.startswith("state ") and '"' in line:
                labels.extend(_extract_quoted_strings(line))
            elif ":" in line:
                labels.append(_unquote_label(line.split(":", 1)[1]))
        else:
            if ":" in line:
                labels.append(_unquote_label(line.split(":", 1)[1]))
            labels.extend(_extract_quoted_strings(line))
    return [lbl for lbl in labels if lbl]


def _parse_pin_match(color_prefix: str | None, pin_prefix: str | None, raw_num: str) -> tuple[str, tuple[str, int]]:
    """Normalize a matched pin/cavity into (display_token, canonical_key)."""
    num = int(raw_num)
    if color_prefix:
        return f"{color_prefix.capitalize()} {num}", (color_prefix.lower(), num)
    prefix = "Pin" if (pin_prefix or "").lower().startswith("pin") else "Cav"
    return f"{prefix} {num}", ("pin", num)


def _extract_pin_tokens(text: str, pattern: re.Pattern[str]) -> list[tuple[str, tuple[str, int]]]:
    """Extract normalized (display_token, canonical_key) pairs, excluding hyphenated connector-pin pairs."""
    return [
        _parse_pin_match(m.group(1), m.group(2), tok)
        for m in pattern.finditer(text)
        for tok in re.split(r"\s*(?:/|&|,)\s*", m.group(3))
        if re.fullmatch(r"\d{1,3}", tok)
    ]


def _extract_fault_codes(text: str) -> list[str]:
    """Extract normalized 1-2 digit 'Code NN' fault tokens from text."""
    return [f"Code {int(n)}" for m in _FAULT_CODE_RE.finditer(text) for n in re.findall(r"\d{1,2}", m.group(1))]


def extract_mermaid_grounding_tokens(block: str) -> dict[str, list[str]]:
    """Extract deduplicated pins, Chrysler circuits, hex literals, and fault codes from diagram labels."""
    pins: dict[str, None] = {}
    circuits: dict[str, None] = {}
    fault_codes: dict[str, None] = {}
    hex_literals, seen_hex = [], set()
    for label in _extract_diagram_labels(block):
        pins.update(dict.fromkeys(disp for disp, _ in _extract_pin_tokens(label, _PIN_EXTRACT_RE)))
        circuits.update(dict.fromkeys(c for m in _CIRCUIT_RE.finditer(label) if (c := f"{m.group(1)}{m.group(2)}") not in _NON_CIRCUIT_TOKENS))
        for m in _HEX_RE.finditer(label):
            norm = f"0x{m.group(1)[2:]}" if m.group(1)[:2].lower() == "0x" else m.group(1)
            if norm.lower() not in seen_hex:
                seen_hex.add(norm.lower())
                hex_literals.append(norm)
        fault_codes.update(dict.fromkeys(_extract_fault_codes(label)))
    return {"pins": list(pins), "circuits": list(circuits), "hex_literals": hex_literals, "fault_codes": list(fault_codes)}


def _extract_table_pins(combined: str) -> set[tuple[str, int]]:
    """Extract numeric pin tokens from Markdown table columns whose header includes Pin or Cav."""
    pins, lines = set(), combined.splitlines()
    for idx in range(len(lines) - 2):
        hdr, sep = lines[idx].strip(), lines[idx + 1].strip()
        if hdr.startswith("|") and re.fullmatch(r"\|[\s:\-|]+\|", sep) and "-" in sep and any(k in hdr.strip("|").split("|", 1)[0].lower() for k in ("pin", "cav")):
            for line in lines[idx + 2 :]:
                if not line.strip().startswith("|"):
                    break
                c0 = line.strip().strip("|").split("|", 1)[0]
                pins.update(("pin", int(n)) for n in re.findall(r"(?<![A-Za-z0-9-])\d{1,3}(?!\.\.|[-\s]?way\b|-\d|[A-Za-z0-9%])", c0))
    return pins


def _collect_backing_sets(combined: str) -> tuple[set[tuple[str, int]], set[tuple[str, int]], set[str], set[str]]:
    """Build normalized lookup sets from OCR backing text and walkthrough."""
    backing_pins = {key for _, key in _extract_pin_tokens(combined, _PIN_BACKING_RE)} | _extract_table_pins(combined)
    backing_circuits = {(m.group(1).upper(), int(m.group(2))) for m in _CIRCUIT_BACKING_RE.finditer(combined)}
    backing_hex = {m.group(1).lower().replace("$", "0x") for m in _HEX_RE.finditer(combined)}
    return backing_pins, backing_circuits, backing_hex, set(_extract_fault_codes(combined))


def verify_mermaid_gate(
    block: str, backing_text: str, *, walkthrough: str = "", use_external_linter: bool = False, max_repairs: int = 2
) -> dict[str, Any]:
    """Run Gate 4a syntax/repair and Gate 4b Page-OCR token grounding verification."""
    gate4a = repair_and_validate_gate4a(block, max_repairs=max_repairs, use_external_linter=use_external_linter)
    extracted = extract_mermaid_grounding_tokens(gate4a["repaired_block"])
    combined = f"{backing_text or ''}\n{walkthrough or ''}"
    backing_empty = not combined.strip()
    b_pins, b_circuits, b_hex, b_codes = (set(), set(), set(), set()) if backing_empty else _collect_backing_sets(combined)
    matched, unmatched = [], []

    def _check_all(tokens: list[str], predicate: Any) -> None:
        for tok in tokens:
            target = matched if predicate(tok) else unmatched
            if tok not in target:
                target.append(tok)

    _check_all(extracted["pins"], lambda t: (m := _PIN_EXTRACT_RE.fullmatch(t)) is not None and _parse_pin_match(*m.groups())[1] in b_pins)
    _check_all(extracted["circuits"], lambda t: (m := _CIRCUIT_RE.fullmatch(t)) is not None and (m.group(1), int(m.group(2))) in b_circuits)
    _check_all(extracted["hex_literals"], lambda t: t.lower().replace("$", "0x") in b_hex)
    _check_all(extracted["fault_codes"], lambda t: t in b_codes)
    verified = gate4a["valid"] and not unmatched and not (any(extracted.values()) and backing_empty)
    errors = list(gate4a["errors"])
    if unmatched:
        errors.append(f"Unbacked Mermaid grounding token(s): {', '.join(unmatched)}")
    return {
        **gate4a, "mermaid_verification": "VERIFIED" if verified else "NEEDS_HUMAN_REVIEW",
        "errors": errors, "extracted_tokens": extracted, "matched_tokens": matched, "unmatched_tokens": unmatched,
    }
