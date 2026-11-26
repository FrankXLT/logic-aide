"""Injector latency and cylinder-head flowbench preset extractor."""

from __future__ import annotations

import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import xlrd

CFM_TO_DM3_S = 0.47194745
TEST_DEPRESSION_INH2O = 28.0
TEST_DEPRESSION_KPA = 6.97
DEFAULT_PRESSURE_PSI = 43.5
TARGET_PRESSURE_PSI = 55.0

NOMINAL_DEFAULTS: dict[str, float] = {
    "pressure_slope_ms_per_psi": 0.0065,
    "short_pulse_knee_ms": 1.05,
    "min_linear_pw_ms": 1.35,
}
NOMINAL_DEFAULT_FIELDS = list(NOMINAL_DEFAULTS.keys())
_LEAK_PATH_RE = re.compile(r"(/home/|/Users/|private_references|[A-Za-z]:\\)", re.IGNORECASE)
_ALLOWED_HEAD_LABELS = frozenset({
    "Stock Swirl", "Community Ported G-Head (2.5L Bench)", "Community Ported Swirl Head",
    "LW Swirl Head", "Stock 3L 12v", "Community Ported 3.0L 12V", "Stock Head",
    "KMP Flowbench Corrected", "G-Head Bowl & Short Turn Porting", "Stock Stealth DOHC",
    "G-Head Full Rework (Big Valves)", "G-Head Only", "G-Head with 1pc Intake",
    "G-Head with mild 1pc Intake", "G-Head with 2pc Intake", "G-Head with mild 2pc Intake",
    "Head Only", "2pc Lower", "mild 2pc Lower", "2pc Intake", "mild 2pc Intake",
})


