"""High-speed 62,500-baud Chrysler SCI host poller with Hardware Deadbolt and Dead-Cell Quarantine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from logicaide.rom.manifest import NO_VERIFIED_SOURCE_MATCH, _normalize_sci_offset
from logicaide.telemetry.dead_cell import (
    DEFAULT_PROBE_TIMEOUT_MS,
    REASON_RUNTIME_TIMEOUT,
    STATE_ACTIVE,
    STATE_QUARANTINED,
    CellHealth,
    DeadCellQuarantineManager,
)
from logicaide.telemetry.deadbolt import NoVerifiedSourceMatchError, SciTxGate, validate_serial_target
from logicaide.telemetry.log_parsers import (
    ALL_ENGINEERED_CHANNELS,
    STANDARD_BARO_KPA,
    ParsedTelemetrySession,
    TelemetrySample,
)
from logicaide.telemetry.sci_channels import (
    DEFAULT_TPS_SPAN_V,
    DEFAULT_TPS_ZERO_V,
    WatchedChannel,
    _parse_sci_table,
    build_telemetry_sample,
    resolve_manifest_watchlist,
)

LOW_SPEED_BAUD = 7812
HIGH_SPEED_BAUD = 62500
_SCI_HANDSHAKE = b"\x12"
_SCI_EXIT = b"\xFE"


def _ns_to_iso_utc(ts_utc_ns: int) -> str:
    sec, rem_ns = divmod(ts_utc_ns, 1_000_000_000)
    return datetime.fromtimestamp(sec, tz=timezone.utc).replace(microsecond=rem_ns // 1_000).isoformat()


def _low_symbol_name(ch: WatchedChannel) -> str | None:
    if ch.low_offset is None or not ch.symbol:
        return None
    return f"{ch.symbol[:-3]}_LB" if ch.symbol.endswith("_HB") else f"{ch.symbol}_LB"


def _match_channels_for_token(
    channels: Sequence[WatchedChannel], token: str, gate: SciTxGate, *, fx_filter: int | None = None,
) -> list[tuple[int, int]] | None:
    low = token.strip().lower()
    for ch in channels:
        if fx_filter is not None and ch.fx_byte != fx_filter:
            continue
        if ch.channel_name.lower() == low and ch.low_offset is not None:
            gate.get_verified_entry(ch.fx_byte, ch.high_offset)
            gate.get_verified_entry(ch.fx_byte, ch.low_offset)
            return [(ch.fx_byte, ch.high_offset), (ch.fx_byte, ch.low_offset)]
        if ch.symbol.lower() == low or ch.channel_name.lower() == low:
            gate.get_verified_entry(ch.fx_byte, ch.high_offset)
            return [(ch.fx_byte, ch.high_offset)]
        if (lo_sym := _low_symbol_name(ch)) is not None and lo_sym.lower() == low:
            gate.get_verified_entry(ch.fx_byte, int(ch.low_offset))
            return [(ch.fx_byte, int(ch.low_offset))]
    return None


def _resolve_single_offset_spec(
    spec: str | tuple[int, int], *, channels: Sequence[WatchedChannel],
    gate: SciTxGate, preferred_table: str | None,
) -> list[tuple[int, int]]:
    if isinstance(spec, tuple):
        if len(spec) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in spec):
            raise NoVerifiedSourceMatchError(f"{NO_VERIFIED_SOURCE_MATCH}: invalid cell key {spec!r}.")
        fx, off = int(spec[0]), int(spec[1])
        gate.get_verified_entry(fx, off)
        return [(fx, off)]
    raw = str(spec).strip()
    if not raw:
        raise NoVerifiedSourceMatchError(f"{NO_VERIFIED_SOURCE_MATCH}: empty offset specification.")
    pref_fx = parsed[1] if preferred_table and (parsed := _parse_sci_table(preferred_table)) else None
    if ":" in raw:
        tbl_part, target_part = [p.strip() for p in raw.split(":", 1)]
        parsed_tbl = _parse_sci_table(tbl_part)
        if parsed_tbl is None:
            raise NoVerifiedSourceMatchError(f"{NO_VERIFIED_SOURCE_MATCH}: invalid SCI table in {spec!r}.")
        fx = parsed_tbl[1]
        try:
            off_int, _ = _normalize_sci_offset(target_part)
        except ValueError:
            off_int = None
        if off_int is not None:
            gate.get_verified_entry(fx, off_int)
            return [(fx, off_int)]
        if (hits := _match_channels_for_token(channels, target_part, gate, fx_filter=fx)) is not None:
            return hits
        target_low = target_part.lower()
        for (efx, eoff), entry in gate._verified_entries.items():
            if efx == fx and target_low in {
                str(entry.get("symbol") or "").lower(), str(entry.get("short_name") or "").lower(),
            }:
                return [(efx, eoff)]
        raise NoVerifiedSourceMatchError(f"{NO_VERIFIED_SOURCE_MATCH}: {spec!r}.")

    ordered_ch = [c for c in channels if pref_fx is None or c.fx_byte == pref_fx] + (
        [c for c in channels if c.fx_byte != pref_fx] if pref_fx is not None else []
    )
    if (hits := _match_channels_for_token(ordered_ch, raw, gate)) is not None:
        return hits
    try:
        off_int, _ = _normalize_sci_offset(raw)
    except ValueError:
        off_int = None
    candidates: list[int] = [fx for fx in (pref_fx, 0xF2, *sorted(gate.allowed_tables)) if fx is not None]
    dedup_cands = list(dict.fromkeys(candidates))
    if off_int is not None and 0 <= off_int <= 0xFF:
        for ch_cand in ordered_ch:
            if off_int in (ch_cand.high_offset, ch_cand.low_offset) and gate.is_verified_read(ch_cand.fx_byte, off_int):
                return [(ch_cand.fx_byte, off_int)]
        for fx_cand in dedup_cands:
            if gate.is_verified_read(fx_cand, off_int):
                return [(fx_cand, off_int)]
    raw_low = raw.lower()
    for fx_cand in dedup_cands:
        for (efx, eoff), entry in gate._verified_entries.items():
            if efx == fx_cand and raw_low in {
                str(entry.get("symbol") or "").lower(), str(entry.get("short_name") or "").lower(),
            }:
                return [(efx, eoff)]
    raise NoVerifiedSourceMatchError(f"{NO_VERIFIED_SOURCE_MATCH}: {spec!r}.")


def resolve_offset_specs(
    specs: Sequence[str | tuple[int, int]],
    *,
    channels: Sequence[WatchedChannel],
    gate: SciTxGate | None = None,
    manifest: Mapping[str, Any] | None = None,
    preferred_table: str | None = None,
) -> list[tuple[int, int]]:
    """Resolve CLI/API offset specs (including 16-bit channel names and ``_LB``) via manifest gate."""
    if gate is None:
        if manifest is None:
            raise ValueError("resolve_offset_specs requires either gate or manifest.")
        gate = SciTxGate(dict(manifest))
    resolved: list[tuple[int, int]] = []
    for spec in specs:
        for key in _resolve_single_offset_spec(spec, channels=channels, gate=gate, preferred_table=preferred_table):
            if key not in resolved:
                resolved.append(key)
    return resolved


@dataclass(slots=True)
class SciWatchConfig:
    """Configuration for a 62,500-baud High-Speed SCI watch/capture session."""

    car_id: str
    build_id: str = "STOCK"
    rom_id: str = "sbec_90_93_v3_1"
    ecu_family: str = "SBEC_SINGLE_BOARD"
    year: int = 1990
    model: str = "Chrysler Turbo"
    rom_sha256: str = ""
    sci_table: str | None = None
    extra_specs: tuple[str, ...] = ()
    strict_table: bool = False
    port: str = "loop://"
    max_frames: int | None = 50
    duration_s: float | None = None
    probe_timeout_ms: float = DEFAULT_PROBE_TIMEOUT_MS
    poll_interval_ms: float = 0.0
    baro_kpa: float = STANDARD_BARO_KPA
    map_sensor_bar: int = 2
    tps_zero_v: float = DEFAULT_TPS_ZERO_V
    tps_span_v: float = DEFAULT_TPS_SPAN_V
    wideband_afr: bool = True
    run_kind: str = "VEHICLE_SCI"
    drop_offsets: tuple[tuple[int, int], ...] = ()
    fixture_path: Path | None = None


@dataclass(slots=True)
class SciWatchResult:
    """Result bundle produced by ``SciPoller.run`` ready for Parquet lake persistence."""

    session_id: str
    car_id: str
    build_id: str
    rom_id: str
    ecu_family: str
    rom_sha256: str
    port: str
    handshake_ok: bool
    exit_reset_sent: bool
    frame_count: int
    quarantine_event_count: int
    rx_flush_count: int
    hz: float
    channels: list[WatchedChannel]
    quarantined_cells: list[dict[str, Any]]
    samples: list[TelemetrySample]
    parsed_session: ParsedTelemetrySession
    events: list[dict[str, Any]]


class SciPoller:
    """Manifest-gated 62,500-baud Chrysler SCI polling engine with Dead-Cell Quarantine."""

    def __init__(
        self,
        manifest: dict[str, Any],
        config: SciWatchConfig,
        channels: Sequence[WatchedChannel] | None = None,
    ) -> None:
        self.manifest = manifest
        self.config = config
        validate_serial_target(config.port)
        self.tx_gate = SciTxGate(manifest)
        self.channels: list[WatchedChannel] = (
            list(channels)
            if channels is not None
            else resolve_manifest_watchlist(
                manifest,
                sci_table=config.sci_table,
                extra_specs=config.extra_specs,
                wideband_afr=config.wideband_afr,
                strict_table=config.strict_table,
            )
        )
        self.manager = DeadCellQuarantineManager.from_watchlist(
            self.channels, probe_timeout_ms=config.probe_timeout_ms,
        )
        self._partner_map: dict[tuple[int, int], tuple[int, int]] = {}
        for ch in self.channels:
            if ch.low_offset is not None:
                hi_k, lo_k = (ch.fx_byte, ch.high_offset), (ch.fx_byte, ch.low_offset)
                self._partner_map[hi_k], self._partner_map[lo_k] = lo_k, hi_k
        self.rx_flush_count = 0
        self.quarantine_event_count = 0
        self.events: list[dict[str, Any]] = []
        self.handshake_ok = False
        self.exit_reset_sent = False

    def _flush_rx(self, uart: Any) -> None:
        uart.reset_input_buffer()
        self.rx_flush_count += 1

    def _settle_and_flush_rx(self, uart: Any) -> None:
        settle_s = min(0.004, max(0.0, (self.config.probe_timeout_ms / 1000.0) * 0.25))
        if settle_s > 0.0:
            time.sleep(settle_s)
        self._flush_rx(uart)

    def _poll_cell(self, uart: Any, fx: int, offset: int) -> int | None:
        if getattr(uart, "in_waiting", 0) > 0:
            self._flush_rx(uart)
        payload = bytes([fx, offset])
        self.tx_gate.assert_tx_allowed(payload)
        uart.write(payload)
        reply = uart.read(1)
        return reply[0] if len(reply) == 1 else None

    def enter_high_speed_mode(self, uart: Any) -> None:
        """Execute echoed 0x12 handshake at 7,812 baud, then switch to 62,500 baud zero-echo mode."""
        probe_s = max(0.001, self.config.probe_timeout_ms / 1000.0)
        uart.baudrate = LOW_SPEED_BAUD
        uart.timeout = max(0.08, probe_s * 5.0)
        uart.reset_input_buffer()
        self.tx_gate.assert_tx_allowed(_SCI_HANDSHAKE)
        uart.write(_SCI_HANDSHAKE)
        ack = uart.read(1)
        if ack != _SCI_HANDSHAKE:
            raise TimeoutError(f"SCI 0x12 handshake failed on {self.config.port!r}: expected b'\\x12', got {ack!r}")
        uart.baudrate = HIGH_SPEED_BAUD
        uart.timeout = probe_s
        self.handshake_ok = True

    def exit_high_speed_mode(self, uart: Any) -> None:
        """Send 0xFE diagnostic reset and restore 7,812 baud."""
        self.tx_gate.assert_tx_allowed(_SCI_EXIT)
        uart.write(_SCI_EXIT)
        time.sleep(0.005)
        uart.baudrate = LOW_SPEED_BAUD
        self.exit_reset_sent = True

    def _probe_single_cell(
        self, uart: Any, cell: CellHealth, *, phase: str, ts_ns_fn: Callable[[], int],
    ) -> CellHealth:
        for _ in range(self.manager.startup_miss_threshold):
            ts_ns = ts_ns_fn()
            replied = self._poll_cell(uart, cell.fx, cell.offset) is not None
            new_state = self.manager.record_startup_probe(cell.key, replied=replied, ts_ns=ts_ns)
            if not replied:
                self._settle_and_flush_rx(uart)
            if new_state == STATE_ACTIVE:
                self.events.append({
                    "event": "CELL_ACTIVE", "phase": phase, "cell": cell.label,
                    "symbol": cell.symbol, "channel": cell.channel, "ts_ns": ts_ns,
                })
                break
            if new_state == STATE_QUARANTINED:
                self.quarantine_event_count += 1
                self.events.append({
                    "event": "CELL_QUARANTINED", "phase": phase, "reason": cell.quarantine_reason,
                    "cell": cell.label, "symbol": cell.symbol, "channel": cell.channel, "ts_ns": ts_ns,
                })
                break
        return cell

    def run_startup_probe(self, uart: Any, ts_ns_fn: Callable[[], int] = time.time_ns) -> list[CellHealth]:
        """Probe each watchlist cell in isolation at 62,500 baud before hot-ring polling."""
        sync = getattr(uart, "sync_frame_boundary", None)
        if sync is not None:
            sync("STARTUP_START")
        try:
            for cell in self.manager.all_cells():
                if cell.state not in (STATE_ACTIVE, STATE_QUARANTINED):
                    self._probe_single_cell(uart, cell, phase="STARTUP_PROBE", ts_ns_fn=ts_ns_fn)
        finally:
            if sync is not None:
                sync("STARTUP_DONE")
        return self.manager.all_cells()

    def reprobe_offset(
        self, uart: Any, fx: int, offset: int, ts_ns_fn: Callable[[], int] = time.time_ns,
    ) -> CellHealth:
        """Explicitly re-probe a verified ``(fx, offset)`` cell to unlock it from quarantine."""
        if not self.tx_gate.is_verified_read(fx, offset):
            raise NoVerifiedSourceMatchError(
                f"{NO_VERIFIED_SOURCE_MATCH}: (0x{fx:02X}, 0x{offset:02X}) is not source_verified in manifest."
            )
        cell = self.manager.begin_reprobe((fx, offset))
        self.events.append({
            "event": "REPROBE_STARTED", "cell": cell.label, "symbol": cell.symbol,
            "channel": cell.channel, "reprobe_count": cell.reprobe_count, "ts_ns": ts_ns_fn(),
        })
        sync = getattr(uart, "sync_frame_boundary", None)
        if sync is not None:
            sync("REPROBE")
        try:
            return self._probe_single_cell(uart, cell, phase="REPROBE", ts_ns_fn=ts_ns_fn)
        finally:
            if sync is not None:
                sync("REPROBE_DONE")

    def poll_once(self, uart: Any, ts_ns: int) -> TelemetrySample:
        """Poll every cell currently in the ACTIVE ring and emit one ``TelemetrySample``."""
        if (sync := getattr(uart, "sync_frame_boundary", None)) is not None:
            sync("FRAME_START")
        active_keys = [
            k for k in self.manager.active_keys()
            if (partner := self._partner_map.get(k)) is None or not self.manager.is_quarantined(partner)
        ]
        if getattr(uart, "in_waiting", 0) > 0:
            self._flush_rx(uart)

        cell_values: dict[tuple[int, int], int | None] = {}
        frame_had_timeout = False
        frame_reasons: list[str] = []

        for fx, offset in active_keys:
            val = self._poll_cell(uart, fx, offset)
            cell_values[(fx, offset)] = val
            newly_q, needs_flush = self.manager.record_runtime_poll(
                (fx, offset), replied=(val is not None), ts_ns=ts_ns,
            )
            if needs_flush:
                frame_had_timeout = True
                self._settle_and_flush_rx(uart)
            if newly_q:
                cell = self.manager.get_cell((fx, offset))
                self.quarantine_event_count += 1
                label = cell.label if cell else f"0x{fx:02X}:0x{offset:02X}"
                frame_reasons.append(f"{REASON_RUNTIME_TIMEOUT}:{label}")
                self.events.append({
                    "event": "CELL_QUARANTINED", "phase": "RUNTIME_POLL",
                    "reason": REASON_RUNTIME_TIMEOUT, "cell": label,
                    "symbol": cell.symbol if cell else "", "channel": cell.channel if cell else "", "ts_ns": ts_ns,
                })

        quarantined_keys = {c.key for c in self.manager.quarantined_entries()}
        dead_flag = frame_had_timeout or not active_keys or not self.manager.active_keys()
        q_reason = (
            ",".join(frame_reasons) if frame_reasons
            else (REASON_RUNTIME_TIMEOUT if frame_had_timeout else ("ALL_CELLS_QUARANTINED" if not active_keys else None))
        )
        return build_telemetry_sample(
            ts_utc_ns=ts_ns, channels=self.channels, cell_values=cell_values, quarantined_keys=quarantined_keys,
            baro_kpa=self.config.baro_kpa, map_sensor_bar=self.config.map_sensor_bar,
            tps_zero_v=self.config.tps_zero_v, tps_span_v=self.config.tps_span_v,
            wideband_afr=self.config.wideband_afr, dead_cell_quarantine=dead_flag, quarantine_reason=q_reason,
        )

    def _build_parsed_session(
        self, session_id: str, samples: list[TelemetrySample], quarantined_records: list[dict[str, Any]],
    ) -> ParsedTelemetrySession:
        polled = tuple(ch for ch in ALL_ENGINEERED_CHANNELS if any(getattr(s, ch) is not None for s in samples))
        unpolled = tuple(ch for ch in ALL_ENGINEERED_CHANNELS if ch not in polled)
        now_iso = _ns_to_iso_utc(time.time_ns())
        return ParsedTelemetrySession(
            session_id=session_id, source_format="SCI_HIGH_SPEED", source_rel_path=self.config.port,
            car_id=self.config.car_id, build_id=self.config.build_id, run_kind=self.config.run_kind,
            ecu_family=self.config.ecu_family, year=self.config.year, model=self.config.model,
            baro_kpa=self.config.baro_kpa,
            started_at_utc=_ns_to_iso_utc(samples[0].ts_utc_ns) if samples else now_iso,
            ended_at_utc=_ns_to_iso_utc(samples[-1].ts_utc_ns) if samples else now_iso,
            sample_count=len(samples), quarantined_sample_count=sum(1 for s in samples if s.dead_cell_quarantine),
            tps_zero_v=self.config.tps_zero_v, tps_span_v=self.config.tps_span_v,
            polled_channels=polled, unpolled_channels=unpolled, gps_stripped=True,
            quarantined_cells=quarantined_records, samples=samples,
        )

    def run(
        self, uart: Any, *, session_id: str | None = None,
        on_frame: Callable[[int, TelemetrySample, SciPoller, Any], None] | None = None,
        reprobe_at_frame: Mapping[int, Sequence[tuple[int, int]]] | None = None,
        before_reprobe: Callable[[int, Sequence[tuple[int, int]]], None] | None = None,
    ) -> SciWatchResult:
        """Run full 0x12 handshake, startup probe, active polling loop, and 0xFE reset."""
        sess_id = session_id or f"sci_{self.config.car_id}_{int(time.time())}"
        samples: list[TelemetrySample] = []
        start_mono = time.monotonic()

        self.enter_high_speed_mode(uart)
        try:
            self.run_startup_probe(uart)
            frame_idx = 0
            while True:
                if self.config.max_frames is not None and frame_idx >= self.config.max_frames:
                    break
                if self.config.duration_s is not None and (time.monotonic() - start_mono) >= self.config.duration_s:
                    break
                if reprobe_at_frame and frame_idx in reprobe_at_frame:
                    targets = reprobe_at_frame[frame_idx]
                    if before_reprobe is not None:
                        before_reprobe(frame_idx, targets)
                    for fx, offset in targets:
                        self.reprobe_offset(uart, fx, offset)
                ts_ns = time.time_ns()
                if samples and ts_ns <= samples[-1].ts_utc_ns:
                    ts_ns = samples[-1].ts_utc_ns + 1
                sample = self.poll_once(uart, ts_ns)
                samples.append(sample)
                if on_frame is not None:
                    on_frame(frame_idx, sample, self, uart)
                frame_idx += 1
                if self.config.poll_interval_ms > 0.0:
                    time.sleep(self.config.poll_interval_ms / 1000.0)
        finally:
            self.exit_high_speed_mode(uart)

        elapsed_s = max(1e-9, time.monotonic() - start_mono)
        q_records = self.manager.to_session_quarantine_records()
        parsed_session = self._build_parsed_session(sess_id, samples, q_records)
        return SciWatchResult(
            session_id=sess_id, car_id=self.config.car_id, build_id=self.config.build_id,
            rom_id=self.config.rom_id, ecu_family=self.config.ecu_family, rom_sha256=self.config.rom_sha256,
            port=self.config.port, handshake_ok=self.handshake_ok, exit_reset_sent=self.exit_reset_sent,
            frame_count=len(samples), quarantine_event_count=self.quarantine_event_count,
            rx_flush_count=self.rx_flush_count, hz=len(samples) / elapsed_s,
            channels=list(self.channels), quarantined_cells=q_records,
            samples=samples, parsed_session=parsed_session, events=list(self.events),
        )
