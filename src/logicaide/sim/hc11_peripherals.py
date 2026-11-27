"""68HC11 I/O peripheral hooks ($1000-$103F), result records, and $FFC0-$FFFF vector validator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import importlib.util
from pathlib import Path
import sys
from typing import Any

import m68hc11

_REPO_ROOT = Path(__file__).resolve().parents[3]
if importlib.util.find_spec("rom") is None and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from logicaide.sim.assembler import AssembledRom

HC11_CLOCK_HZ = 2_000_000
MAX_MAIN_LOOP_MS = 11.0
MAX_MAIN_LOOP_CYCLES = 22_000
VECTOR_BASE = 0xFFC0
VECTOR_END = 0x10000
RESET_VECTOR_ADDR = 0xFFFE

_MANDATORY_VECTORS: dict[int, str] = {
    0xFFD6: "SCI",
    0xFFE8: "TOC1",
    0xFFFA: "COP",
    0xFFFC: "CMF",
    0xFFFE: "RESET",
}
_PREFIX_TABLES: dict[int, dict[int, Any]] = {
    0x18: m68hc11.PAGE2,
    0x1A: m68hc11.PAGE3,
    0xCD: m68hc11.PAGE4,
}


@dataclass(frozen=True)
class VectorCheckResult:
    """Result of validating all 32 68HC11 interrupt/reset vectors ($FFC0-$FFFF)."""

    ok: bool
    reset_vector: int
    checked_vectors: dict[str, int]
    invalid_vectors: list[dict[str, Any]]
    error_code: str | None

    def as_dict(self) -> dict[str, Any]:
        """Serialize vector check result to a JSON-ready dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class LoopTimingResult:
    """Cycle-count and wall-clock timing proof for a bounded 68HC11 subroutine call."""

    ok: bool
    cycles: int
    duration_ms: float
    max_cycles: int
    max_ms: float
    exceeded: bool
    error_code: str | None
    halt_reason: str
    final_pc: int

    def as_dict(self) -> dict[str, Any]:
        """Serialize loop timing result to a JSON-ready dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class TwinParityCase:
    """Per-corner comparison between Python Behavioral Twins and m68hc11 execution."""

    case_id: str
    rpm_hb: int
    map_raw: int
    baro_raw: int
    batt_raw: int
    knock_raw: int
    fuel_trim_u8: int
    twin_pw_u16: int
    hc11_pw_u16: int
    twin_spark_u8: int
    hc11_spark_u8: int
    twin_wg_u8: int
    hc11_wg_u8: int
    twin_cutout_u8: int
    hc11_cutout_u8: int
    twin_dwell_u16: int
    hc11_dwell_u16: int
    loop_cycles: int
    passed: bool
    max_lsb_diff: int

    def as_dict(self) -> dict[str, Any]:
        """Serialize twin parity case to a JSON-ready dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class TwinParitySummary:
    """Aggregate 0-LSB parity, loop timing, and vector validation summary."""

    ok: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    max_lsb_diff: int
    max_loop_cycles: int
    max_loop_ms: float
    loop_budget_ok: bool
    vectors_ok: bool
    vector_check: dict[str, Any]
    cases: list[dict[str, Any]]
    failures: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """Serialize twin parity summary to a JSON-ready dictionary."""
        return asdict(self)


def _install_io_hooks(cpu: m68hc11.HC11, periph: dict[str, Any]) -> None:
    """Install non-overlapping PORT, SCI, and ADC hooks prior to enable_timer(0x1000)."""
    ports, sci, adc = periph["ports"], periph["sci"], periph["adc"]

    def _read_sci(addr: int) -> int:
        if addr == 0x102B:
            return sci["baud"]
        if addr == 0x102C:
            return sci["sccr1"]
        if addr == 0x102D:
            return sci["sccr2"]
        if addr == 0x102E:
            return 0xC0 | (0x20 if sci["rx_queue"] else 0x00)
        if sci["rx_queue"]:
            rx_byte = sci["rx_queue"].pop(0)
            sci["rx_bytes_read"].append(rx_byte)
            return rx_byte
        return 0

    def _write_sci(addr: int, val: int) -> None:
        v = val & 0xFF
        if addr == 0x102B:
            sci["baud"] = v
        elif addr == 0x102C:
            sci["sccr1"] = v
        elif addr == 0x102D:
            sci["sccr2"] = v
        elif addr == 0x102F:
            sci["tx_bytes"].append(v)

    def _read_adc(addr: int) -> int:
        return (adc["adctl"] | 0x80) if addr == 0x1030 else adc["channels"][addr - 0x1031]

    def _write_adc(addr: int, val: int) -> None:
        if addr == 0x1030:
            adc["adctl"] = (val & 0x7F) | 0x80

    # Register $1000..$100A, $102B..$102F, and $1030..$1034 BEFORE enable_timer(0x1000)
    # because enable_timer claims the entire $1000..$103F window and first match wins.
    cpu.on_read(0x1000, 0x100A, lambda addr: ports.get(addr, 0))
    cpu.on_write(0x1000, 0x100A, lambda addr, val: ports.__setitem__(addr, val & 0xFF))
    cpu.on_read(0x102B, 0x102F, _read_sci)
    cpu.on_write(0x102B, 0x102F, _write_sci)
    cpu.on_read(0x1030, 0x1034, _read_adc)
    cpu.on_write(0x1030, 0x1034, _write_adc)
    cpu.enable_timer(0x1000)


