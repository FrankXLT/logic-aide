"""Manifest-only 62,500-baud SCI watchlist resolution and NIST SP 811 channel codecs."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Sequence

from logicaide.rom.manifest import NO_VERIFIED_SOURCE_MATCH, _normalize_sci_offset
from logicaide.telemetry.log_parsers import PSI_TO_KPA, STANDARD_BARO_KPA, TelemetrySample

HIGH_SPEED_SCI_BAUD = 62500
DEFAULT_TPS_ZERO_V, DEFAULT_TPS_SPAN_V = 0.5, 4.0
MAP_MIN_PSI = -14.7
_MAP_BAR_MAX_PSI: dict[int, float] = {1: 0.0, 2: 14.7, 3: 29.4}
WIDEBAND_AFR_MIN, WIDEBAND_AFR_MAX = 7.34, 22.376
CTS_DEFAULT_MIN_F, CTS_DEFAULT_MAX_F = -198.4, 260.6
CHARGE_TEMP_DEFAULT_HOT_F, CHARGE_TEMP_DEFAULT_COLD_F = 261.0, -21.0

_NIST_UNIT_MAP: dict[str, str] = {
    "rpm": "min^-1", "psi": "psi", "volts": "V", "msec": "ms", "deg": "deg",
    "degf": "°C", "afr": "AFR", "%": "%", "inhg": "InHg", "sec": "s", "mph": "mph", "units": "raw",
}
_CORE_SPECS: tuple[tuple[str, tuple[str, ...], str | None, str, str], ...] = (
    ("rpm", ("EngineRpm_HB",), "EngineRpm_LB", "min^-1", "rpm"),
    ("map_kpa", ("MapValue",), None, "kPa", "psi"),
    ("tps_pct", ("TpsVolts",), None, "%", "volts"),
    ("pw_ms", ("InjectorPulsewidth_HB",), "InjectorPulsewidth_LB", "ms", "mSec"),
    ("spark_deg_btdc", ("CalculatedSparkAdvance",), None, "deg BTDC", "deg"),
    ("act_c", ("ChargeTemp",), None, "°C", "degF"),
    ("cts_c", ("CoolantTemp",), None, "°C", "degF"),
    ("batt_v", ("BatteryVolts",), None, "V", "volts"),
    ("knock_v", ("KnockVolts",), None, "V", "volts"),
)
_SIMPLE_SAMPLE_FIELDS: dict[str, tuple[str, str]] = {
    "CalculatedSparkAdvance": ("raw_spark_b", "spark_deg_btdc"), "spark_deg_btdc": ("raw_spark_b", "spark_deg_btdc"),
    "ChargeTemp": ("raw_act_b", "act_c"), "act_c": ("raw_act_b", "act_c"),
    "CoolantTemp": ("raw_cts_b", "cts_c"), "cts_c": ("raw_cts_b", "cts_c"),
    "BatteryVolts": ("raw_batt_b", "batt_v"), "batt_v": ("raw_batt_b", "batt_v"),
}


@dataclass(slots=True, frozen=True)
class WatchedChannel:
    """Verified SCI polling channel descriptor bound to a specific ROM manifest table."""

    channel_name: str
    sci_table: str
    fx_byte: int
    high_offset: int
    low_offset: int | None = None
    symbol: str = ""
    units: str = ""
    nist_unit: str = ""
    settings: dict[str, Any] | None = None
    function_set: list[dict[str, Any]] | None = None

    @property
    def is_16bit(self) -> bool:
        """Return ``True`` when the channel polls both a high byte and low byte."""
        return self.low_offset is not None

    @property
    def cell_keys(self) -> tuple[tuple[int, int], ...]:
        """Return ordered ``(fx_byte, offset)`` cell keys required for this channel."""
        if self.low_offset is not None:
            return ((self.fx_byte, self.high_offset), (self.fx_byte, self.low_offset))
        return ((self.fx_byte, self.high_offset),)


def _parse_sci_table(raw_table: Any) -> tuple[str, int] | None:
    if not isinstance(raw_table, str):
        return None
    text = raw_table.strip()
    digits = text[2:] if text.lower().startswith(("0x", "0h")) else (text[1:] if text.startswith("$") else "")
    if not digits:
        return None
    try:
        val = int(digits, 16)
    except ValueError:
        return None
    return (f"0x{val:02X}", val) if 0xF0 <= val <= 0xFD else None


def _validate_byte(value: Any, label: str = "byte") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 0xFF):
        raise ValueError(f"{label} must be an integer in 0..255, got {value!r}")
    return value


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return lo if value < lo else (hi if value > hi else value)


def _f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def _c_to_f(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0


def _decode_linear(raw: int, max_raw: float, ecu_min: float, ecu_max: float) -> float:
    return ecu_min + (raw / max_raw) * (ecu_max - ecu_min)


def _encode_linear(value: float, max_raw: int, ecu_min: float, ecu_max: float) -> int:
    if ecu_max == ecu_min:
        return 0
    return _clamp_int(round(((value - ecu_min) / (ecu_max - ecu_min)) * max_raw), 0, max_raw)


def _decode_map_pair(raw: int, *, baro_kpa: float, map_sensor_bar: int) -> tuple[float, float]:
    if map_sensor_bar not in _MAP_BAR_MAX_PSI:
        raise ValueError(f"Unsupported map_sensor_bar={map_sensor_bar!r}; expected 1, 2, or 3")
    boost_psi = _decode_linear(raw, 255.0, MAP_MIN_PSI, _MAP_BAR_MAX_PSI[map_sensor_bar])
    return baro_kpa + boost_psi * PSI_TO_KPA, boost_psi


def _interp_1d(points: list[tuple[float, float]], x_val: float) -> float:
    ordered = sorted(points, key=lambda pt: pt[0])
    if x_val <= ordered[0][0]:
        return ordered[0][1]
    if x_val >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:]):
        if x0 <= x_val <= x1:
            return y0 if x1 == x0 else y0 + ((x_val - x0) / (x1 - x0)) * (y1 - y0)
    return ordered[-1][1]


def _charge_temp_bounds_f(settings: Mapping[str, Any] | None) -> tuple[float, float]:
    if settings and settings.get("units") == "degF":
        lo, hi = settings.get("ecu_min"), settings.get("ecu_max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and (lo, hi) != (0, 5):
            return float(lo), float(hi)
    return CHARGE_TEMP_DEFAULT_HOT_F, CHARGE_TEMP_DEFAULT_COLD_F


def _index_verified_tables(manifest: Mapping[str, Any]) -> tuple[
    list[str], dict[str, dict[str, dict[str, Any]]], dict[str, dict[int, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]
]:
    declared: list[str] = []
    for raw_tbl in manifest.get("sci_tables", []):
        if (parsed := _parse_sci_table(raw_tbl)) is not None and parsed[0] not in declared:
            declared.append(parsed[0])
    if not declared:
        raise ValueError("Manifest declares no 0xF0-0xFD high-speed SCI tables.")
    declared_set = set(declared)
    by_sym: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in declared}
    by_off: dict[str, dict[int, dict[str, Any]]] = {t: {} for t in declared}
    by_short: dict[str, dict[str, dict[str, Any]]] = {t: {} for t in declared}
    for entry in manifest.get("ram_watchlist", []):
        if entry.get("source_verified") is not True:
            continue
        parsed_tbl = _parse_sci_table(entry.get("sci_table"))
        if parsed_tbl is None or parsed_tbl[0] not in declared_set:
            continue
        tbl, off = parsed_tbl[0], entry.get("offset")
        if isinstance(off, bool) or not isinstance(off, int) or not (0 <= off <= 0xFF):
            continue
        if sym := str(entry.get("symbol") or "").strip():
            by_sym[tbl].setdefault(sym, entry)
        by_off[tbl].setdefault(off, entry)
        if sn := str(entry.get("short_name") or "").strip().lower():
            by_short[tbl].setdefault(sn, entry)
    return declared, by_sym, by_off, by_short


def _build_watched_channel(
    *, channel_name: str, tbl: str, entry: Mapping[str, Any], low_entry: Mapping[str, Any] | None,
    f2_by_sym: Mapping[str, Mapping[str, Any]], nist_unit_override: str | None = None, default_units: str = "",
) -> WatchedChannel:
    sym = str(entry.get("symbol") or "")
    f2_ref = f2_by_sym.get(sym)
    raw_settings = entry.get("settings") or (f2_ref.get("settings") if f2_ref else None)
    raw_fn = entry.get("function_set") or (f2_ref.get("function_set") if f2_ref else None)
    settings = dict(raw_settings) if isinstance(raw_settings, Mapping) else None
    fn_set = [dict(pt) for pt in raw_fn] if isinstance(raw_fn, list) else None
    units = str((settings or {}).get("units") or default_units)
    nist_unit = nist_unit_override or _NIST_UNIT_MAP.get(units.lower(), units or "raw")
    _, fx_byte = _parse_sci_table(tbl) or (tbl, 0xF2)
    return WatchedChannel(
        channel_name=channel_name, sci_table=tbl, fx_byte=fx_byte, high_offset=int(entry["offset"]),
        low_offset=int(low_entry["offset"]) if low_entry is not None else None,
        symbol=sym, units=units, nist_unit=nist_unit, settings=settings, function_set=fn_set,
    )


def _resolve_table_order(declared: list[str], sci_table: str | None, strict_table: bool, rom_id: Any) -> list[str]:
    if sci_table is not None:
        parsed = _parse_sci_table(sci_table)
        if parsed is None:
            raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: invalid SCI table {sci_table!r} (expected 0xF0..0xFD).")
        req_tbl = parsed[0]
        if req_tbl not in declared:
            raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: table {req_tbl} missing in manifest {rom_id!r}.")
        return [req_tbl] if strict_table else [req_tbl] + [t for t in ("0xF2", *declared) if t in declared and t != req_tbl]
    order: list[str] = []
    for t in ("0xF2", *declared):
        if t in declared and t not in order:
            order.append(t)
    return order


def _lookup_extra_entry(
    spec: str, *, declared: list[str], table_order: list[str],
    by_sym: Mapping[str, Mapping[str, dict[str, Any]]],
    by_off: Mapping[str, Mapping[int, dict[str, Any]]],
    by_short: Mapping[str, Mapping[str, dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    raw_spec = spec.strip()
    if not raw_spec:
        raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: empty extra_spec {spec!r}")
    if ":" in raw_spec:
        tbl_part, target_part = [p.strip() for p in raw_spec.split(":", 1)]
        parsed_tbl = _parse_sci_table(tbl_part)
        if parsed_tbl is None or parsed_tbl[0] not in declared:
            raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: {spec!r}")
        search_tables = [parsed_tbl[0]]
    else:
        target_part, search_tables = raw_spec, table_order

    try:
        off_int, _ = _normalize_sci_offset(target_part)
    except ValueError:
        off_int = None

    if off_int is not None:
        if off_int > 0xFF:
            raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: {spec!r}")
        for tbl in search_tables:
            if (hit := by_off[tbl].get(off_int)) is not None:
                return tbl, hit
        raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: {spec!r}")

    target_low = target_part.lower()
    for tbl in search_tables:
        for sym, hit in by_sym[tbl].items():
            if sym.lower() == target_low:
                return tbl, hit
        if target_low in by_short[tbl]:
            return tbl, by_short[tbl][target_low]
    raise ValueError(f"{NO_VERIFIED_SOURCE_MATCH}: {spec!r}")


def resolve_manifest_watchlist(
    manifest: dict[str, Any], *, sci_table: str | None = None,
    extra_specs: Sequence[str] = (), wideband_afr: bool = True, strict_table: bool = False,
) -> list[WatchedChannel]:
    """Resolve core and optional SCI watchlist channels strictly from a verified 62,500-baud manifest."""
    if manifest.get("sci_baud") != HIGH_SPEED_SCI_BAUD:
        raise ValueError(
            f"Unsupported sci_baud={manifest.get('sci_baud')!r} for {manifest.get('rom_id', '<unknown>')!r}; "
            f"high-speed SCI requires {HIGH_SPEED_SCI_BAUD} baud."
        )
    declared, by_sym, by_off, by_short = _index_verified_tables(manifest)
    table_order = _resolve_table_order(declared, sci_table, strict_table, manifest.get("rom_id"))
    f2_by_sym = by_sym.get("0xF2", {})
    o2_cands = ("RawAirFuelSensInput", "O2SensorVolts", "O2SensorValue") if wideband_afr else (
        "O2SensorVolts", "O2SensorValue", "RawAirFuelSensInput"
    )
    specs = (
        *_CORE_SPECS[:-1],
        ("afr" if wideband_afr else "o2_v", o2_cands, None, "AFR" if wideband_afr else "V", "afr" if wideband_afr else "volts"),
        _CORE_SPECS[-1],
    )
    resolved: list[WatchedChannel] = []
    seen_cells: set[tuple[int, int]] = set()
    for ch_name, sym_cands, low_sym, nist_unit, def_units in specs:
        for sym in sym_cands:
            for tbl in table_order:
                hi_entry = by_sym[tbl].get(sym)
                lo_entry = by_sym[tbl].get(low_sym) if low_sym is not None else None
                if hi_entry is None or (low_sym is not None and lo_entry is None):
                    continue
                is_nb = sym in ("O2SensorVolts", "O2SensorValue") or (sym == "RawAirFuelSensInput" and not wideband_afr)
                ch = _build_watched_channel(
                    channel_name=("o2_v" if is_nb else ch_name), tbl=tbl, entry=hi_entry, low_entry=lo_entry,
                    f2_by_sym=f2_by_sym, nist_unit_override=("V" if is_nb else nist_unit),
                    default_units=("volts" if is_nb else def_units),
                )
                resolved.append(ch)
                seen_cells.update(ch.cell_keys)
                break
            else:
                continue
            break

    core_by_sym = {sym: (n, u, d) for n, syms, _, u, d in specs for sym in syms}
    for spec in extra_specs:
        tbl, entry = _lookup_extra_entry(
            spec, declared=declared, table_order=table_order, by_sym=by_sym, by_off=by_off, by_short=by_short,
        )
        sym = str(entry.get("symbol") or "")
        lo_entry = by_sym[tbl].get(f"{sym[:-3]}_LB") if sym.endswith("_HB") else None
        meta = core_by_sym.get(sym)
        ch = _build_watched_channel(
            channel_name=meta[0] if meta else sym, tbl=tbl, entry=entry, low_entry=lo_entry,
            f2_by_sym=f2_by_sym, nist_unit_override=meta[1] if meta else None, default_units=meta[2] if meta else "",
        )
        if not any(cell in seen_cells for cell in ch.cell_keys):
            resolved.append(ch)
            seen_cells.update(ch.cell_keys)
    return resolved


def _extract_raw_bytes(
    channel: WatchedChannel, raw_bytes: int | bytes | bytearray | Sequence[int | None] | None,
) -> tuple[int, int | None] | None:
    if raw_bytes is None:
        return None
    if isinstance(raw_bytes, int) and not isinstance(raw_bytes, bool):
        if channel.is_16bit:
            raise ValueError(f"16-bit channel {channel.symbol!r} requires 2 bytes (hb, lb)")
        return _validate_byte(raw_bytes, "raw_byte"), None
    seq = list(raw_bytes)
    expected = 2 if channel.is_16bit else 1
    if len(seq) != expected:
        raise ValueError(f"Channel {channel.symbol!r} requires {expected} byte(s), got {len(seq)}")
    if any(b is None for b in seq):
        return None
    return (_validate_byte(seq[0], "hb"), _validate_byte(seq[1], "lb")) if channel.is_16bit else (
        _validate_byte(seq[0], "raw_byte"), None
    )


def _num_or(settings: Mapping[str, Any], key: str, default: float) -> float:
    val = settings.get(key)
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else default


def _linear_channel_bounds(channel: WatchedChannel) -> tuple[float, float, bool] | None:
    sym, settings = channel.symbol, channel.settings or {}
    if sym == "CoolantTemp" or channel.channel_name == "cts_c":
        return _num_or(settings, "ecu_min", CTS_DEFAULT_MIN_F), _num_or(settings, "ecu_max", CTS_DEFAULT_MAX_F), True
    if sym == "BatteryVolts" or channel.channel_name == "batt_v":
        return _num_or(settings, "ecu_min", 0.0), _num_or(settings, "ecu_max", 15.75), False
    if channel.channel_name == "o2_v" or sym in ("O2SensorVolts", "O2SensorValue", "KnockVolts"):
        return 0.0, 5.0, False
    if sym == "RawAirFuelSensInput":
        return WIDEBAND_AFR_MIN, WIDEBAND_AFR_MAX, False
    lo_val, hi_val = settings.get("ecu_min"), settings.get("ecu_max")
    if (
        isinstance(lo_val, (int, float)) and not isinstance(lo_val, bool)
        and isinstance(hi_val, (int, float)) and not isinstance(hi_val, bool)
        and lo_val != hi_val
    ):
        return float(lo_val), float(hi_val), channel.units.lower() == "degf"
    return None


def decode_channel_value(
    channel: WatchedChannel, raw_bytes: int | bytes | bytearray | Sequence[int | None] | None, *,
    baro_kpa: float = STANDARD_BARO_KPA, map_sensor_bar: int = 2,
    tps_zero_v: float = DEFAULT_TPS_ZERO_V, tps_span_v: float = DEFAULT_TPS_SPAN_V, wideband_afr: bool = True,
) -> float | int | None:
    """Decode raw SCI byte(s) for ``channel`` into NIST SP 811 engineering units (or ``None``)."""
    parsed = _extract_raw_bytes(channel, raw_bytes)
    if parsed is None:
        return None
    hb, lb = parsed
    sym = channel.symbol
    if sym == "EngineRpm_HB" or channel.channel_name == "rpm":
        return ((hb << 8) | (lb or 0)) / 8.0
    if sym == "InjectorPulsewidth_HB" or channel.channel_name == "pw_ms":
        return ((hb << 8) | (lb or 0)) * 0.004
    if sym == "MapValue" or channel.channel_name == "map_kpa":
        return _decode_map_pair(hb, baro_kpa=baro_kpa, map_sensor_bar=map_sensor_bar)[0]
    if sym == "TpsVolts" or channel.channel_name == "tps_pct":
        if tps_span_v <= 0.0:
            raise ValueError(f"tps_span_v must be > 0, got {tps_span_v}")
        return max(0.0, min(100.0, ((_decode_linear(hb, 255.0, 0.0, 5.0) - tps_zero_v) / tps_span_v) * 100.0))
    if sym == "CalculatedSparkAdvance" or channel.channel_name == "spark_deg_btdc":
        return hb * 0.5
    if sym == "RawAirFuelSensInput" and not wideband_afr:
        return None
    if channel.function_set:
        volts = _decode_linear(hb, 255.0, 0.0, 5.0)
        val = _interp_1d([(float(p["volts"]), float(p["value"])) for p in channel.function_set], volts)
        return _f_to_c(val) if (sym == "ChargeTemp" or channel.units.lower() == "degf") else val
    if sym == "ChargeTemp" or channel.channel_name == "act_c":
        f0, f1 = _charge_temp_bounds_f(channel.settings)
        return _f_to_c(_decode_linear(hb, 255.0, f0, f1))
    if (bounds := _linear_channel_bounds(channel)) is not None:
        lo, hi, is_degf = bounds
        val = _decode_linear(((hb << 8) | lb) if lb is not None else hb, 65535.0 if lb is not None else 255.0, lo, hi)
        return _f_to_c(val) if is_degf else val
    return float(((hb << 8) | lb) if lb is not None else hb)


def encode_channel_bytes(
    channel: WatchedChannel, value: float, *,
    baro_kpa: float = STANDARD_BARO_KPA, map_sensor_bar: int = 2,
    tps_zero_v: float = DEFAULT_TPS_ZERO_V, tps_span_v: float = DEFAULT_TPS_SPAN_V,
) -> bytes:
    """Encode a NIST SP 811 value for ``channel`` back into 1 or 2 raw SCI bytes."""
    if value is None or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"Cannot encode non-finite value {value!r} for {channel.symbol!r}")
    val, sym = float(value), channel.symbol
    if sym == "EngineRpm_HB" or channel.channel_name == "rpm":
        raw16 = _clamp_int(round(val * 8.0), 0, 0xFFFF)
        return bytes([(raw16 >> 8) & 0xFF, raw16 & 0xFF])
    if sym == "InjectorPulsewidth_HB" or channel.channel_name == "pw_ms":
        raw16 = _clamp_int(round(val / 0.004), 0, 0xFFFF)
        return bytes([(raw16 >> 8) & 0xFF, raw16 & 0xFF])
    if sym == "MapValue" or channel.channel_name == "map_kpa":
        return bytes([_encode_linear((val - baro_kpa) / PSI_TO_KPA, 255, MAP_MIN_PSI, _map_max_psi(map_sensor_bar))])
    if sym == "TpsVolts" or channel.channel_name == "tps_pct":
        if tps_span_v <= 0.0:
            raise ValueError(f"tps_span_v must be > 0, got {tps_span_v}")
        volts = tps_zero_v + (max(0.0, min(100.0, val)) / 100.0) * tps_span_v
        return bytes([_encode_linear(volts, 255, 0.0, 5.0)])
    if sym == "CalculatedSparkAdvance" or channel.channel_name == "spark_deg_btdc":
        return bytes([_clamp_int(round(val / 0.5), 0, 255)])
    if channel.function_set:
        target = _c_to_f(val) if (sym == "ChargeTemp" or channel.units.lower() == "degf") else val
        volts = _interp_1d([(float(p["value"]), float(p["volts"])) for p in channel.function_set], target)
        return bytes([_encode_linear(volts, 255, 0.0, 5.0)])
    if sym == "ChargeTemp" or channel.channel_name == "act_c":
        f0, f1 = _charge_temp_bounds_f(channel.settings)
        return bytes([_encode_linear(_c_to_f(val), 255, f0, f1)])
    if (bounds := _linear_channel_bounds(channel)) is not None:
        lo, hi, is_degf = bounds
        raw_num = _encode_linear(_c_to_f(val) if is_degf else val, 0xFFFF if channel.is_16bit else 255, lo, hi)
        return bytes([(raw_num >> 8) & 0xFF, raw_num & 0xFF]) if channel.is_16bit else bytes([raw_num])
    if channel.is_16bit:
        raw16 = _clamp_int(round(val), 0, 0xFFFF)
        return bytes([(raw16 >> 8) & 0xFF, raw16 & 0xFF])
    return bytes([_clamp_int(round(val), 0, 255)])


def _map_max_psi(map_sensor_bar: int) -> float:
    if map_sensor_bar not in _MAP_BAR_MAX_PSI:
        raise ValueError(f"Unsupported map_sensor_bar={map_sensor_bar!r}; expected 1, 2, or 3")
    return _MAP_BAR_MAX_PSI[map_sensor_bar]


def build_telemetry_sample(
    ts_utc_ns: int, channels: Sequence[WatchedChannel], cell_values: Mapping[tuple[int, int], int | None], *,
    quarantined_keys: Iterable[tuple[int, int]] | None = None, baro_kpa: float = STANDARD_BARO_KPA,
    map_sensor_bar: int = 2, tps_zero_v: float = DEFAULT_TPS_ZERO_V, tps_span_v: float = DEFAULT_TPS_SPAN_V,
    wideband_afr: bool = True, dead_cell_quarantine: bool = False, quarantine_reason: str | None = None,
) -> TelemetrySample:
    """Build a canonical ``TelemetrySample`` preserving explicit ``None`` (``null != 0``) on unpolled/quarantined cells."""
    q_set = set(quarantined_keys or ())
    fields: dict[str, Any] = {}

    def _read_cell(fx: int, offset: int) -> int | None:
        key = (fx, offset)
        if key in q_set:
            return None
        val = cell_values.get(key)
        return None if val is None else _validate_byte(val, f"cell_values[{key!r}]")

    for ch in channels:
        hb = _read_cell(ch.fx_byte, ch.high_offset)
        if hb is None:
            continue
        if ch.low_offset is not None:
            lb = _read_cell(ch.fx_byte, ch.low_offset)
            if lb is None:
                continue
            if ch.symbol == "EngineRpm_HB" or ch.channel_name == "rpm":
                fields["raw_rpm_b"], fields["rpm"] = hb, float(decode_channel_value(ch, (hb, lb)))
            elif ch.symbol == "InjectorPulsewidth_HB" or ch.channel_name == "pw_ms":
                fields["raw_pw_u16"], fields["pw_ms"] = (hb << 8) | lb, float(decode_channel_value(ch, (hb, lb)))
            continue
        sym = ch.symbol
        if sym == "MapValue" or ch.channel_name == "map_kpa":
            fields["raw_map_b"] = hb
            fields["map_kpa"], fields["boost_psi"] = _decode_map_pair(hb, baro_kpa=baro_kpa, map_sensor_bar=map_sensor_bar)
        elif sym == "TpsVolts" or ch.channel_name == "tps_pct":
            fields["raw_tps_b"] = hb
            fields["tps_pct"] = float(decode_channel_value(ch, hb, tps_zero_v=tps_zero_v, tps_span_v=tps_span_v))
        elif (simple := _SIMPLE_SAMPLE_FIELDS.get(sym) or _SIMPLE_SAMPLE_FIELDS.get(ch.channel_name)) is not None:
            fields[simple[0]], fields[simple[1]] = hb, float(decode_channel_value(ch, hb))
        elif sym == "RawAirFuelSensInput":
            fields["raw_o2_b"] = hb
            fields["afr"] = decode_channel_value(ch, hb, wideband_afr=wideband_afr)
        elif sym in ("O2SensorVolts", "O2SensorValue"):
            fields.setdefault("raw_o2_b", hb)
        elif sym == "KnockVolts":
            fields["raw_knock_b"] = hb
        elif ch.channel_name == "knock_counts" or "knockcount" in sym.lower():
            fields["knock_counts"] = hb

    return TelemetrySample(
        ts_utc_ns=int(ts_utc_ns), dead_cell_quarantine=bool(dead_cell_quarantine),
        quarantine_reason=quarantine_reason, **fields,
    )
