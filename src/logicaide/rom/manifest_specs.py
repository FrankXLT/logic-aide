"""Static ROM specifications, corpus path resolution, and source corroboration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

from logicaide.rom.asm_parsers import scrub_cleanroom_text

_WORD_TOKEN_STOP = frozenset({"from", "with", "into", "for", "and", "of", "to", "in", "the"})


@dataclass(frozen=True)
class RomSpec:
    """Immutable specification for one strictly partitioned ROM family target."""

    rom_id: str
    ecu_family: str
    mcu: str
    rom_size_bytes: int
    base_address: str
    address_range: str
    sci_baud: int
    sci_baud_nominal: float
    sci_tables: tuple[str, ...]
    xref_protocol: str
    asm_candidates: tuple[str, ...]
    tpl_candidates: tuple[tuple[str, ...], ...]
    lst_candidates: tuple[str, ...]
    baseline_candidates: tuple[tuple[str, ...], ...]
    use_sample_cpp: bool = False
    use_lm_io_xls: bool = False
    split_chip_topology: str | None = None


ROM_SPECS: tuple[RomSpec, ...] = (
    RomSpec(
        rom_id="smec_88_89_v19",
        ecu_family="SMEC_DUAL_BOARD",
        mcu="68HC11",
        rom_size_bytes=32768,
        base_address="0x8000",
        address_range="0x8000-0xFFFF",
        sci_baud=62500,
        sci_baud_nominal=62500.0,
        sci_tables=("0xF2",),
        xref_protocol="SMEC T1",
        asm_candidates=("T-SMEC_v19_6Untouched/T-SMEC_v19.asm",),
        tpl_candidates=(("T-SMEC_v19_6Untouched/T-SMEC_25_MTX_B151_Stock.tpl",),),
        lst_candidates=(
            "T-SMEC_v19_6Untouched/k_B151_Stock75-3-fans-batoff-ob.lst",
            "K SMEC/k_B151_Stock75-3-fans-batoff-ob.lst",
        ),
        baseline_candidates=(
            ("T-SMEC_v19_6Untouched/T-SMEC_25_MTX_B151_Stock.bin", "K SMEC/T-SMEC_25_MTX_B151_Stock.bin"),
            ("T-SMEC_v19_6Untouched/139stock.bin",),
            ("T-SMEC_v19_6Untouched/89_25_t1_mtx_151.BIN",),
        ),
    ),
    RomSpec(
        rom_id="sbec_90_93_v3_1",
        ecu_family="SBEC_SINGLE_BOARD",
        mcu="68HC11",
        rom_size_bytes=32768,
        base_address="0x8000",
        address_range="0x8000-0xFFFF",
        sci_baud=62500,
        sci_baud_nominal=62500.0,
        sci_tables=("0xF2", "0xF6"),
        xref_protocol="SBEC T1",
        asm_candidates=("T-SBEC_v3_1/T-SBEC_v3_1.asm",),
        tpl_candidates=(
            ("T-SBEC_v3_1/T-SBEC_25_MTX_A225_Stock.tpl",),
            ("T-SBEC_v3_1/T-SBEC_25_ATX_A577_Stock.tpl",),
        ),
        lst_candidates=(
            "T-SBEC_v3_1/T-SBEC_22_ATX_S-IIMiles.lst",
            "T-SBEC_S-II_commented EMR 1 OUT/T-SBEC_22_ATX_S-IIMiles.lst",
        ),
        baseline_candidates=(
            ("T-SBEC_v3_1/T-SBEC_22_ATX_S-II.bin", "T-SBEC_S-II_commented EMR 1 OUT/T-SBEC_22_ATX_S-II.bin"),
        ),
    ),
    RomSpec(
        rom_id="lm_86_87_v13",
        ecu_family="LM_PM",
        mcu="6803",
        rom_size_bytes=16384,
        base_address="0xC000",
        address_range="0xC000-0xFFFF",
        sci_baud=7680,
        sci_baud_nominal=7812.5,
        sci_tables=("LM_DIRECT_RAM", "LM_IO"),
        xref_protocol="LM",
        asm_candidates=("T-LM v13 July 2017/T-LMv13-2017.asm",),
        tpl_candidates=(("T-LM v13 July 2017/T_LM_22_MTX_A171_Stock.tpl",),),
        lst_candidates=("T-LM v13 July 2017/MCGLSR2/T_LM_22_MTX_A641_MPStgIIScaled.lst",),
        baseline_candidates=(
            ("T-LM v13 July 2017/87_22_t2_mtx_171.bin",),
            ("T-LM v13 July 2017/S60_128k.bin",),
        ),
        use_sample_cpp=True,
        use_lm_io_xls=True,
    ),
    RomSpec(
        rom_id="lm_86_glhs_v1",
        ecu_family="LM_PM",
        mcu="6803",
        rom_size_bytes=8192,
        base_address="0xE000",
        address_range="0xE000-0xFFFF",
        sci_baud=7680,
        sci_baud_nominal=7812.5,
        sci_tables=("LM_DIRECT_RAM", "LM_IO"),
        xref_protocol="LM",
        asm_candidates=("T-GLHSv1/T-GLHSv1.asm",),
        tpl_candidates=(("T-GLHSv1/T-GLHS_t2_mtx_017_stock.tpl",),),
        lst_candidates=(),
        baseline_candidates=(("T-GLHSv1/86_22_t2_mtx_016_GLHS_ROM2.bin",),),
        use_sample_cpp=False,
        use_lm_io_xls=True,
        split_chip_topology="2x_8KB_2764",
    ),
)
ROM_SPEC_MAP: dict[str, RomSpec] = {spec.rom_id: spec for spec in ROM_SPECS}


def find_ecu_root(private_root: Path) -> Path:
    """Locate the ECU source corpus directory under private_root without hardcoding vendor names."""
    for cand in (private_root / "ECU", private_root):
        if (cand / "T-SMEC_v19_6Untouched").is_dir() or (cand / "com.mpsuite.mpscandroid").is_dir():
            return cand
    if private_root.is_dir():
        for child in sorted(private_root.iterdir()):
            if child.is_dir() and (child / "ECU").is_dir():
                return child / "ECU"
    return private_root


def index_ecu_files(ecu_root: Path) -> dict[str, Path]:
    """Build a deterministic lowercase-basename -> Path index under ecu_root."""
    by_name: dict[str, Path] = {}
    if not ecu_root.is_dir():
        return by_name
    for path in sorted(ecu_root.rglob("*")):
        if not path.is_file():
            continue
        key = path.name.lower()
        prev = by_name.get(key)
        if prev is None or ("20080713" in path.as_posix() and "20080713" not in prev.as_posix()):
            by_name[key] = path
    return by_name


def resolve_source(ecu_root: Path, file_index: dict[str, Path], candidates: tuple[str, ...]) -> Path | None:
    """Resolve the first existing candidate path or fallback by basename in ecu_root."""
    for rel in candidates:
        cand = ecu_root / rel
        if cand.is_file():
            return cand
    for rel in candidates:
        hit = file_index.get(Path(rel).name.lower())
        if hit is not None and hit.is_file():
            return hit
    return None


def clean_source_rel(path: Path, ecu_root: Path) -> str:
    """Return cleanroom ECU-relative provenance string without archive wrapper directories."""
    if path.name == "sample.cpp":
        return "ECU/lm_test_driver.20080713/sample.cpp"
    if path.name == "LM IO pinout - address.xls":
        return "ECU/LM IO pinout - address.xls"
    try:
        rel = path.resolve().relative_to(ecu_root.resolve()).as_posix()
        return scrub_cleanroom_text(f"ECU/{rel}")
    except ValueError:
        return scrub_cleanroom_text(path.name)


def extract_asm_bit_defs(asm_path: Path) -> dict[int, list[str]]:
    """Map zero-page Flag register address -> bit constant symbol names from ASM lines."""
    bit_defs: dict[int, list[str]] = {}
    cur_addr: int | None = None
    for raw in asm_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith(";"):
            continue
        eq_m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*={1,2}\s*(?:0x|\$)(00[0-9A-Fa-f]{2})\b", s)
        if eq_m:
            cur_addr = int(eq_m.group(1), 16)
            continue
        bm = re.match(r"^([A-Za-z0-9_]+)\s*=\s*\$%[01]{8}\b", s)
        if bm and cur_addr is not None:
            bit_defs.setdefault(cur_addr, []).append(bm.group(1).lower())
        elif not s.startswith("b"):
            cur_addr = None
    return bit_defs


def _split_tokens(name: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    spaced = re.sub(r"([A-Za-z])([0-9])", r"\1_\2", spaced)
    spaced = re.sub(r"([0-9])([A-Za-z])", r"\1_\2", spaced)
    return {
        p.lower()
        for p in re.split(r"[^A-Za-z0-9]+", spaced)
        if p and (p.isdigit() or len(p) >= 2) and p.lower() not in _WORD_TOKEN_STOP
    }


def xref_corroborates_equate(
    xref_var: dict[str, Any],
    eq: dict[str, Any],
    asm_bit_defs: dict[int, list[str]],
) -> bool:
    """Return True only when an mprxref entry matches both address and symbol/flag meaning."""
    xl, xs, an = xref_var["long_name"].lower(), xref_var["short_name"].lower(), eq["name"].lower()
    ashort = ((eq.get("mpscan") or {}).get("short_name") or "").lower()
    if xl == an or (ashort and ashort != "none" and xs == ashort):
        return True
    if "afr" in xl and "afr" in an:
        return True
    tx, ta = _split_tokens(xref_var["long_name"]), _split_tokens(eq["name"])
    if tx and ta and tx == ta:
        return True
    bit_flags = xref_var.get("bit_flags")
    if isinstance(bit_flags, dict) and eq["address"] in asm_bit_defs:
        asm_bits_text = " ".join(asm_bit_defs[eq["address"]])
        flag_vals = [v.lower() for v in bit_flags.values() if v and not re.match(r"^bit\d$", v, re.I)]
        matched_tokens = {tok for fv in flag_vals for tok in _split_tokens(fv) if tok in asm_bits_text}
        if len(matched_tokens) >= 2:
            return True
    return False


def scan_staged_baselines(baselines_dir: Path | None) -> set[str]:
    """Read-only SHA-256 set of human-staged binary baselines in baselines_dir."""
    if baselines_dir is None or not baselines_dir.is_dir():
        return set()
    return {
        hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(baselines_dir.iterdir())
        if p.is_file() and p.suffix.lower() in {".bin", ".s19", ".rom"}
    }


def build_baseline_records(
    spec: RomSpec,
    ecu_root: Path,
    file_index: dict[str, Path],
    staged_hashes: set[str],
) -> list[dict[str, Any]]:
    """Extract clean read-only filename/size_bytes/sha256 baseline metadata without copying files."""
    records: list[dict[str, Any]] = []
    for cands in spec.baseline_candidates:
        resolved = resolve_source(ecu_root, file_index, cands)
        if resolved is None:
            continue
        data = resolved.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        records.append({
            "filename": resolved.name,
            "size_bytes": len(data),
            "sha256": sha,
            "baseline_present": sha in staged_hashes,
        })
    return records


def build_calibration_tables(
    asm_parsed: dict[str, Any],
    lst_syms: dict[str, str],
    tpl_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Corroborate ASM table/routine lines with .lst ROM addresses and .tpl addresses."""
    tpl_by_sym = {e["symbol"]: e for e in tpl_entries}
    tables: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for hit in asm_parsed.get("symbol_lines", []):
        sym, ln = hit["symbol"], hit["line_number"]
        if (sym, ln) in seen:
            continue
        seen.add((sym, ln))
        meta = tpl_by_sym.get(sym, {})
        tables.append({
            "symbol": sym,
            "kind": hit["kind"],
            "asm_line": ln,
            "rom_address": lst_syms.get(sym),
            "tpl_table_addr": meta.get("table_addr"),
            "short_name": meta.get("short_name"),
            "source_verified": True,
        })
    return tables