def create_hc11_cpu(
    rom_bytes: bytes,
    *,
    base_address: int = 0x8000,
    ram_init: Mapping[int, int] | None = None,
    adc_channels: tuple[int, int, int, int] | None = None,
    sci_rx_bytes: list[int] | None = None,
) -> tuple[m68hc11.HC11, dict[str, Any]]:
    """Instantiate an HC11 CPU loaded with ROM/RAM and non-overlapping peripheral hooks."""
    channels = [int(v) & 0xFF for v in (adc_channels if adc_channels is not None else (0, 0, 0, 0))]
    if len(channels) != 4:
        raise ValueError(f"adc_channels must contain exactly 4 channels, got {len(channels)}")

    cpu = m68hc11.HC11(ram_fill=0x00)
    cpu.load(bytes(rom_bytes), base_address)
    for addr, val in (ram_init or {}).items():
        cpu.mem[int(addr) & 0xFFFF] = int(val) & 0xFF

    periph: dict[str, Any] = {
        "ports": {addr: 0 for addr in range(0x1000, 0x100B)},
        "sci": {
            "baud": 0,
            "sccr1": 0,
            "sccr2": 0,
            "rx_queue": [int(b) & 0xFF for b in (sci_rx_bytes or [])],
            "rx_bytes_read": [],
            "tx_bytes": [],
        },
        "adc": {"adctl": 0x80, "channels": channels},
    }
    _install_io_hooks(cpu, periph)
    return cpu, periph


def _check_vector_target_opcode(
    rom: bytes, base_address: int, target: int, table_ranges: tuple[tuple[int, int], ...]
) -> str | None:
    """Return None if ``target`` points to valid non-table 68HC11 code, else reason."""
    if not (max(0x8000, base_address) <= target <= 0xFFBF):
        return "target_out_of_code_range"
    if any(r_start <= target < r_end for r_start, r_end in table_ranges):
        return "target_in_table_range"
    off = target - base_address
    if not (0 <= off < len(rom)):
        return "target_outside_rom"
    op1 = rom[off]
    if op1 == 0xFF:
        return "erased_fill_0xFF"
    if op1 in _PREFIX_TABLES:
        if off + 1 >= len(rom) or target + 1 > 0xFFBF:
            return "truncated_prefixed_opcode"
        op2 = rom[off + 1]
        return None if op2 in _PREFIX_TABLES[op1] else f"illegal_prefixed_opcode_0x{op1:02X}{op2:02X}"
    return None if op1 in m68hc11.PAGE1 else f"illegal_opcode_0x{op1:02X}"


def validate_rom_vectors(
    assembled: AssembledRom, *, rom_bytes_override: bytes | None = None
) -> VectorCheckResult:
    """Validate all 32 vectors at $FFC0-$FFFF, mandatory vectors, and RESET target."""
    rom = bytes(rom_bytes_override) if rom_bytes_override is not None else assembled.rom_bytes
    base = assembled.base_address
    if base > VECTOR_BASE or base + len(rom) < VECTOR_END:
        return VectorCheckResult(
            ok=False,
            reset_vector=0,
            checked_vectors={},
            invalid_vectors=[{"vector": "0xFFFE", "reason": "rom_excludes_vectors", "mandatory": True}],
            error_code="INVALID_VECTOR",
        )

    checked: dict[str, int] = {}
    invalid: list[dict[str, Any]] = []
    reset_vector = 0
    for vec_addr in range(VECTOR_BASE, VECTOR_END, 2):
        off = vec_addr - base
        target = (rom[off] << 8) | rom[off + 1]
        vec_key = f"0x{vec_addr:04X}"
        checked[vec_key] = target
        if vec_addr == RESET_VECTOR_ADDR:
            reset_vector = target
        mandatory = vec_addr in _MANDATORY_VECTORS
        if target == 0xFFFF and not mandatory:
            continue
        reason = _check_vector_target_opcode(rom, base, target, assembled.table_ranges)
        if reason is not None:
            invalid.append({
                "vector": vec_key,
                "name": _MANDATORY_VECTORS.get(vec_addr, "VECTOR"),
                "target": f"0x{target:04X}",
                "target_addr": target,
                "mandatory": mandatory,
                "reason": reason,
            })

    ok = len(invalid) == 0
    return VectorCheckResult(
        ok=ok,
        reset_vector=reset_vector,
        checked_vectors=checked,
        invalid_vectors=invalid,
        error_code=None if ok else "INVALID_VECTOR",
    )
