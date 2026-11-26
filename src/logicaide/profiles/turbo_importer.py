"""TurboCalc v5 archive importer, SHA-256 figure deduplicator, and 4-point affine builder."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

# NIST SP 811 dry-air reference density at 298.15 K, 101.325 kPa
RHO_AIR_REF_KG_M3 = 1.184
LB_TO_KG = 0.45359237
LB_MIN_TO_KG_S = LB_TO_KG / 60.0
KG_S_TO_LB_MIN = 60.0 / LB_TO_KG
M3_S_TO_LB_MIN = RHO_AIR_REF_KG_M3 * 60.0 / LB_TO_KG
M3_MIN_TO_LB_MIN = RHO_AIR_REF_KG_M3 / LB_TO_KG
CFM_TO_LB_MIN = 0.069  # TurboCalc airflowcalc.php:71 legacy inlet density factor
NIST_298K_VOLUMETRIC_FACTOR = round(RHO_AIR_REF_KG_M3 * (0.3048**3) / LB_TO_KG, 6)

UNIT_TO_LB_MIN: dict[str, float] = {
    "lb/min": 1.0, "kg/sec": KG_S_TO_LB_MIN, "kg/s": KG_S_TO_LB_MIN,
    "m^3/sec": M3_S_TO_LB_MIN, "m3/sec": M3_S_TO_LB_MIN, "m3/s": M3_S_TO_LB_MIN,
    "m^3/min": M3_MIN_TO_LB_MIN, "m3/min": M3_MIN_TO_LB_MIN, "cfm": CFM_TO_LB_MIN, "cfmc": CFM_TO_LB_MIN,
}
COMPANY_SLUGS = {
    "garrett/honeywell": "garrett", "mitsubishi": "mitsubishi", "holset": "holset",
    "ihi": "ihi", "borgwarner turbo systems": "borgwarner", "aerocharger": "aerocharger",
}
FAMILY_PREFIX_SLUGS = {
    "T04E Series": "t04e", "T04B Series": "t04b", "T3 Series": "t3", "TD04 Series": "td04",
    "TD05 Series": "td05", "TD06 Series": "td06", "53130": "53130", "66130": "66130",
    "S Series: Bullseye-Power": "bullseye", "Legacy-KKK/BW/Schwitzer": "kkk",
}
DEFAULT_REL_PATHS = {
    "rar": Path("private_references/frank/Turbo Calc Stuff/turbocalc v5 beta 1/turbocalc.rar"),
    "maps": Path("private_references/frank/Turbo Calc Stuff/turbocalc v5 beta 4 - canvas/maps"),
    "docx": Path("private_references/frank/Turbo Calc Stuff/turbodata_2010_10_27.docx"),
    "xml": Path("private_references/frank/Turbo Calc Stuff/Latest Work/turbocalc/data/turbocalc.xml"),
    "profiles": Path("profiles/turbo"), "figures": Path("data/heritage_raw/figures/turbo_maps"),
    "examples": Path("profiles/examples"),
}
_PII_RE = re.compile(
    r"(?:https?|ftp)://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|"
    r"/(?:home|Users|var|tmp|mnt|etc|opt)/\S+|[A-Za-z]:\\\S+|private_references\S*",
    re.IGNORECASE,
)


def sanitize_text(text: str) -> str:
    """Scrub URLs, emails, and local/private filesystem paths from metadata strings."""
    return " ".join(_PII_RE.sub("", text or "").split())


def slugify(text: str) -> str:
    """Return a deterministic lowercase alphanumeric-underscore slug."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def convert_flow_to_lb_min(value: float, unit: str) -> float:
    """Convert airflow from TurboCalc source units into NIST-normalized lb/min."""
    key = unit.strip().lower()
    if key not in UNIT_TO_LB_MIN:
        raise ValueError(f"Unsupported airflow unit: {unit!r}")
    return float(value) * UNIT_TO_LB_MIN[key]


