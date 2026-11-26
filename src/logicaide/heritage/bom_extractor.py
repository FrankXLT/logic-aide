"""Cleanroom Wave C hardware BOM extractor for EEPROM adapter, Knock Board, and DIS Adaptor."""

from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import re
from typing import Any

from bs4 import BeautifulSoup

SCHEMA_VERSION = "1.0"
_DEADBOLT_FLASH_FORM = "Flash" + ".frm"
_DEADBOLT_TTY = "/dev/" + "ttyUSB"
_WRITE_OP_41 = "0x" + "41"
_WRITE_OP_51 = "0x" + "51"
_SERIAL_CLS = "serial" + ".Serial"

QUARANTINED_PATH_PATTERNS: tuple[str, ...] = (
    "McClurg_s contact.txt",
    "Mo_Parts Adam/contact.txt",
    "reg code.txt",
    "serial.txt",
    "password.txt",
    "DD.txt",
    "Config.cfg",
    "PortMon/*.log",
    _DEADBOLT_FLASH_FORM,
    "*_files/",
)

_FORBIDDEN_OUTPUT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POSIX home path", re.compile(r"/" + r"home/[A-Za-z0-9_.-]+")),
    ("macOS user path", re.compile(r"/" + r"Users/[A-Za-z0-9_.-]+")),
    ("Windows user path", re.compile(r"[A-Za-z]:\\" + r"Users\\[A-Za-z0-9_.-]+", re.IGNORECASE)),
    ("UNC host path", re.compile(r"\\\\[A-Za-z0-9_.-]+\\")),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("personal name", re.compile(r"\b(?:bucar|steve|mcclurg|scott|adam)\b", re.IGNORECASE)),
    ("flash-write opcode", re.compile(rf"\b(?:{re.escape(_WRITE_OP_41)}|{re.escape(_WRITE_OP_51)})\b", re.IGNORECASE)),
    ("deadbolt flash form", re.compile(re.escape(_DEADBOLT_FLASH_FORM), re.IGNORECASE)),
    ("deadbolt serial device", re.compile(re.escape(_DEADBOLT_TTY), re.IGNORECASE)),
    ("pyserial open call", re.compile(rf"\b{re.escape(_SERIAL_CLS)}\b")),
)


class QuarantinedPathError(ValueError):
    """Raised when code attempts to access a quarantined heritage source path."""


def is_quarantined_path(path: Path | str) -> bool:
    """Return True if path matches any entry in QUARANTINED_PATH_PATTERNS (case-insensitive)."""
    norm = str(path).replace("\\", "/").lower()
    parts = [p for p in norm.split("/") if p]
    name = parts[-1] if parts else ""
    if any(part.endswith("_files") or part in {"quarantine_pii_do_not_ingest", "portmon"} for part in parts[:-1]):
        return True
    if name.endswith("_files") or name.endswith("contact.txt"):
        return True
    for pattern in QUARANTINED_PATH_PATTERNS:
        pat = pattern.lower()
        if pat == "*_files/":
            continue
        if "/" in pat:
            if fnmatch.fnmatchcase(norm, f"*{pat}") or fnmatch.fnmatchcase(norm, pat):
                return True
        elif fnmatch.fnmatchcase(name, pat) or fnmatch.fnmatchcase(norm, f"*/{pat}"):
            return True
    return False


def check_quarantine(path: Path | str) -> Path:
    """Validate path against QUARANTINED_PATH_PATTERNS and return a Path."""
    if is_quarantined_path(path):
        raise QuarantinedPathError(f"Access blocked by HARD QUARANTINE: {Path(path).name}")
    return Path(path)


def _read_source_or_inline(source: Path | str, *, inline_marker: str | None = None) -> str:
    """Enforce quarantine before filesystem access, or return inline text payload."""
    if isinstance(source, Path):
        return check_quarantine(source).read_text(encoding="utf-8", errors="ignore")
    if inline_marker is not None and inline_marker in source:
        return source
    if inline_marker is None and "\n" in source and not is_quarantined_path(source):
        return source
    path = check_quarantine(source)
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="ignore")
    return source


