"""68HC11 ROM binary splayer, SHA-256 roundtrip verifier, and calibration extractor."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if importlib.util.find_spec("rom") is None and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from logicaide.profiles.compiler import compile_profile
from logicaide.sim.assembler import AssembledRom, assemble_tree
from rom.models import (
    BoostCalibration,
    FuelCalibration,
    SparkCalibration,
    boost_calibration_from_profile,
    default_fuel_calibration,
    encode_4byte_table,
    encode_5byte_table,
    fuel_calibration_from_profile,
    kpa_to_raw_map,
    lookup_4byte_table,
    lookup_5byte_table,
    raw_map_to_kpa,
    spark_calibration_from_profile,
)
from rom.models.boost_twin import REV_LIMIT_RPM_PER_BIT, validate_overboost_cut_kpa, validate_rev_limit_rpm

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_MODULE_FILES: tuple[str, ...] = (
    "main.asm", "fuel_tables.asm", "spark_tables.asm", "boost_vnt.asm", "sci_diag.asm", "vectors.asm"
)
_REGION_SPECS: tuple[tuple[str, int, int, str], ...] = (
    ("main.asm", 0x8000, 0x8200, "code"), ("fuel_tables.asm", 0x8200, 0x8600, "table"),
    ("fuel_tables.asm", 0x8600, 0x9000, "code"), ("spark_tables.asm", 0x9000, 0x9400, "table"),
    ("spark_tables.asm", 0x9400, 0xA000, "code"), ("boost_vnt.asm", 0xA000, 0xA300, "table"),
    ("boost_vnt.asm", 0xA300, 0xB000, "code"), ("sci_diag.asm", 0xB000, 0xFFC0, "code"),
)


@dataclass(frozen=True)
class RoundtripVerificationResult:
    """Result of splay-and-reassemble byte-for-byte SHA-256 verification."""

    ok: bool
    error_code: str | None
    expected_sha256: str
    actual_sha256: str
    rom_size_bytes: int
    splay_dir: str
    entry_asm: str
    mismatch_count: int
    first_mismatch_offset: int | None
    first_mismatch_address: int | None
    source_files: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialize verification result to a JSON-ready dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class SplayedTree:
    """Metadata describing a splayed 68HC11 assembly directory tree."""

    output_dir: str
    entry_asm: str
    files: tuple[str, ...]
    source_sha256: str
    code_ranges: tuple[tuple[int, int], ...]
    table_ranges: tuple[tuple[int, int], ...]
    vector_range: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        """Serialize splayed tree metadata to a JSON-ready dictionary."""
        return asdict(self)


def ensure_writable_sim_path(path: Path | str) -> Path:
    """Reject writes into immutable ROM input/baseline zones or tracked rom/src."""
    resolved = Path(path).resolve()
    for zone in ("inputs", "stock_baselines"):
        protected = (_REPO_ROOT / "rom" / zone).resolve()
        if resolved == protected or protected in resolved.parents:
            raise ValueError(f"SECURITY.md: Refusing write inside immutable rom/{zone}: {resolved}")
        parts = resolved.parts
        if any(parts[idx : idx + 2] == ("rom", zone) for idx in range(len(parts) - 1)):
            raise ValueError(f"SECURITY.md: Refusing write inside immutable rom/{zone} path: {resolved}")
    tracked_src = (_REPO_ROOT / "rom" / "src").resolve()
    if resolved == tracked_src or tracked_src in resolved.parents:
        raise ValueError(f"SECURITY.md: Refusing write inside tracked rom/src: {resolved}")
    return resolved


def _find_non_ff_spans(
    rom: bytes, base_address: int, start_addr: int, end_addr: int, min_ff_run: int = 8
) -> list[tuple[int, bytes]]:
    """Return contiguous (start_addr, slice) spans omitting >= min_ff_run 0xFF gaps."""
    lo, hi = max(start_addr, base_address), min(end_addr, base_address + len(rom))
    if lo >= hi:
        return []
    non_ff = [idx for idx in range(lo - base_address, hi - base_address) if rom[idx] != 0xFF]
    if not non_ff:
        return []
    spans: list[tuple[int, bytes]] = []
    span_start = last = non_ff[0]
    for pos in non_ff[1:]:
        if pos - last - 1 >= min_ff_run:
            spans.append((base_address + span_start, rom[span_start : last + 1]))
            span_start = pos
        last = pos
    spans.append((base_address + span_start, rom[span_start : last + 1]))
    return spans


def _emit_span_lines(
    span_addr: int, payload: bytes, syms_by_addr: dict[int, list[str]], emitted_syms: set[str]
) -> list[str]:
    """Format a ROM byte span with .org, symbol labels, and <=16-byte .byte rows."""
    lines, idx, total = [f"        .org    0x{span_addr:04X}"], 0, len(payload)
    while idx < total:
        for sym in syms_by_addr.get(span_addr + idx, ()):
            if sym not in emitted_syms:
                lines.append(f"{sym}:")
                emitted_syms.add(sym)
        nxt = idx + 1
        while nxt < total and nxt - idx < 16:
            if any(s not in emitted_syms for s in syms_by_addr.get(span_addr + nxt, ())):
                break
            nxt += 1
        lines.append("        .byte   " + ", ".join(f"0x{b:02X}" for b in payload[idx:nxt]))
        idx = nxt
    return lines


def _render_vectors_asm(
    rom: bytes, base_address: int, syms_by_addr: dict[int, list[str]],
    emitted_syms: set[str], valid_syms: dict[str, int],
) -> tuple[str, tuple[int, int]]:
    """Render $FFC0-$FFFF interrupt/reset vector table as .word entries."""
    vec_lo, vec_hi = max(0xFFC0, base_address), min(0x10000, base_address + len(rom))
    if vec_lo >= vec_hi:
        return "; No vector region in image\n", (0xFFC0, 0xFFC0)
    addr_to_sym: dict[int, str] = {}
    for sym, addr in sorted(valid_syms.items()):
        if addr >= base_address and addr not in addr_to_sym:
            addr_to_sym[addr] = sym
    lines = ["; Splayed 68HC11 Interrupt & Reset Vectors ($FFC0-$FFFF)", "", f"        .org    0x{vec_lo:04X}"]
    for addr in range(vec_lo, vec_hi - 1, 2):
        for sym in syms_by_addr.get(addr, ()):
            if sym not in emitted_syms:
                lines.append(f"{sym}:")
                emitted_syms.add(sym)
        off = addr - base_address
        word = (rom[off] << 8) | rom[off + 1]
        target = addr_to_sym.get(word) if word != 0xFFFF else None
        lines.append(f"        .word   {(target or f'0x{word:04X}'):<16} ; ${addr:04X}")
    return "\n".join(lines) + "\n", (vec_lo, vec_hi)


def splay_rom_binary(
    rom_bytes: bytes, output_dir: Path | str, *, base_address: int = 0x8000, symbols: dict[str, int] | None = None
) -> SplayedTree:
    """Split a 68HC11 ROM binary into cleanly assemblable subsystem .asm modules."""
    raw = bytes(rom_bytes)
    if not raw or base_address < 0 or base_address + len(raw) > 0x10000:
        raise ValueError(f"Invalid ROM bounds: base=0x{base_address:X}, size={len(raw)}")
    out_dir = ensure_writable_sim_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    valid_syms = {k: int(v) for k, v in (symbols or {}).items() if _IDENT_RE.match(k)}
    syms_by_addr: dict[int, list[str]] = {}
    for sym, addr in sorted(valid_syms.items()):
        syms_by_addr.setdefault(addr, []).append(sym)
    emitted_syms: set[str] = set()

    buckets: dict[str, list[str]] = {name: [] for name in _MODULE_FILES}
    code_ranges: list[tuple[int, int]] = []
    table_ranges: list[tuple[int, int]] = []
    for mod_name, r_lo, r_hi, kind in _REGION_SPECS:
        for span_addr, payload in _find_non_ff_spans(raw, base_address, r_lo, r_hi):
            buckets[mod_name].extend(_emit_span_lines(span_addr, payload, syms_by_addr, emitted_syms))
            (code_ranges if kind == "code" else table_ranges).append((span_addr, span_addr + len(payload)))

    vec_text, vec_range = _render_vectors_asm(raw, base_address, syms_by_addr, emitted_syms, valid_syms)
    (out_dir / "vectors.asm").write_text(vec_text, encoding="utf-8", newline="\n")
    for mod_name in ("fuel_tables.asm", "spark_tables.asm", "boost_vnt.asm", "sci_diag.asm"):
        body = "\n".join(buckets[mod_name]) if buckets[mod_name] else f"; No non-0xFF spans in {mod_name}"
        (out_dir / mod_name).write_text(f"; Splayed {mod_name}\n\n{body}\n", encoding="utf-8", newline="\n")

    equ_lines = [f"{sym:<18} .equ    0x{addr:04X}" for sym, addr in sorted(valid_syms.items()) if sym not in emitted_syms]
    main_parts = ["; Splayed 68HC11 Kernel Entry Module", ""]
    if equ_lines:
        main_parts.extend([*equ_lines, ""])
    main_parts.extend(buckets["main.asm"] or ["; No non-0xFF spans in main.asm"])
    main_parts.extend(["", *(f'        .include "{inc}"' for inc in _MODULE_FILES[1:])])
    entry_path = out_dir / "main.asm"
    entry_path.write_text("\n".join(main_parts) + "\n", encoding="utf-8", newline="\n")

    return SplayedTree(
        output_dir=str(out_dir), entry_asm=str(entry_path), files=_MODULE_FILES,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        code_ranges=tuple(sorted(code_ranges)), table_ranges=tuple(sorted(table_ranges)), vector_range=vec_range,
    )


def verify_roundtrip_sha256(
    source_rom: Path | str | bytes | None = None,
    splay_dir: Path | str | None = None,
    *,
    base_address: int = 0x8000,
    rom_path: Path | str | None = None,
    asm_path: Path | str | None = None,
) -> RoundtripVerificationResult:
    """Verify byte-for-byte SHA-256 equivalence between source ROM and assembled splay tree."""
    src = source_rom if source_rom is not None else rom_path
    if src is not None:
        expected_bytes = bytes(src) if isinstance(src, (bytes, bytearray, memoryview)) else Path(src).read_bytes()
    elif asm_path is not None:
        expected_bytes = assemble_tree(asm_path, base_address=base_address).rom_bytes
    else:
        raise ValueError("verify_roundtrip_sha256 requires source_rom, rom_path, or asm_path")
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()

    def _run_verify(target_dir: Path, *, already_splayed: bool) -> RoundtripVerificationResult:
        if not already_splayed:
            splay_rom_binary(expected_bytes, target_dir, base_address=base_address)
        entry_asm = target_dir / "main.asm"
        assembled = assemble_tree(entry_asm, base_address=base_address, rom_size_bytes=len(expected_bytes))
        actual = assembled.rom_bytes
        mismatches = [idx for idx, (a, b) in enumerate(zip(expected_bytes, actual)) if a != b]
        extra = abs(len(expected_bytes) - len(actual))
        first_off = mismatches[0] if mismatches else (min(len(expected_bytes), len(actual)) if extra else None)
        mismatch_count = len(mismatches) + extra
        ok = (expected_sha == assembled.sha256) and mismatch_count == 0
        return RoundtripVerificationResult(
            ok=ok, error_code=None if ok else "ROUNDTRIP_SHA256_MISMATCH",
            expected_sha256=expected_sha, actual_sha256=assembled.sha256, rom_size_bytes=len(expected_bytes),
            splay_dir=str(target_dir), entry_asm=str(entry_asm), mismatch_count=mismatch_count,
            first_mismatch_offset=first_off,
            first_mismatch_address=(base_address + first_off) if first_off is not None else None,
            source_files=assembled.source_files,
        )

    if splay_dir is None:
        with tempfile.TemporaryDirectory(prefix="logicaide_splay_") as tmp_dir:
            return _run_verify(Path(tmp_dir), already_splayed=False)

    target = Path(splay_dir).resolve()
    if (target / "main.asm").is_file():
        return _run_verify(target, already_splayed=True)
    return _run_verify(ensure_writable_sim_path(target), already_splayed=False)


def _symbol_slot_bounds(assembled: AssembledRom, name: str) -> tuple[int, int]:
    """Return (offset, slot_len_or_0) for a symbol inside assembled ROM."""
    if name not in assembled.symbols:
        raise ValueError(f"Missing required ROM symbol: {name}")
    addr = assembled.symbols[name]
    off = addr - assembled.base_address
    if not (0 <= off < len(assembled.rom_bytes)):
        raise ValueError(f"Symbol {name} at 0x{addr:04X} outside ROM image")
    for r_start, r_end in assembled.table_ranges:
        if r_start <= addr < r_end:
            next_addrs = [v for v in assembled.symbols.values() if addr < v <= r_end]
            return off, (min(next_addrs) if next_addrs else r_end) - addr
    return off, 0


def _read_4byte_table(assembled: AssembledRom, name: str) -> bytes:
    """Slice a [N, x, y, s_hi, s_lo, ...] 4-byte table from ROM."""
    off, _ = _symbol_slot_bounds(assembled, name)
    rom = assembled.rom_bytes
    count = rom[off]
    total = 1 + 4 * count
    if count < 1 or off + total > len(rom):
        raise ValueError(f"Invalid 4-byte table {name}: N={count} at offset 0x{off:X}")
    return rom[off : off + total]


def _read_5byte_table(assembled: AssembledRom, name: str) -> bytes:
    """Slice a 0xFF-sentinel-terminated 5-byte table from ROM."""
    off, slot_len = _symbol_slot_bounds(assembled, name)
    rom = assembled.rom_bytes
    if slot_len >= 6 and slot_len % 5 == 1 and rom[off + slot_len - 1] == 0xFF:
        return rom[off : off + slot_len]
    pos = off
    while pos + 5 < len(rom):
        if rom[pos] == 0xFF:
            if pos + 5 < len(rom) and rom[pos + 5] == 0xFF and rom[pos + 3 : pos + 5] == b"\x00\x00":
                return rom[off : pos + 6]
            return rom[off : pos + 1]
        pos += 5
    raise ValueError(f"Unterminated 5-byte table {name} at offset 0x{off:X}")


def _read_uint(assembled: AssembledRom, name: str, width: int = 1) -> int:
    off, _ = _symbol_slot_bounds(assembled, name)
    return int.from_bytes(assembled.rom_bytes[off : off + width], "big")


def extract_calibrations_from_rom(
    assembled: AssembledRom,
    *,
    map_sensor_bar: int = 2,
    rev_limit_rpm: int | None = None,
    overboost_cut_kpa: float | None = None,
) -> tuple[FuelCalibration, SparkCalibration, BoostCalibration]:
    """Extract FuelCalibration, SparkCalibration, and BoostCalibration directly from ROM bytes."""
    base_fuel = default_fuel_calibration(map_sensor_bar)
    fuel_cal = FuelCalibration(
        batoff_table=_read_4byte_table(assembled, "BATOFF_TBL"),
        peftbl_table=_read_4byte_table(assembled, "PEFTBL"),
        wottbl_table=_read_5byte_table(assembled, "WOTTBL"),
        map_mult=base_fuel.map_mult, map_add=base_fuel.map_add, map_sensor_bar=map_sensor_bar,
        min_pw_ticks=_read_uint(assembled, "MIN_PW_FLOOR_U16", 2),
    )
    spark_cal = SparkCalibration(
        base_spk_table=_read_4byte_table(assembled, "BASE_SPK_TBL"),
        map_retard_table=_read_4byte_table(assembled, "MAP_RETARD_TBL"),
        dltknk_table=_read_4byte_table(assembled, "DLTKNK_TBL"),
        retmax_table=_read_4byte_table(assembled, "RETMAX_TBL"),
        spklmt_table=_read_4byte_table(assembled, "SPKLMT_TBL"),
        dwell_table=_read_5byte_table(assembled, "DWELL_TBL"),
        knklim_max_u8=_read_uint(assembled, "KNKLIM_MAX_U8"),
        min_adwell_ticks=_read_uint(assembled, "MIN_ADWELL_U16", 2),
        max_safe_advance_u8=_read_uint(assembled, "MAX_SAFE_ADV_U8"),
    )
    rev_u8, ob_u8 = _read_uint(assembled, "REV_LIMIT_U8"), _read_uint(assembled, "OVERBOOST_CUT_U8")
    eff_rev = rev_u8 * REV_LIMIT_RPM_PER_BIT if rev_limit_rpm is None else int(rev_limit_rpm)
    eff_ob = raw_map_to_kpa(ob_u8, map_sensor_bar) if overboost_cut_kpa is None else float(overboost_cut_kpa)
    boost_cal = BoostCalibration(
        wg_duty_table=_read_4byte_table(assembled, "WG_DUTY_TBL"),
        map_sensor_bar=map_sensor_bar, rev_limit_rpm=eff_rev, rev_limit_u8=rev_u8,
        overboost_cut_kpa=eff_ob, overboost_cut_u8=ob_u8,
        knock_wg_reduce_u8=_read_uint(assembled, "KNK_WG_GAIN_U8"),
        max_wg_duty_u8=_read_uint(assembled, "WG_MAX_DUTY_U8"),
    )
    return fuel_cal, spark_cal, boost_cal


def _fit_4byte_to_slot(table_bytes: bytes, target_len: int) -> bytes:
    """Preserve exact bytes or resample a 4-byte table into a fixed ROM slot length."""
    if len(table_bytes) == target_len:
        return table_bytes
    n_pts, src_n = (target_len - 1) // 4, table_bytes[0]
    lo, hi = table_bytes[1], table_bytes[1 + 4 * (src_n - 1)]
    if n_pts < 2 or hi - lo < n_pts - 1:
        raise ValueError(f"Cannot fit {src_n}-point 4-byte table into {target_len}-byte slot")
    sample_xs = [lo + round((hi - lo) * i / (n_pts - 1)) for i in range(n_pts)]
    return encode_4byte_table([(x, lookup_4byte_table(x, table_bytes)) for x in sample_xs])


def _fit_5byte_to_slot(table_bytes: bytes, target_len: int) -> bytes:
    """Preserve exact bytes or resample a 5-byte table into a fixed ROM slot length."""
    if len(table_bytes) == target_len:
        return table_bytes
    n_pts = (target_len - 1) // 5
    anchors = (0x00, 0x69, 0x89, 0xAA, 0xFF) if n_pts == 5 and len(table_bytes) > target_len else tuple(
        round(255 * i / (n_pts - 1)) for i in range(n_pts)
    )
    return encode_5byte_table([(x, lookup_5byte_table(x, table_bytes)) for x in anchors])


def _load_profile_mapping(profile_or_path: Mapping[str, Any] | Path | str) -> Mapping[str, Any]:
    """Load and compile-validate a profile path, or extract raw profile mapping."""
    if isinstance(profile_or_path, (Path, str)):
        path = Path(profile_or_path)
        compile_profile(path, repo_root=_REPO_ROOT, persist=False, require_rom_manifest=False)
        return json.loads(path.read_text(encoding="utf-8"))
    if "profile" in profile_or_path and "ecu_and_sensors" not in profile_or_path and isinstance(profile_or_path["profile"], Mapping):
        return profile_or_path["profile"]
    return profile_or_path


def _write_slot(rom_buf: bytearray, baseline: AssembledRom, sym: str, payload: bytes) -> None:
    """Write ``payload`` into ``sym`` slot after asserting exact slot size."""
    off, slot_len = _symbol_slot_bounds(baseline, sym)
    if slot_len and len(payload) != slot_len:
        raise ValueError(f"Override for {sym} has length {len(payload)}, expected exact slot length {slot_len}")
    if off + len(payload) > len(rom_buf):
        raise ValueError(f"Override for {sym} exceeds ROM image size")
    rom_buf[off : off + len(payload)] = payload


def build_candidate_rom_bytes(
    profile_or_path: Mapping[str, Any] | Path | str | None = None,
    *,
    cleanroom_src_dir: Path | str | None = None,
    rev_limit_rpm: int | None = None,
    overboost_cut_kpa: float | None = None,
    table_overrides: Mapping[str, bytes] | None = None,
    patch_bytes: Mapping[int, bytes] | None = None,
) -> tuple[AssembledRom, AssembledRom]:
    """Assemble baseline ROM and construct an in-memory patched candidate AssembledRom."""
    src_dir = Path(cleanroom_src_dir) if cleanroom_src_dir is not None else (_REPO_ROOT / "rom" / "src")
    baseline = assemble_tree(src_dir / "main.asm")
    rom_buf, map_bar = bytearray(baseline.rom_bytes), 2

    if profile_or_path is not None:
        profile = _load_profile_mapping(profile_or_path)
        fuel_cal, spark_cal, boost_cal = (
            fuel_calibration_from_profile(profile), spark_calibration_from_profile(profile), boost_calibration_from_profile(profile)
        )
        map_bar = boost_cal.map_sensor_bar
        slots = {
            "BATOFF_TBL": _fit_4byte_to_slot(fuel_cal.batoff_table, _symbol_slot_bounds(baseline, "BATOFF_TBL")[1]),
            "PEFTBL": fuel_cal.peftbl_table,
            "WOTTBL": _fit_5byte_to_slot(fuel_cal.wottbl_table, _symbol_slot_bounds(baseline, "WOTTBL")[1]),
            "MIN_PW_FLOOR_U16": fuel_cal.min_pw_ticks.to_bytes(2, "big"),
            "BASE_SPK_TBL": spark_cal.base_spk_table, "MAP_RETARD_TBL": spark_cal.map_retard_table,
            "DLTKNK_TBL": spark_cal.dltknk_table, "RETMAX_TBL": spark_cal.retmax_table,
            "SPKLMT_TBL": spark_cal.spklmt_table, "DWELL_TBL": spark_cal.dwell_table,
            "KNKLIM_MAX_U8": bytes((spark_cal.knklim_max_u8,)), "MAX_SAFE_ADV_U8": bytes((spark_cal.max_safe_advance_u8,)),
            "MIN_ADWELL_U16": spark_cal.min_adwell_ticks.to_bytes(2, "big"), "WG_DUTY_TBL": boost_cal.wg_duty_table,
            "REV_LIMIT_U8": bytes((boost_cal.rev_limit_u8,)), "OVERBOOST_CUT_U8": bytes((boost_cal.overboost_cut_u8,)),
            "KNK_WG_GAIN_U8": bytes((boost_cal.knock_wg_reduce_u8,)), "WG_MAX_DUTY_U8": bytes((boost_cal.max_wg_duty_u8,)),
        }
        for sym, data in slots.items():
            _write_slot(rom_buf, baseline, sym, data)

    if rev_limit_rpm is not None:
        _write_slot(rom_buf, baseline, "REV_LIMIT_U8", bytes((validate_rev_limit_rpm(rev_limit_rpm),)))
    if overboost_cut_kpa is not None:
        _write_slot(rom_buf, baseline, "OVERBOOST_CUT_U8", bytes((kpa_to_raw_map(validate_overboost_cut_kpa(overboost_cut_kpa, map_bar), map_bar),)))
    for sym, data in (table_overrides or {}).items():
        _write_slot(rom_buf, baseline, sym, bytes(data))
    for addr, data in (patch_bytes or {}).items():
        off = int(addr) - baseline.base_address
        if off < 0 or off + len(data) > baseline.rom_size_bytes:
            raise ValueError(f"patch_bytes at 0x{addr:04X}+{len(data)} out of ROM bounds")
        rom_buf[off : off + len(data)] = data

    rom_bytes = bytes(rom_buf)
    return baseline, replace(baseline, rom_bytes=rom_bytes, sha256=hashlib.sha256(rom_bytes).hexdigest())
