"""Bit-exact Motorola 68HC11 Python Behavioral Twin for Chrysler SMEC/SBEC fueling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

MAP_MIN_PSI = -14.7
_MAP_BAR_MAX_PSI: dict[int, float] = {1: 0.0, 2: 14.7, 3: 29.4}
PSI_TO_KPA = 6.894757293168361
STANDARD_BARO_KPA = 101.325

DEFAULT_MIN_PW_TICKS = 0x0060
PW_TICK_MS = 0.004
BATOFF_TICK_MS = 0.016
BATT_MAX_V = 15.75
STOCK_INJECTOR_FLOW_LB_HR = 34.9

_MAP_ADC_DEFAULTS: dict[int, tuple[int, int]] = {1: (0x07, 0x0361), 2: (0x07, 0x0361), 3: (0x09, 0x0700)}
_DEFAULT_BATOFF_POINTS: tuple[tuple[int, int], ...] = ((0x59, 0x6A), (0x81, 0x3B), (0xC2, 0x1F), (0xFA, 0x13))
_DEFAULT_PEFTBL_POINTS: tuple[tuple[int, int], ...] = (
    (0x4B, 0xDE), (0x58, 0xDF), (0x64, 0xE6), (0x71, 0xE9), (0x86, 0xE9),
    (0x9C, 0xDC), (0xB2, 0xBB), (0xC2, 0xA6), (0xD7, 0xA6), (0xEA, 0xA6),
)
_DEFAULT_WOTTBL_POINTS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((0x00, 0x0000), (0x80, 0x0668), (0xFF, 0x0CC3)),
    2: ((0x00, 0x0000), (0x9D, 0x0FB9), (0xC6, 0x1376), (0xCE, 0x14C3), (0xFF, 0x194A)),
    3: ((0x00, 0x0000), (0x69, 0x0FB9), (0x84, 0x1376), (0x89, 0x14C3), (0xAA, 0x194A), (0xFF, 0x25EF)),
}


def u8(val: int) -> int:
    """Wrap integer to unsigned 8-bit value (0..255)."""
    return val & 0xFF


def u16(val: int) -> int:
    """Wrap integer to unsigned 16-bit value (0..65535)."""
    return val & 0xFFFF


def s8(val: int) -> int:
    """Interpret low 8 bits as signed two's-complement integer (-128..127)."""
    v = val & 0xFF
    return v - 0x100 if (v & 0x80) else v


def hc11_mul(a: int, b: int) -> tuple[int, bool]:
    """Execute 68HC11 MUL (A * B -> D), returning (d_u16, carry_bit7)."""
    prod = (a & 0xFF) * (b & 0xFF)
    return prod & 0xFFFF, bool(prod & 0x80)


