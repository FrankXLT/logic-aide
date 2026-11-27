"""Dead-Cell Timeout Quarantine state machine for 62,500 baud High-Speed SCI polling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_PROBE_TIMEOUT_MS: float = 12.0
STARTUP_MISS_THRESHOLD: int = 2
RUNTIME_TIMEOUT_THRESHOLD: int = 3
REASON_STARTUP_TIMEOUT: str = "STARTUP_PROBE_TIMEOUT"
REASON_RUNTIME_TIMEOUT: str = "RUNTIME_TIMEOUT"

STATE_PROBING: str = "PROBING"
STATE_ACTIVE: str = "ACTIVE"
STATE_QUARANTINED: str = "QUARANTINED"
VALID_CELL_STATES: frozenset[str] = frozenset({STATE_PROBING, STATE_ACTIVE, STATE_QUARANTINED})


def _validate_byte(value: int, name: str) -> int:
    """Validate an 8-bit unsigned SCI byte value."""
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 0xFF):
        raise ValueError(f"{name} must be an integer in 0..255, got {value!r}")
    return value


@dataclass(slots=True)
class CellHealth:
    """Per-(FX, AA) SCI RAM cell health and quarantine state."""

    fx: int
    offset: int
    symbol: str
    channel: str
    state: str = STATE_PROBING
    startup_misses: int = 0
    runtime_timeouts: int = 0
    quarantine_reason: str | None = None
    quarantined_at_ns: int | None = None
    reprobe_count: int = 0

    def __post_init__(self) -> None:
        _validate_byte(self.fx, "fx")
        _validate_byte(self.offset, "offset")
        if self.state not in VALID_CELL_STATES:
            raise ValueError(f"Invalid CellHealth state {self.state!r}; expected one of {sorted(VALID_CELL_STATES)}")

    @property
    def fx_hex(self) -> str:
        """Uppercase 2-digit hex representation of the SCI table selector byte."""
        return f"0x{self.fx:02X}"

    @property
    def offset_hex(self) -> str:
        """Uppercase 2-digit hex representation of the SCI offset byte."""
        return f"0x{self.offset:02X}"

    @property
    def label(self) -> str:
        """Canonical ``0xFX:0xAA`` cell identifier."""
        return f"0x{self.fx:02X}:0x{self.offset:02X}"

    @property
    def key(self) -> tuple[int, int]:
        """Tuple key ``(fx, offset)`` identifying this cell."""
        return (self.fx, self.offset)


class DeadCellQuarantineManager:
    """Deterministic state machine managing SCI startup probes and runtime timeouts."""

    def __init__(
        self,
        cells: Iterable[CellHealth] = (),
        *,
        startup_miss_threshold: int = STARTUP_MISS_THRESHOLD,
        runtime_timeout_threshold: int = RUNTIME_TIMEOUT_THRESHOLD,
        probe_timeout_ms: float = DEFAULT_PROBE_TIMEOUT_MS,
    ) -> None:
        if startup_miss_threshold < 1:
            raise ValueError(f"startup_miss_threshold must be >= 1, got {startup_miss_threshold}")
        if runtime_timeout_threshold < 1:
            raise ValueError(f"runtime_timeout_threshold must be >= 1, got {runtime_timeout_threshold}")
        if probe_timeout_ms <= 0.0:
            raise ValueError(f"probe_timeout_ms must be > 0, got {probe_timeout_ms}")
        self.startup_miss_threshold = int(startup_miss_threshold)
        self.runtime_timeout_threshold = int(runtime_timeout_threshold)
        self.probe_timeout_ms = float(probe_timeout_ms)
        self._cells: dict[tuple[int, int], CellHealth] = {}
        for cell in cells:
            self.register_cell(cell)

    @classmethod
    def from_watchlist(
        cls,
        channels: Iterable[Any],
        *,
        startup_miss_threshold: int = STARTUP_MISS_THRESHOLD,
        runtime_timeout_threshold: int = RUNTIME_TIMEOUT_THRESHOLD,
        probe_timeout_ms: float = DEFAULT_PROBE_TIMEOUT_MS,
    ) -> DeadCellQuarantineManager:
        """Construct a manager populated from ``WatchedChannel``-like descriptors."""
        cells: list[CellHealth] = []
        seen: set[tuple[int, int]] = set()
        for ch in channels:
            fx = int(ch.fx_byte)
            hi = int(ch.high_offset)
            sym = str(ch.symbol or ch.channel_name)
            ch_name = str(ch.channel_name)
            if (fx, hi) not in seen:
                cells.append(CellHealth(fx=fx, offset=hi, symbol=sym, channel=ch_name))
                seen.add((fx, hi))
            lo = getattr(ch, "low_offset", None)
            if lo is not None and (fx, int(lo)) not in seen:
                lo_int = int(lo)
                lo_sym = f"{sym[:-3]}_LB" if sym.endswith("_HB") else f"{sym}_LB"
                cells.append(CellHealth(fx=fx, offset=lo_int, symbol=lo_sym, channel=ch_name))
                seen.add((fx, lo_int))
        return cls(
            cells,
            startup_miss_threshold=startup_miss_threshold,
            runtime_timeout_threshold=runtime_timeout_threshold,
            probe_timeout_ms=probe_timeout_ms,
        )

    def register_cell(self, cell: CellHealth) -> CellHealth:
        """Register or overwrite a tracked ``CellHealth`` entry."""
        self._cells[cell.key] = cell
        return cell

    def get_cell(self, cell_key: tuple[int, int]) -> CellHealth | None:
        """Return tracked ``CellHealth`` for ``cell_key``, or ``None`` if untracked."""
        return self._cells.get(cell_key)

    def all_cells(self) -> list[CellHealth]:
        """Return all tracked ``CellHealth`` records in registration order."""
        return list(self._cells.values())

    def _get_or_create_cell(self, cell_key: tuple[int, int], *, default_state: str) -> CellHealth:
        existing = self._cells.get(cell_key)
        if existing is not None:
            return existing
        fx, offset = _validate_byte(cell_key[0], "fx"), _validate_byte(cell_key[1], "offset")
        cell = CellHealth(fx=fx, offset=offset, symbol=f"0x{fx:02X}:0x{offset:02X}", channel="", state=default_state)
        self._cells[(fx, offset)] = cell
        return cell

    def record_startup_probe(self, cell_key: tuple[int, int], *, replied: bool, ts_ns: int) -> str:
        """Record one isolated startup probe outcome and return the resulting cell state."""
        cell = self._get_or_create_cell(cell_key, default_state=STATE_PROBING)
        # Never auto-rejoin a quarantined cell without an explicit manual begin_reprobe().
        if cell.state == STATE_QUARANTINED:
            return STATE_QUARANTINED
        if replied:
            cell.state = STATE_ACTIVE
            cell.startup_misses = 0
            cell.runtime_timeouts = 0
            return STATE_ACTIVE
        cell.startup_misses += 1
        if cell.startup_misses >= self.startup_miss_threshold:
            cell.state = STATE_QUARANTINED
            cell.quarantine_reason = REASON_STARTUP_TIMEOUT
            cell.quarantined_at_ns = int(ts_ns)
            return STATE_QUARANTINED
        return cell.state

    def record_runtime_poll(self, cell_key: tuple[int, int], *, replied: bool, ts_ns: int) -> tuple[bool, bool]:
        """Record one runtime poll result, returning ``(newly_quarantined, needs_rx_flush)``."""
        cell = self._get_or_create_cell(cell_key, default_state=STATE_ACTIVE)
        if cell.state == STATE_QUARANTINED:
            return (False, False)
        if replied:
            cell.runtime_timeouts = 0
            return (False, False)
        cell.runtime_timeouts += 1
        if cell.runtime_timeouts >= self.runtime_timeout_threshold:
            cell.state = STATE_QUARANTINED
            cell.quarantine_reason = REASON_RUNTIME_TIMEOUT
            cell.quarantined_at_ns = int(ts_ns)
            return (True, True)
        # Request an RX flush on every runtime timeout so stray late bytes cannot cause 1-byte frame slip.
        return (False, True)

    def begin_reprobe(self, cell_key: tuple[int, int]) -> CellHealth:
        """Explicitly unlock a cell for manual startup re-probing."""
        cell = self._cells.get(cell_key)
        if cell is None:
            raise KeyError(f"Unknown SCI cell {cell_key!r}")
        cell.state = STATE_PROBING
        cell.startup_misses = 0
        cell.runtime_timeouts = 0
        cell.quarantine_reason = None
        cell.quarantined_at_ns = None
        cell.reprobe_count += 1
        return cell

    def active_keys(self) -> list[tuple[int, int]]:
        """Return ``(fx, offset)`` keys currently in the ``ACTIVE`` polling ring."""
        return [c.key for c in self._cells.values() if c.state == STATE_ACTIVE]

    def quarantined_entries(self) -> list[CellHealth]:
        """Return all ``CellHealth`` entries currently in ``QUARANTINED`` state."""
        return [c for c in self._cells.values() if c.state == STATE_QUARANTINED]

    def is_quarantined(self, key: tuple[int, int]) -> bool:
        """Return ``True`` iff ``key`` is tracked and currently ``QUARANTINED``."""
        cell = self._cells.get(key)
        return cell is not None and cell.state == STATE_QUARANTINED

    def to_session_quarantine_records(self) -> list[dict[str, Any]]:
        """Serialize quarantined cells to JSON-compatible dicts for ``sessions.quarantined_cells_json``."""
        records: list[dict[str, Any]] = []
        for cell in self.quarantined_entries():
            records.append({
                "fx": cell.fx,
                "fx_hex": cell.fx_hex,
                "offset": cell.offset,
                "offset_hex": cell.offset_hex,
                "label": cell.label,
                "symbol": cell.symbol,
                "channel": cell.channel,
                "reason": cell.quarantine_reason,
                "quarantined_at_ns": cell.quarantined_at_ns,
                "startup_misses": cell.startup_misses,
                "runtime_timeouts": cell.runtime_timeouts,
                "reprobe_count": cell.reprobe_count,
                "detail": f"SCI cell {cell.label} ({cell.symbol}) quarantined: {cell.quarantine_reason}",
            })
        return records
