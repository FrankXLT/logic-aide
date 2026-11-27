"""Offline virtual loop:// UART pair and stateful Chrysler 68HC11 mock SCI ECU responder."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

import pyarrow.parquet as pq

from logicaide.telemetry.deadbolt import open_read_only_serial, validate_serial_target
from logicaide.telemetry.log_parsers import FORBIDDEN_GPS_COLUMNS, STANDARD_BARO_KPA, parse_telemetry_file
from logicaide.telemetry.sci_channels import (
    DEFAULT_TPS_SPAN_V, DEFAULT_TPS_ZERO_V, WatchedChannel, encode_channel_bytes, resolve_manifest_watchlist,
)

LOW_SPEED_BAUD, HIGH_SPEED_BAUD = 7812, 62500
SCI_HANDSHAKE_BYTE, SCI_EXIT_BYTE = 0x12, 0xFE
_DEFAULT_CYCLE_TUPLES: tuple[tuple[float, ...], ...] = (
    (880.0, 38.0, 0.0, 2.15, 14.0, 28.0, 88.0, 14.1, 14.7, 0.04),
    (1650.0, 58.0, 10.0, 3.60, 24.0, 30.0, 88.5, 14.2, 14.6, 0.06),
    (2450.0, 76.0, 22.0, 5.40, 29.0, 33.0, 89.0, 14.2, 14.5, 0.08),
    (3200.0, 118.0, 55.0, 9.80, 24.5, 37.0, 89.5, 14.1, 12.8, 0.12),
    (4250.0, 162.0, 88.0, 15.20, 20.5, 42.0, 90.0, 14.0, 11.8, 0.20),
    (5350.0, 188.0, 98.0, 18.40, 18.5, 47.0, 90.5, 13.9, 11.4, 0.25),
    (5850.0, 194.0, 100.0, 19.10, 18.0, 50.0, 91.0, 13.9, 11.3, 0.28),
    (4100.0, 42.0, 4.0, 2.20, 31.0, 46.0, 90.5, 14.2, 15.1, 0.06),
    (2600.0, 26.0, 0.0, 1.40, 27.0, 41.0, 90.0, 14.2, 15.6, 0.04),
    (920.0, 36.0, 0.0, 2.10, 14.5, 36.0, 89.5, 14.1, 14.7, 0.04),
)


@dataclass(slots=True, frozen=True)
class OperatingPoint:
    """Single engine operating snapshot used by the virtual mock SCI ECU."""

    rpm: float
    map_kpa: float
    tps_pct: float
    pw_ms: float
    spark_deg_btdc: float
    act_c: float
    cts_c: float
    batt_v: float
    afr: float | None = 14.7
    knock_v: float = 0.05

    def to_dict(self) -> dict[str, float]:
        """Convert non-null numeric channels to a plain engineering-unit mapping."""
        out: dict[str, float] = {
            "rpm": self.rpm, "map_kpa": self.map_kpa, "tps_pct": self.tps_pct,
            "pw_ms": self.pw_ms, "spark_deg_btdc": self.spark_deg_btdc,
            "act_c": self.act_c, "cts_c": self.cts_c, "batt_v": self.batt_v, "knock_v": self.knock_v,
        }
        if self.afr is not None:
            out["afr"] = self.afr
        return out


def default_chrysler_turbo_cycle() -> list[OperatingPoint]:
    """Return a realistic 10-point Chrysler turbo cycle (idle -> cruise -> boost -> decel)."""
    return [OperatingPoint(*row) for row in _DEFAULT_CYCLE_TUPLES]


def _assert_no_gps_headers(columns: Sequence[str]) -> None:
    for col in columns:
        norm = col.strip().lower()
        if norm in FORBIDDEN_GPS_COLUMNS or norm.startswith("gps_"):
            raise ValueError(f"Forbidden GPS column in fixture: {col!r}")


def _row_to_operating_dict(row: Mapping[str, Any]) -> dict[str, float] | None:
    q_flag = row.get("dead_cell_quarantine")
    is_quarantined = q_flag.strip().lower() in {"1", "true", "yes"} if isinstance(q_flag, str) else bool(q_flag)
    if is_quarantined:
        return None
    rpm = row.get("rpm")
    if rpm is None or rpm == "":
        return None
    defaults = {
        "map_kpa": 65.0, "tps_pct": 12.0, "pw_ms": 4.5, "spark_deg_btdc": 22.0,
        "act_c": 32.0, "cts_c": 88.0, "batt_v": 14.1, "afr": 14.7, "knock_v": 0.05,
    }
    point: dict[str, float] = {"rpm": float(rpm)}
    for key, fallback in defaults.items():
        val = row.get(key)
        point[key] = float(val) if val not in (None, "") else fallback
    return point


def load_fixture_operating_points(fixture_path: Path | str, *, max_points: int = 250) -> list[dict[str, float]]:
    """Load a GPS-stripped CSV or Parquet fixture into ECU operating-point mappings."""
    path = Path(fixture_path)
    if not path.is_file():
        raise FileNotFoundError(f"Telemetry fixture not found: {path}")
    suffix = path.suffix.lower()
    raw_rows: list[Mapping[str, Any]] = []
    if suffix == ".parquet":
        table = pq.read_table(path)
        _assert_no_gps_headers(table.column_names)
        raw_rows = table.to_pylist()
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            _assert_no_gps_headers(headers)
            if "ts_utc_ns" in headers and ("rpm" in headers or "map_kpa" in headers):
                raw_rows = list(reader)
        if not raw_rows:
            sessions = parse_telemetry_file(path)
            for sess in sessions:
                for sample in sess.samples:
                    raw_rows.append({
                        "dead_cell_quarantine": sample.dead_cell_quarantine, "rpm": sample.rpm,
                        "map_kpa": sample.map_kpa, "tps_pct": sample.tps_pct, "pw_ms": sample.pw_ms,
                        "spark_deg_btdc": sample.spark_deg_btdc, "act_c": sample.act_c,
                        "cts_c": sample.cts_c, "batt_v": sample.batt_v, "afr": sample.afr,
                    })
    else:
        raise ValueError(f"Unsupported fixture extension {path.suffix!r}; expected .csv or .parquet")

    points: list[dict[str, float]] = []
    for row in raw_rows:
        if (pt := _row_to_operating_dict(row)) is not None:
            points.append(pt)
            if len(points) >= max_points:
                break
    if not points:
        raise ValueError(f"No valid non-quarantined operating points found in {path}")
    return points


class HostLoopbackPort:
    """UART-like host endpoint wrapping a dual-channel ``loop://`` pair."""

    def __init__(self, pair: LoopbackUartPair) -> None:
        self._pair = pair

    @property
    def port(self) -> str:
        return self._pair.port

    @property
    def baudrate(self) -> int:
        return self._pair.host_baudrate

    @baudrate.setter
    def baudrate(self, value: int) -> None:
        self._pair.host_baudrate = int(value)

    @property
    def timeout(self) -> float | None:
        return self._pair.ecu_to_host.timeout

    @timeout.setter
    def timeout(self, value: float | None) -> None:
        self._pair.ecu_to_host.timeout = None if value is None else float(value)

    @property
    def in_waiting(self) -> int:
        return int(self._pair.ecu_to_host.in_waiting)

    def write(self, data: bytes | bytearray) -> int:
        return self._pair.host_write(bytes(data))

    def read(self, size: int = 1) -> bytes:
        return bytes(self._pair.ecu_to_host.read(size))

    def reset_input_buffer(self) -> None:
        self._pair.ecu_to_host.reset_input_buffer()

    def sync_frame_boundary(self, phase: str) -> None:
        """Notify any attached loopback responder of probe/frame lifecycle boundaries."""
        if self._pair._frame_sync_cb is not None:
            self._pair._frame_sync_cb(phase)

    def close(self) -> None:
        self._pair.close()


