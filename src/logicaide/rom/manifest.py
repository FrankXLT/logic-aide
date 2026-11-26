"""Partitioned ROM manifest builder, strict SCI lookup gate, and ctags synchronizer."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from logicaide.rom.asm_parsers import (
    parse_asm_file,
    parse_lm_io_pinout_xls,
    parse_lst_file,
    parse_sample_cpp_lm_offsets,
    parse_tpl_calibration_symbols,
)
from logicaide.rom.manifest_specs import (
    ROM_SPECS,
    ROM_SPEC_MAP,
    RomSpec,
    build_baseline_records,
    build_calibration_tables,
    build_fast_data_table,
    clean_source_rel,
    collect_rom_src_and_twin_tags,
    extract_asm_bit_defs,
    find_ecu_root,
    index_ecu_files,
    resolve_optional,
    resolve_source,
    scan_staged_baselines,
    xref_corroborates_equate,
)
from logicaide.rom.sci_xref import (
    DEFAULT_XREF_OUTPUT,
    build_mpr_sci_crossref,
    load_mpr_sci_crossref,
)

NO_VERIFIED_SOURCE_MATCH = "No Verified Source Match"
DEFAULT_MANIFESTS_DIR = Path("rom/manifests")
DEFAULT_CATALOG_PATH = DEFAULT_MANIFESTS_DIR / "catalog.json"

_SETTING_KEYS = (
    "size_bytes", "major_interval", "minor_interval", "divisor",
    "units", "display_min", "display_max", "decimals", "ecu_min", "ecu_max",
)


def _build_verified_watchlist(
    spec: RomSpec,
    asm_path: Path,
    ram_equates: list[dict[str, Any]],
    lst_syms: dict[str, str],
    fast_slots: list[dict[str, Any]],
    xref: dict[str, Any],
    xref_by_addr: dict[int, list[dict[str, Any]]],
    cpp_by_addr: dict[int, dict[str, Any]],
    xls_data: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], set[int]]:
    """Build strictly source-verified ram_watchlist entries and track corroborated xref ItemNums."""
    asm_bit_defs = extract_asm_bit_defs(asm_path)
    err_regs, primary_table = xref.get("error_registers", {}), spec.sci_tables[0]
    watchlist: list[dict[str, Any]] = []
    corroborated: set[int] = set()

    for eq in ram_equates:
        addr = eq["address"]
        matches = [xv for xv in xref_by_addr.get(addr, []) if xref_corroborates_equate(xv, eq, asm_bit_defs)]
        best = matches[0] if matches else None
        if best is not None:
            corroborated.add(best["item_num"])
        mpscan_sn = (eq.get("mpscan") or {}).get("short_name")
        short_name = best["short_name"] if best else (mpscan_sn if mpscan_sn not in (None, "", "None") else None)
        cpp_entry = cpp_by_addr.get(addr)
        sources = [asm_path.name] + (["lst_symbol_table"] if eq["name"] in lst_syms else []) + (["sample.cpp"] if cpp_entry else [])
        err_entry = err_regs.get(short_name) if short_name in err_regs and best is not None else None
        watchlist.append({
            "offset": addr,
            "offset_hex": f"0x{addr:02X}",
            "symbol": eq["name"],
            "short_name": short_name,
            "sci_table": primary_table,
            "asm_line": eq["line_number"],
            "lst_address": lst_syms.get(eq["name"]),
            "source_verified": True,
            "verified_sources": sources,
            "settings": {k: best[k] for k in _SETTING_KEYS} if best else None,
            "bit_flags": best.get("bit_flags") if best else None,
            "error_codes": err_entry.get("bit_flags") if err_entry else None,
            "function_set": best.get("function_set") if best else None,
            "sample_cpp_mask": cpp_entry.get("mask_hex") if cpp_entry else None,
        })

    for slot in fast_slots:
        if slot.get("source_verified"):
            watchlist.append({
                "offset": slot["slot"],
                "offset_hex": slot["slot_hex"],
                "symbol": slot["symbol"],
                "short_name": None,
                "sci_table": "0xF6",
                "target_address": slot["target_address"],
                "table_rom_address": slot["table_rom_address"],
                "source_verified": True,
                "verified_sources": ["FastDataOutputAddressTable"],
            })

    if xls_data and "LM_IO" in spec.sci_tables:
        for io_row in xls_data.get("entries", []):
            addr_hex = io_row.get("address")
            if not addr_hex:
                continue
            sig = io_row.get("signal_87_t2") or io_row.get("internal_function") or io_row.get("signal_85_t1") or f"IO_{addr_hex}"
            watchlist.append({
                "offset": int(addr_hex, 16),
                "offset_hex": addr_hex,
                "symbol": re.sub(r"[^A-Za-z0-9]+", "_", sig).strip("_"),
                "short_name": None,
                "sci_table": "LM_IO",
                "source_verified": True,
                "verified_sources": ["LM IO pinout - address.xls"],
                "io_pinout": io_row,
            })
    return watchlist, corroborated


def _build_unverified_candidates(
    spec: RomSpec,
    ram_by_addr: dict[int, dict[str, Any]],
    xref: dict[str, Any],
    corroborated: set[int],
    cpp_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Record mprxref / driver candidates lacking source corroboration with source_verified=False."""
    unverified: list[dict[str, Any]] = []
    for xv in xref.get("variables", []):
        p = xv.get("protocols", {}).get(spec.xref_protocol)
        if p is not None and xv["item_num"] not in corroborated:
            eq = ram_by_addr.get(p["offset"])
            unverified.append({
                "item_num": xv["item_num"],
                "offset": p["offset"],
                "offset_hex": p["hex"],
                "short_name": xv["short_name"],
                "long_name": xv["long_name"],
                "xref_protocol": spec.xref_protocol,
                "asm_symbol_at_offset": eq["name"] if eq else None,
                "source_verified": False,
                "reason": "symbol_mismatch_at_offset" if eq else "offset_absent_in_ecu_asm",
            })
    for ce in (cpp_data or {}).get("entries", []):
        if ce["offset"] not in ram_by_addr:
            unverified.append({
                "offset": ce["offset"],
                "offset_hex": ce["offset_hex"],
                "short_name": None,
                "long_name": ce["field_name"],
                "xref_protocol": spec.xref_protocol,
                "asm_symbol_at_offset": None,
                "source_verified": False,
                "reason": "sample_cpp_offset_absent_in_ecu_asm",
            })
    return unverified