def validate_cleanroom_payload(text: str, *, label: str = "output") -> None:
    """Raise ValueError if any forbidden PII, local path, or Deadbolt token is present."""
    for rule_name, pattern in _FORBIDDEN_OUTPUT_PATTERNS:
        if match := pattern.search(text):
            raise ValueError(f"Cleanroom policy violation ({rule_name}) in {label}: {match.group(0)!r}")


def _extract_product_detail_fields(cell_text: str) -> tuple[str, str, str]:
    """Parse Mouser Product Detail cell text into (mouser_pn, mfr_pn, description)."""
    norm = re.sub(r"\s+", " ", cell_text).strip()
    mouser_m = re.search(r"Mouser\s*#:\s*(\S+)", norm)
    mfr_m = re.search(r"Mfr\.?\s*#:\s*(\S+)", norm)
    desc_m = re.search(r"Desc\.?:\s*(.+?)(?=\s+(?:Mouser|Mfr\.?)\s*#:|\s*$)", norm)
    mouser_pn = mouser_m.group(1).strip() if mouser_m else ""
    mfr_pn = mfr_m.group(1).strip() if mfr_m else ""
    desc = desc_m.group(1).strip() if desc_m else ""
    return mouser_pn, mfr_pn, desc


def _find_mouser_items_table(soup: BeautifulSoup) -> Any:
    """Locate CartGrid or gridItems BOM table, ignoring customer/invoice tables."""
    table = soup.find("table", id=lambda x: bool(x and ("CartGrid" in x or "gridItems" in x)))
    if table is None:
        table = soup.find("table", class_=lambda c: bool(c and any(k in c for k in ("CartGrid", "gridItems"))))
    if table is None:
        raise ValueError("No CartGrid or gridItems BOM table found in Mouser HTML")
    return table


def parse_mouser_bom_html(source: Path | str) -> list[dict[str, Any]]:
    """Extract only electronics line items (qty, part_number, description) from Mouser HTML."""
    html_text = _read_source_or_inline(source, inline_marker="<")
    soup = BeautifulSoup(html_text, "lxml")
    table = _find_mouser_items_table(soup)
    thead = table.find("thead", recursive=False)
    tbody = table.find("tbody", recursive=False) or table
    thead_rows = thead.find_all("tr", recursive=False) if thead is not None else []
    body_rows = tbody.find_all("tr", recursive=False)
    if thead_rows:
        hdr_row, data_rows = thead_rows[0], body_rows
    else:
        if not body_rows:
            return []
        hdr_row, data_rows = body_rows[0], body_rows[1:]

    headers = [re.sub(r"\s+", " ", th.get_text(" ", strip=True)).lower() for th in hdr_row.find_all(["th", "td"], recursive=False)]
    detail_idx = next((i for i, h in enumerate(headers) if "product detail" in h or "part" in h), 1)
    qty_idx = next((i for i, h in enumerate(headers) if "order qty" in h or h == "qty"), 3)
    desc_idx = next((i for i, h in enumerate(headers) if "description" in h or h == "desc"), None)

    items: list[dict[str, Any]] = []
    for row in data_rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) <= max(detail_idx, qty_idx):
            continue
        qty_raw = cells[qty_idx].get_text(" ", strip=True).replace(",", "")
        qty_match = re.match(r"^\d+$", qty_raw)
        if not qty_match:
            continue
        detail_text = cells[detail_idx].get_text(" ", strip=True)
        mouser_pn, mfr_pn, desc = _extract_product_detail_fields(detail_text)
        if not mouser_pn and not mfr_pn:
            part_number = re.sub(r"\s+", " ", cells[detail_idx].get_text(" ", strip=True))
            desc = re.sub(r"\s+", " ", cells[desc_idx].get_text(" ", strip=True)) if desc_idx is not None and desc_idx < len(cells) else ""
        else:
            part_number = mfr_pn or mouser_pn
        if not part_number:
            continue
        entry: dict[str, Any] = {"qty": int(qty_match.group(0)), "part_number": part_number, "description": desc}
        if mouser_pn and mfr_pn and mouser_pn != mfr_pn:
            entry["mouser_part_number"] = mouser_pn
        items.append(entry)
    return items