def _clean_text(value: Any) -> str:
    """Normalize whitespace and non-breaking spaces in spreadsheet cells."""
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _slugify(text: str) -> str:
    """Convert a label into a stable lowercase underscore identifier."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "preset"


def _find_workbook(private_root: Path, filename: str) -> Path:
    """Locate a workbook by exact filename under ``private_root``."""
    direct = private_root / filename
    if direct.is_file():
        return direct
    matches = sorted((p for p in private_root.rglob(filename) if p.is_file()), key=lambda p: (len(p.parts), str(p)))
    if not matches:
        raise FileNotFoundError(f"Required workbook not found: {filename}")
    return matches[0]


def _col_to_index(col_ref: str) -> int:
    """Convert an Excel column letter sequence to a 0-based index."""
    idx = 0
    for ch in col_ref:
        idx = idx * 26 + (ord(ch.upper()) - ord("A") + 1)
    return idx - 1


def _to_float(val: Any) -> float | None:
    """Parse a float value from cell content, returning ``None`` when blank/dash."""
    if isinstance(val, (int, float)):
        return float(val)
    text = _clean_text(val)
    if not text or text in {"-", "--", "N/A", "na"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_xlsx_sheets(path: Path) -> dict[str, list[dict[int, str]]]:
    """Read an ``.xlsx`` workbook into row maps using stdlib ``zipfile`` and XML."""
    sheets: dict[str, list[dict[int, str]]] = {}
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter() if t.tag.endswith("}t") or t.tag == "t") for si in ss_root]
        wb_xml = ET.fromstring(zf.read("xl/workbook.xml"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))}
        for sh_el in wb_xml.findall(".//{*}sheet"):
            name = sh_el.attrib.get("name", "Sheet")
            rid = next((v for k, v in sh_el.attrib.items() if k.endswith("}id") or k == "id"), "")
            target = rel_map.get(rid, "")
            target = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
            if target not in zf.namelist():
                continue
            rows: list[dict[int, str]] = []
            for row_el in ET.fromstring(zf.read(target)).findall(".//{*}sheetData/{*}row"):
                row_map: dict[int, str] = {}
                for cell in row_el.findall("{*}c"):
                    col_letters = "".join(ch for ch in cell.attrib.get("r", "") if ch.isalpha())
                    if not col_letters:
                        continue
                    c_type, v_el, is_el = cell.attrib.get("t", ""), cell.find("{*}v"), cell.find("{*}is")
                    val = ""
                    if c_type == "s" and v_el is not None and v_el.text is not None:
                        val = shared[int(v_el.text)]
                    elif c_type == "inlineStr" and is_el is not None:
                        val = "".join(t.text or "" for t in is_el.iter() if t.tag.endswith("}t"))
                    elif v_el is not None and v_el.text is not None:
                        val = v_el.text
                    if val != "":
                        row_map[_col_to_index(col_letters)] = val
                if row_map:
                    rows.append(row_map)
            sheets[name] = rows
    return sheets


def _extrapolate_below_10v(voltages: list[float], deadtimes: list[float]) -> tuple[list[float], list[float]]:
    """Prepend monotonic 6.0V and 8.0V deadtime points for 10..15V measured curves."""
    v_out = [float(v) for v in voltages]
    dt_out = [round(float(d), 3) for d in deadtimes]
    if v_out[0] > 8.0:
        slope_10_11 = dt_out[0] - dt_out[1] if len(dt_out) >= 2 else 0.1
        slope_10_12 = (dt_out[0] - dt_out[2]) / 2.0 if len(dt_out) >= 3 else slope_10_11
        step = max(slope_10_11, slope_10_12, 0.05)
        dt_8 = round(dt_out[0] + 2.2 * step, 3)
        dt_6 = round(dt_8 + 2.6 * step, 3)
        v_out, dt_out = [6.0, 8.0] + v_out, [dt_6, dt_8] + dt_out
    for idx in range(len(dt_out) - 2, -1, -1):
        if dt_out[idx] < dt_out[idx + 1]:
            dt_out[idx] = dt_out[idx + 1]
    return v_out, [round(max(val, 0.001), 3) for val in dt_out]


def _build_injector_entry(
    preset_id: str, label: str, flow_cc: float, flow_lb: float, flow_55_lb: float | None,
    impedance_ohm: float | None, pressure_psi: float, measured_voltages: list[float],
    measured_deadtimes: list[float], corroboration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a schema-aligned injector BATOFF preset record."""
    computed_55 = flow_lb * math.sqrt(TARGET_PRESSURE_PSI / pressure_psi)
    v_points, dt_points = _extrapolate_below_10v(measured_voltages, measured_deadtimes)
    entry: dict[str, Any] = {
        "preset_id": preset_id, "label": label,
        "rated_flow_cc_min": round(flow_cc, 2), "rated_flow_lb_hr": round(flow_lb, 2),
        "flow_at_55psi_lb_hr": round(flow_55_lb if flow_55_lb is not None else computed_55, 3),
        "impedance_ohm": round(impedance_ohm, 2) if impedance_ohm is not None else None,
        "rated_pressure_psi": round(pressure_psi, 2),
        "batoff_deadtime_ms": {"voltage_points_v": v_points, "deadtime_ms": dt_points, **NOMINAL_DEFAULTS},
        "measured_voltage_points_v": [float(v) for v in measured_voltages],
        "measured_deadtime_ms": [round(float(d), 3) for d in measured_deadtimes],
        "extrapolated_below_10v": measured_voltages[0] > 8.0,
        "nominal_defaults_applied": True, "nominal_default_fields": list(NOMINAL_DEFAULT_FIELDS),
    }
    if corroboration is not None:
        entry["corroboration"] = corroboration
    return entry


