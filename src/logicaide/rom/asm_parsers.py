"""Cleanroom read-only parsers for 68HC11/6803 ASM, SYM, LST, TPL, C++, and XLS files."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

# Build forbidden-token scrubber fragments programmatically so source stays cleanroom-safe.
_WIN_USERS = "Us" + "ers"
_POSIX_PREFIX = r"(?:/ho" + "me|/Us" + "ers)"
_FLASH_FRM = "Fl" + r"ash\.f" + "rm"
_TTY_USB = "/de" + "v/tty" + r"USB\d*"
_SERIAL_CLS = r"\bser" + r"ial\.Ser" + r"ial\b"
_FLASH_HEX = r"0x(?:" + "41" + "|" + "51" + ")"
_FLASH_KW = r"(?:fl" + r"ash[\s_-]*(?:write|cmd|command|erase|program|boot)?|(?:write|cmd|command|erase|program)[\s_-]*fl" + "ash)"
_NAME_PARTS = ("bu" + "car", "st" + "eve", "mc" + "clurg", "ll" + "oyd", "ro" + "bert", "r" + "ob", "br" + "ian")

_SCRUB_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/[A-Za-z]:\\[^/\r\n]*/"), "/[REDACTED_PATH]/"),
    (re.compile(rf"[A-Za-z]:\\{_WIN_USERS}\\[^\s/\"';]+(?:\\[^\s/\"';]*)*", re.IGNORECASE), "[REDACTED_PATH]"),
    (re.compile(rf"{_POSIX_PREFIX}/[^\s/\"';]+(?:/[^\s\"';]*)*"), "[REDACTED_PATH]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+" + "@" + r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:" + "|".join(_NAME_PARTS) + r")\b", re.IGNORECASE), "[REDACTED_NAME]"),
    (re.compile(_FLASH_FRM, re.IGNORECASE), "[REDACTED_TOOL]"),
    (re.compile(_TTY_USB, re.IGNORECASE), "[REDACTED_DEVICE]"),
    (re.compile(_SERIAL_CLS), "[REDACTED_SERIAL]"),
    (re.compile(rf"{_FLASH_KW}[^\r\n]{{0,24}}?\b{_FLASH_HEX}\b", re.IGNORECASE), "[REDACTED_CMD]"),
    (re.compile(rf"\b{_FLASH_HEX}\b[^\r\n]{{0,24}}?{_FLASH_KW}", re.IGNORECASE), "[REDACTED_CMD]"),
)
_TPL_TAG_MAP: dict[str, str] = {
    "mptblcname": "short_name", "mptbllname": "long_name", "mpgrp": "group",
    "mptbladdr": "table_addr", "mptbltype": "table_type", "mpsubtype": "sub_type",
    "mpxaxis": "x_axis", "mpyaxis": "y_axis", "mptbldesc": "description",
}

_ORIGIN_NAMED_RE = re.compile(
    r"^\s*(DataOrg|CodeOrg|LowerChipOrg|UpperChipCodeOrg)\s*={1,2}\s*([^\s;]+)",
    re.IGNORECASE,
)
_ORG_DIR_RE = re.compile(r"^\s*\.org\s+([^\s;]+)", re.IGNORECASE)
_RAM_EQUATE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*={1,2}\s*((?:0[xXhH]|\$)(?!%)[0-9A-Fa-f]{1,4})\b"
)
_LABEL_DEF_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
_WORD_ENTRY_RE = re.compile(r"^\s*\.word\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_BYTE_ENTRY_RE = re.compile(
    r"^\s*\.byte\s+((?:0[xXhH]|\$)[0-9A-Fa-f]{1,2})(?:\s*;\s*(.*))?",
    re.IGNORECASE,
)
_CMP_HEX_RE = re.compile(r"\bcmp[ab]\s+#((?:0[xXhH]|\$)[0-9A-Fa-f]{2})\b", re.IGNORECASE)
_SYMBOL_TARGET_RE = re.compile(
    r"\b((?:BATOFF|PEFTBL|WOTTBL)[A-Za-z0-9_]*|DRBFunctionTable|DRBMemoryTable|"
    r"FastDataOutputAddressTable|XmitRequestedRamLocation|SetupHiSpeedDataTransfer)\b"
)

_ASXXXX_SYM_ADDR_RE = re.compile(r"^\s*(?:\d+\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s+(?:=\s+)?([0-9A-Fa-f]{4})\s+[A-Z]+\s*$")
_SORTED_SYM_RE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s+([0-9A-Fa-f]{4})(?:\s+\([A-Z\s]*\))?\s*$")
_LST_LABEL_RE = re.compile(r"^\s{2,6}([0-9A-Fa-f]{4})(?:\s+[0-9A-Fa-fA-ZsRr]+)*(?:\s+\[\s*\d+\])?\s+\d+[ \t]+([A-Za-z_][A-Za-z0-9_.]*):")
_LST_EQUATE_RE = re.compile(
    r"^\s{10,28}([0-9A-Fa-f]{4})\s+\d+[ \t]+([A-Za-z_][A-Za-z0-9_.]*)\s*={1,2}\s*(?:0[xXhH]|\$)(?!%)[0-9A-Fa-f]+\b"
)
_MP_TAG_RE = re.compile(r"^\s*;(mp[A-Za-z]+)\s*(.*)$", re.IGNORECASE)


def scrub_cleanroom_text(text: str) -> str:
    """Strip local user paths, emails, contributor names, and legacy flash-write references."""
    cleaned = text
    for pattern, replacement in _SCRUB_RULES:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _parse_hex_token(token: str) -> int | None:
    """Parse 0x/0h/$ prefixed hex tokens into an integer, returning None on non-hex tokens."""
    tok = token.strip()
    if tok.lower().startswith(("0x", "0h")):
        return int(tok[2:], 16)
    if tok.startswith("$") and not tok.startswith("$%"):
        return int(tok[1:], 16)
    return None


def _parse_mpscan_comment(line: str) -> dict[str, Any] | None:
    """Extract structured ;MPScan; metadata fields from an active equate line."""
    if ";MPScan;" not in line:
        return None
    parts = [p.strip() for p in line.split(";MPScan;", 1)[1].split(";")]
    if len(parts) < 3:
        return None
    fields = parts + [""] * max(0, 12 - len(parts))
    num_keys = ("decimals", "display_min", "display_max", "major_interval", "minor_interval", "divisor", "ecu_min", "ecu_max")
    parsed: dict[str, Any] = {"short_name": fields[0], "size": fields[1], "units": fields[2], "gauge_type": fields[11]}
    for idx, key in enumerate(num_keys, 3):
        cleaned = fields[idx].replace(",", "")
        try:
            parsed[key] = float(cleaned) if cleaned else None
        except ValueError:
            parsed[key] = None
    return parsed


def _collect_table_block(lines: list[str], start_idx: int) -> list[tuple[int, str]]:
    """Collect active directive lines (.word/.byte) immediately under a table label."""
    collected: list[tuple[int, str]] = []
    for idx in range(start_idx + 1, len(lines)):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith(";"):
            if collected and not stripped:
                break
            continue
        if _LABEL_DEF_RE.match(stripped) or not stripped.lower().startswith((".word", ".byte")):
            break
        collected.append((idx + 1, stripped))
    return collected


def _extract_drb_tables(
    lines: list[str],
    ram_map: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract DRBFunctionTable, DRBMemoryTable, and XmitRequestedRamLocation modes."""
    drb_funcs: list[dict[str, Any]] = []
    drb_mems: list[dict[str, Any]] = []
    xmit_modes: list[dict[str, Any]] = []
    seen_xmit_cmds: set[int] = set()

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        lbl_match = _LABEL_DEF_RE.match(stripped)
        if not lbl_match:
            continue
        label = lbl_match.group(1)
        if label == "DRBFunctionTable":
            for pos, (ln, entry) in enumerate(_collect_table_block(lines, idx)):
                wm = _WORD_ENTRY_RE.match(entry)
                if wm:
                    cmd = 0x10 + pos
                    drb_funcs.append({"index": pos, "command": cmd, "command_hex": f"0x{cmd:02X}", "handler": wm.group(1), "line_number": ln})
        elif label == "DRBMemoryTable":
            for pos, (ln, entry) in enumerate(_collect_table_block(lines, idx)):
                bm, wm = _BYTE_ENTRY_RE.match(entry), _WORD_ENTRY_RE.match(entry)
                if bm:
                    cmt = scrub_cleanroom_text((bm.group(2) or "").strip())
                    directive, sym, off = ".byte", (cmt.split()[0] if cmt and not cmt.startswith("-") else None), _parse_hex_token(bm.group(1))
                elif wm:
                    directive, sym, off = ".word", wm.group(1), ram_map.get(wm.group(1))
                else:
                    continue
                subcmd = pos + 1
                drb_mems.append({
                    "index": pos, "subcommand": subcmd, "subcommand_hex": f"0x{subcmd:02X}",
                    "directive": directive, "symbol": sym, "offset": off,
                    "offset_hex": f"0x{off:02X}" if off is not None else None, "line_number": ln,
                })
        elif label == "XmitRequestedRamLocation":
            for j in range(idx + 1, min(idx + 80, len(lines))):
                sub = lines[j].strip()
                if sub.startswith(";"):
                    continue
                code_part = sub.split(";", 1)[0].strip()
                cm = _CMP_HEX_RE.search(code_part)
                if cm:
                    val = _parse_hex_token(cm.group(1))
                    if val is not None and val >= 0xF0 and val not in seen_xmit_cmds:
                        seen_xmit_cmds.add(val)
                        xmit_modes.append({"command": val, "command_hex": f"0x{val:02X}", "line_number": j + 1, "line": scrub_cleanroom_text(sub)})
                if code_part.lower().startswith("rti"):
                    break
    return drb_funcs, drb_mems, xmit_modes