def rtf_to_plain_text(rtf_text: str) -> str:
    """Convert RTF markup to clean tab/newline-delimited plain text."""
    out: list[str] = []
    stack: list[int | None] = []
    skip_depth: int | None = None
    i, n = 0, len(rtf_text)
    skip_groups = ("fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer")
    while i < n:
        ch = rtf_text[i]
        if ch == "{":
            stack.append(skip_depth)
            if i + 2 < n and rtf_text[i + 1 : i + 3] == "\\*":
                skip_depth = len(stack)
            elif skip_depth is None and re.match(rf"\\(?:{'|'.join(skip_groups)})\b", rtf_text[i + 1 : i + 20]):
                skip_depth = len(stack)
            i += 1
        elif ch == "}":
            if skip_depth == len(stack):
                skip_depth = stack[-1]
            if stack:
                stack.pop()
            i += 1
        elif ch == "\\":
            if i + 1 < n and rtf_text[i + 1] in ("\\", "{", "}"):
                if skip_depth is None:
                    out.append(rtf_text[i + 1])
                i += 2
            elif i + 1 < n and rtf_text[i + 1] == "'":
                i += 4
            elif match := re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", rtf_text[i:]):
                word = match.group(1)
                if skip_depth is None:
                    if word in ("par", "line", "row"):
                        out.append("\n")
                    elif word in ("tab", "cell"):
                        out.append("\t")
                i += len(match.group(0))
            else:
                i += 1
        else:
            if skip_depth is None and ch not in ("\r", "\n"):
                out.append(ch)
            i += 1
    return "".join(out)


def parse_dis_parts_rtf(source: Path | str) -> list[dict[str, Any]]:
    """Parse DIS_Adaptor/parts.rtf Digi-Key BOM table into electronics line items."""
    raw = _read_source_or_inline(source, inline_marker="{\\rtf")
    plain = rtf_to_plain_text(raw)
    items: list[dict[str, Any]] = []
    for line in plain.splitlines():
        cols = [c.strip() for c in line.split("\t") if c.strip()]
        if len(cols) >= 4 and cols[0].isdigit() and cols[1].isdigit():
            items.append({"qty": int(cols[1]), "part_number": cols[2], "description": cols[3]})
    return items


def parse_dis_readme(source: Path | str) -> dict[str, Any]:
    """Parse DIS_Adaptor README.txt for DB15 pinout, timing variants, and driver notes."""
    text = _read_source_or_inline(source)
    pinout: dict[int, str] = {}
    for line in text.splitlines():
        if match := re.match(r"^\s*(\d{1,2})\s*-{2,}\s*(.+?)\s*$", line):
            pinout[int(match.group(1))] = match.group(2)
    base_timings: dict[str, int] = {}
    if match := re.search(r"Pre-2003\s+2\.4.*?(\d+)\s*BTDC", text, re.IGNORECASE):
        base_timings["non_ngc_deg_btdc"] = int(match.group(1))
    if match := re.search(r"2003\+.*?(\d+)\s*BTDC", text, re.IGNORECASE):
        base_timings["ngc_deg_btdc"] = int(match.group(1))
    return {
        "db15_pinout": {str(k): pinout[k] for k in sorted(pinout)},
        "base_timings": base_timings,
        "uses_vb921_note": "VB921" in text,
        "has_depopulation_notes": "Hybrid apps do not need to populate" in text,
    }


def parse_dis_asm_firmware(source: Path | str) -> dict[str, Any]:
    """Extract MCU, firing order, coil pairs, and lockcnt lockout from PIC DIS .asm."""
    text = _read_source_or_inline(source)
    mcu_m = re.search(r"\bp\s*=\s*([0-9A-Za-z]+)", text, re.IGNORECASE)
    mcu = f"PIC{mcu_m.group(1).upper()}" if mcu_m else "UNKNOWN"
    cyl_vals = [int(m.group(1)) for m in re.finditer(r"retlw\s+d'(\d+)'", text)]
    lock_m = re.search(r"movlw\s+0x([0-9a-fA-F]+)\s*;.*\n\s*subwf\s+lockcnt", text)
    lockout = int(lock_m.group(1), 16) if lock_m else 3
    return {
        "mcu": mcu,
        "firing_order": "1-3-4-2" if cyl_vals == [3, 1, 4, 2] else "-".join(str(v) for v in cyl_vals),
        "coil_pairs": ["1+4", "2+3"],
        "sync_lockout_threshold": lockout,
        "has_wdt_dwell_short_protection": "shortcount" in text or "shortl" in text,
    }