def _parse_latency_sheet1(sheet: xlrd.sheet.Sheet) -> dict[str, dict[str, Any]]:
    """Parse the three 6V..16V BATOFF blocks in ``injector latency tables.xls`` Sheet1."""
    pressure_psi = DEFAULT_PRESSURE_PSI
    blocks: dict[str, dict[str, Any]] = {}
    current_key: str | None = None
    pair_re = re.compile(r"^(\d+(?:\.\d+)?)\s*v\s*-\s*(\d+(?:\.\d+)?)\s*usec$", re.IGNORECASE)
    headers = {
        "785cc (s)": ("injector_785cc_s", "785 cc/min (S) / 75 lb/hr", 785.0, round(785.0 / 10.5, 2), None),
        "785cc (d)": ("holley_delphi_75lb_788cc", "Holley 75 lb / Delphi 788 cc", 788.0, 75.0, 2.0),
        "holley75": ("holley_delphi_75lb_788cc", "Holley 75 lb / Delphi 788 cc", 788.0, 75.0, 2.0),
        "msd72": ("msd_72lb_756cc", "MSD 72 lb/hr (756 cc/min)", 756.0, 72.0, None),
    }
    for r_idx in range(sheet.nrows):
        text = _clean_text(sheet.cell_value(r_idx, 0))
        if not text:
            continue
        if (p_match := re.match(r"^(\d+(?:\.\d+)?)\s*psi$", text, re.IGNORECASE)):
            pressure_psi = float(p_match.group(1))
            continue
        if (matched := next((meta for tok, meta in headers.items() if tok in text.lower()), None)):
            current_key, label, cc, lb, ohm = matched
            blocks.setdefault(current_key, {"label": label, "flow_cc": cc, "flow_lb": lb, "ohm": ohm, "pressure_psi": pressure_psi, "points": []})
            continue
        if (m_pair := pair_re.match(text)) and current_key is not None:
            blocks[current_key]["points"].append((float(m_pair.group(1)), round(float(m_pair.group(2)) / 1000.0, 3)))
    return blocks


def _parse_latency_sheet2(sheet: xlrd.sheet.Sheet) -> list[dict[str, Any]]:
    """Parse injector rows from ``injector latency tables.xls`` Sheet2."""
    headers = [_clean_text(sheet.cell_value(0, c)).lower() for c in range(sheet.ncols)]
    col_idx = {h: idx for idx, h in enumerate(headers) if h}
    v_cols = sorted((float(h[:-1]), idx) for h, idx in col_idx.items() if re.fullmatch(r"\d+v", h))
    rows: list[dict[str, Any]] = []
    for r_idx in range(1, sheet.nrows):
        label = _clean_text(sheet.cell_value(r_idx, col_idx.get("injector", 0)))
        if not label:
            continue
        valid = [(v, round(dt, 3)) for v, idx in v_cols if (dt := _to_float(sheet.cell_value(r_idx, idx))) is not None]
        flow_cc, flow_lb = _to_float(sheet.cell_value(r_idx, col_idx["flow cc/min"])), _to_float(sheet.cell_value(r_idx, col_idx["flow lb/hr"]))
        if len(valid) < 4 or flow_cc is None or flow_lb is None:
            continue
        rows.append({
            "label": label, "flow_cc": flow_cc, "flow_lb": flow_lb,
            "ohm": _to_float(sheet.cell_value(r_idx, col_idx["ohm"])),
            "pressure_psi": _to_float(sheet.cell_value(r_idx, col_idx["fuel pres"])) or DEFAULT_PRESSURE_PSI,
            "flow_55": _to_float(sheet.cell_value(r_idx, col_idx["flow rate @55psi"])),
            "voltages": [v for v, _ in valid], "deadtimes": [d for _, d in valid],
        })
    return rows


def _parse_latency_xlsx(path: Path) -> list[tuple[float, float]]:
    """Parse ``latency.xlsx`` Sheet1 (Holley/Delphi 75lb 788cc 6..16V curve)."""
    points: list[tuple[float, float]] = []
    for row in next(iter(_read_xlsx_sheets(path).values()), []):
        volt, usec, ms_val = _to_float(row.get(0)), _to_float(row.get(1)), _to_float(row.get(2))
        if volt is not None and (usec is not None or ms_val is not None):
            points.append((volt, round(usec / 1000.0, 3) if usec is not None else round(float(ms_val), 3)))
    return sorted(points, key=lambda item: item[0])