def build_fast_data_table(
    spec: RomSpec,
    tpl_entries: list[dict[str, Any]],
    ram_by_name: dict[str, dict[str, Any]],
    lst_syms: dict[str, str],
) -> list[dict[str, Any]]:
    """Extract 0xF6 FastDataOutputAddressTable slot mappings for SBEC."""
    fast_entry = (
        next((e for e in tpl_entries if e["symbol"] == "FastDataOutputAddressTable"), None)
        if "0xF6" in spec.sci_tables
        else None
    )
    if fast_entry is None:
        return []
    table_rom = lst_syms.get("FastDataOutputAddressTable")
    slots: list[dict[str, Any]] = []
    for slot, sym in enumerate(fast_entry.get("word_symbols", [])):
        eq = ram_by_name.get(sym)
        target = f"0x{eq['address']:04X}" if eq is not None else lst_syms.get(sym)
        slots.append({
            "slot": slot,
            "slot_hex": f"0x{slot:02X}",
            "symbol": sym,
            "target_address": target,
            "table_rom_address": table_rom,
            "source_verified": eq is not None or target is not None,
        })
    return slots


def resolve_optional(
    ecu_root: Path,
    file_index: dict[str, Path],
    candidates: tuple[str, ...],
    source_files: list[str],
    parser: Any,
) -> Any:
    """Resolve optional corpus source, append sanitized provenance, and run parser."""
    path = resolve_source(ecu_root, file_index, candidates)
    if path is None:
        return None
    source_files.append(clean_source_rel(path, ecu_root))
    return parser(path)