class LoopbackUartPair:
    """Dual ``loop://`` serial transport separating host->ECU and ECU->host streams."""

    def __init__(self, port: str = "loop://", *, timeout_s: float = 0.012) -> None:
        self.port = validate_serial_target(port)
        self.host_to_ecu = open_read_only_serial(self.port, baudrate=LOW_SPEED_BAUD, timeout=timeout_s)
        try:
            self.ecu_to_host = open_read_only_serial(self.port, baudrate=LOW_SPEED_BAUD, timeout=timeout_s)
        except Exception:
            try:
                self.host_to_ecu.close()
            except Exception:
                pass
            raise
        self._host_baudrate: int = LOW_SPEED_BAUD
        self._ecu_baudrate: int = LOW_SPEED_BAUD
        self._tx_lock, self._rx_lock = threading.Lock(), threading.Lock()
        self._closed = False
        self._frame_sync_cb: Any = None
        self.host_port = HostLoopbackPort(self)

    @property
    def host_baudrate(self) -> int:
        return self._host_baudrate

    @host_baudrate.setter
    def host_baudrate(self, value: int) -> None:
        if value <= 0:
            raise ValueError(f"host_baudrate must be positive, got {value!r}")
        self._host_baudrate = int(value)
        self.host_to_ecu.baudrate = self._host_baudrate

    @property
    def ecu_baudrate(self) -> int:
        return self._ecu_baudrate

    @ecu_baudrate.setter
    def ecu_baudrate(self, value: int) -> None:
        if value <= 0:
            raise ValueError(f"ecu_baudrate must be positive, got {value!r}")
        self._ecu_baudrate = int(value)
        self.ecu_to_host.baudrate = self._ecu_baudrate

    def host_write(self, data: bytes) -> int:
        with self._tx_lock:
            return int(self.host_to_ecu.write(data))

    def ecu_read(self, size: int = 1) -> bytes:
        return bytes(self.host_to_ecu.read(size))

    def ecu_write(self, data: bytes) -> int:
        with self._rx_lock:
            return int(self.ecu_to_host.write(data))

    def ecu_flush_input(self) -> None:
        self.host_to_ecu.reset_input_buffer()

    def close(self) -> None:
        """Close both loopback serial handles idempotently."""
        if self._closed:
            return
        self._closed = True
        for dev in (self.host_to_ecu, self.ecu_to_host):
            try:
                dev.close()
            except Exception:
                pass