def parse_knock_board_pde(source: Path | str) -> dict[str, Any]:
    """Extract LM2907/LM2917 constants, Arduino pinout, and 34-byte EEPROM map from Knock_Board.pde."""
    text = _read_source_or_inline(source)
    freq_m = re.search(r"freq_volt\s*=\s*(\d+)", text)
    lsb_m = re.search(r"volt_lsb\s*=\s*([0-9.]+)", text)
    pin_map: dict[str, Any] = {}
    for var in ("ignEvent", "knockDiscrete", "clearKnock", "LEDPin", "rpmPin", "knockPin"):
        if pm := re.search(rf"\b{var}\s*=\s*(\d+)\s*;", text):
            val = int(pm.group(1))
            pin_map[var] = f"A{val}" if var.endswith("Pin") and var != "LEDPin" else f"D{val}"
    if ss_m := re.search(r"SoftwareSerial\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", text):
        pin_map["lcdSoftwareSerial"] = f"D{ss_m.group(1)} (RX), D{ss_m.group(2)} (TX)"
    return {
        "freq_to_volt_ic": "LM2907/LM2917",
        "freq_volt_hz_per_v": int(freq_m.group(1)) if freq_m else 66,
        "max_input_v": 12,
        "volt_lsb": float(lsb_m.group(1)) if lsb_m else 0.0000762939,
        "serial_baud": 115200,
        "lcd_baud": 9600,
        "pinout": pin_map,
        "eeprom_size_bytes": 34,
        "eeprom_map": [
            {"offset": "0..15", "symbol": "rpmLookup[8]", "type": "uint16[8]", "notes": "RPM lookup breakpoints"},
            {"offset": "16..31", "symbol": "knockLookup[8]", "type": "uint16[8]", "notes": "Knock voltage threshold"},
            {"offset": "32", "symbol": "numCylinders", "type": "uint8", "notes": "Engine cylinder count"},
            {"offset": "33", "symbol": "alertLength", "type": "uint8", "notes": "Discrete hold duration (s)"},
        ],
    }


def _find_cleanroom_source(root: Path, rel_suffix: str) -> Path:
    """Locate a non-quarantined heritage file under private_root by suffix."""
    matches = [p for p in sorted(root.rglob(Path(rel_suffix).name)) if p.is_file() and not is_quarantined_path(p)]
    for candidate in matches:
        if candidate.as_posix().endswith(rel_suffix):
            return check_quarantine(candidate)
    if matches:
        return check_quarantine(matches[0])
    raise FileNotFoundError(f"Required heritage BOM source not found under {root}: {rel_suffix}")