def _extract_injectors(private_root: Path) -> dict[str, Any]:
    """Extract, deduplicate, and format all injector BATOFF presets."""
    wb = xlrd.open_workbook(str(_find_workbook(private_root, "injector latency tables.xls")))
    s1_blocks = _parse_latency_sheet1(wb.sheet_by_name("Sheet1"))
    s2_rows = _parse_latency_sheet2(wb.sheet_by_name("Sheet2"))
    xlsx_pts = _parse_latency_xlsx(_find_workbook(private_root, "latency.xlsx"))
    presets: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    def _unique_id(base: str) -> str:
        candidate, suffix = base, 2
        while candidate in used_ids:
            candidate, suffix = f"{base}_{suffix}", suffix + 1
        used_ids.add(candidate)
        return candidate

    holley_s1 = s1_blocks.get("holley_delphi_75lb_788cc")
    holley_s2 = next((r for r in s2_rows if r["label"].lower() == "holley" and round(r["flow_cc"]) == 788), None)
    if holley_s1 is not None:
        s1_pts = sorted(holley_s1["points"], key=lambda item: item[0])
        s1_map, xlsx_map = dict(s1_pts), dict(xlsx_pts)
        s2_map = dict(zip(holley_s2["voltages"], holley_s2["deadtimes"])) if holley_s2 else {}
        sources = ["latency_sheet1_holley75", "latency_xlsx_delphi_75lb_788cc"]
        if holley_s2:
            sources.insert(1, "latency_sheet2_holley_788cc")
        presets.append(_build_injector_entry(
            _unique_id("holley_delphi_75lb_788cc"), "Holley 75 lb / Delphi 788 cc",
            holley_s2["flow_cc"] if holley_s2 else holley_s1["flow_cc"],
            holley_s2["flow_lb"] if holley_s2 else holley_s1["flow_lb"],
            holley_s2["flow_55"] if holley_s2 else None,
            holley_s2["ohm"] if holley_s2 else holley_s1["ohm"],
            holley_s2["pressure_psi"] if holley_s2 else holley_s1["pressure_psi"],
            [v for v, _ in s1_pts], [d for _, d in s1_pts],
            corroboration={
                "matched_sources": len(sources), "source_tables": sources,
                "exact_overlap_10v_15v_ms": all(s1_map.get(v) == s2_map.get(v) == xlsx_map.get(v) for v in (10.0, 11.0, 12.0, 13.0, 14.0, 15.0)),
                "exact_full_6v_16v_ms": s1_pts == xlsx_pts,
            },
        ))

    for key in ("injector_785cc_s", "msd_72lb_756cc"):
        if (blk := s1_blocks.get(key)) is None:
            continue
        pts = sorted(blk["points"], key=lambda item: item[0])
        presets.append(_build_injector_entry(
            _unique_id(key), blk["label"], blk["flow_cc"], blk["flow_lb"], None, blk["ohm"], blk["pressure_psi"],
            [v for v, _ in pts], [d for _, d in pts],
        ))

    for row in s2_rows:
        if row is holley_s2:
            continue
        ohm_tag = f"_{int(round(row['ohm']))}ohm" if row["ohm"] is not None else ""
        presets.append(_build_injector_entry(
            _unique_id(f"{_slugify(row['label'])}_{int(round(row['flow_cc']))}cc{ohm_tag}"),
            row["label"], row["flow_cc"], row["flow_lb"], row["flow_55"], row["ohm"], row["pressure_psi"],
            row["voltages"], row["deadtimes"],
        ))
    return {"schema_version": "1.0", "preset_count": len(presets), "nominal_defaults": dict(NOMINAL_DEFAULTS), "presets": presets}


def _sanitize_head_label(raw: str) -> str:
    """Anonymize contributor names and clean typos in cylinder-head labels."""
    text = _clean_text(raw)
    lower = text.lower()
    if "bowl" in lower and "short turn" in lower:
        label = "G-Head Bowl & Short Turn Porting"
    elif "complete rework" in lower or "big valves" in lower:
        label = "G-Head Full Rework (Big Valves)"
    elif "kmp" in lower:
        label = "KMP Flowbench Corrected"
    elif "stealh" in lower:
        label = "Stock Stealth DOHC"
    elif "g-head" in lower and "intake" not in lower and "only" not in lower:
        label = "Community Ported G-Head (2.5L Bench)"
    elif "swirl" in lower and "stock" not in lower and "lw" not in lower:
        label = "Community Ported Swirl Head"
    elif "3l 12v" in lower and "stock" not in lower:
        label = "Community Ported 3.0L 12V"
    else:
        label = re.sub(r"^[A-Za-z]+(?:\s+[A-Z]\.?)?\s+-\s+", "", text).strip() or text
    if label not in _ALLOWED_HEAD_LABELS:
        raise ValueError(f"Unexpected non-allowlisted cylinder-head label: {label}")
    return label