def hc11_idiv(d: int, x: int) -> tuple[int, int, bool]:
    """Execute 68HC11 IDIV (D / X -> X, rem D), returning (x_q, d_rem, div0_carry)."""
    num, den = d & 0xFFFF, x & 0xFFFF
    if den == 0:
        return 0xFFFF, num, True
    return (num // den) & 0xFFFF, (num % den) & 0xFFFF, False


def hc11_fdiv(d: int, x: int) -> tuple[int, int, bool, bool]:
    """Execute 68HC11 FDIV ((D << 16) / X), returning (x_q, d_rem, overflow_v, div0_carry)."""
    num, den = d & 0xFFFF, x & 0xFFFF
    if den == 0:
        return 0xFFFF, num, True, True
    return ((num << 16) // den) & 0xFFFF, ((num << 16) % den) & 0xFFFF, num >= den, False


def scale_y_by_a(a_u8: int, y_u16: int) -> int:
    """Multiply uint16 Y by fractional uint8 A/256 with 68HC11 bit-7 rounding."""
    a, y = a_u8 & 0xFF, y_u16 & 0xFFFF
    hi_prod = a * (y >> 8)
    lo_prod = a * (y & 0xFF)
    c_round = 1 if (lo_prod & 0x80) else 0
    frac_adj = ((lo_prod >> 8) + c_round) & 0xFF
    return (hi_prod + frac_adj) & 0xFFFF


def scale_x_by_b_adding_a(a_flag: int, b_u8: int, val_u16: int) -> tuple[int, bool]:
    """Scale val_u16 by B/256, optionally add 1.0*val_u16 if a_flag != 0, saturating at 0xFFFF."""
    val = val_u16 & 0xFFFF
    scaled = scale_y_by_a(b_u8, val)
    if (a_flag & 0xFF) == 0:
        return scaled, False
    total = scaled + val
    return (0xFFFF, True) if total > 0xFFFF else (total, False)


def lookup_4byte_table(x_u8: int, table_bytes: Sequence[int]) -> int:
    """Evaluate 68HC11 4-byte piecewise-linear uint8 lookup table ([N, x, y, s_hi, s_lo, ...])."""
    if not table_bytes:
        raise ValueError("4-byte table cannot be empty")
    n = table_bytes[0] & 0xFF
    if n < 1:
        raise ValueError(f"4-byte table count must be >= 1, got {n}")
    min_len = 1 + 4 * (n - 1) + 2
    if len(table_bytes) < min_len:
        raise ValueError(f"4-byte table truncated: expected >= {min_len} bytes for N={n}, got {len(table_bytes)}")

    a = x_u8 & 0xFF
    x0, y0 = table_bytes[1] & 0xFF, table_bytes[2] & 0xFF
    if a <= x0:
        return y0

    seg, rem = 0, n
    while True:
        rem -= 1
        if rem == 0:
            return table_bytes[1 + 4 * seg + 1] & 0xFF
        if a < (table_bytes[1 + 4 * (seg + 1)] & 0xFF):
            break
        seg += 1

    base = 1 + 4 * seg
    delta = (a - (table_bytes[base] & 0xFF)) & 0xFF
    y_i = table_bytes[base + 1] & 0xFF
    hi_prod = delta * (table_bytes[base + 2] & 0xFF)
    lo_prod = delta * (table_bytes[base + 3] & 0xFF)
    frac_adj = ((lo_prod >> 8) + (1 if (lo_prod & 0x80) else 0)) & 0xFF
    y_sum = (hi_prod + frac_adj) & 0xFFFF
    return ((y_sum & 0xFF) + y_i) & 0xFF


def lookup_5byte_table(x_u8: int, table_bytes: Sequence[int]) -> int:
    """Evaluate 68HC11 5-byte piecewise-linear uint16 lookup table ([x, y_hi, y_lo, s_hi, s_lo, ...])."""
    n_bytes = len(table_bytes)
    if n_bytes < 5:
        raise ValueError(f"5-byte table requires at least 5 bytes, got {n_bytes}")

    a = x_u8 & 0xFF
    x0 = table_bytes[0] & 0xFF
    y0 = ((table_bytes[1] & 0xFF) << 8) | (table_bytes[2] & 0xFF)
    if a <= x0:
        return y0

    idx = 0
    while idx + 5 < n_bytes and a > (table_bytes[idx + 5] & 0xFF):
        if idx + 9 >= n_bytes:
            raise ValueError("5-byte table truncated before next record")
        idx += 5

    x_i = table_bytes[idx] & 0xFF
    y_i = ((table_bytes[idx + 1] & 0xFF) << 8) | (table_bytes[idx + 2] & 0xFF)
    slope_16 = ((table_bytes[idx + 3] & 0xFF) << 8) | (table_bytes[idx + 4] & 0xFF)
    delta = (a - x_i) & 0xFF
    return (scale_y_by_a(delta, slope_16) + y_i) & 0xFFFF


def _validate_table_points(
    points: Sequence[tuple[int, int]], *, max_points: int, max_y: int, non_decreasing: bool,
) -> list[tuple[int, int]]:
    if not (1 <= len(points) <= max_points):
        raise ValueError(f"Expected 1..{max_points} table points, got {len(points)}")
    validated: list[tuple[int, int]] = []
    prev_x = prev_y = -1
    for x, y in points:
        if isinstance(x, bool) or not isinstance(x, int) or not (0 <= x <= 0xFF):
            raise ValueError(f"Breakpoint x must be int in 0..255, got {x!r}")
        if isinstance(y, bool) or not isinstance(y, int) or not (0 <= y <= max_y):
            raise ValueError(f"Table value y must be int in 0..{max_y}, got {y!r}")
        if x <= prev_x:
            raise ValueError(f"Breakpoints x must be strictly ascending ({prev_x} >= {x})")
        if non_decreasing and y < prev_y:
            raise ValueError(f"5-byte table values must be non-decreasing ({y} < {prev_y})")
        validated.append((x, y))
        prev_x, prev_y = x, y
    return validated


def encode_4byte_table(points: Sequence[tuple[int, int]]) -> bytes:
    """Encode (x_u8, y_u8) breakpoints into a 68HC11 4-byte lookup table ([N] + 4*N body bytes)."""
    pts = _validate_table_points(points, max_points=255, max_y=0xFF, non_decreasing=False)
    out = bytearray([len(pts)])
    for idx, (x0, y0) in enumerate(pts):
        slope = (round(((pts[idx + 1][1] - y0) * 256) / (pts[idx + 1][0] - x0)) & 0xFFFF) if idx + 1 < len(pts) else 0
        out.extend((x0, y0, (slope >> 8) & 0xFF, slope & 0xFF))
    return bytes(out)


def encode_5byte_table(points: Sequence[tuple[int, int]]) -> bytes:
    """Encode non-decreasing (x_u8, y_u16) points into a 5-byte table with 0xFF sentinel."""
    pts = _validate_table_points(points, max_points=256, max_y=0xFFFF, non_decreasing=True)
    out = bytearray()
    for idx, (x0, y0) in enumerate(pts):
        if idx + 1 < len(pts):
            x1, y1 = pts[idx + 1]
            slope = round(((y1 - y0) * 256) / (x1 - x0))
            if slope > 0xFFFF:
                raise ValueError(f"Q8 slope {slope} exceeds uint16 max between x={x0} and x={x1}")
        else:
            slope = 0
        out.extend((x0, (y0 >> 8) & 0xFF, y0 & 0xFF, (slope >> 8) & 0xFF, slope & 0xFF))
    out.append(0xFF)
    return bytes(out)


def _validate_map_sensor_bar(map_sensor_bar: int) -> int:
    if isinstance(map_sensor_bar, bool) or not isinstance(map_sensor_bar, int) or map_sensor_bar not in _MAP_BAR_MAX_PSI:
        raise ValueError(f"Unsupported map_sensor_bar={map_sensor_bar!r}; expected 1, 2, or 3")
    return map_sensor_bar


def _require_finite(val: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
        raise ValueError(f"{name} must be finite, got {val!r}")
    fval = float(val)
    if positive and fval <= 0.0:
        raise ValueError(f"{name} must be > 0, got {val!r}")
    return fval


def kpa_to_raw_map(map_kpa: float, map_sensor_bar: int = 2, *, baro_kpa: float = STANDARD_BARO_KPA) -> int:
    """Convert absolute MAP (kPa) to clamped uint8 MapValue for 1/2/3-bar sensors."""
    bar = _validate_map_sensor_bar(map_sensor_bar)
    kpa_f = _require_finite(map_kpa, "map_kpa")
    baro_f = _require_finite(baro_kpa, "baro_kpa")
    boost_psi = (kpa_f - baro_f) / PSI_TO_KPA
    raw = round(((boost_psi - MAP_MIN_PSI) / (_MAP_BAR_MAX_PSI[bar] - MAP_MIN_PSI)) * 255.0)
    return 0 if raw < 0 else (255 if raw > 255 else raw)


def raw_map_to_kpa(raw_map: int, map_sensor_bar: int = 2, *, baro_kpa: float = STANDARD_BARO_KPA) -> float:
    """Convert uint8 MapValue to absolute MAP (kPa) for 1/2/3-bar sensors."""
    bar = _validate_map_sensor_bar(map_sensor_bar)
    if isinstance(raw_map, bool) or not isinstance(raw_map, int) or not (0 <= raw_map <= 0xFF):
        raise ValueError(f"raw_map must be an int in 0..255, got {raw_map!r}")
    baro_f = _require_finite(baro_kpa, "baro_kpa")
    boost_psi = MAP_MIN_PSI + (raw_map / 255.0) * (_MAP_BAR_MAX_PSI[bar] - MAP_MIN_PSI)
    return baro_f + boost_psi * PSI_TO_KPA


def scale_map_adc(adc_raw_u8: int, map_mult: int, map_add: int) -> tuple[int, bool]:
    """Scale raw MAP ADC byte into uint8 MapValue via ConvertMAPVoltsToValue with 0xFF clamp."""
    adc, mult, add_u16 = u8(adc_raw_u8), u8(map_mult), u16(map_add)
    temp5 = 0 if (mult & 0x80) else adc
    d_mul, _ = hc11_mul(adc, mult)
    hi, lo = (d_mul >> 8) & 0xFF, d_mul & 0xFF
    sum_hi = hi + temp5
    clipped = sum_hi > 0xFF
    if clipped:
        hi = 0xFF
    total = ((hi << 8) | lo) + add_u16
    return (0xFF, True) if total > 0xFFFF else ((total >> 8) & 0xFF, clipped)


@dataclass(frozen=True)
class FuelCalibration:
    """Fixed-point fuel tables and sensor constants for 68HC11 fuel evaluation."""

    batoff_table: bytes
    peftbl_table: bytes
    wottbl_table: bytes
    map_mult: int
    map_add: int
    map_sensor_bar: int
    min_pw_ticks: int = DEFAULT_MIN_PW_TICKS
    fuel_trim_u8: int = 0


@dataclass(frozen=True)
class FuelEvalResult:
    """Outputs and saturation/floor diagnostics from a single fuel PW evaluation."""

    pw_ticks_u16: int
    pw_ms: float
    base_pw_u16: int
    pef_u8: int
    batoff_u8: int
    latency_ticks_u16: int
    saturated_u16: bool
    floor_clamped: bool


def _build_wottbl(map_sensor_bar: int, effective_flow_lb_hr: float) -> bytes:
    scale = STOCK_INJECTOR_FLOW_LB_HR / effective_flow_lb_hr
    scaled_points: list[tuple[int, int]] = []
    prev_y = 0
    for x, base_y in _DEFAULT_WOTTBL_POINTS[map_sensor_bar]:
        y = max(prev_y, min(0xFFFF, round(base_y * scale)))
        scaled_points.append((x, y))
        prev_y = y
    return encode_5byte_table(scaled_points)


def _encode_batoff_from_curve(batoff: Mapping[str, Any]) -> bytes:
    raw_volts, raw_dts = batoff.get("voltage_points_v"), batoff.get("deadtime_ms")
    if not isinstance(raw_volts, Sequence) or isinstance(raw_volts, (str, bytes)):
        raise ValueError("batoff_deadtime_ms.voltage_points_v must be a sequence")
    if not isinstance(raw_dts, Sequence) or isinstance(raw_dts, (str, bytes)):
        raise ValueError("batoff_deadtime_ms.deadtime_ms must be a sequence")
    if len(raw_volts) < 2 or len(raw_volts) != len(raw_dts):
        raise ValueError("voltage_points_v and deadtime_ms must have equal length >= 2")

    volts = [_require_finite(v, "voltage_points_v") for v in raw_volts]
    deadtimes = [_require_finite(dt, "deadtime_ms", positive=True) for dt in raw_dts]
    if any(volts[idx] >= volts[idx + 1] for idx in range(len(volts) - 1)):
        raise ValueError("voltage_points_v must be strictly ascending")
    if any(deadtimes[idx] < deadtimes[idx + 1] for idx in range(len(deadtimes) - 1)):
        raise ValueError("deadtime_ms must be non-increasing")

    pts: list[tuple[int, int]] = []
    for idx, (v, dt) in enumerate(zip(volts, deadtimes)):
        eff_dt = dt
        if v > BATT_MAX_V and idx > 0 and volts[idx - 1] < BATT_MAX_V:
            eff_dt = deadtimes[idx - 1] + ((BATT_MAX_V - volts[idx - 1]) / (v - volts[idx - 1])) * (dt - deadtimes[idx - 1])
        x_u8 = max(0, min(0xFF, round((v / BATT_MAX_V) * 255.0)))
        y_u8 = max(0, min(0xFF, round(eff_dt / BATOFF_TICK_MS)))
        if pts and x_u8 <= pts[-1][0]:
            pts[-1] = (pts[-1][0], y_u8)
        else:
            pts.append((x_u8, y_u8))
    return encode_4byte_table(pts)


def default_fuel_calibration(map_sensor_bar: int = 2) -> FuelCalibration:
    """Return baseline 68HC11 fuel calibration for 1-bar, 2-bar, or 3-bar MAP sensors."""
    bar = _validate_map_sensor_bar(map_sensor_bar)
    map_mult, map_add = _MAP_ADC_DEFAULTS[bar]
    return FuelCalibration(
        batoff_table=encode_4byte_table(_DEFAULT_BATOFF_POINTS),
        peftbl_table=encode_4byte_table(_DEFAULT_PEFTBL_POINTS),
        wottbl_table=_build_wottbl(bar, STOCK_INJECTOR_FLOW_LB_HR),
        map_mult=map_mult,
        map_add=map_add,
        map_sensor_bar=bar,
    )


def fuel_calibration_from_profile(profile: Mapping[str, Any]) -> FuelCalibration:
    """Build a FuelCalibration from a LogicAIDE vehicle profile mapping."""
    fuel_sys, ecu = profile.get("fuel_system"), profile.get("ecu_and_sensors")
    if not isinstance(fuel_sys, Mapping) or not isinstance(ecu, Mapping):
        raise ValueError("profile must contain 'fuel_system' and 'ecu_and_sensors' mappings")
    bar = _validate_map_sensor_bar(ecu.get("map_sensor_bar", 2))
    eff_flow = _require_finite(fuel_sys.get("effective_flow_lb_hr"), "effective_flow_lb_hr", positive=True)
    batoff_cfg = fuel_sys.get("batoff_deadtime_ms")
    if not isinstance(batoff_cfg, Mapping):
        raise ValueError("fuel_system missing 'batoff_deadtime_ms' mapping")

    map_mult, map_add = _MAP_ADC_DEFAULTS[bar]
    return FuelCalibration(
        batoff_table=_encode_batoff_from_curve(batoff_cfg),
        peftbl_table=encode_4byte_table(_DEFAULT_PEFTBL_POINTS),
        wottbl_table=_build_wottbl(bar, eff_flow),
        map_mult=map_mult,
        map_add=map_add,
        map_sensor_bar=bar,
    )


def eval_batoff(batt_raw_u8: int, cal: FuelCalibration) -> int:
    """Evaluate BATOFF injector battery deadtime table (uint8, 16 us/bit)."""
    return lookup_4byte_table(u8(batt_raw_u8), cal.batoff_table)


def eval_peftbl(rpm_hb_u8: int, cal: FuelCalibration) -> int:
    """Evaluate PEFTBL volumetric pumping efficiency table (uint8) from EngineRpm_HB."""
    return lookup_4byte_table(u8(rpm_hb_u8), cal.peftbl_table)


def eval_wottbl(map_raw_u8: int, cal: FuelCalibration) -> int:
    """Evaluate WOTTBL full-throttle base fuel pulse width table (uint16, 4 us/tick)."""
    return lookup_5byte_table(u8(map_raw_u8), cal.wottbl_table)


def calc_fuel_pw(
    rpm_hb_u8: int, map_raw_u8: int, batt_raw_u8: int, cal: FuelCalibration, *, fuel_trim_u8: int | None = None,
) -> FuelEvalResult:
    """Evaluate 68HC11 fixed-point injector pulse width with PEF, trim, latency, and clamps."""
    base_pw = eval_wottbl(map_raw_u8, cal)
    pef = eval_peftbl(rpm_hb_u8, cal)
    scaled = scale_y_by_a(pef, base_pw) if pef != 0 else base_pw

    trim = (cal.fuel_trim_u8 & 0xFF) if fuel_trim_u8 is None else (fuel_trim_u8 & 0xFF)
    scaled, sat1 = scale_x_by_b_adding_a(1, trim, scaled) if trim != 0 else (scaled, False)

    batoff = eval_batoff(batt_raw_u8, cal)
    latency = (batoff << 2) & 0xFFFF
    total = scaled + latency
    if total > 0xFFFF:
        pw_ticks, sat2, floor_clamped = 0xFFFF, True, False
    elif total < cal.min_pw_ticks:
        pw_ticks, sat2, floor_clamped = cal.min_pw_ticks, False, True
    else:
        pw_ticks, sat2, floor_clamped = total, False, False

    return FuelEvalResult(
        pw_ticks_u16=pw_ticks, pw_ms=pw_ticks * PW_TICK_MS, base_pw_u16=base_pw, pef_u8=pef,
        batoff_u8=batoff, latency_ticks_u16=latency, saturated_u16=sat1 or sat2, floor_clamped=floor_clamped,
    )