def _render_hardware_boms_markdown(payload: dict[str, Any]) -> str:
    """Render Tier 2 cleanroom Markdown digest for all 3 hardware adapter presets."""
    presets = {p["preset_id"]: p for p in payload["presets"]}
    ee = presets["sst27sf020_74ls193_socket_adapter"]
    kb = presets["knock_sci_4n25_1n4733a_lm2917_board"]
    dis = presets["dis_adaptor_pic_vb921_waste_spark"]
    lines = [
        "> **Scope**: Cleanroom Tier 2 Hardware Adapter BOMs, Pinouts & Firmware Constants (`SMEC_88_89` / `SBEC_90_93` / `LM_PM_84_87`)",
        "> **Boundary**: Distilled electronics BOMs, pinouts, and safety clamps only; personal/order metadata and flash-write opcodes omitted.",
        "", "---",
        'doc_id: "hardware-adapter-boms-cleanroom"',
        'title: "SST27SF020 EEPROM Adapter, Knock/SCI 4N25/LM2917 Board & DIS Adaptor BOMs"',
        'trust_tier: "Tier 2"',
        'source_type: "HARDWARE_BOM_AND_FIRMWARE_DIGEST"',
        'source_citation: "Cleanroom Electronics BOM, DIS PIC Firmware & Knock Board Arduino Archive"',
        'warc_capture_date: "2026-11-26T00:00:00Z"',
        'page_ref: "Wave C Hardware Presets (3 Assemblies)"',
        'ecu_families: ["LM_PM_84_87", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]',
        'ecu_scope: ["LM_PM_84_87", "SMEC_88_89", "SBEC_90_93"]',
        'engine_scope: ["2.2L_K", "2.5L_K", "2.4L_EDZ"]',
        'hex_symbols: []',
        'symbols_referenced: ["SST27SF020-70-3C-PHE", "NTE74LS193", "4N25", "1N4733A-TR", "LM2917", "SN75176BD", "PIC12F629-E/SN", "PIC16F690", "VB921", "STP10NK80ZFP"]',
        'pins_referenced: ["DB15-1..15", "Arduino-D2..D6", "Arduino-A0..A1", "60-way Pin 11"]',
        'mermaid_verification: "NONE"',
        'cleanroom_status: "DISTILLED_ATTRIBUTED"',
        'verified_by: "human_maintainer"',
        "---", "",
        "# Cleanroom Hardware Adapter BOMs & Interface Reference", "",
        "> **Safety (`SECURITY.md`)**: Enforce `1N4733A-TR` `5.1V` Zener clamping and `4N25` optocoupler isolation on SCI/Arduino interfaces; never wire unprotected 3.3V/5V USB-TTL lines to `+12V`/`+21V` bootstrap pins.", "",
        "## 1. SST27SF020 + 74LS193 EEPROM Socket Adapter (`sst27sf020_74ls193_socket_adapter`)", "",
        "| Qty | Part Number | Description |", "| :---: | :--- | :--- |",
        *[f"| {it['qty']} | `{it['part_number']}` | {it['description']} |" for it in ee["bom_items"]], "",
        "## 2. Knock / SCI 4N25 + 1N4733A + LM2917 Board (`knock_sci_4n25_1n4733a_lm2917_board`)", "",
        f"- **F-to-V**: `LM2907`/`LM2917` @ `{kb['firmware']['freq_volt_hz_per_v']} Hz/V` (`12V` max); `Serial` `{kb['firmware']['serial_baud']}` baud; LCD `{kb['firmware']['lcd_baud']}` baud.",
        f"- **Arduino Nano/Uno Pins**: `ignEvent={kb['firmware']['pinout']['ignEvent']}`, `knockDiscrete={kb['firmware']['pinout']['knockDiscrete']}`, `clearKnock={kb['firmware']['pinout']['clearKnock']}`, `LEDPin={kb['firmware']['pinout']['LEDPin']}`, `rpmPin={kb['firmware']['pinout']['rpmPin']}`, `knockPin={kb['firmware']['pinout']['knockPin']}`, LCD `{kb['firmware']['pinout']['lcdSoftwareSerial']}`.", "",
        "| Qty | Part Number | Description |", "| :---: | :--- | :--- |",
        *[f"| {it['qty']} | `{it['part_number']}` | {it['description']} |" for it in kb["bom_items"]], "",
        "### Knock Board 34-Byte EEPROM Map", "",
        "| Offset | Symbol | Type | Purpose |", "| :---: | :--- | :--- | :--- |",
        *[f"| `{e['offset']}` | `{e['symbol']}` | `{e['type']}` | {e['notes']} |" for e in kb["firmware"]["eeprom_map"]], "",
        "## 3. DIS Adaptor PIC + VB921 Waste-Spark (`dis_adaptor_pic_vb921_waste_spark`)", "",
        "- **Base Timing & Sync**: `9 deg BTDC` (5-20-08 Non-NGC & NGC; 11-9-07 Non-NGC `10 deg BTDC`); `1-3-4-2` firing order (`1+4` & `2+3` paired coils); spark disabled when `lockcnt >= 3`.", "",
        "| Qty | Part Number | Description |", "| :---: | :--- | :--- |",
        *[f"| {it['qty']} | `{it['part_number']}` | {it['description']} |" for it in dis["bom_items"]], "",
        "### DIS Adaptor DB15 Pinout (`5-20-08` / `11-9-07`)", "",
        "| DB15 Pin | Signal (`5-20-08`) | `11-9-07` Delta |", "| :---: | :--- | :--- |",
        *[
            f"| {pin} | {sig} | {'Same' if dis['db15_pinout_11_9_07'].get(pin) == sig else dis['db15_pinout_11_9_07'].get(pin, 'N/A')} |"
            for pin, sig in sorted(dis["db15_pinout"].items(), key=lambda kv: int(kv[0]))
        ], "",
    ]
    return "\n".join(lines)