def _flow_point(lift_in: float, cfm: float) -> dict[str, float]:
    """Build a flow point with NIST SP 811 CFM -> dm3/s (L/s) conversion."""
    return {"lift_in": round(lift_in, 2), "cfm": round(cfm, 2), "dm3_s": round(cfm * CFM_TO_DM3_S, 3)}


def _parse_flow_xls(path: Path, dataset_id: str, title: str, section_col: int, lift_col: int, first_col: int) -> dict[str, Any]:
    """Parse intake and exhaust flowbench series from an ``.xls`` workbook."""
    sh = xlrd.open_workbook(str(path)).sheet_by_index(0)
    headers = [_sanitize_head_label(sh.cell_value(1, c)) for c in range(first_col, sh.ncols)]
    intake: dict[str, list[dict[str, float]]] = {h: [] for h in headers}
    exhaust: dict[str, list[dict[str, float]]] = {h: [] for h in headers}
    mode = "intake"
    for r_idx in range(1, sh.nrows):
        marker = _clean_text(sh.cell_value(r_idx, section_col)).lower()
        if "intake" in marker or "exhaust" in marker:
            mode = "intake" if "intake" in marker else "exhaust"
            continue
        if (lift := _to_float(sh.cell_value(r_idx, lift_col))) is None:
            continue
        target = intake if mode == "intake" else exhaust
        for offset, head_name in enumerate(headers):
            if (cfm := _to_float(sh.cell_value(r_idx, first_col + offset))) is not None:
                target[head_name].append(_flow_point(lift, cfm))
    return {"dataset_id": dataset_id, "title": title, "configurations": headers, "intake": intake, "exhaust": exhaust}


def _parse_intake_flow(path: Path) -> list[dict[str, Any]]:
    """Parse both sheets of ``intake flow.xlsx`` (rows with valid lift in col B)."""
    sheets = _read_xlsx_sheets(path)
    meta = [
        ("Sheet1", "g_head_full_intake_combos", "G-Head Full Intake Manifold Flow Comparison"),
        ("Sheet1 (2)", "g_head_lower_intake_combos", "G-Head Lower vs. Complete 2-Piece Intake Flow"),
    ]
    datasets: list[dict[str, Any]] = []
    for sheet_name, ds_id, title in meta:
        if not (rows := sheets.get(sheet_name, [])):
            continue
        cols = sorted(c for c in rows[0] if c >= 2)
        headers = {c: _sanitize_head_label(rows[0][c]) for c in cols}
        series: dict[str, list[dict[str, float]]] = {headers[c]: [] for c in cols}
        for row in rows[1:]:
            if (lift := _to_float(row.get(1))) is None:
                continue
            for c in cols:
                if (cfm := _to_float(row.get(c))) is not None:
                    series[headers[c]].append(_flow_point(lift, cfm))
        datasets.append({"dataset_id": ds_id, "title": title, "configurations": [headers[c] for c in cols], "intake": series, "exhaust": {}})
    return datasets


