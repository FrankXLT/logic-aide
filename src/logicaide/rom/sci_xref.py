"""Builder and query API for the 402-variable / 10-protocol MPR SCI cross-reference."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Any

from logicaide.rom.asm_parsers import scrub_cleanroom_text

PROTOCOL_COLUMNS: tuple[str, ...] = (
    "LM",
    "SMEC T1",
    "SMEC T2",
    "SBEC T1",
    "SBEC TIII",
    "SBECII T1",
    "SBECII TIII",
    "FCC",
    "CUSTOM1",
    "CUSTOM2",
)
DEFAULT_XREF_OUTPUT = Path("rom/sci/mpr_sci_crossref.json")
ERROR_REGISTER_ITEM_RANGE = frozenset(range(174, 182))

_FUNC_PAIR_RE = re.compile(r"\(\s*([^:()\s]+)\s*:\s*([^:()\s]+)\s*\)")
_PROTO_ALIAS_MAP: dict[str, str] = {
    re.sub(r"[\s_]+", "", proto.lower()): proto for proto in PROTOCOL_COLUMNS
}


def _read_nonempty_csv(csv_path: Path) -> tuple[int, list[dict[str, str]]]:
    """Read non-empty CSV rows via csv.DictReader and return (nonempty_line_count, rows)."""
    raw_lines = csv_path.read_text(encoding="utf-8", errors="replace").splitlines()
    nonempty = [line for line in raw_lines if line.strip()]
    if not nonempty:
        return 0, []
    return len(nonempty), list(csv.DictReader(nonempty))


def _row_item_num(row: dict[str, str]) -> int:
    """Extract ItemNum from rows that use either 'ItemNum' or the 'ItemNum2034' header quirk."""
    raw = row.get("ItemNum")
    if raw is None or not raw.strip():
        raw = row.get("ItemNum2034")
    if raw is None or not raw.strip():
        raise ValueError(f"CSV row missing ItemNum/ItemNum2034: {row!r}")
    return int(raw.strip())


def _parse_proto_offset(raw: str | None) -> dict[str, Any] | None:
    """Parse a protocol offset cell into {'offset': int, 'hex': '0xXX'} or None when blank."""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    val = _parse_offset_value(stripped)
    return {"offset": val, "hex": f"0x{val:02X}"}


def _parse_offset_value(offset: int | str) -> int:
    """Parse integer, '0x63', or '$63' offset input into an integer."""
    if isinstance(offset, bool):
        raise ValueError("Boolean is not a valid SCI offset")
    if isinstance(offset, int):
        return offset
    text = offset.strip()
    if not text:
        raise ValueError("Empty offset string")
    if text.lower().startswith("0x"):
        return int(text, 16)
    if text.startswith("$"):
        return int(text[1:], 16)
    return int(text, 10)


def _parse_number(raw: str | None) -> int | float | None:
    """Parse numeric setting cell preserving integers when no decimal point is present."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    return float(text) if "." in text else int(text)


def _parse_bit_flags(row: dict[str, str]) -> dict[str, str]:
    """Build bit_7..bit_0 flag mapping from an mprbitsettings.csv row."""
    return {
        f"bit_{bit}": scrub_cleanroom_text((row.get(f"Bit {bit}") or "").strip())
        for bit in range(7, -1, -1)
    }


def _parse_function_points(raw: str | None) -> list[dict[str, float]]:
    """Parse '(V:Y);...' transfer-function string into [{'volts': float, 'value': float}, ...]."""
    if not raw:
        return []
    return [
        {"volts": float(v_str), "value": float(y_str)}
        for v_str, y_str in _FUNC_PAIR_RE.findall(raw)
    ]


def _resolve_protocol(protocol: str) -> str:
    """Resolve a protocol name (allowing whitespace/underscore/case variation) to canonical name."""
    if protocol in PROTOCOL_COLUMNS:
        return protocol
    key = re.sub(r"[\s_]+", "", protocol.strip().lower())
    resolved = _PROTO_ALIAS_MAP.get(key)
    if resolved is None:
        raise ValueError(f"Unknown SCI protocol {protocol!r}; expected one of {PROTOCOL_COLUMNS}")
    return resolved