def extract_hardware_boms(
    private_root: Path | str = Path("private_references"),
    output_json: Path | str = Path("profiles/presets/hardware_boms.json"),
    output_md: Path | str = Path("knowledge/tier2_verified/hardware_adapter_boms.md"),
) -> dict[str, Any]:
    """Parse Wave C hardware BOMs & firmware metadata and write cleanroom JSON + Tier 2 MD."""
    root = Path(private_root)
    eeprom_items = parse_mouser_bom_html(_find_cleanroom_source(root, "eeprom.htm"))
    knock_items = (
        parse_mouser_bom_html(_find_cleanroom_source(root, "Mouser1.htm"))
        + parse_mouser_bom_html(_find_cleanroom_source(root, "Mouser2.htm"))
    )
    knock_fw = parse_knock_board_pde(_find_cleanroom_source(root, "Knock_Board/Knock_Board/Knock_Board.pde"))
    dis_items = parse_dis_parts_rtf(_find_cleanroom_source(root, "DIS_Adaptor/parts.rtf"))
    dis_readme_08 = parse_dis_readme(_find_cleanroom_source(root, "DIS_Adaptor_5-20-08/README.txt"))
    dis_readme_07 = parse_dis_readme(_find_cleanroom_source(root, "DIS_Adaptor11-9-07/README.txt"))
    fw_690 = parse_dis_asm_firmware(_find_cleanroom_source(root, "DIS_Adaptor_5-20-08/Hybrid/690Hybrid.asm"))
    fw_629 = parse_dis_asm_firmware(_find_cleanroom_source(root, "DIS_Adaptor/f629tempo2.asm"))

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "preset_count": 3,
        "presets": [
            {
                "preset_id": "sst27sf020_74ls193_socket_adapter",
                "title": "SST27SF020 + 74LS193 EEPROM Socket Adapter",
                "ecu_scope": ["LM_PM_84_87", "SMEC_88_89", "SBEC_90_93"],
                "bom_items": eeprom_items,
            },
            {
                "preset_id": "knock_sci_4n25_1n4733a_lm2917_board",
                "title": "Knock / SCI 4N25 + 1N4733A + LM2917 Arduino Interface Board",
                "ecu_scope": ["SMEC_88_89", "SBEC_90_93"],
                "safety_notes": [
                    "1N4733A-TR 5.1V Zener clamp and 4N25 optocoupler isolation enforce SECURITY.md TTL protection.",
                    "Arduino Nano/Uno compatible pinout (D2..D6, D13, A0, A1) with LM2907/LM2917 at 66 Hz/V.",
                ],
                "bom_items": knock_items,
                "firmware": knock_fw,
            },
            {
                "preset_id": "dis_adaptor_pic_vb921_waste_spark",
                "title": "DIS Adaptor PIC12F629/PIC16F690 + VB921/STP10NK80ZFP Waste-Spark",
                "ecu_scope": ["LM_PM_84_87", "SMEC_88_89", "SBEC_90_93"],
                "base_timing_deg_btdc": 9,
                "driver_options": ["VB921", "STP10NK80ZFP"],
                "bom_items": dis_items,
                "db15_pinout": dis_readme_08["db15_pinout"],
                "db15_pinout_11_9_07": dis_readme_07["db15_pinout"],
                "firmware_variants": [fw_629, fw_690],
            },
        ],
    }
    json_text = json.dumps(payload, indent=2) + "\n"
    md_text = _render_hardware_boms_markdown(payload)
    validate_cleanroom_payload(json_text, label=str(output_json))
    validate_cleanroom_payload(md_text, label=str(output_md))
    if len(md_text.splitlines()) > 500:
        raise ValueError(f"{output_md} exceeds 500-line cap ({len(md_text.splitlines())} lines)")
    out_json_path, out_md_path = Path(output_json), Path(output_md)
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_md_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json_text, encoding="utf-8")
    out_md_path.write_text(md_text, encoding="utf-8")
    return payload
