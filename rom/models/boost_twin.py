"""Bit-exact 68HC11 Python Behavioral Twin for Chrysler wastegate and fuel-cut limiters."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rom.models.fuel_twin import (
    encode_4byte_table,
    kpa_to_raw_map,
    lookup_4byte_table,
    u8,
)

REV_LIMIT_MAX_RPM = 8160
REV_LIMIT_RPM_PER_BIT = 32
WG_MAX_DUTY_U8 = 0xC8
MAP_BAR_OVERBOOST_CEILING_KPA: dict[int, float] = {
    1: 105.0,
    2: 205.0,
    3: 315.0,
}

_DEFAULT_OVERBOOST_CUT_KPA: dict[int, float] = {
    1: 102.0,
    2: 200.0,
    3: 285.0,
}


def validate_rev_limit_rpm(rev_limit_rpm: int) -> int:
    """Validate rev limit RPM in 1000..8160 and return uint8 threshold (RPM // 32)."""
    if isinstance(rev_limit_rpm, bool) or not isinstance(rev_limit_rpm, int):
        raise ValueError(f"rev_limit_rpm must be an int, got {rev_limit_rpm!r}")
    if not (1000 <= rev_limit_rpm <= REV_LIMIT_MAX_RPM):
        raise ValueError(
            f"rev_limit_rpm must be in 1000..{REV_LIMIT_MAX_RPM}, got {rev_limit_rpm}"
        )
    return rev_limit_rpm // REV_LIMIT_RPM_PER_BIT


def validate_overboost_cut_kpa(overboost_kpa: float, map_sensor_bar: int) -> float:
    """Validate overboost fuel-cut threshold in kPa against the MAP sensor bar ceiling."""
    if (
        isinstance(map_sensor_bar, bool)
        or not isinstance(map_sensor_bar, int)
        or map_sensor_bar not in MAP_BAR_OVERBOOST_CEILING_KPA
    ):
        raise ValueError(f"map_sensor_bar must be 1, 2, or 3, got {map_sensor_bar!r}")
    if isinstance(overboost_kpa, bool) or not isinstance(overboost_kpa, (int, float)):
        raise ValueError(f"overboost_kpa must be a finite number, got {overboost_kpa!r}")
    val = float(overboost_kpa)
    ceiling = MAP_BAR_OVERBOOST_CEILING_KPA[map_sensor_bar]
    if not math.isfinite(val) or not (0.0 < val <= ceiling):
        raise ValueError(
            f"overboost_kpa must be in (0, {ceiling}] for {map_sensor_bar}-bar MAP, got {overboost_kpa!r}"
        )
    return val


@dataclass(frozen=True)
class BoostCalibration:
    """Calibration tables and fuel-cut limits for wastegate and limiter evaluation."""

    wg_duty_table: bytes
    map_sensor_bar: int
    rev_limit_rpm: int
    rev_limit_u8: int
    overboost_cut_kpa: float
    overboost_cut_u8: int
    knock_wg_reduce_u8: int = 0x18
    max_wg_duty_u8: int = WG_MAX_DUTY_U8


@dataclass(frozen=True)
class BoostEvalResult:
    """Wastegate duty cycle and overboost/rev-limit fuel-cut flags."""

    wg_duty_u8: int
    wg_duty_pct: float
    boost_delta_u8: int
    overboost_cut: bool
    rev_limit_cut: bool
    fuel_cut: bool
    cutout_flags_u8: int


def default_boost_calibration(
    map_sensor_bar: int = 2,
    *,
    rev_limit_rpm: int = 6500,
    overboost_cut_kpa: float | None = None,
) -> BoostCalibration:
    """Build a default BoostCalibration validated for the given MAP sensor bar rating."""
    if (
        isinstance(map_sensor_bar, bool)
        or not isinstance(map_sensor_bar, int)
        or map_sensor_bar not in _DEFAULT_OVERBOOST_CUT_KPA
    ):
        raise ValueError(f"map_sensor_bar must be 1, 2, or 3, got {map_sensor_bar!r}")

    rev_limit_u8 = validate_rev_limit_rpm(rev_limit_rpm)
    target_cut_kpa = (
        _DEFAULT_OVERBOOST_CUT_KPA[map_sensor_bar]
        if overboost_cut_kpa is None
        else overboost_cut_kpa
    )
    validated_cut_kpa = validate_overboost_cut_kpa(target_cut_kpa, map_sensor_bar)
    overboost_cut_u8 = u8(kpa_to_raw_map(validated_cut_kpa, map_sensor_bar))

    if map_sensor_bar == 1:
        wg_points = [(0x20, 0x00), (0x60, 0x00), (0xA0, 0x00), (0xE0, 0x00)]
    else:
        wg_points = [(0x20, 0x20), (0x40, 0x64), (0x70, 0xAA), (0xC0, WG_MAX_DUTY_U8)]
    wg_duty_table = encode_4byte_table(wg_points)

    return BoostCalibration(
        wg_duty_table=wg_duty_table,
        map_sensor_bar=map_sensor_bar,
        rev_limit_rpm=rev_limit_rpm,
        rev_limit_u8=rev_limit_u8,
        overboost_cut_kpa=validated_cut_kpa,
        overboost_cut_u8=overboost_cut_u8,
    )


def boost_calibration_from_profile(profile: Mapping[str, Any]) -> BoostCalibration:
    """Extract and validate BoostCalibration parameters from a vehicle profile."""
    ecu = profile.get("ecu_and_sensors")
    if not isinstance(ecu, Mapping):
        raise ValueError("profile missing 'ecu_and_sensors' mapping")
    for required_key in ("map_sensor_bar", "rev_limit_rpm", "overboost_fuel_cut_kpa"):
        if required_key not in ecu:
            raise ValueError(f"ecu_and_sensors missing required key {required_key!r}")

    return default_boost_calibration(
        ecu["map_sensor_bar"],
        rev_limit_rpm=ecu["rev_limit_rpm"],
        overboost_cut_kpa=ecu["overboost_fuel_cut_kpa"],
    )


def calc_wastegate_and_limiters(
    rpm_hb_u8: int,
    map_raw_u8: int,
    baro_raw_u8: int,
    knock_retard_u8: int,
    cal: BoostCalibration,
) -> BoostEvalResult:
    """Evaluate wastegate solenoid duty and overboost/rev-limit fuel-cut conditions."""
    rpm = u8(rpm_hb_u8)
    map_raw = u8(map_raw_u8)
    baro_raw = u8(baro_raw_u8)
    knock_retard = u8(knock_retard_u8)

    boost_delta_u8 = max(0, map_raw - baro_raw)
    wg = lookup_4byte_table(rpm, cal.wg_duty_table)
    if knock_retard > 0:
        wg = max(0, wg - u8(cal.knock_wg_reduce_u8))
    wg = min(wg, u8(cal.max_wg_duty_u8))

    overboost_cut = map_raw >= u8(cal.overboost_cut_u8)
    rev_limit_cut = rpm >= u8(cal.rev_limit_u8)
    fuel_cut = overboost_cut or rev_limit_cut
    flags = (
        (0x01 if overboost_cut else 0)
        | (0x02 if rev_limit_cut else 0)
        | (0x04 if fuel_cut else 0)
    )

    return BoostEvalResult(
        wg_duty_u8=wg,
        wg_duty_pct=round(wg * 100.0 / 200.0, 2),
        boost_delta_u8=boost_delta_u8,
        overboost_cut=overboost_cut,
        rev_limit_cut=rev_limit_cut,
        fuel_cut=fuel_cut,
        cutout_flags_u8=flags,
    )