def parse_mpr_directory(mpr_dir: Path | str) -> dict[str, Any]:
    """Parse all 5 MPR CSV files into the cleanroom 402-variable / 10-protocol structure."""
    base = Path(mpr_dir)
    xref_lines, xref_rows = _read_nonempty_csv(base / "mprxref.csv")
    _, ramdesc_rows = _read_nonempty_csv(base / "mprramdesc.csv")
    _, settings_rows = _read_nonempty_csv(base / "mprsettings.csv")
    _, bit_rows = _read_nonempty_csv(base / "mprbitsettings.csv")
    _, func_rows = _read_nonempty_csv(base / "mprfunctionsets.csv")

    ram_by_id = {
        _row_item_num(r): scrub_cleanroom_text((r.get("RamDesc") or "").strip())
        for r in ramdesc_rows
    }
    settings_by_id = {_row_item_num(r): r for r in settings_rows}
    bits_by_id = {_row_item_num(r): _parse_bit_flags(r) for r in bit_rows}
    funcs_by_id: dict[int, dict[str, Any]] = {}
    for r in func_rows:
        item_num = _row_item_num(r)
        funcs_by_id[item_num] = {
            "item_num": item_num,
            "long_name": scrub_cleanroom_text((r.get("Long Name") or "").strip()),
            "short_name": (r.get("Short Name") or "").strip(),
            "points": _parse_function_points(r.get("FunctionSet")),
        }

    variables: list[dict[str, Any]] = []
    error_registers: dict[str, dict[str, Any]] = {}

    for row in xref_rows:
        item_num = _row_item_num(row)
        long_name = scrub_cleanroom_text((row.get("Long Name") or "").strip())
        short_name = (row.get("Short Name") or "").strip()
        protocols = {proto: _parse_proto_offset(row.get(proto)) for proto in PROTOCOL_COLUMNS}
        sett = settings_by_id.get(item_num, {})
        bit_flags = bits_by_id.get(item_num)
        func_entry = funcs_by_id.get(item_num)

        record: dict[str, Any] = {
            "item_num": item_num,
            "long_name": long_name,
            "short_name": short_name,
            "ram_desc": ram_by_id.get(item_num, ""),
            "protocols": protocols,
            "size_bytes": _parse_number(sett.get("Size")),
            "major_interval": _parse_number(sett.get("Major Interval")),
            "minor_interval": _parse_number(sett.get("Minor Interval")),
            "divisor": _parse_number(sett.get("Divisor")),
            "units": (sett.get("Units") or "").strip(),
            "display_min": _parse_number(sett.get("Display Min")),
            "display_max": _parse_number(sett.get("Display Max")),
            "decimals": _parse_number(sett.get("Dec's")),
            "ecu_min": _parse_number(sett.get("Ecu Min")),
            "ecu_max": _parse_number(sett.get("Ecu Max")),
            "bit_flags": bit_flags,
            "function_set": func_entry["points"] if func_entry is not None else None,
        }
        variables.append(record)

        if item_num in ERROR_REGISTER_ITEM_RANGE and bit_flags is not None:
            error_registers[short_name] = {
                "item_num": item_num,
                "long_name": long_name,
                "short_name": short_name,
                "protocols": protocols,
                "bit_flags": bit_flags,
            }

    variables.sort(key=lambda item: item["item_num"])
    function_sets = {str(k): funcs_by_id[k] for k in sorted(funcs_by_id)}

    return {
        "schema_version": "1.0.0",
        "source_format": "mpr_csv_crossref",
        "variable_count": len(variables),
        "csv_nonempty_line_count": xref_lines,
        "protocol_count": len(PROTOCOL_COLUMNS),
        "protocols": list(PROTOCOL_COLUMNS),
        "error_registers": error_registers,
        "function_sets": function_sets,
        "variables": variables,
    }


def build_mpr_sci_crossref(
    mpr_dir: Path | str,
    output_path: Path | str | None = DEFAULT_XREF_OUTPUT,
) -> dict[str, Any]:
    """Build the cleanroom MPR SCI cross-reference dict and optionally persist pretty JSON."""
    crossref = parse_mpr_directory(mpr_dir)
    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(crossref, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return crossref


def load_mpr_sci_crossref(path: Path | str = DEFAULT_XREF_OUTPUT) -> dict[str, Any]:
    """Load a persisted MPR SCI cross-reference JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lookup_xref_variable(
    crossref: dict[str, Any],
    *,
    protocol: str | None = None,
    offset: int | str | None = None,
    short_name: str | None = None,
    item_num: int | None = None,
) -> list[dict[str, Any]]:
    """Query cross-reference variables by protocol, offset ('0x63'/'$63'/99), short_name, or item_num."""
    resolved_proto = _resolve_protocol(protocol) if protocol is not None else None
    target_offset = _parse_offset_value(offset) if offset is not None else None
    target_short = short_name.strip().lower() if short_name is not None else None
    target_item = int(item_num) if item_num is not None else None

    matches: list[dict[str, Any]] = []
    for var in crossref.get("variables", []):
        if target_item is not None and var.get("item_num") != target_item:
            continue
        if target_short is not None and (var.get("short_name") or "").strip().lower() != target_short:
            continue
        proto_map = var.get("protocols", {})
        if resolved_proto is not None:
            entry = proto_map.get(resolved_proto)
            if entry is None:
                continue
            if target_offset is not None and entry.get("offset") != target_offset:
                continue
        elif target_offset is not None:
            if not any(e is not None and e.get("offset") == target_offset for e in proto_map.values()):
                continue
        matches.append(var)
    return matches