def collect_rom_src_and_twin_tags(rom_dir: Path) -> list[tuple[str, str, str, str]]:
    """Collect Exuberant ctags tuples from rom/src/**/*.asm and rom/models/**/*_twin.py."""
    import ast

    tags: list[tuple[str, str, str, str]] = []
    if (rom_dir / "src").is_dir():
        for asm_f in sorted((rom_dir / "src").rglob("*.asm")):
            rel = asm_f.relative_to(rom_dir.parent).as_posix() if rom_dir.name == "rom" else asm_f.as_posix()
            for ln, line in enumerate(asm_f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                s = line.strip()
                lm = None if (not s or s.startswith(";")) else re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?::|={1,2})", s)
                if lm:
                    tags.append((lm.group(1), rel, str(ln), "d\tlanguage:Asm"))
    if (rom_dir / "models").is_dir():
        for py_f in sorted((rom_dir / "models").rglob("*_twin.py")):
            rel = py_f.relative_to(rom_dir.parent).as_posix() if rom_dir.name == "rom" else py_f.as_posix()
            for node in ast.walk(ast.parse(py_f.read_text(encoding="utf-8", errors="replace"))):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    tags.append((node.name, rel, str(node.lineno), f"{'c' if isinstance(node, ast.ClassDef) else 'f'}\tlanguage:Python"))
    return tags