def _build_single_manifest(
    spec: RomSpec,
    ecu_root: Path,
    file_index: dict[str, Path],
    staged_hashes: set[str],
    xref: dict[str, Any],
) -> dict[str, Any]:
    """Build one partitioned ROM manifest dictionary from read-only corpus sources."""
    asm_path = resolve_source(ecu_root, file_index, spec.asm_candidates)
    if asm_path is None:
        raise FileNotFoundError(f"Required ASM source not found for {spec.rom_id}: {spec.asm_candidates}")
    asm_parsed = parse_asm_file(asm_path)
    source_files = [clean_source_rel(asm_path, ecu_root)]

    tpl_entries: list[dict[str, Any]] = []
    for tpl_cands in spec.tpl_candidates:
        parsed_tpl = resolve_optional(ecu_root, file_index, tpl_cands, source_files, parse_tpl_calibration_symbols)
        if parsed_tpl and not tpl_entries:
            tpl_entries = parsed_tpl
    lst_syms = (resolve_optional(ecu_root, file_index, spec.lst_candidates, source_files, parse_lst_file) if spec.lst_candidates else None) or {}
    cpp_data = resolve_optional(ecu_root, file_index, ("lm_test_driver.20080713/sample.cpp", "sample.cpp"), source_files, parse_sample_cpp_lm_offsets) if spec.use_sample_cpp else None
    xls_data = resolve_optional(ecu_root, file_index, ("LM IO pinout - address.xls",), source_files, parse_lm_io_pinout_xls) if spec.use_lm_io_xls else None

    ram_equates = asm_parsed.get("ram_equates", [])
    ram_by_name = {eq["name"]: eq for eq in ram_equates}
    ram_by_addr: dict[int, dict[str, Any]] = {}
    for eq in ram_equates:
        ram_by_addr.setdefault(eq["address"], eq)
    xref_by_addr: dict[int, list[dict[str, Any]]] = {}
    for xv in xref.get("variables", []):
        p = xv.get("protocols", {}).get(spec.xref_protocol)
        if p is not None:
            xref_by_addr.setdefault(p["offset"], []).append(xv)
    cpp_by_addr = {ce["offset"]: ce for ce in (cpp_data or {}).get("entries", [])}

    fast_slots = build_fast_data_table(spec, tpl_entries, ram_by_name, lst_syms)
    cal_tables = build_calibration_tables(asm_parsed, lst_syms, tpl_entries)
    watchlist, corroborated = _build_verified_watchlist(
        spec, asm_path, ram_equates, lst_syms, fast_slots, xref, xref_by_addr, cpp_by_addr, xls_data,
    )
    unverified = _build_unverified_candidates(spec, ram_by_addr, xref, corroborated, cpp_data)
    baselines = build_baseline_records(spec, ecu_root, file_index, staged_hashes)
    drb_mem = [{**m, "rom_address": lst_syms.get("DRBMemoryTable")} for m in asm_parsed.get("drb_memory_table", [])]

    manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "rom_id": spec.rom_id,
        "ecu_family": spec.ecu_family,
        "mcu": spec.mcu,
        "rom_size_bytes": spec.rom_size_bytes,
        "base_address": spec.base_address,
        "address_range": spec.address_range,
        "sci_baud": spec.sci_baud,
        "sci_baud_nominal": spec.sci_baud_nominal,
        "sci_tables": list(spec.sci_tables),
        "xref_protocol": spec.xref_protocol,
        "origins": asm_parsed.get("origins", {}),
        "source_files": source_files,
        "reference_baselines": baselines,
        "drb_function_table": asm_parsed.get("drb_function_table", []),
        "drb_memory_table": drb_mem,
        "xmit_command_modes": asm_parsed.get("xmit_command_modes", []),
        "fast_data_output_addresses": fast_slots,
        "calibration_tables": cal_tables,
        "ram_watchlist": watchlist,
        "unverified_xref_candidates": unverified,
        "summary": {
            "verified_ram_count": len(watchlist),
            "unverified_candidate_count": len(unverified),
            "calibration_table_count": len(cal_tables),
            "fast_data_slot_count": len(fast_slots),
            "baseline_count": len(baselines),
            "baselines_present_count": sum(1 for b in baselines if b["baseline_present"]),
        },
    }
    if spec.split_chip_topology is not None:
        manifest["split_chip_topology"] = spec.split_chip_topology
    return manifest