def compute_affine_transform(
    x0: float, y0: float, w: float, h: float,
    m_min: float, m_max: float, pr_min: float, pr_max: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build 2x3 pixel-to-(lb/min, PR) affine matrix and 4 calibration corners."""
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid calibration pixel bounds: width={w}, height={h}")
    a11, a22 = (m_max - m_min) / float(w), -(pr_max - pr_min) / float(h)
    b1, b2 = m_min - a11 * float(x0), pr_max - a22 * float(y0)
    matrix = {
        "A": [[a11, 0.0], [0.0, a22]], "b": [b1, b2],
        "A_kg_s": [[a11 * LB_MIN_TO_KG_S, 0.0], [0.0, a22]], "b_kg_s": [b1 * LB_MIN_TO_KG_S, b2],
        "input_frame": "image_pixels_top_left_origin", "output_units": ["lb/min", "PR/1"],
    }
    def _pt(px: float, py: float, fl: float, pr: float) -> dict[str, Any]:
        return {
            "pixel": [int(px) if float(px).is_integer() else float(px), int(py) if float(py).is_integer() else float(py)],
            "flow_lb_min": round(fl, 6), "flow_kg_s": round(fl * LB_MIN_TO_KG_S, 6), "pressure_ratio": round(pr, 6),
        }
    corners = {
        "top_left": _pt(x0, y0, m_min, pr_max), "top_right": _pt(x0 + w, y0, m_max, pr_max),
        "bottom_left": _pt(x0, y0 + h, m_min, pr_min), "bottom_right": _pt(x0 + w, y0 + h, m_max, pr_min),
    }
    return matrix, corners


def pixel_to_flow_pr(spec: dict[str, Any], xp: float, yp: float) -> tuple[float, float]:
    """Pure affine evaluator mapping pixel (xp, yp) to (flow_lb_min, pressure_ratio)."""
    matrix = spec.get("affine_matrix_2x3")
    if not matrix or spec.get("axis_calibration_pending"):
        raise ValueError(f"Turbo spec {spec.get('turbo_id')!r} is not axis-calibrated")
    mat_a, vec_b = matrix["A"], matrix["b"]
    return (mat_a[0][0] * float(xp) + mat_a[0][1] * float(yp) + vec_b[0], mat_a[1][0] * float(xp) + mat_a[1][1] * float(yp) + vec_b[1])


def extract_tools_sql(rar_path: Path, tmpdir: Path) -> Path:
    """Safely extract turbocalc.rar into tmpdir using unar and locate tools.sql."""
    rar_resolved = rar_path.resolve()
    if not rar_resolved.is_file():
        raise FileNotFoundError(f"Archive not found: {rar_path.name}")
    subprocess.run(["unar", "-q", "-o", str(tmpdir), str(rar_resolved)], check=True)
    tmp_resolved = tmpdir.resolve()
    candidates = [tmpdir / "turbocalc" / "tools.sql", *sorted(tmpdir.rglob("tools.sql"))]
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink() and candidate.resolve().is_relative_to(tmp_resolved):
            return candidate
    raise FileNotFoundError(f"tools.sql not found inside {rar_path.name}")


def _read_sql_quoted(text: str, pos: int) -> tuple[str, int]:
    escapes = {"'": "'", '"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t", "0": "\0"}
    buf: list[str] = []
    i, n = pos + 1, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(escapes.get(text[i + 1], text[i + 1]))
            i += 2
        elif ch == "'" and i + 1 < n and text[i + 1] == "'":
            buf.append("'")
            i += 2
        elif ch == "'":
            return "".join(buf), i + 1
        else:
            buf.append(ch)
            i += 1
    raise ValueError("Unterminated SQL string literal")


def _scan_sql_tuples(text: str, start: int) -> list[list[Any]]:
    rows, i, n = [], start, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == ";":
            break
        if text[i] != "(":
            i += 1
            continue
        i += 1
        row: list[Any] = []
        while i < n:
            while i < n and text[i] in " \t\r\n,":
                i += 1
            if i >= n or text[i] == ")":
                i += 1
                break
            if text[i] == "'":
                val, i = _read_sql_quoted(text, i)
                row.append(val)
            else:
                s = i
                while i < n and text[i] not in ",)":
                    i += 1
                raw = text[s:i].strip()
                row.append(
                    None if raw.upper() == "NULL"
                    else int(raw) if re.fullmatch(r"-?\d+", raw)
                    else float(raw) if re.fullmatch(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", raw) else raw
                )
        rows.append(row)
    return rows


def parse_sql_inserts(sql_text: str, table_name: str) -> list[dict[str, Any]]:
    """Parse all INSERT INTO `table_name` (...) VALUES ...; rows cleanly."""
    header_re = re.compile(rf"INSERT\s+INTO\s+`{re.escape(table_name)}`\s*\(([^)]+)\)\s*VALUES\s*", re.I | re.S)
    records: list[dict[str, Any]] = []
    for match in header_re.finditer(sql_text):
        cols = [c.strip().strip("`") for c in match.group(1).split(",")]
        records.extend(dict(zip(cols, tup)) for tup in _scan_sql_tuples(sql_text, match.end()) if len(tup) == len(cols))
    return records


def _to_opt_float(val: str) -> float | None:
    try:
        return float(val.strip()) if val.strip() else None
    except ValueError:
        return None


def _clean_opt_str(val: str) -> str | None:
    s = sanitize_text(val)
    return s if s and s not in {"0", "0.0"} else None


def parse_docx_wheel_specs(docx_path: Path) -> dict[str, Any]:
    """Parse Table 0 wheel dimensions from turbodata_2010_10_27.docx using stdlib XML."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(docx_path) as zf:
        tbl = ET.fromstring(zf.read("word/document.xml")).find(".//w:tbl", ns)
    if tbl is None:
        return {"rows": [], "chrysler_oem_turbos": []}
    rows, chrysler_oem, section = [], [], ""
    for tr in tbl.findall("./w:tr", ns)[2:]:
        cells: list[str] = []
        for tc in tr.findall("./w:tc", ns):
            gs = tc.find("./w:tcPr/w:gridSpan", ns)
            span = int(gs.attrib[f"{{{ns['w']}}}val"]) if gs is not None else 1
            txt = " ".join("".join(t.text or "" for t in p.findall(".//w:t", ns)).strip() for p in tc.findall("./w:p", ns)).strip()
            cells.extend([txt] + [""] * (span - 1))
        cells.extend([""] * max(0, 16 - len(cells)))
        if not any(cells):
            continue
        if cells[0] and all(c == "" for c in cells[1:]):
            section = sanitize_text(cells[0])
            continue
        entry = {
            "section": section, "model": sanitize_text(cells[0]), "part_number": _clean_opt_str(cells[1]),
            "compressor": {"inducer_in": _to_opt_float(cells[2]), "inducer_mm": _to_opt_float(cells[3]), "exducer_in": _to_opt_float(cells[4]), "exducer_mm": _to_opt_float(cells[5]), "trim": _clean_opt_str(cells[6]), "ar": _clean_opt_str(cells[7])},
            "turbine": {"major_dia_in": _to_opt_float(cells[8]), "major_dia_mm": _to_opt_float(cells[9]), "exducer_in": _to_opt_float(cells[10]), "exducer_mm": _to_opt_float(cells[11]), "trim": _clean_opt_str(cells[12]), "ar": _clean_opt_str(cells[13]), "housing_type": _clean_opt_str(cells[14])},
            "hp_rating": _clean_opt_str(cells[15]),
        }
        rows.append(entry)
        if section.upper() == "CHRYSLER OEM TURBOS":
            chrysler_oem.append(entry)
    return {"rows": rows, "chrysler_oem_turbos": chrysler_oem}


def parse_turbocalc_xml(xml_path: Path) -> list[dict[str, Any]]:
    """Parse Latest Work turbocalc.xml for supplemental units and wheel specs."""
    root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    return [
        {
            "company_name": sanitize_text(comp.findtext("name") or ""), "family_name": sanitize_text(fam.findtext("name") or ""),
            "product_name": sanitize_text(prod.findtext("name") or ""), "map_path": (prod.findtext("file") or "").strip(),
            "units": (prod.findtext("Graph/Units") or "").strip(), "wheel_size": _clean_opt_str(prod.findtext("Info/WheelSize") or ""),
            "trim": _clean_opt_str(prod.findtext("Info/Trim") or ""), "ar": _clean_opt_str(prod.findtext("Info/AR") or ""),
        }
        for comp in root.findall("./Company") for fam in comp.findall("./Family") for prod in fam.findall("./Product")
    ]


def _deduplicate_maps(maps_dir: Path, figures_dir: Path, sql_map_paths: set[str]) -> tuple[dict[str, dict[str, Any]], int]:
    all_files = sorted(p for p in maps_dir.rglob("*") if p.is_file())
    groups: dict[str, list[tuple[str, Path]]] = {}
    for src in all_files:
        groups.setdefault(hashlib.sha256(src.read_bytes()).hexdigest(), []).append(("maps/" + src.relative_to(maps_dir).as_posix(), src))
    figures_dir.mkdir(parents=True, exist_ok=True)
    dedup: dict[str, dict[str, Any]] = {}
    for digest, candidates in groups.items():
        candidates.sort(key=lambda x: (0 if x[0] in sql_map_paths else 1, 0 if x[0].startswith("maps/kkk/") else 1, x[0].lower()))
        canonical_rel, src_file = candidates[0]
        subpath = canonical_rel.removeprefix("maps/")
        dest = figures_dir / subpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            shutil.copy2(src_file, dest)
        dedup[digest] = {
            "sha256": digest, "canonical_map_path": canonical_rel,
            "figure_rel_path": f"data/heritage_raw/figures/turbo_maps/{subpath}",
            "duplicate_paths": [rel for rel, _ in candidates],
        }
    return dedup, len(all_files)


def _match_sql_to_figure(map_path: str, dedup: dict[str, dict[str, Any]]) -> tuple[str, str]:
    norm = map_path.strip().lstrip("/")
    for match_kind, key_fn in (("exact_path", lambda p: p), ("basename_case_insensitive", lambda p: Path(p).name.lower()), ("stem_case_insensitive", lambda p: Path(p).stem.lower())):
        target = key_fn(norm)
        for digest, info in dedup.items():
            if any(key_fn(p) == target for p in info["duplicate_paths"]):
                return digest, match_kind
    raise KeyError(f"Unable to reconcile SQL map_path {map_path!r} against maps directory")


def _build_turbo_id(company_name: str, family_name: str, product_name: str, product_id: int, used_ids: set[str]) -> str:
    comp_slug, prod_slug = COMPANY_SLUGS.get(company_name.lower(), slugify(company_name)), slugify(product_name)
    fam_prefix = FAMILY_PREFIX_SLUGS.get(family_name)
    base = f"{comp_slug}_{fam_prefix}_{prod_slug}" if (fam_prefix and not prod_slug.startswith(fam_prefix)) else f"{comp_slug}_{prod_slug}"
    if base in used_ids:
        alt = f"{comp_slug}_{slugify(family_name)}_{prod_slug}"
        base = alt if alt not in used_ids else f"{base}_p{product_id}"
    while base in used_ids:
        base = f"{base}_{product_id}"
    used_ids.add(base)
    return base


def _extract_points(row: dict[str, Any], unit: str, prefixes: tuple[tuple[str, int, int], ...]) -> list[dict[str, float]]:
    pts, seen = [], set()
    for prefix, start, end in prefixes:
        for idx in range(start, end + 1):
            k_af, k_pr = f"{prefix}_{idx}_airflow", f"{prefix}_{idx}_pr"
            if k_af in row and k_pr in row and (prefix, idx) not in seen:
                seen.add((prefix, idx))
                af, pr = float(row[k_af] or 0.0), float(row[k_pr] or 0.0)
                if af != 0.0 or pr != 0.0:
                    lb_min = convert_flow_to_lb_min(af, unit)
                    pts.append({"flow_lb_min": round(lb_min, 6), "flow_kg_s": round(lb_min * LB_MIN_TO_KG_S, 6), "pressure_ratio": round(pr, 6)})
    return pts


def _derive_boundaries(row: dict[str, Any], unit: str, env_pts: list[dict[str, float]]) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    surge = _extract_points(row, unit, (("surge", 1, 4),))
    choke: list[dict[str, float]] = []
    if len(env_pts) >= 4:
        peak_idx = max(range(len(env_pts)), key=lambda idx: env_pts[idx]["pressure_ratio"])
        split = peak_idx + 1 if 0 < peak_idx < len(env_pts) - 1 else len(env_pts) // 2
        if not surge:
            surge = sorted(env_pts[:split], key=lambda p: p["pressure_ratio"])
        choke = sorted(env_pts[split:], key=lambda p: p["pressure_ratio"])
    return surge, choke


def _match_docx_wheel(company_name: str, family_name: str, product_name: str, docx_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comp_key = COMPANY_SLUGS.get(company_name.lower(), "")
    allowed = {"garrett": {"GARRETT", "TURBONETICS"}, "mitsubishi": {"MITSUBISHI"}, "holset": {"HOLSET"}, "borgwarner": {"BORG WARNER"}}.get(comp_key, set())
    if not allowed:
        return []
    candidates = {slugify(product_name)}
    fam_prefix = FAMILY_PREFIX_SLUGS.get(family_name, "")
    if fam_prefix:
        candidates.add(slugify(f"{fam_prefix}_{product_name}"))
    if fam_prefix == "t3":
        candidates.add(slugify(f"t03_{product_name}").replace("_trim", "").replace("_super60", "_s60"))
    if fam_prefix == "t04e":
        candidates.add(slugify(f"t04e_{product_name}").replace("_trim", ""))
    if comp_key == "borgwarner" and product_name.startswith("S"):
        candidates.update({slugify(f"s300_{product_name}"), slugify(f"s200_{product_name}")})
    return [r for r in docx_rows if r["section"].upper() in allowed and slugify(r["model"]) in candidates][:4]


def _lookup_xml(xml_by_path: dict[str, dict[str, Any]], *paths: str) -> dict[str, Any] | None:
    for p in paths:
        hit = xml_by_path.get(p.lower()) or xml_by_path.get(Path(p).name.lower())
        if hit is not None:
            return hit
    return None


def _build_calibrated_spec(
    row: dict[str, Any], company_name: str, family_name: str, turbo_id: str,
    fig: dict[str, Any], match_kind: str, xml_by_path: dict[str, dict[str, Any]], docx_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Construct calibrated turbo_spec.json dictionary for one SQL row."""
    unit = str(row["airflow_unit"]).strip()
    m_min, m_max = convert_flow_to_lb_min(float(row["airflow_min"]), unit), convert_flow_to_lb_min(float(row["airflow_max"]), unit)
    pr_min, pr_max = float(row["pr_min"]), float(row["pr_max"])
    matrix, corners = compute_affine_transform(float(row["pixel_x_start"]), float(row["pixel_y_start"]), float(row["pixel_width"]), float(row["pixel_height"]), m_min, m_max, pr_min, pr_max)
    env_pts = _extract_points(row, unit, (("envelope", 0, 9),))
    surge_line, choke_boundary = _derive_boundaries(row, unit, env_pts)
    eff_pts = _extract_points(row, unit, (("eff_line", 1, 2), ("efficiency", 0, 4)))
    xml_hit = _lookup_xml(xml_by_path, str(row["map_path"]), fig["canonical_map_path"])
    return {
        "schema_version": "1.0.0", "turbo_id": turbo_id, "calibrated": True, "axis_calibration_pending": False, "is_alias": False,
        "company_name": sanitize_text(company_name), "family_name": sanitize_text(family_name),
        "product_name": sanitize_text(str(row["product_name"])), "source_product_id": int(row["product_id"]),
        "figure_sha256": fig["sha256"], "figure_path": fig["figure_rel_path"], "legacy_map_path": str(row["map_path"]), "figure_match_method": match_kind,
        "nist_sp811_normalization": {
            "source_airflow_unit": unit, "target_airflow_unit": "lb/min", "secondary_airflow_unit": "kg/s",
            "rho_ref_kg_m3": RHO_AIR_REF_KG_M3, "t_ref_k": 298.15, "p_ref_kpa": 101.325,
            "legacy_cfm_to_lb_min_factor": CFM_TO_LB_MIN, "nist_298k_volumetric_factor": NIST_298K_VOLUMETRIC_FACTOR,
        },
        "source_bounds": {
            "airflow_min_raw": float(row["airflow_min"]), "airflow_max_raw": float(row["airflow_max"]),
            "airflow_min_lb_min": round(m_min, 6), "airflow_max_lb_min": round(m_max, 6), "pr_min": pr_min, "pr_max": pr_max,
            "pixel_x_start": int(row["pixel_x_start"]), "pixel_y_start": int(row["pixel_y_start"]), "pixel_width": int(row["pixel_width"]), "pixel_height": int(row["pixel_height"]),
        },
        "affine_matrix_2x3": matrix, "calibration_corners": corners, "envelope_points": env_pts, "surge_line": surge_line, "choke_boundary": choke_boundary,
        "efficiency_spine_points": eff_pts, "efficiency_islands": [{"id": "efficiency_spine", "efficiency": None, "label": "Peak efficiency spine", "spine_points": eff_pts}] if eff_pts else [],
        "geometry": {
            "wheel_size": _clean_opt_str(str(row.get("wheel_size", ""))) or (xml_hit["wheel_size"] if xml_hit else None),
            "trim": _clean_opt_str(str(row.get("trim", ""))) or (xml_hit["trim"] if xml_hit else None),
            "ar": _clean_opt_str(str(row.get("ar", ""))) or (xml_hit["ar"] if xml_hit else None),
            "docx_wheel_dimensions": _match_docx_wheel(company_name, family_name, str(row["product_name"]), docx_rows),
        },
        "xml_crosscheck": {"xml_units": xml_hit["units"], "units_match": ("cfm" if xml_hit["units"].lower() == "cfmc" else xml_hit["units"].lower()) == unit.lower()} if xml_hit else None,
        "source_notes": sanitize_text(str(row.get("notes", ""))),
    }


def _build_stub_spec(turbo_id: str, fig: dict[str, Any], xml_by_path: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Construct uncalibrated stub turbo_spec.json for unmatched map figures."""
    rel = fig["canonical_map_path"]
    xml_hit = _lookup_xml(xml_by_path, rel)
    return {
        "schema_version": "1.0.0", "turbo_id": turbo_id, "calibrated": False, "axis_calibration_pending": True, "is_alias": False,
        "company_name": xml_hit["company_name"] if xml_hit else sanitize_text(Path(rel).parent.name),
        "family_name": xml_hit["family_name"] if xml_hit else None, "product_name": xml_hit["product_name"] if xml_hit else sanitize_text(Path(rel).stem),
        "figure_sha256": fig["sha256"], "figure_path": fig["figure_rel_path"], "legacy_map_path": rel,
        "affine_matrix_2x3": None, "calibration_corners": None, "envelope_points": [], "surge_line": [], "choke_boundary": [],
        "efficiency_spine_points": [], "efficiency_islands": [], "xml_crosscheck": {"xml_units": xml_hit["units"]} if xml_hit else None, "source_notes": "",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def _emit_aliases_and_catalog(
    p_profiles: Path, p_examples: Path, specs_by_id: dict[str, dict[str, Any]],
    counts: dict[str, int], docx_data: dict[str, Any], xml_count: int,
) -> dict[str, str]:
    targets = {"garrett_t3_t4_50trim": ("Garrett/Honeywell", "T04E Series", "50 Trim"), "garrett_t3_super60_t2": ("Garrett/Honeywell", "T3 Series", "60 Trim - Super60")}
    aliases: dict[str, str] = {}
    for alias_id, target_key in targets.items():
        for tid, spec in specs_by_id.items():
            if spec.get("calibrated") and (spec.get("company_name"), spec.get("family_name"), spec.get("product_name")) == target_key:
                aliases[alias_id] = tid
                _write_json(p_profiles / alias_id / "turbo_spec.json", {**spec, "turbo_id": alias_id, "is_alias": True, "alias_of": tid, "canonical_turbo_id": tid, "canonical_spec_ref": f"profiles/turbo/{tid}/turbo_spec.json"})
                break
    if p_examples.is_dir():
        for ex_file in sorted(p_examples.glob("*.json")):
            ex_tid = json.loads(ex_file.read_text(encoding="utf-8")).get("turbo_and_intercooler", {}).get("turbo_id")
            if ex_tid and ex_tid not in specs_by_id and ex_tid not in aliases:
                raise KeyError(f"Example profile {ex_file.name} references unmapped turbo_id {ex_tid!r}")
    entries = [{k: s[k] for k in ("company_name", "family_name", "product_name", "calibrated", "axis_calibration_pending", "figure_sha256", "figure_path")} | {"turbo_id": tid, "spec_ref": f"profiles/turbo/{tid}/turbo_spec.json"} for tid, s in sorted(specs_by_id.items())]
    _write_json(p_profiles / "catalog.json", {
        "schema_version": "1.0.0", **counts, "alias_count": len(aliases), "aliases": aliases,
        "chrysler_oem_turbos": docx_data["chrysler_oem_turbos"], "docx_wheel_specs_count": len(docx_data["rows"]), "xml_crosscheck_count": xml_count, "entries": entries,
    })
    return aliases


def _resolve_source_path(explicit: Path | str | None, source_dir: Path | str | None, root: Path, key: str) -> Path:
    if explicit is not None:
        return Path(explicit)
    rel = DEFAULT_REL_PATHS[key]
    if source_dir is not None:
        sdir = Path(source_dir)
        tc_rel = Path(*rel.parts[3:]) if len(rel.parts) > 3 else rel
        for cand in (sdir / tc_rel, sdir / rel, sdir / rel.name):
            if cand.exists():
                return cand
    return root / rel


def run_import_turbocalc(
    *,
    repo_root: Path | str | None = None, source_dir: Path | str | None = None, output_dir: Path | str | None = None,
    rar_path: Path | str | None = None, sql_path: Path | str | None = None, maps_dir: Path | str | None = None,
    docx_path: Path | str | None = None, xml_path: Path | str | None = None, profiles_dir: Path | str | None = None,
    figures_dir: Path | str | None = None, examples_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Import TurboCalc SQL/XML/DOCX/figures into profiles/turbo and figures/turbo_maps."""
    root = (Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[3]).resolve()
    p_profiles = Path(profiles_dir) if profiles_dir is not None else (Path(output_dir) if output_dir is not None else root / DEFAULT_REL_PATHS["profiles"])
    p_figures = Path(figures_dir) if figures_dir is not None else (root / DEFAULT_REL_PATHS["figures"])
    if sql_path is not None:
        sql_text = Path(sql_path).read_text(encoding="utf-8", errors="replace")
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            sql_text = extract_tools_sql(_resolve_source_path(rar_path, source_dir, root, "rar"), Path(tmpdir)).read_text(encoding="utf-8", errors="replace")
    companies = {int(r["company_id"]): r for r in parse_sql_inserts(sql_text, "turbocalc_company")}
    families = {int(r["family_id"]): r for r in parse_sql_inserts(sql_text, "turbocalc_family")}
    data_rows = sorted(parse_sql_inserts(sql_text, "turbocalc_data"), key=lambda r: int(r["product_id"]))
    docx_data = parse_docx_wheel_specs(_resolve_source_path(docx_path, source_dir, root, "docx"))
    xml_items = parse_turbocalc_xml(_resolve_source_path(xml_path, source_dir, root, "xml"))
    xml_by_path: dict[str, dict[str, Any]] = {}
    for item in xml_items:
        xml_by_path[item["map_path"].lower()] = item
        xml_by_path.setdefault(Path(item["map_path"]).name.lower(), item)
    dedup, source_file_count = _deduplicate_maps(_resolve_source_path(maps_dir, source_dir, root, "maps"), p_figures, {str(r["map_path"]).strip() for r in data_rows})
    used_ids, calibrated_hashes, specs_by_id = set(), set(), {}
    for row in data_rows:
        digest, match_kind = _match_sql_to_figure(str(row["map_path"]), dedup)
        calibrated_hashes.add(digest)
        cname, fname = str(companies[int(row["company_id"])]["company_name"]), str(families[int(row["family_id"])]["family_name"])
        tid = _build_turbo_id(cname, fname, str(row["product_name"]), int(row["product_id"]), used_ids)
        specs_by_id[tid] = _build_calibrated_spec(row, cname, fname, tid, dedup[digest], match_kind, xml_by_path, docx_data["rows"])
        _write_json(p_profiles / tid / "turbo_spec.json", specs_by_id[tid])
    stub_count = 0
    for digest, fig in sorted(dedup.items(), key=lambda kv: kv[1]["canonical_map_path"].lower()):
        if digest not in calibrated_hashes:
            rel = fig["canonical_map_path"]
            base = slugify(f"{Path(rel).parent.name}_{Path(rel).stem}")
            tid = base if base not in used_ids else f"{base}_{digest[:6]}"
            used_ids.add(tid)
            specs_by_id[tid] = _build_stub_spec(tid, fig, xml_by_path)
            _write_json(p_profiles / tid / "turbo_spec.json", specs_by_id[tid])
            stub_count += 1
    counts = {"company_count": len(companies), "family_count": len(families), "calibrated_count": len(data_rows), "stub_count": stub_count, "unique_figure_count": len(dedup), "source_figure_count": source_file_count}
    aliases = _emit_aliases_and_catalog(p_profiles, Path(examples_dir) if examples_dir is not None else (root / DEFAULT_REL_PATHS["examples"]), specs_by_id, counts, docx_data, len(xml_items))
    return {**counts, "alias_count": len(aliases), "aliases": aliases, "profiles_dir": str(p_profiles), "figures_dir": str(p_figures)}
