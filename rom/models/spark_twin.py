"""Bit-exact 68HC11 Python Behavioral Twin for Chrysler SMEC/SBEC spark and dwell."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rom.models.fuel_twin import (
    encode_4byte_table,
    encode_5byte_table,
    kpa_to_raw_map,
    lookup_4byte_table,
    lookup_5byte_table,
    u8,
    u16,
)


@dataclass(frozen=True)
class SparkCalibration:
    """Calibration tables and safety limits for fixed-point spark and dwell evaluation."""

    base_spk_table: bytes
    map_retard_table: bytes
    dltknk_table: bytes
    retmax_table: bytes
    spklmt_table: bytes
    dwell_table: bytes
    knklim_max_u8: int = 0x14
    min_adwell_ticks: int = 0x0080
    max_safe_advance_u8: int = 0x7F


@dataclass(frozen=True)
class SparkEvalResult:
    """Outputs and diagnostic clamp flags from a single spark/dwell evaluation."""

    spark_u8: int
    spark_deg_btdc: float
    base_adv_u8: int
    map_retard_u8: int
    knock_retard_u8: int
    spklmt_u8: int
    underflow_clamped: bool
    wrap_detected: bool
    dwell_ticks_u16: int


_MAP_RETARD_KPA_CURVES: dict[int, tuple[tuple[float, int], ...]] = {
    1: ((40.0, 0), (65.0, 2), (85.0, 6), (105.0, 8)),
    2: ((100.0, 0), (135.0, 8), (170.0, 20), (200.0, 32)),
    3: ((100.0, 0), (160.0, 10), (225.0, 22), (290.0, 32)),
}

_RETMAX_KPA_CURVES: dict[int, tuple[tuple[float, int], ...]] = {
    1: ((40.0, 0x04), (65.0, 0x06), (85.0, 0x0A), (105.0, 0x0C)),
    2: ((80.0, 0x04), (130.0, 0x08), (170.0, 0x0C), (200.0, 0x10)),
    3: ((80.0, 0x04), (150.0, 0x08), (220.0, 0x0C), (290.0, 0x10)),
}


def _encode_map_curve(
    kpa_points: tuple[tuple[float, int], ...],
    map_sensor_bar: int,
) -> bytes:
    """Convert (kPa, uint8_val) breakpoints into a strictly monotonic 4-byte table."""
    encoded_points: list[tuple[int, int]] = []
    prev_x = -1
    for kpa, value in kpa_points:
        raw_x = max(prev_x + 1, u8(kpa_to_raw_map(kpa, map_sensor_bar)))
        raw_x = min(raw_x, 0xFF)
        encoded_points.append((raw_x, u8(value)))
        prev_x = raw_x
    return encode_4byte_table(encoded_points)


def _build_spark_calibration(map_sensor_bar: int) -> SparkCalibration:
    """Build a realistic SparkCalibration scaled for 1/2/3-bar MAP sensors."""
    if isinstance(map_sensor_bar, bool) or not isinstance(map_sensor_bar, int) or map_sensor_bar not in {1, 2, 3}:
        raise ValueError(f"map_sensor_bar must be 1, 2, or 3, got {map_sensor_bar!r}")

    base_spk_table = encode_4byte_table([
        (0x20, 36),
        (0x40, 48),
        (0x64, 64),
        (0x96, 76),
        (0xC8, 80),
    ])
    map_retard_table = _encode_map_curve(_MAP_RETARD_KPA_CURVES[map_sensor_bar], map_sensor_bar)
    dltknk_table = encode_4byte_table([
        (0x20, 0x80),
        (0x60, 0x90),
        (0xA0, 0xA0),
        (0xE0, 0xB0),
    ])
    retmax_table = _encode_map_curve(_RETMAX_KPA_CURVES[map_sensor_bar], map_sensor_bar)
    spklmt_table = encode_4byte_table([
        (0x20, 0x50),
        (0x50, 0x60),
        (0x90, 0x70),
        (0xD0, 0x78),
    ])
    # 1's-complemented 5-byte dwell curve over ~8V..15V so table slope stays non-negative.
    dwell_table = encode_5byte_table([
        (0x7E, (~5000) & 0xFFFF),
        (0x9E, (~3800) & 0xFFFF),
        (0xBE, (~3000) & 0xFFFF),
        (0xD8, (~2500) & 0xFFFF),
        (0xEB, (~2100) & 0xFFFF),
    ])
    return SparkCalibration(
        base_spk_table=base_spk_table,
        map_retard_table=map_retard_table,
        dltknk_table=dltknk_table,
        retmax_table=retmax_table,
        spklmt_table=spklmt_table,
        dwell_table=dwell_table,
    )


def default_spark_calibration() -> SparkCalibration:
    """Return the default 2-bar SMEC/SBEC spark and dwell calibration."""
    return _build_spark_calibration(map_sensor_bar=2)


def spark_calibration_from_profile(profile: Mapping[str, Any]) -> SparkCalibration:
    """Construct a SparkCalibration adapted to a vehicle profile's MAP sensor bar rating."""
    ecu = profile.get("ecu_and_sensors")
    if not isinstance(ecu, Mapping):
        raise ValueError("profile missing 'ecu_and_sensors' mapping")
    map_sensor_bar = ecu.get("map_sensor_bar", 2)
    return _build_spark_calibration(map_sensor_bar=map_sensor_bar)