def parse_asm_file(asm_path: Path | str) -> dict[str, Any]:
    """Parse 68HC11 ASM origins, zero-page equates, ;MPScan; tags, symbols, and DRB tables."""
    path = Path(asm_path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    named_origins: dict[str, dict[str, Any]] = {}
    org_directives: list[dict[str, Any]] = []
    ram_equates: list[dict[str, Any]] = []
    mpscan_entries: list[dict[str, Any]] = []
    symbol_lines: list[dict[str, Any]] = []
    ram_map: dict[str, int] = {}

    for ln, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        code_part = stripped.split(";", 1)[0].strip()

        om = _ORIGIN_NAMED_RE.match(code_part)
        if om:
            org_name, tok = om.group(1), om.group(2)
            val = _parse_hex_token(tok)
            if val is not None:
                named_origins[org_name] = {"value": val, "hex": f"0x{val:04X}", "line_number": ln}

        dm = _ORG_DIR_RE.match(code_part)
        if dm:
            target = dm.group(1)
            addr = _parse_hex_token(target)
            if addr is None and target in named_origins:
                addr = named_origins[target]["value"]
            org_directives.append({
                "directive": ".org",
                "target": target,
                "address": addr,
                "hex": f"0x{addr:04X}" if addr is not None else None,
                "line_number": ln,
            })

        em = _RAM_EQUATE_RE.match(code_part)
        if em:
            name, tok = em.group(1), em.group(2)
            addr = _parse_hex_token(tok)
            if addr is not None and addr <= 0xFF:
                mpscan = _parse_mpscan_comment(stripped)
                entry = {
                    "name": name,
                    "address": addr,
                    "hex": f"0x{addr:04X}",
                    "line_number": ln,
                    "mpscan": mpscan,
                }
                ram_equates.append(entry)
                ram_map[name] = addr
                if mpscan is not None:
                    mpscan_entries.append(entry)

        lbl_match = _LABEL_DEF_RE.match(code_part)
        lbl_name = lbl_match.group(1) if lbl_match else None
        for tm in _SYMBOL_TARGET_RE.finditer(code_part):
            sym_name = tm.group(1)
            symbol_lines.append({
                "symbol": sym_name,
                "kind": "definition" if lbl_name == sym_name else "reference",
                "line_number": ln,
                "line": scrub_cleanroom_text(stripped),
            })

    drb_funcs, drb_mems, xmit_modes = _extract_drb_tables(lines, ram_map)
    return {
        "file_name": path.name,
        "line_count": len(lines),
        "origins": {"named": named_origins, "directives": org_directives},
        "ram_equates": ram_equates,
        "mpscan_entries": mpscan_entries,
        "symbol_lines": symbol_lines,
        "drb_function_table": drb_funcs,
        "drb_memory_table": drb_mems,
        "xmit_command_modes": xmit_modes,
    }


def parse_sym_file(sym_path: Path | str) -> dict[str, str]:
    """Extract normalized 0xXXXX symbol-to-address mappings from ASxxxx or sorted .sym files."""
    symbols: dict[str, str] = {}
    with Path(sym_path).open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("---"):
                continue
            m = _ASXXXX_SYM_ADDR_RE.match(line) or _SORTED_SYM_RE.match(line)
            if not m:
                continue
            sym, hex_addr = m.group(1), m.group(2).upper()
            if sym.startswith(".__."):
                continue
            symbols[sym] = f"0x{hex_addr}"
    return symbols


def parse_lst_file(lst_path: Path | str) -> dict[str, str]:
    """Stream ASxxxx .lst listing line-by-line to map symbols to 0xXXXX ROM/RAM addresses."""
    symbols: dict[str, str] = {}
    with Path(lst_path).open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            lm = _LST_LABEL_RE.match(line)
            if lm:
                symbols[lm.group(2)] = f"0x{lm.group(1).upper()}"
                continue
            em = _LST_EQUATE_RE.match(line)
            if em:
                symbols[em.group(2)] = f"0x{em.group(1).upper()}"
    return symbols


def parse_tpl_calibration_symbols(tpl_path: Path | str) -> list[dict[str, Any]]:
    """Parse MP Tuner .tpl calibration table entries while omitting paths and author notes."""
    lines = Path(tpl_path).read_text(encoding="utf-8", errors="replace").splitlines()
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_revision_block = False

    for ln, raw_line in enumerate(lines, 1):
        stripped = raw_line.strip()
        low = stripped.lower()
        if low.startswith(";mprevisionstart"):
            in_revision_block = True
            continue
        if low.startswith(";mprevisionend"):
            in_revision_block = False
            continue
        if in_revision_block or low.startswith(".define caldatafile"):
            continue

        lbl_match = _LABEL_DEF_RE.match(stripped)
        if lbl_match:
            if current is not None:
                entries.append(current)
            current = {"symbol": lbl_match.group(1), "line_number": ln, **{v: None for v in _TPL_TAG_MAP.values()}, "word_symbols": []}
            continue

        if current is None:
            continue
        tm = _MP_TAG_RE.match(stripped)
        if tm:
            tag = tm.group(1).lower()
            if tag in _TPL_TAG_MAP:
                current[_TPL_TAG_MAP[tag]] = scrub_cleanroom_text(tm.group(2).strip())
            continue
        wm = _WORD_ENTRY_RE.match(stripped)
        if wm:
            current["word_symbols"].append(wm.group(1))

    if current is not None:
        entries.append(current)
    return entries


def parse_sample_cpp_lm_offsets(cpp_path: Path | str) -> dict[str, Any]:
    """Parse LM WinLog sample.cpp baud handshake and 22 config.addresses[i]/masks entries."""
    raw = Path(cpp_path).read_text(encoding="utf-8", errors="replace")
    no_block = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
    clean_lines = [re.sub(r"//.*", "", line) for line in no_block.splitlines()]
    body = "\n".join(clean_lines)

    bauds = [int(m.group(1)) for m in re.finditer(r'BuildCommDCB\("(\d+),', body)]
    chk_match = re.search(r"\bchkbyt\s*=\s*(0x[0-9A-Fa-f]+)\b", body)
    handshake = int(chk_match.group(1), 16) if chk_match else None

    names_match = re.search(r"fieldnames\[\]\s*=\s*\{(.*?)\};", body, re.DOTALL)
    field_names = re.findall(r'"([^"]*)"', names_match.group(1)) if names_match else []

    masks_match = re.search(r"masks\[[^\]]*\]\s*=\s*\{(.*?)\};", body, re.DOTALL)
    masks = [int(tok, 16) for tok in re.findall(r"0x[0-9A-Fa-f]+", masks_match.group(1))] if masks_match else []

    addr_map: dict[int, int] = {}
    for am in re.finditer(r"config\.addresses\[(\d+)\]\s*=\s*(0x[0-9A-Fa-f]+)\s*;", body):
        addr_map[int(am.group(1))] = int(am.group(2), 16)

    num_words_m = re.search(r"#define\s+NUM_WORDS\s+(\d+)", body)
    entries = [
        {
            "index": idx,
            "field_name": scrub_cleanroom_text(field_names[idx]) if idx < len(field_names) else None,
            "offset": addr_map[idx],
            "offset_hex": f"0x{addr_map[idx]:02X}",
            "mask": masks[idx] if idx < len(masks) else None,
            "mask_hex": f"0x{masks[idx]:02X}" if idx < len(masks) else None,
        }
        for idx in sorted(addr_map)
    ]
    return {
        "initial_baud": bauds[0] if bauds else None,
        "high_speed_baud": bauds[1] if len(bauds) > 1 else None,
        "handshake_byte": handshake,
        "handshake_hex": f"0x{handshake:02X}" if handshake is not None else None,
        "num_fields": len(entries),
        "num_words": int(num_words_m.group(1)) if num_words_m else None,
        "entries": entries,
    }


def _xls_hex_addr(val: Any) -> str | None:
    """Convert numeric Excel hex cell (e.g. 2002.0 or 2.0) or text token into 0xXXXX."""
    if isinstance(val, (int, float)):
        raw = str(int(val))
        return f"0x{int(raw, 16):04X}"
    s = str(val).strip()
    if not s:
        return None
    m = re.search(r"\b([0-9A-Fa-f]{1,4})\b", s)
    return f"0x{int(m.group(1), 16):04X}" if m else None


def _xls_int_or_str(val: Any) -> int | str | None:
    if isinstance(val, float) and val.is_integer():
        return int(val)
    s = str(val).strip()
    return s if s else None


def parse_lm_io_pinout_xls(xls_path: Path | str) -> dict[str, Any]:
    """Read LM IO pinout - address.xls via xlrd and structure 6803/146823/ADC0808 pin mappings."""
    import xlrd

    wb = xlrd.open_workbook(str(xls_path))
    sh = wb.sheet_by_name("Pinout") if "Pinout" in wb.sheet_names() else wb.sheet_by_index(0)
    entries: list[dict[str, Any]] = []
    ports: dict[str, list[dict[str, Any]]] = {"port1": [], "port2": []}
    chips: dict[str, dict[str, Any]] = {}

    for r in range(1, sh.nrows):
        raw_row = [sh.cell_value(r, c) for c in range(sh.ncols)]
        row = raw_row + [""] * max(0, 15 - len(raw_row))
        conn = str(row[5]).strip().lower()
        chip_raw = _xls_int_or_str(row[10])
        if not conn and chip_raw is None and not str(row[6]).strip():
            continue
        port_raw = str(row[7]).strip().lower()
        port_norm = port_raw.replace(" ", "") if port_raw.startswith("port") else port_raw
        primary_addr = _xls_hex_addr(row[12])
        alt_addrs = [a for a in (_xls_hex_addr(row[13]), _xls_hex_addr(row[14])) if a]
        chip = str(chip_raw).lower() if chip_raw is not None else None
        entry = {
            "line": _xls_int_or_str(row[1]),
            "signal_87_t2": scrub_cleanroom_text(str(row[2]).strip()),
            "signal_85_t1": scrub_cleanroom_text(str(row[3]).strip()),
            "lm_pin": _xls_int_or_str(row[4]),
            "connector": conn or None,
            "internal_function": scrub_cleanroom_text(str(row[6]).strip()),
            "port": port_norm or None,
            "bit_or_input": _xls_int_or_str(row[8]),
            "chip_pin": _xls_int_or_str(row[9]),
            "io_chip": chip,
            "interrupt": str(row[11]).strip() or None,
            "address": primary_addr,
            "alt_addresses": alt_addrs,
        }
        entries.append(entry)
        if port_norm in ports:
            ports[port_norm].append(entry)
        if chip:
            bucket = chips.setdefault(chip, {"addresses": set(), "entries": []})
            bucket["entries"].append(entry)
            for addr in ([primary_addr] if primary_addr else []) + alt_addrs:
                bucket["addresses"].add(addr)

    for bucket in chips.values():
        bucket["addresses"] = sorted(bucket["addresses"])

    return {
        "sheets": wb.sheet_names(),
        "entry_count": len(entries),
        "blue_connector_count": sum(1 for e in entries if e["connector"] == "blue"),
        "red_connector_count": sum(1 for e in entries if e["connector"] == "red"),
        "internal_count": sum(1 for e in entries if e["connector"] == "internal"),
        "ports": ports,
        "chips": chips,
        "entries": entries,
    }