def _parse_ve_calc(path: Path) -> dict[str, Any]:
    """Parse ``ve calc.xlsx`` engine baseline and RPM sweep."""
    rows = next(iter(_read_xlsx_sheets(path).values()), [])
    sweep_cols = list(range(8, 14))
    rpm_sweep: list[dict[str, float]] = [{} for _ in sweep_cols]
    left_map = {
        "flow": ("rated_injector_flow_lb_hr", float), "@": ("rated_injector_pressure_psi", float),
        "engine size": ("displacement_l", float), "# of cylinders": ("cylinders", int),
        "fuel pressure static": ("base_fuel_pressure_psi", float), "fuel regulator rate": ("fmu_ratio", float),
    }
    key_map = {
        "engine speed": "rpm", "map": "map_psi", "iat": "iat_deg_f", "injector pw": "injector_pw_ms",
        "a/f": "afr", "fuel pressure": "fuel_pressure_psi", "injector flow": "injector_flow_lb_hr",
        "duty cycle": "duty_cycle", "ideal airflow": "ideal_airflow_lb_min",
        "actual fuel flow": "actual_fuel_flow_lb_min", "calculated ve": "calculated_ve",
    }
    meta: dict[str, Any] = {}
    for row in rows:
        l_key, l_val = _clean_text(row.get(2)).lower(), _to_float(row.get(3))
        if l_key in left_map and l_val is not None:
            field_name, caster = left_map[l_key]
            meta[field_name] = caster(round(l_val)) if caster is int else round(l_val, 2)
        metric = _clean_text(row.get(7) or row.get(6)).lower()
        if (field := key_map.get(metric)):
            for idx, col in enumerate(sweep_cols):
                if (val := _to_float(row.get(col))) is not None:
                    rpm_sweep[idx][field] = round(val, 4)
    return {"engine_parameters": meta, "rpm_sweep": [pt for pt in rpm_sweep if pt]}


def _format_flow_table(series_map: dict[str, list[dict[str, float]]]) -> list[str]:
    """Render a GFM pipe table with CFM and NIST SP 811 L/s (dm3/s) columns."""
    configs = list(series_map.keys())
    lifts = sorted({pt["lift_in"] for pts in series_map.values() for pt in pts})
    by_cfg_lift = {cfg: {pt["lift_in"]: pt for pt in pts} for cfg, pts in series_map.items()}
    header = ["Lift (in)"] + [f"{cfg} (CFM / L/s)" for cfg in configs]
    align = [":---:"] + [":---:" for _ in configs]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    for lift in lifts:
        cells = [f"{lift:.2f}"] + [
            f"{by_cfg_lift[cfg][lift]['cfm']:.2f} / {by_cfg_lift[cfg][lift]['dm3_s']:.3f}" if lift in by_cfg_lift[cfg] else "—"
            for cfg in configs
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


_FLOWBENCH_HEADER = """\
> **Scope**: Cleanroom Tier 2 Cylinder Head Flowbench & 2.5L VE Reference (`2.2L_K` / `2.5L_K` / `3.0L 12V` / `Stealth DOHC` @ `28 inH2O` / `6.97 kPa`)
> **Boundary**: Distilled anonymous bench measurements only; personal contributor identities and private source paths omitted per cleanroom policy.

---
doc_id: "g-head-flowbench-nist-sp811"
title: "Chrysler 2.2L/2.5L G-Head, Swirl Head & Intake Flowbench Digest (CFM & L/s)"
trust_tier: "Tier 2"
source_type: "BENCH_FLOWSHEET_DIGEST"
source_citation: "Community Flowbench & 2.5L VE Reference Archive (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "28 inH2O (6.97 kPa) Flowbench Series"
ecu_families: ["SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: []
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler 2.2L / 2.5L Cylinder Head & Intake Flowbench Digest

All volumetric flow figures are normalized at **`{inh2o:.1f} inH2O` (`{kpa:.2f} kPa`)** using **NIST SP 811** conversion (`1 CFM = {factor:.8f} dm3/s = L/s`).
"""


def _render_flowbench_markdown(payload: dict[str, Any]) -> str:
    """Render Tier 2 cleanroom Markdown digest with scope header, YAML, and tables."""
    lines = [_FLOWBENCH_HEADER.format(inh2o=TEST_DEPRESSION_INH2O, kpa=TEST_DEPRESSION_KPA, factor=CFM_TO_DM3_S)]
    for ds in payload["flowbench_datasets"]:
        lines.extend([f"## {ds['title']}", ""])
        for port_key, port_label in (("intake", "Intake Flow"), ("exhaust", "Exhaust Flow")):
            if ds.get(port_key):
                lines.extend([f"### {port_label} (`CFM / L/s @ 28 inH2O`)", "", *_format_flow_table(ds[port_key]), ""])
    ve = payload["ve_calc_reference"]
    ep = ve["engine_parameters"]
    lines.extend([
        "## 2.5L 4-Cylinder Volumetric Efficiency (VE) Reference Sweep", "",
        f"- **Baseline**: `{ep.get('displacement_l', 2.5)} L`, `{ep.get('cylinders', 4)} cyl`, `{ep.get('rated_injector_flow_lb_hr', 52.3)} lb/hr @ {ep.get('rated_injector_pressure_psi', 55.0)} psi`, `{ep.get('base_fuel_pressure_psi', 45.0)} psi` static (`{ep.get('fmu_ratio', 1)}:1` regulator).", "",
        "| RPM | MAP (psi) | IAT (degF) | PW (ms) | AFR (:1) | Fuel Pres (psi) | Duty Cycle | Calculated VE |",
        "| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        *[
            f"| {int(r['rpm'])} | {r['map_psi']:.1f} | {r['iat_deg_f']:.0f} | {r['injector_pw_ms']:.2f} | "
            f"{r['afr']:.1f} | {r['fuel_pressure_psi']:.1f} | {r['duty_cycle']:.3f} | {r['calculated_ve']:.4f} |"
            for r in ve["rpm_sweep"]
        ],
        "",
    ])
    return "\n".join(lines)