def build_partitioned_manifests(
    private_root: Path | str,
    rom_dir: Path | str = Path("rom"),
    *,
    baselines_dir: Path | str | None = None,
    mpr_crossref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build partitioned rom_manifest.json files, catalog.json, and mpr_sci_crossref.json."""
    ecu_root = find_ecu_root(Path(private_root))
    file_index = index_ecu_files(ecu_root)
    rom_base = Path(rom_dir)
    manifests_dir = rom_base / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    xref = mpr_crossref
    if xref is None:
        mpr_dir = ecu_root / "com.mpsuite.mpscandroid/files/mpr"
        xref_out = rom_base / "sci" / DEFAULT_XREF_OUTPUT.name
        if mpr_dir.is_dir():
            xref = build_mpr_sci_crossref(mpr_dir, xref_out)
        elif xref_out.is_file():
            xref = load_mpr_sci_crossref(xref_out)
        else:
            xref = {"variables": [], "error_registers": {}, "function_sets": {}}

    staged_hashes = scan_staged_baselines(Path(baselines_dir) if baselines_dir is not None else (rom_base / "stock_baselines"))
    repo_anchor = rom_base.resolve().parent if rom_base.name == "rom" else rom_base.resolve()
    catalog_entries, results = [], {}

    for spec in ROM_SPECS:
        manifest = _build_single_manifest(spec, ecu_root, file_index, staged_hashes, xref)
        target_dir = manifests_dir / spec.rom_id
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = target_dir / "rom_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        catalog_entries.append({
            "rom_id": spec.rom_id,
            "ecu_family": spec.ecu_family,
            "mcu": spec.mcu,
            "rom_size_bytes": spec.rom_size_bytes,
            "base_address": spec.base_address,
            "address_range": spec.address_range,
            "sci_baud": spec.sci_baud,
            "sci_tables": list(spec.sci_tables),
            "xref_protocol": spec.xref_protocol,
            "manifest_ref": manifest_path.resolve().relative_to(repo_anchor).as_posix(),
            "summary": manifest["summary"],
        })
        results[spec.rom_id] = manifest["summary"]

    catalog_path = manifests_dir / "catalog.json"
    catalog = {"schema_version": "1.0.0", "manifest_count": len(catalog_entries), "manifests": catalog_entries}
    catalog_path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"catalog_path": catalog_path.as_posix(), "manifest_count": len(catalog_entries), "manifests": results}


_SEED_HINT = "Run 'uv run python scripts/seed_rom_manifest.py' to generate partitioned ROM manifests."


def load_rom_manifest(manifest_or_rom_id: str | Path, catalog_path: Path | str = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    """Load a partitioned ROM manifest from a direct path or rom_id via catalog.json."""
    if isinstance(manifest_or_rom_id, Path) or str(manifest_or_rom_id).endswith(".json"):
        path = Path(manifest_or_rom_id)
        if not path.is_file():
            raise FileNotFoundError(f"ROM manifest not found: {path}. {_SEED_HINT}")
        return json.loads(path.read_text(encoding="utf-8"))

    rom_id, cat_path = str(manifest_or_rom_id).strip(), Path(catalog_path)
    if not cat_path.is_file():
        raise FileNotFoundError(f"ROM manifest catalog not found: {cat_path}. {_SEED_HINT}")
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))
    entry = next((m for m in catalog.get("manifests", []) if m.get("rom_id") == rom_id), None)
    if entry is None:
        raise KeyError(f"Unknown rom_id {rom_id!r} in {cat_path}")
    ref = entry.get("manifest_ref", f"rom/manifests/{rom_id}/rom_manifest.json")
    for cand in (cat_path.parent / rom_id / "rom_manifest.json", cat_path.parent.parent / ref, cat_path.parent.parent.parent / ref):
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Partitioned manifest file missing for {rom_id!r}: {ref}. {_SEED_HINT}")


def _normalize_sci_offset(offset: str | int | None) -> tuple[int | None, str | None]:
    """Normalize 0x63 / $63 / '99' (decimal) / 99 / 16-bit '0x00F2' into (int_value, hex_str)."""
    if offset is None:
        return None, None
    if isinstance(offset, bool):
        raise ValueError("Boolean is not a valid SCI offset")
    if isinstance(offset, int):
        val, is_wide = offset, offset > 0xFF
    else:
        text = offset.strip()
        if not text:
            return None, None
        if text.lower().startswith(("0x", "0h")):
            digits = text[2:]
            val, is_wide = int(digits, 16), len(digits) >= 4
        elif text.startswith("$"):
            digits = text[1:]
            val, is_wide = int(digits, 16), len(digits) >= 4
        else:
            val, is_wide = int(text, 10), False
    if val < 0:
        raise ValueError(f"SCI offset must be non-negative (got {val})")
    return val, f"0x{val:0{4 if (val > 0xFF or is_wide) else 2}X}"


def lookup_sci(
    rom_id_or_manifest: str | Path,
    *,
    offset: str | int | None = None,
    var_name: str | None = None,
    sci_table: str | None = None,
    strict: bool = True,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
) -> dict[str, Any]:
    """Look up an SCI offset or variable with strict source-verification enforcement."""
    manifest = load_rom_manifest(rom_id_or_manifest, catalog_path=catalog_path)
    target_int, target_hex = _normalize_sci_offset(offset)
    target_var = var_name.strip().lower() if var_name is not None else None
    if target_var == "":
        raise ValueError("var_name must be non-empty when provided.")
    target_tbl = sci_table.strip().lower() if sci_table is not None else None
    manifest_tables = {t.strip().lower() for t in manifest.get("sci_tables", [])}

    def _matches(entry: dict[str, Any], *, require_entry_table: bool) -> bool:
        if target_tbl is not None:
            if require_entry_table and (entry.get("sci_table") or "").strip().lower() != target_tbl:
                return False
            if not require_entry_table and target_tbl not in manifest_tables:
                return False
        if target_int is not None and entry.get("offset") != target_int:
            return False
        if target_var is not None:
            candidates = {val for k in ("symbol", "short_name", "long_name") if (val := (entry.get(k) or "").strip().lower())}
            if target_var not in candidates:
                return False
        return True

    verified = [e for e in manifest.get("ram_watchlist", []) if e.get("source_verified") is True and _matches(e, require_entry_table=True)]
    if verified:
        first = verified[0]
        return {
            "matched": True, "status": "VERIFIED_SOURCE_MATCH", "rom_id": manifest.get("rom_id"),
            "sci_table": first.get("sci_table"), "offset": first.get("offset_hex"),
            "var_name": first.get("symbol"), "entry": first, "matches": verified,
        }
    if not strict:
        unverified = [e for e in manifest.get("unverified_xref_candidates", []) if _matches(e, require_entry_table=False)]
        if unverified:
            first = unverified[0]
            return {
                "matched": True, "status": "UNVERIFIED_XREF_MATCH", "rom_id": manifest.get("rom_id"),
                "sci_table": sci_table, "offset": first.get("offset_hex"),
                "var_name": first.get("long_name") or first.get("short_name"), "entry": first, "matches": unverified,
            }
    return {
        "matched": False, "status": "NO_VERIFIED_SOURCE_MATCH", "message": NO_VERIFIED_SOURCE_MATCH,
        "rom_id": manifest.get("rom_id"), "sci_table": sci_table, "offset": target_hex, "var_name": var_name,
    }


def sync_manifests(
    *,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
    rom_id: str | None = None,
    tags_path: Path | str = Path("tags"),
    rom_dir: Path | str = Path("rom"),
) -> dict[str, Any]:
    """Generate sorted Exuberant CTAGS file from verified manifests, ASM labels, and twin ASTs."""
    cat_path = Path(catalog_path)
    if not cat_path.is_file():
        raise FileNotFoundError(f"ROM manifest catalog not found: {cat_path}. {_SEED_HINT}")
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))
    selected_ids = [e["rom_id"] for e in catalog.get("manifests", []) if rom_id is None or e.get("rom_id") == rom_id]
    if rom_id is not None and not selected_ids:
        raise KeyError(f"Unknown rom_id {rom_id!r} in {cat_path}")
    raw_tags: set[tuple[str, str, str, str]] = set(collect_rom_src_and_twin_tags(Path(rom_dir)))

    for rid in selected_ids:
        manifest = load_rom_manifest(rid, catalog_path=cat_path)
        mref = f"rom/manifests/{rid}/rom_manifest.json"
        for ct in manifest.get("calibration_tables", []):
            if ct.get("source_verified") and ct.get("symbol"):
                raw_tags.add((ct["symbol"], mref, str(ct.get("asm_line") or 1), f't\trom:{rid}\taddr:{ct.get("rom_address") or ct.get("tpl_table_addr") or ""}'))
        for dm in manifest.get("drb_memory_table", []):
            if dm.get("symbol"):
                raw_tags.add((dm["symbol"], mref, str(dm.get("line_number") or 1), f'v\trom:{rid}\taddr:{dm.get("offset_hex") or ""}'))
        for fs in manifest.get("fast_data_output_addresses", []):
            if fs.get("symbol"):
                raw_tags.add((fs["symbol"], mref, "1", f'v\trom:{rid}\taddr:{fs.get("slot_hex") or ""}'))
        for rw in manifest.get("ram_watchlist", []):
            if rw.get("source_verified") is True and rw.get("symbol"):
                raw_tags.add((rw["symbol"], mref, str(rw.get("asm_line") or 1), f'd\trom:{rid}\taddr:{rw.get("offset_hex") or ""}\ttable:{rw.get("sci_table") or ""}'))

    header = [
        "!_TAG_FILE_FORMAT\t2\t/extended format; --format=1 will not append ;\" to lines/",
        "!_TAG_FILE_SORTED\t1\t/0=unsorted, 1=sorted, 2=foldcase/",
        "!_TAG_PROGRAM_NAME\tLogicAIDE\t//",
    ]
    body = sorted(f'{name}\t{fpath}\t{cmd};"\t{ext}' for name, fpath, cmd, ext in raw_tags)
    dest = Path(tags_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    return {"tags_path": dest.as_posix(), "tag_count": len(body), "rom_ids": selected_ids}