def check_unclamped_spark_wrap(base_u8: int, retard_u8: int) -> bool:
    """Return True if unsigned 8-bit subtraction (base_u8 - retard_u8) would borrow/wrap."""
    return (base_u8 & 0xFF) < (retard_u8 & 0xFF)


def calc_dwell_ticks(batt_raw_u8: int, cal: SparkCalibration) -> int:
    """Look up 1's-complemented battery dwell table and return uint16 dwell ticks."""
    comp = lookup_5byte_table(u8(batt_raw_u8), cal.dwell_table)
    return (~comp) & 0xFFFF


def calc_anti_dwell_ticks(batt_raw_u8: int, dist_pw_u16: int, cal: SparkCalibration) -> int:
    """Compute anti-dwell timer ticks enforcing minimum coil recovery time."""
    dwell = calc_dwell_ticks(batt_raw_u8, cal)
    dist_pw = u16(dist_pw_u16)
    min_adwell = u16(cal.min_adwell_ticks)
    if (dwell + min_adwell) < dist_pw:
        return u16(dist_pw - dwell)
    return min_adwell


def calc_spark_advance(
    rpm_hb_u8: int,
    map_raw_u8: int,
    knock_raw_u8: int,
    cal: SparkCalibration,
    *,
    batt_raw_u8: int = 0xD8,
) -> SparkEvalResult:
    """Evaluate 68HC11 fixed-point spark advance (0.5 deg/bit BTDC) and ignition dwell."""
    rpm = u8(rpm_hb_u8)
    map_raw = u8(map_raw_u8)
    knock_raw = u8(knock_raw_u8)

    base_adv = lookup_4byte_table(rpm, cal.base_spk_table)
    map_ret = lookup_4byte_table(map_raw, cal.map_retard_table)

    if base_adv < map_ret:
        after_map = 0
        underflow = True
    else:
        after_map = base_adv - map_ret
        underflow = False

    knk_thr = lookup_4byte_table(rpm, cal.dltknk_table)
    if knock_raw > knk_thr:
        knk_ret = min(lookup_4byte_table(map_raw, cal.retmax_table), u8(cal.knklim_max_u8))
    else:
        knk_ret = 0

    if after_map < knk_ret:
        after_knk = 0
        underflow = True
    else:
        after_knk = after_map - knk_ret

    spklmt = min(lookup_4byte_table(rpm, cal.spklmt_table), u8(cal.max_safe_advance_u8))
    final_u8 = after_knk if after_knk <= spklmt else spklmt
    dwell_ticks = calc_dwell_ticks(batt_raw_u8, cal)

    return SparkEvalResult(
        spark_u8=final_u8,
        spark_deg_btdc=final_u8 * 0.5,
        base_adv_u8=base_adv,
        map_retard_u8=map_ret,
        knock_retard_u8=knk_ret,
        spklmt_u8=spklmt,
        underflow_clamped=underflow,
        wrap_detected=underflow,
        dwell_ticks_u16=dwell_ticks,
    )