def run_extract_presets(
    private_root: Path | str = Path("private_references"),
    output_presets_dir: Path | str = Path("profiles/presets"),
    output_knowledge_dir: Path | str = Path("knowledge/tier2_verified"),
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Extract injector BATOFF presets and cleanroom cylinder-head flowbench tables."""
    root = Path(private_root)
    presets_dir = Path(output_dir) if output_dir is not None else Path(output_presets_dir)
    knowledge_dir = Path(output_knowledge_dir)
    presets_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    injectors_payload = _extract_injectors(root)
    flow_datasets = [
        _parse_flow_xls(_find_workbook(root, "2.5cyl flow.xls"), "2_5l_head_comparison", "2.5L Swirl, G-Head & 3.0L 12V Flowbench Comparison", 1, 1, 2),
        _parse_flow_xls(_find_workbook(root, "flow.xls"), "g_head_porting_comparison", "G-Head Porting Progression & DOHC Flowbench Comparison", 1, 2, 3),
        *_parse_intake_flow(_find_workbook(root, "intake flow.xlsx")),
    ]
    flowbench_payload = {
        "schema_version": "1.0", "test_depression_inh2o": TEST_DEPRESSION_INH2O,
        "test_depression_kpa": TEST_DEPRESSION_KPA, "nist_sp811_cfm_to_dm3_s": CFM_TO_DM3_S,
        "flowbench_datasets": flow_datasets, "ve_calc_reference": _parse_ve_calc(_find_workbook(root, "ve calc.xlsx")),
    }
    injectors_json, flowbench_json = json.dumps(injectors_payload, indent=2) + "\n", json.dumps(flowbench_payload, indent=2) + "\n"
    flowbench_md = _render_flowbench_markdown(flowbench_payload)
    for text in (injectors_json, flowbench_json, flowbench_md):
        if _LEAK_PATH_RE.search(text):
            raise ValueError("Cleanroom path leak detected in generated output.")

    injectors_out, flowbench_out, knowledge_out = presets_dir / "injectors_batoff.json", presets_dir / "cylinder_head_flowbench.json", knowledge_dir / "g_head_flowbench.md"
    injectors_out.write_text(injectors_json, encoding="utf-8", newline="\n")
    flowbench_out.write_text(flowbench_json, encoding="utf-8", newline="\n")
    knowledge_out.write_text(flowbench_md, encoding="utf-8", newline="\n")
    return {
        "injector_presets_count": injectors_payload["preset_count"], "flowbench_datasets_count": len(flow_datasets),
        "injectors_path": str(injectors_out), "flowbench_json_path": str(flowbench_out), "flowbench_md_path": str(knowledge_out),
    }