def _normalize_op_points(
    operating_points: Sequence[OperatingPoint | Mapping[str, float]] | None,
    fixture_path: Path | str | None,
) -> list[dict[str, float]]:
    if fixture_path is not None:
        return load_fixture_operating_points(fixture_path)
    if operating_points:
        return [pt.to_dict() if isinstance(pt, OperatingPoint) else {k: float(v) for k, v in pt.items() if v is not None}
                for pt in operating_points]
    return [pt.to_dict() for pt in default_chrysler_turbo_cycle()]


class MockSciEcuResponder:
    """Stateful background Chrysler 68HC11 mock ECU responder over ``loop://``."""

    def __init__(
        self, manifest: Mapping[str, Any], channels: Sequence[WatchedChannel] | None = None, *,
        port: str = "loop://", uart_pair: LoopbackUartPair | None = None, probe_timeout_ms: float = 12.0,
        map_sensor_bar: int = 2, baro_kpa: float = STANDARD_BARO_KPA,
        tps_zero_v: float = DEFAULT_TPS_ZERO_V, tps_span_v: float = DEFAULT_TPS_SPAN_V,
        wideband_afr: bool = True, operating_points: Sequence[OperatingPoint | Mapping[str, float]] | None = None,
        fixture_path: Path | str | None = None, drop_offsets: Iterable[tuple[int, int]] = (),
        transient_drops: Mapping[tuple[int, int], Iterable[int]] | None = None,
        stray_byte_drops: Mapping[tuple[int, int], int] | None = None, stray_delay_s: float | None = None,
    ) -> None:
        self.manifest = dict(manifest)
        self.channels = list(channels) if channels is not None else resolve_manifest_watchlist(
            self.manifest, wideband_afr=wideband_afr,
        )
        self.probe_timeout_s = probe_timeout_ms / 1000.0
        self.map_sensor_bar, self.baro_kpa = map_sensor_bar, baro_kpa
        self.tps_zero_v, self.tps_span_v = tps_zero_v, tps_span_v
        self._operating_points = _normalize_op_points(operating_points, fixture_path)
        self._drop_offsets: set[tuple[int, int]] = {tuple(k) for k in drop_offsets}
        self._transient_drops: dict[tuple[int, int], set[int]] = {
            tuple(k): set(v) for k, v in (transient_drops or {}).items()
        }
        self._stray_byte_drops: dict[tuple[int, int], int] = {
            tuple(k): int(v) & 0xFF for k, v in (stray_byte_drops or {}).items()
        }
        self._stray_delay_s = self.probe_timeout_s + 0.001 if stray_delay_s is None else float(stray_delay_s)
        self._snapshot_cache = [self._encode_operating_point(pt) for pt in self._operating_points]
        self._low_to_high: dict[tuple[int, int], tuple[int, int]] = {
            (ch.fx_byte, ch.low_offset): (ch.fx_byte, ch.high_offset)
            for ch in self.channels if ch.low_offset is not None
        }
        self._lock, self._stop_event = threading.Lock(), threading.Event()
        self._thread: threading.Thread | None = None
        self._timers: list[threading.Timer] = []
        self._snapshot_idx, self._in_startup, self._probe_hold = 0, False, False
        self._frame_seen_cells: set[tuple[int, int]] = set()
        self._request_counts: dict[tuple[int, int], int] = {}
        self._last_key: tuple[int, int] | None = None
        self.high_speed_active = False

        self._owns_pair = uart_pair is None
        self.uart_pair = uart_pair or LoopbackUartPair(port, timeout_s=max(0.002, self.probe_timeout_s))
        try:
            self.uart_pair._frame_sync_cb = self.sync_frame_boundary
        except Exception:
            if self._owns_pair:
                self.uart_pair.close()
            raise

    @property
    def host_port(self) -> HostLoopbackPort:
        return self.uart_pair.host_port

    @property
    def snapshot_idx(self) -> int:
        with self._lock:
            return self._snapshot_idx

    @property
    def request_counts(self) -> dict[tuple[int, int], int]:
        with self._lock:
            return dict(self._request_counts)

    def sync_frame_boundary(self, phase: str) -> None:
        """Coordinate startup, re-probe, and frame snapshot boundaries without touching UART data."""
        with self._lock:
            if phase == "STARTUP_START":
                self._in_startup, self._probe_hold, self._last_key = True, False, None
                self._frame_seen_cells.clear()
            elif phase == "STARTUP_DONE":
                self._in_startup, self._probe_hold, self._snapshot_idx, self._last_key = False, False, 0, None
                self._frame_seen_cells.clear()
            elif phase == "FRAME_START":
                self._probe_hold, self._last_key = False, None
                if self._in_startup:
                    self._in_startup, self._snapshot_idx = False, 0
                    self._frame_seen_cells.clear()
                elif self._frame_seen_cells:
                    self._snapshot_idx = (self._snapshot_idx + 1) % len(self._snapshot_cache)
                    self._frame_seen_cells.clear()
            elif phase == "REPROBE":
                self._probe_hold = True
            elif phase == "REPROBE_DONE":
                self._probe_hold = False

    def set_drop_offsets(self, offsets: Iterable[tuple[int, int]]) -> None:
        with self._lock:
            self._drop_offsets = {tuple(k) for k in offsets}

    def add_drop_offset(self, cell_key: tuple[int, int]) -> None:
        with self._lock:
            self._drop_offsets.add(tuple(cell_key))

    def clear_drop_offset(self, cell_key: tuple[int, int]) -> None:
        with self._lock:
            self._drop_offsets.discard(tuple(cell_key))
            self._stray_byte_drops.pop(tuple(cell_key), None)

    def inject_stray_rx_byte(self, byte_val: int) -> None:
        self.uart_pair.ecu_write(bytes([int(byte_val) & 0xFF]))

    def _encode_operating_point(self, point: Mapping[str, float]) -> dict[tuple[int, int], int]:
        encoded: dict[tuple[int, int], int] = {}
        for ch in self.channels:
            val = point.get(ch.channel_name)
            if val is None:
                val = 0.45 if ch.channel_name == "o2_v" else (0.05 if ch.channel_name == "knock_v" else 0.0)
            raw = encode_channel_bytes(
                ch, val, baro_kpa=self.baro_kpa, map_sensor_bar=self.map_sensor_bar,
                tps_zero_v=self.tps_zero_v, tps_span_v=self.tps_span_v,
            )
            encoded[(ch.fx_byte, ch.high_offset)] = raw[0]
            if ch.low_offset is not None and len(raw) > 1:
                encoded[(ch.fx_byte, ch.low_offset)] = raw[1]
        return encoded

    def _schedule_stray_byte(self, byte_val: int) -> None:
        def _emit() -> None:
            if self._stop_event.is_set():
                return
            try:
                self.uart_pair.ecu_write(bytes([byte_val]))
            except Exception:
                pass

        with self._lock:
            if self._stop_event.is_set():
                return
            self._timers = [t for t in self._timers if t.is_alive()]
            timer = threading.Timer(self._stray_delay_s, _emit)
            timer.daemon = True
            self._timers.append(timer)
            timer.start()

    def _handle_cell_request(self, fx: int, offset: int) -> None:
        key = (fx, offset)
        with self._lock:
            if not self._in_startup and not self._probe_hold:
                high_pair = self._low_to_high.get(key)
                pair_open = high_pair is not None and high_pair in self._frame_seen_cells and key not in self._frame_seen_cells
                immediate_retry = key == self._last_key and len(self._frame_seen_cells) > 1
                if key in self._frame_seen_cells and not pair_open and not immediate_retry:
                    self._snapshot_idx = (self._snapshot_idx + 1) % len(self._snapshot_cache)
                    self._frame_seen_cells.clear()
                self._frame_seen_cells.add(key)
                self._last_key = key

            req_idx = self._request_counts.get(key, 0)
            self._request_counts[key] = req_idx + 1
            in_drop = key in self._drop_offsets
            in_transient = req_idx in self._transient_drops.get(key, ())
            stray_byte = self._stray_byte_drops.get(key)
            reply_byte = self._snapshot_cache[self._snapshot_idx].get(key)

        if in_drop or in_transient or (stray_byte is not None and key not in self._transient_drops):
            if stray_byte is not None and (in_transient or not self._transient_drops.get(key)):
                self._schedule_stray_byte(stray_byte)
            return
        if reply_byte is not None:
            self.uart_pair.ecu_write(bytes([reply_byte]))

    def _read_second_byte(self) -> int | None:
        deadline = time.monotonic() + 0.025
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            if b1 := self.uart_pair.ecu_read(1):
                return b1[0]
        return None

    def _serve_loop(self) -> None:
        self.uart_pair.host_to_ecu.timeout = 0.005
        while not self._stop_event.is_set():
            try:
                b0 = self.uart_pair.ecu_read(1)
            except Exception:
                break
            if not b0:
                continue
            byte0 = b0[0]
            if not self.high_speed_active:
                if byte0 == SCI_HANDSHAKE_BYTE and self.uart_pair.host_baudrate == self.uart_pair.ecu_baudrate == LOW_SPEED_BAUD:
                    self.uart_pair.ecu_write(bytes([SCI_HANDSHAKE_BYTE]))
                    self.uart_pair.ecu_baudrate = HIGH_SPEED_BAUD
                    self.high_speed_active = True
                continue
            if byte0 == SCI_EXIT_BYTE:
                self.uart_pair.ecu_baudrate = LOW_SPEED_BAUD
                self.high_speed_active = False
                with self._lock:
                    self._frame_seen_cells.clear()
                    self._last_key = None
                continue
            if self.uart_pair.host_baudrate != self.uart_pair.ecu_baudrate:
                self.uart_pair.ecu_flush_input()
                continue
            if 0xF0 <= byte0 <= 0xFD and (byte1 := self._read_second_byte()) is not None:
                if self.uart_pair.host_baudrate != self.uart_pair.ecu_baudrate:
                    self.uart_pair.ecu_flush_input()
                else:
                    self._handle_cell_request(byte0, byte1)

    def start(self) -> MockSciEcuResponder:
        """Start the background mock ECU responder thread."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve_loop, name="MockSciEcuResponder", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the responder thread, cancel pending timers, and close owned UARTs."""
        self._stop_event.set()
        with self._lock:
            for timer in self._timers:
                timer.cancel()
            self._timers.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.uart_pair._frame_sync_cb == self.sync_frame_boundary:
            self.uart_pair._frame_sync_cb = None
        if self._owns_pair:
            self.uart_pair.close()

    def __enter__(self) -> MockSciEcuResponder:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()
