"""Single-drive / fixture deterministic 68HC11 + Python Twin telemetry replay engine."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import csv
from dataclasses import asdict, dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any
import uuid

import pyarrow.parquet as pq

_REPO_ROOT = Path(__file__).resolve().parents[3]
if importlib.util.find_spec("rom") is None and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from logicaide.sim.assembler import AssembledRom, assemble_tree  # noqa: E402
from logicaide.sim.hc11_peripherals import HC11_CLOCK_HZ, MAX_MAIN_LOOP_CYCLES, MAX_MAIN_LOOP_MS, validate_rom_vectors  # noqa: E402
from logicaide.sim.hc11_runner import execute_ram_case  # noqa: E402
from logicaide.sim.splay import build_candidate_rom_bytes, extract_calibrations_from_rom  # noqa: E402
from logicaide.telemetry.fixtures import _is_gps_column  # noqa: E402
from logicaide.telemetry.log_parsers import FORBIDDEN_GPS_COLUMNS, PSI_TO_KPA, STANDARD_BARO_KPA, parse_telemetry_file  # noqa: E402
from logicaide.telemetry.parquet_chunker import write_session_parquet_chunks  # noqa: E402
from logicaide.telemetry.sci_loopback import OperatingPoint, default_chrysler_turbo_cycle  # noqa: E402
from rom.models import BATT_MAX_V, PW_TICK_MS, calc_fuel_pw, calc_spark_advance, calc_wastegate_and_limiters, kpa_to_raw_map  # noqa: E402

RUN_KIND_SINGLE_REPLAY = "SINGLE_REPLAY"
_ROM_SIZE_BYTES, _BASE_ADDRESS, _DEFAULT_BATT_V = 32768, 0x8000, 14.1
_BASE_TS_UTC_NS, _STEP_TS_NS = 1_700_000_000_000_000_000, 100_000_000
_VALID_ECUS = frozenset({"LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"})
_NULL_KEYS = (
    "rpm_hb", "map_raw", "baro_raw", "batt_raw", "knock_raw", "twin_pw_u16", "hc11_pw_u16", "pw_ms", "twin_spark_u8",
    "hc11_spark_u8", "spark_deg_btdc", "twin_wg_u8", "hc11_wg_u8", "twin_cutout_u8", "hc11_cutout_u8", "twin_dwell_u16", "hc11_dwell_u16", "loop_cycles", "loop_ms", "max_lsb_diff",
)
_NULL_FRAME_OUTPUTS: dict[str, None] = dict.fromkeys(_NULL_KEYS)


@dataclass(frozen=True)
class SingleReplaySummary:
    """Aggregate parity, vector, timing, and lake metrics for a single telemetry replay."""

    ok: bool
    session_id: str
    car_id: str
    build_id: str
    run_kind: str
    rom_sha256: str
    frame_count: int
    active_frames: int
    quarantined_frames: int
    parity_ok: bool
    max_lsb_diff: int
    vectors_ok: bool
    loop_budget_ok: bool
    max_loop_cycles: int
    max_loop_ms: float
    parquet_chunks: list[str]
    db_path: str
    lake_dir: str
    frames: list[dict[str, Any]]
    violations: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        """Serialize single replay summary into a JSON-ready dictionary."""
        return asdict(self)


def _assert_no_gps_keys(keys: Iterable[str], source_label: str) -> None:
    """Reject any forbidden GPS column or key name (`FORBIDDEN_GPS_COLUMNS` or `gps_*`)."""
    for raw_key in keys:
        key = str(raw_key)
        if key.strip().lower() in FORBIDDEN_GPS_COLUMNS or _is_gps_column(key):
            raise ValueError(f"Forbidden GPS column/key in {source_label}: {key!r}")


def _opt_num(val: Any, *, as_int: bool = False) -> Any:
    if val is None or val == "" or isinstance(val, bool):
        return None
    if as_int and isinstance(val, int):
        return val
    if as_int and isinstance(val, str):
        try:
            return int(val.strip())
        except ValueError:
            pass
    fval = float(val)
    return (int(round(fval)) if as_int else fval) if math.isfinite(fval) else None


def _clamp_u8(val: float) -> int:
    return min(255, max(0, round(val)))


def _parse_knock_counts(val: Any) -> int | None:
    """Parse optional knock_counts into 0..0xFFFF; reject bool, negative, and non-finite inputs."""
    if val is None:
        return None
    if isinstance(val, bool):
        raise ValueError(f"knock_counts must not be a boolean, got {val!r}")
    if isinstance(val, str):
        if not (val := val.strip()):
            return None
        try:
            val = int(val)
        except ValueError:
            pass
    if isinstance(val, int):
        if val < 0:
            raise ValueError(f"knock_counts must be non-negative, got {val!r}")
        return min(0xFFFF, val)
    fval = float(val)
    if not math.isfinite(fval) or fval < 0:
        raise ValueError(f"knock_counts must be a non-negative finite number, got {val!r}")
    return min(0xFFFF, int(round(fval)))


def _load_profile_and_meta(
    profile_path: Path | str | None, profile: Mapping[str, Any] | None, car_id: str | None, fix_car_id: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    p: dict[str, Any] | None = dict(profile) if profile is not None else None
    if p is None and profile_path is not None:
        raw = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Profile {profile_path} must decode to a JSON object.")
        p = raw
    src = p or {}
    ident, bottom = src.get("identity_and_chassis") or {}, src.get("engine_bottom_end") or {}
    head, trans = src.get("cylinder_head_and_cam") or {}, src.get("transaxle_and_gearing") or {}
    turbo, ecu = src.get("turbo_and_intercooler") or {}, src.get("ecu_and_sensors") or {}
    yr, raw_ecu, bar = ident.get("year"), str(ecu.get("ecu_family") or "SBEC_SINGLE_BOARD"), ecu.get("map_sensor_bar")
    block, hcode = bottom.get("block_code"), head.get("head_code")
    return p, {
        "car_id": str(car_id or ident.get("car_id") or fix_car_id or "stock_sim"),
        "year": yr if isinstance(yr, int) and not isinstance(yr, bool) and 1984 <= yr <= 1995 else 1991,
        "model": str(ident.get("model") or "SMEC/SBEC Sim"), "ecu_family": raw_ecu if raw_ecu in _VALID_ECUS else "SBEC_SINGLE_BOARD",
        "engine_profile_id": f"{block}+{hcode}" if block and hcode else str(block or "2.5L_K+8V_782_SWIRL"),
        "transaxle_code": str(trans.get("transaxle_code") or "A555"), "turbo_spec_id": turbo.get("turbo_id"),
        "pinout_verified": bool(ecu.get("pin11_continuity_verified") is True and ecu.get("pin2_3_25_45_verified") is True),
        "map_sensor_bar": int(bar) if isinstance(bar, int) and not isinstance(bar, bool) else 2,
    }


def _resolve_replay_rom(
    *, assembled: AssembledRom | None, rom_path: Path | str | None, asm_path: Path | str,
    profile_path: Path | str | None, profile_dict: dict[str, Any] | None,
    rev_limit_rpm: int | None, overboost_cut_kpa: float | None,
) -> AssembledRom:
    if assembled is not None:
        return assembled
    asm_entry = Path(asm_path)
    if not asm_entry.exists() and asm_entry == Path("rom/src/main.asm") and (_REPO_ROOT / "rom" / "src" / "main.asm").is_file():
        asm_entry = _REPO_ROOT / "rom" / "src" / "main.asm"
    if rom_path is not None:
        bin_path = Path(rom_path)
        if not bin_path.is_file():
            raise FileNotFoundError(f"ROM binary not found: {bin_path}")
        raw = bin_path.read_bytes()
        if len(raw) != _ROM_SIZE_BYTES:
            raise ValueError(f"Expected {_ROM_SIZE_BYTES}-byte ROM binary, got {len(raw)} bytes ({bin_path})")
        ref = assemble_tree(asm_entry) if asm_entry.is_file() else None
        return AssembledRom(
            rom_bytes=raw, base_address=_BASE_ADDRESS, rom_size_bytes=_ROM_SIZE_BYTES, sha256=hashlib.sha256(raw).hexdigest(),
            symbols=dict(ref.symbols) if ref else {}, written_ranges=((_BASE_ADDRESS, _BASE_ADDRESS + _ROM_SIZE_BYTES),),
            table_ranges=ref.table_ranges if ref else (), code_ranges=ref.code_ranges if ref else (), source_files=(str(bin_path),), backend="binary",
        )
    if not asm_entry.is_file():
        raise FileNotFoundError(f"Assembly entry file not found: {asm_entry}")
    prof_arg = profile_path if profile_path is not None else profile_dict
    if asm_entry.name != "main.asm" and prof_arg is None and rev_limit_rpm is None and overboost_cut_kpa is None:
        return assemble_tree(asm_entry)
    return build_candidate_rom_bytes(profile_or_path=prof_arg, cleanroom_src_dir=asm_entry.parent,
                                     rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa)[1]


def _load_csv_fixture(path: Path) -> tuple[list[dict[str, Any]], str | None, float | None]:
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
    if not lines:
        return [], None, None
    first = [c.strip() for c in next(csv.reader([lines[0]]))]
    _assert_no_gps_keys(first, str(path))
    is_lasky = first[0].lower().startswith("session:") or (first[0] == "time" and "LM1_O2" in first)
    for row in csv.reader(lines) if is_lasky else ():
        if row and row[0].strip() == "time":
            _assert_no_gps_keys([c.strip() for c in row], str(path))
    if is_lasky or first[0] in {"TimeStamp", "Date/Time"}:
        sessions = parse_telemetry_file(path, run_kind=RUN_KIND_SINGLE_REPLAY)
        fix_car = sessions[0].car_id if sessions and sessions[0].car_id != "CHRYSLER_UNKNOWN" else None
        return [asdict(s) for sess in sessions for s in sess.samples], fix_car, (sessions[0].baro_kpa if sessions else None)
    reader = csv.DictReader(lines)
    _assert_no_gps_keys(reader.fieldnames or (), str(path))
    return [dict(rec) for rec in reader], None, None


def _load_json_fixture(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        raw_items = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        if isinstance(payload, dict):
            _assert_no_gps_keys(payload.keys(), str(path))
            raw_items = next((payload[k] for k in ("rows", "frames", "operating_points") if isinstance(payload.get(k), list)), [payload])
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raise ValueError(f"JSON fixture {path} must decode to a list or object.")
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise ValueError(f"JSON fixture {path} entries must be objects, got {type(item).__name__}")
        _assert_no_gps_keys(item.keys(), str(path))
    return [dict(item) for item in raw_items]


def _load_replay_inputs(
    fixture_path: Path | str | None, operating_points: Sequence[OperatingPoint | Mapping[str, Any]] | None, max_frames: int,
) -> tuple[list[dict[str, Any]], str | None, float | None]:
    if operating_points is not None:
        rows = [item.to_dict() if isinstance(item, OperatingPoint) else dict(item) for item in operating_points]
        for row in rows:
            _assert_no_gps_keys(row.keys(), "operating_points")
        return rows[:max_frames], None, None
    if fixture_path is None:
        return [pt.to_dict() for pt in default_chrysler_turbo_cycle()][:max_frames], None, None
    path = Path(fixture_path)
    if not path.is_file():
        raise FileNotFoundError(f"Telemetry fixture not found: {path}")
    suffix, fix_car, fix_baro = path.suffix.lower(), None, None
    if suffix == ".parquet":
        table = pq.read_table(path)
        _assert_no_gps_keys(table.column_names, str(path))
        rows = table.to_pylist()
    elif suffix == ".csv":
        rows, fix_car, fix_baro = _load_csv_fixture(path)
    elif suffix in {".json", ".jsonl", ".ndjson"}:
        rows = _load_json_fixture(path)
    else:
        raise ValueError(f"Unsupported fixture extension {path.suffix!r}; expected .csv, .parquet, or .json")
    if fix_car is None and rows and rows[0].get("car_id"):
        fix_car = str(rows[0]["car_id"])
    return rows[:max_frames], fix_car, fix_baro


def _normalize_frame_fields(raw_row: Mapping[str, Any], *, default_baro_kpa: float) -> dict[str, Any]:
    raw_baro = _opt_num(raw_row.get("baro_kpa"))
    baro_kpa = default_baro_kpa if raw_baro is None else raw_baro
    rpm = _opt_num(raw_row["rpm"] if "rpm" in raw_row else raw_row.get("RPM"))
    map_kpa = _opt_num(raw_row["map_kpa"]) if "map_kpa" in raw_row else (
        baro_kpa + b_psi * PSI_TO_KPA if (b_psi := _opt_num(raw_row.get("MAP3BAR"))) is not None else None
    )
    cool_f, egt_val = _opt_num(raw_row.get("COOL")), _opt_num(raw_row.get("EGT"))
    cts_c = _opt_num(raw_row.get("cts_c"))
    cts_c = ((cool_f - 32.0) * 5.0 / 9.0) if (cts_c is None and cool_f is not None) else cts_c
    egt_rail, raw_q = (egt_val is not None and egt_val >= 2200.0), raw_row.get("dead_cell_quarantine")
    q_flag = (raw_q.strip().lower() in {"1", "true", "yes"} if isinstance(raw_q, str) else bool(raw_q)) or egt_rail
    q_reason = raw_row.get("quarantine_reason") or (
        "OPEN_THERMOCOUPLE_EGT_RAIL" if egt_rail else ("DEAD_CELL_QUARANTINE" if q_flag else ("MISSING_RPM_OR_MAP" if rpm is None or map_kpa is None else None))
    )
    return {
        "ts_utc_ns": _opt_num(raw_row.get("ts_utc_ns"), as_int=True), "quarantined": q_flag or rpm is None or map_kpa is None,
        "quarantine_reason": str(q_reason) if q_reason else None, "rpm": rpm, "map_kpa": map_kpa, "baro_kpa": baro_kpa,
        "batt_v": _opt_num(raw_row.get("batt_v")), "tps_pct": _opt_num(raw_row.get("tps_pct")),
        "act_c": _opt_num(raw_row.get("act_c")), "cts_c": cts_c,
        "afr": _opt_num(raw_row["afr"] if "afr" in raw_row else raw_row.get("AIR FUE")),
        "knock_raw": _opt_num(raw_row["knock_raw"] if "knock_raw" in raw_row else raw_row.get("raw_knock_b"), as_int=True),
        "knock_counts": _parse_knock_counts(raw_row.get("knock_counts")),
        "knock_v": _opt_num(raw_row["knock_v"] if "knock_v" in raw_row else raw_row.get("KNKV")),
        "fuel_trim_u8": _opt_num(raw_row.get("fuel_trim_u8"), as_int=True), "imu_grade_pct": _opt_num(raw_row.get("imu_grade_pct")),
        "imu_lon_accel_mps2": _opt_num(raw_row.get("imu_lon_accel_mps2")), "imu_lat_accel_mps2": _opt_num(raw_row.get("imu_lat_accel_mps2")),
    }


def _to_u8_inputs(norm: Mapping[str, Any], *, map_sensor_bar: int) -> dict[str, Any]:
    rpm, map_kpa, baro_kpa = float(norm["rpm"]), float(norm["map_kpa"]), float(norm["baro_kpa"])
    eff_batt = _DEFAULT_BATT_V if norm["batt_v"] is None else float(norm["batt_v"])
    knock_cnt = _parse_knock_counts(norm["knock_counts"])
    if norm["knock_raw"] is not None:
        knock_u8 = _clamp_u8(float(norm["knock_raw"]))
        knock_cnt = knock_u8 if knock_cnt is None else knock_cnt
    elif knock_cnt is not None:
        knock_u8 = _clamp_u8(float(knock_cnt))
    elif norm["knock_v"] is not None:
        knock_u8 = _clamp_u8((float(norm["knock_v"]) / 5.0) * 255.0)
    else:
        knock_u8 = 0
    return {
        "rpm_hb": _clamp_u8(rpm / 32.0), "map_raw": kpa_to_raw_map(map_kpa, map_sensor_bar, baro_kpa=baro_kpa),
        "baro_raw": kpa_to_raw_map(baro_kpa, map_sensor_bar, baro_kpa=baro_kpa), "batt_raw": _clamp_u8((eff_batt / BATT_MAX_V) * 255.0),
        "knock_raw": knock_u8, "knock_polled": any(norm[k] is not None for k in ("knock_raw", "knock_counts", "knock_v")),
        "knock_counts": knock_cnt, "tps_raw": None if norm["tps_pct"] is None else _clamp_u8((float(norm["tps_pct"]) / 100.0) * 255.0),
        "act_raw": None if norm["act_c"] is None else _clamp_u8(((float(norm["act_c"]) + 40.0) / 165.0) * 255.0),
        "cts_raw": None if norm["cts_c"] is None else _clamp_u8(((float(norm["cts_c"]) + 40.0) / 165.0) * 255.0),
    }


def _evaluate_frame(
    idx: int, ts_ns: int, norm: Mapping[str, Any], *, rom: AssembledRom, cals: tuple[Any, Any, Any],
    map_sensor_bar: int, use_adc: bool, max_cycles: int, session_id: str, car_id: str, build_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], int, float, int, bool]:
    rpm, map_kpa, baro_kpa = norm["rpm"], norm["map_kpa"], float(norm["baro_kpa"])
    pq_row: dict[str, Any] = {
        "ts_utc_ns": ts_ns, "session_id": session_id, "car_id": car_id, "build_id": build_id, "run_kind": RUN_KIND_SINGLE_REPLAY,
        "trial_idx": idx, "dead_cell_quarantine": bool(norm["quarantined"]), "rpm": rpm, "map_kpa": map_kpa,
        "boost_psi": ((map_kpa - baro_kpa) / PSI_TO_KPA) if map_kpa is not None else None,
        "tps_pct": norm["tps_pct"], "act_c": norm["act_c"], "cts_c": norm["cts_c"], "batt_v": norm["batt_v"], "afr": norm["afr"],
    }
    if norm["quarantined"]:
        return {"frame_idx": idx, "ts_utc_ns": ts_ns, "quarantined": True, "dead_cell_quarantine": True,
                "quarantine_reason": norm["quarantine_reason"], "rpm": rpm, "map_kpa": map_kpa, **_NULL_FRAME_OUTPUTS, "passed": True}, pq_row, [], 0, 0.0, 0, True

    fuel_cal, spark_cal, boost_cal = cals
    u8_in = _to_u8_inputs(norm, map_sensor_bar=map_sensor_bar)
    trim = fuel_cal.fuel_trim_u8 if norm["fuel_trim_u8"] is None else (int(norm["fuel_trim_u8"]) & 0xFF)
    f_res = calc_fuel_pw(u8_in["rpm_hb"], u8_in["map_raw"], u8_in["batt_raw"], fuel_cal, fuel_trim_u8=trim)
    s_res = calc_spark_advance(u8_in["rpm_hb"], u8_in["map_raw"], u8_in["knock_raw"], spark_cal, batt_raw_u8=u8_in["batt_raw"])
    b_res = calc_wastegate_and_limiters(u8_in["rpm_hb"], u8_in["map_raw"], u8_in["baro_raw"], s_res.knock_retard_u8, boost_cal)
    twin_pw, twin_wg = (0 if b_res.fuel_cut else f_res.pw_ticks_u16), (0 if b_res.fuel_cut else b_res.wg_duty_u8)
    hc11 = execute_ram_case(rom, rpm_hb=u8_in["rpm_hb"], map_raw=u8_in["map_raw"], baro_raw=u8_in["baro_raw"],
                            batt_raw=u8_in["batt_raw"], knock_raw=u8_in["knock_raw"], fuel_trim_u8=trim, use_adc=use_adc, max_cycles=max_cycles)
    h_pw, h_spk, h_wg, h_cut, h_dwl = (int(hc11[k]) for k in ("pw_ticks_u16", "spark_u8", "wg_duty_u8", "cutout_flags_u8", "dwell_ticks_u16"))
    pairs = (("pw_ticks_u16", twin_pw, h_pw), ("spark_u8", s_res.spark_u8, h_spk), ("wg_duty_u8", twin_wg, h_wg),
             ("cutout_flags_u8", b_res.cutout_flags_u8, h_cut), ("dwell_ticks_u16", s_res.dwell_ticks_u16, h_dwl))
    lsb_diff, cycles, duration_ms = max(abs(t - h) for _, t, h in pairs), int(hc11["cycles"]), float(hc11["duration_ms"])
    loop_ok = bool(hc11["ok"]) and cycles <= max_cycles and (max_cycles != MAX_MAIN_LOOP_CYCLES or duration_ms <= MAX_MAIN_LOOP_MS)
    viols: list[dict[str, Any]] = []
    if not loop_ok:
        cat = "LOOP_BUDGET_EXCEEDED" if cycles > max_cycles else "HC11_EXECUTION_ERROR"
        viols.append({"frame_idx": idx, "category": cat, "severity": "CRITICAL", "rpm": float(rpm), "map_kpa": float(map_kpa),
                      "detail": f"HC11 cycles={cycles} (max={max_cycles}), err={hc11.get('error_code')}"})
    if lsb_diff != 0:
        bad = ", ".join(f"{name}:hc11={h}!=twin={t}" for name, t, h in pairs if t != h)
        viols.append({"frame_idx": idx, "category": "HC11_TWIN_MISMATCH", "severity": "CRITICAL", "rpm": float(rpm), "map_kpa": float(map_kpa),
                      "detail": f"0-LSB parity mismatch (max_lsb_diff={lsb_diff}): {bad}"})
    frame = {
        "frame_idx": idx, "ts_utc_ns": ts_ns, "quarantined": False, "dead_cell_quarantine": False, "quarantine_reason": None,
        "rpm": float(rpm), "map_kpa": float(map_kpa), "rpm_hb": u8_in["rpm_hb"], "map_raw": u8_in["map_raw"], "baro_raw": u8_in["baro_raw"],
        "batt_raw": u8_in["batt_raw"], "knock_raw": u8_in["knock_raw"], "twin_pw_u16": twin_pw, "hc11_pw_u16": h_pw, "pw_ms": float(h_pw * PW_TICK_MS),
        "twin_spark_u8": s_res.spark_u8, "hc11_spark_u8": h_spk, "spark_deg_btdc": float(h_spk * 0.5), "twin_wg_u8": twin_wg, "hc11_wg_u8": h_wg,
        "twin_cutout_u8": b_res.cutout_flags_u8, "hc11_cutout_u8": h_cut, "twin_dwell_u16": s_res.dwell_ticks_u16, "hc11_dwell_u16": h_dwl,
        "loop_cycles": cycles, "loop_ms": duration_ms, "max_lsb_diff": lsb_diff, "passed": loop_ok and lsb_diff == 0,
    }
    pq_row.update({
        "raw_rpm_b": u8_in["rpm_hb"], "raw_map_b": u8_in["map_raw"], "raw_tps_b": u8_in["tps_raw"], "raw_pw_u16": h_pw, "raw_spark_b": h_spk,
        "raw_act_b": u8_in["act_raw"], "raw_cts_b": u8_in["cts_raw"], "raw_batt_b": u8_in["batt_raw"] if norm["batt_v"] is not None else None,
        "raw_knock_b": u8_in["knock_raw"] if u8_in["knock_polled"] else None, "pw_ms": float(h_pw * PW_TICK_MS),
        "spark_deg_btdc": float(h_spk * 0.5), "knock_counts": u8_in["knock_counts"], "imu_grade_pct": norm["imu_grade_pct"],
        "imu_lon_accel_mps2": norm["imu_lon_accel_mps2"], "imu_lat_accel_mps2": norm["imu_lat_accel_mps2"],
    })
    return frame, pq_row, viols, cycles, duration_ms, lsb_diff, loop_ok


def run_single_replay(
    *, assembled: AssembledRom | None = None, profile_path: Path | str | None = None, profile: Mapping[str, Any] | None = None,
    rom_path: Path | str | None = None, asm_path: Path | str = Path("rom/src/main.asm"),
    fixture_path: Path | str | None = None, operating_points: Sequence[Mapping[str, Any]] | None = None,
    car_id: str | None = None, build_id: str = "STOCK", session_id: str | None = None,
    rev_limit_rpm: int | None = None, overboost_cut_kpa: float | None = None, use_adc: bool = False,
    max_cycles: int = MAX_MAIN_LOOP_CYCLES, max_frames: int = 250, lake_dir: Path | str = Path("data/drives"),
    db_path: Path | str = Path("data/drives/catalog.sqlite"), write_parquet: bool = True,
) -> SingleReplaySummary:
    """Replay fixture or cycle frames across Python Twins and m68hc11, writing SINGLE_REPLAY Parquet chunks."""
    for label, val in (("max_frames", max_frames), ("max_cycles", max_cycles)):
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(f"{label} must be a positive integer, got {val!r}")
    raw_rows, fix_car_id, fix_baro = _load_replay_inputs(fixture_path, operating_points, max_frames)
    profile_dict, meta = _load_profile_and_meta(profile_path, profile, car_id, fix_car_id)
    eff_car_id, eff_bar = meta["car_id"], meta["map_sensor_bar"]
    eff_baro = fix_baro if fix_baro is not None else STANDARD_BARO_KPA
    eff_session_id = session_id or f"replay_{eff_car_id}_{uuid.uuid4().hex[:8]}"
    rom = _resolve_replay_rom(assembled=assembled, rom_path=rom_path, asm_path=asm_path, profile_path=profile_path,
                              profile_dict=profile_dict, rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa)
    cals = extract_calibrations_from_rom(rom, map_sensor_bar=eff_bar, rev_limit_rpm=rev_limit_rpm, overboost_cut_kpa=overboost_cut_kpa)
    vec_res = validate_rom_vectors(rom)
    violations: list[dict[str, Any]] = [] if vec_res.ok else [{"frame_idx": None, "category": vec_res.error_code or "INVALID_VECTOR",
                                                                 "severity": "CRITICAL", "rpm": None, "map_kpa": None,
                                                                 "detail": f"Invalid ROM vectors: {vec_res.invalid_vectors}"}]
    frames, pq_rows, q_cells, active_cnt, q_cnt, max_cyc, max_diff, max_ms, loop_ok, prev_ts_ns = [], [], [], 0, 0, 0, 0, 0.0, True, None
    for idx, raw_row in enumerate(raw_rows):
        norm = _normalize_frame_fields(raw_row, default_baro_kpa=eff_baro)
        ts_ns = (_BASE_TS_UTC_NS + idx * _STEP_TS_NS) if norm["ts_utc_ns"] is None else norm["ts_utc_ns"]
        ts_ns = (prev_ts_ns + 1) if (prev_ts_ns is not None and ts_ns <= prev_ts_ns) else ts_ns
        prev_ts_ns = ts_ns
        frame, pq_row, f_viols, cyc, ms, diff, f_loop_ok = _evaluate_frame(
            idx, ts_ns, norm, rom=rom, cals=cals, map_sensor_bar=eff_bar, use_adc=use_adc,
            max_cycles=max_cycles, session_id=eff_session_id, car_id=eff_car_id, build_id=build_id,
        )
        if norm["quarantined"]:
            q_cnt += 1
            q_cells.append({"frame_idx": idx, "ts_utc_ns": ts_ns, "reason": norm["quarantine_reason"]})
        else:
            active_cnt += 1
            max_cyc, max_ms, max_diff = max(max_cyc, cyc), max(max_ms, ms), max(max_diff, diff)
            loop_ok = loop_ok and f_loop_ok
            violations.extend(f_viols)
        frames.append(frame)
        pq_rows.append(pq_row)
    parquet_chunks: list[str] = []
    if write_parquet and pq_rows:
        session_payload = {**meta, "session_id": eff_session_id, "build_id": build_id, "run_kind": RUN_KIND_SINGLE_REPLAY,
                           "rom_sha256": rom.sha256, "baro_kpa": eff_baro, "quarantined_cells": q_cells, "rows": pq_rows}
        pq_res = write_session_parquet_chunks(session_payload, lake_dir=lake_dir, db_path=db_path, chunk_seconds=30)
        parquet_chunks = [str(chunk["file_path"]) for chunk in pq_res.get("chunks", [])]
    parity_ok = max_diff == 0 and not any(v["category"] == "HC11_TWIN_MISMATCH" for v in violations)
    max_ms = max(max_ms, round((max_cyc / HC11_CLOCK_HZ) * 1000.0, 6) if max_cyc > 0 else 0.0)
    ok = bool(vec_res.ok) and parity_ok and loop_ok and (len(raw_rows) > 0) and (len(violations) == 0)
    return SingleReplaySummary(
        ok=ok, session_id=eff_session_id, car_id=eff_car_id, build_id=build_id, run_kind=RUN_KIND_SINGLE_REPLAY,
        rom_sha256=rom.sha256, frame_count=len(raw_rows), active_frames=active_cnt, quarantined_frames=q_cnt,
        parity_ok=parity_ok, max_lsb_diff=max_diff, vectors_ok=bool(vec_res.ok), loop_budget_ok=loop_ok,
        max_loop_cycles=max_cyc, max_loop_ms=max_ms, parquet_chunks=parquet_chunks,
        db_path=str(db_path), lake_dir=str(lake_dir), frames=frames, violations=violations,
    )
