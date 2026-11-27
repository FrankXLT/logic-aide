"""Deterministic Monte Carlo (Sobol, LHS, Optuna) ROM calibration sweep & invariant verifier."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any
import uuid

import optuna
from scipy.stats import qmc

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rom.models import (  # noqa: E402
    BATT_MAX_V,
    PSI_TO_KPA,
    PW_TICK_MS,
    STANDARD_BARO_KPA,
    BoostCalibration,
    FuelCalibration,
    SparkCalibration,
    calc_fuel_pw,
    calc_spark_advance,
    calc_wastegate_and_limiters,
    kpa_to_raw_map,
)
from logicaide.catalog.sqlite_store import connect_catalog, upsert_vehicle  # noqa: E402
from logicaide.sim.assembler import AssembledRom  # noqa: E402
from logicaide.sim.hc11_runner import MAX_MAIN_LOOP_CYCLES, execute_ram_case  # noqa: E402
from logicaide.sim.splay import build_candidate_rom_bytes, extract_calibrations_from_rom  # noqa: E402
from logicaide.telemetry.parquet_chunker import write_session_parquet_chunks  # noqa: E402

MC_SAMPLERS: tuple[str, ...] = ("SOBOL", "LHS", "OPTUNA")
REV_LIMIT_CEILING_RPM: int = 8160
BAR_OVERBOOST_CEILINGS: dict[int, float] = {1: 105.0, 2: 205.0, 3: 315.0}
_MAP_SWEEP_MAX_KPA: dict[int, float] = {1: 120.0, 2: 225.0, 3: 330.0}
_BASE_TS_UTC_NS: int = 1_700_000_000_000_000_000
_STEP_TS_NS: int = 100_000_000


@dataclass(frozen=True)
class SweepSummary:
    """Summary metrics and safety violation findings from a Monte Carlo sweep."""

    ok: bool
    campaign_id: str
    car_id: str
    build_id: str
    sampler: str
    n_trials: int
    seed: int
    parquet_chunks: list[str]
    db_path: str
    lake_dir: str
    overflow_count: int
    wrap_count: int
    safety_clamp_events: int
    max_loop_cycles: int
    max_loop_ms: float
    max_rpm_seen: float
    max_map_kpa_seen: float
    violations: list[dict[str, Any]]
    issue_ids: list[str]
    best_objective: float | None

    def as_dict(self) -> dict[str, Any]:
        """Serialize sweep summary into a JSON-ready dictionary."""
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _viol(idx: int | None, cat: str, rpm: float | None, map_kpa: float | None, detail: str) -> dict[str, Any]:
    return {"trial_idx": idx, "category": cat, "severity": "CRITICAL", "rpm": rpm, "map_kpa": map_kpa, "detail": detail}


def _load_profile(profile_path: Path | str | None, profile: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if profile is not None:
        return dict(profile)
    if profile_path is None:
        return None
    raw = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Profile {profile_path} must decode to a JSON object.")
    return raw


def _extract_vehicle_meta(profile_dict: dict[str, Any] | None, map_sensor_bar: int) -> dict[str, Any]:
    p = profile_dict or {}
    ident, bottom = p.get("identity_and_chassis") or {}, p.get("engine_bottom_end") or {}
    head, trans = p.get("cylinder_head_and_cam") or {}, p.get("transaxle_and_gearing") or {}
    turbo, ecu = p.get("turbo_and_intercooler") or {}, p.get("ecu_and_sensors") or {}
    block, hcode = bottom.get("block_code"), head.get("head_code")
    raw_year = ident.get("year")
    db_year = raw_year if isinstance(raw_year, int) and not isinstance(raw_year, bool) and 1984 <= raw_year <= 1995 else 1991
    raw_ecu = str(ecu.get("ecu_family") or "SBEC_SINGLE_BOARD")
    db_ecu = raw_ecu if raw_ecu in ("LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD") else "SBEC_SINGLE_BOARD"
    raw_bar = ecu.get("map_sensor_bar")
    eff_bar = int(raw_bar) if isinstance(raw_bar, int) and not isinstance(raw_bar, bool) else map_sensor_bar
    return {
        "car_id": str(ident.get("car_id") or "stock_sim"), "year": db_year,
        "model": str(ident.get("model") or "SMEC/SBEC Sim"), "ecu_family": db_ecu,
        "engine_profile_id": f"{block}+{hcode}" if block and hcode else str(block or "2.5L_K+8V_782_SWIRL"),
        "transaxle_code": str(trans.get("transaxle_code") or "A555"), "turbo_spec_id": turbo.get("turbo_id"),
        "pinout_verified": bool(ecu.get("pin11_continuity_verified") is True and ecu.get("pin2_3_25_45_verified") is True),
        "map_sensor_bar": eff_bar,
        "rev_limit_rpm": ecu.get("rev_limit_rpm"), "overboost_fuel_cut_kpa": ecu.get("overboost_fuel_cut_kpa"),
    }


def _map_unit_sample(idx: int, u: list[float], map_sensor_bar: int) -> dict[str, Any]:
    rpm = 600.0 + u[0] * (8160.0 - 600.0)
    map_kpa = 25.0 + u[1] * (_MAP_SWEEP_MAX_KPA[map_sensor_bar] - 25.0)
    act_c, cts_c = 10.0 + u[2] * 85.0, 15.0 + u[3] * 95.0
    batt_v, tps_pct = 8.0 + u[4] * (BATT_MAX_V - 8.0), 5.0 + u[6] * 95.0
    knock_raw = int(min(160, max(0, round(u[5] * 160.0))))
    return {
        "trial_idx": idx, "rpm": rpm, "map_kpa": map_kpa, "act_c": act_c, "cts_c": cts_c,
        "batt_v": batt_v, "knock_raw": knock_raw, "tps_pct": tps_pct,
        "rpm_hb": min(255, max(0, round(rpm / 32.0))),
        "map_raw": kpa_to_raw_map(map_kpa, map_sensor_bar, baro_kpa=STANDARD_BARO_KPA),
        "baro_raw": kpa_to_raw_map(STANDARD_BARO_KPA, map_sensor_bar, baro_kpa=STANDARD_BARO_KPA),
        "batt_raw": min(255, max(0, round((batt_v / BATT_MAX_V) * 255.0))),
        "act_raw": min(255, max(0, round(((act_c + 40.0) / 165.0) * 255.0))),
        "cts_raw": min(255, max(0, round(((cts_c + 40.0) / 165.0) * 255.0))),
        "tps_raw": min(255, max(0, round((tps_pct / 100.0) * 255.0))),
    }


def _evaluate_twin_point(
    pt: dict[str, Any], fuel_cal: FuelCalibration, spark_cal: SparkCalibration, boost_cal: BoostCalibration,
    *, eff_rev_limit: int, eff_overboost_kpa: float, map_sensor_bar: int,
) -> dict[str, Any]:
    fuel_res = calc_fuel_pw(pt["rpm_hb"], pt["map_raw"], pt["batt_raw"], fuel_cal)
    spark_res = calc_spark_advance(pt["rpm_hb"], pt["map_raw"], pt["knock_raw"], spark_cal, batt_raw_u8=pt["batt_raw"])
    boost_res = calc_wastegate_and_limiters(pt["rpm_hb"], pt["map_raw"], pt["baro_raw"], spark_res.knock_retard_u8, boost_cal)
    eff_pw, eff_wg = (0 if boost_res.fuel_cut else fuel_res.pw_ticks_u16), (0 if boost_res.fuel_cut else boost_res.wg_duty_u8)
    violations: list[dict[str, Any]] = []
    idx, rpm, map_kpa = pt["trial_idx"], pt["rpm"], pt["map_kpa"]

    if eff_rev_limit <= REV_LIMIT_CEILING_RPM and rpm >= eff_rev_limit and not (boost_res.rev_limit_cut and boost_res.fuel_cut and eff_wg == 0):
        violations.append(_viol(idx, "REV_LIMIT_VIOLATION", rpm, map_kpa, f"Rev limit inactive at {rpm:.1f} >= {eff_rev_limit} RPM."))
    if eff_overboost_kpa <= BAR_OVERBOOST_CEILINGS[map_sensor_bar] and map_kpa > eff_overboost_kpa and not (boost_res.overboost_cut and boost_res.fuel_cut and eff_wg == 0):
        violations.append(_viol(idx, "OVERBOOST_CUT_VIOLATION", rpm, map_kpa, f"Overboost cut inactive at {map_kpa:.1f} > {eff_overboost_kpa:.1f} kPa."))
    wrap_hit = bool(spark_res.wrap_detected or spark_res.spark_u8 > spark_res.base_adv_u8)
    if wrap_hit:
        violations.append(_viol(idx, "SPARK_UNDERFLOW_WRAP", rpm, map_kpa, f"Spark wrap/underflow: base={spark_res.base_adv_u8}, final={spark_res.spark_u8}."))
    overflow_hit = bool((not boost_res.fuel_cut and eff_pw < fuel_cal.min_pw_ticks) or eff_wg > boost_cal.max_wg_duty_u8)
    if overflow_hit:
        violations.append(_viol(idx, "PW_WG_OVERFLOW_OR_UNDERFLOW", rpm, map_kpa, f"PW/WG out of bounds: pw={eff_pw}, wg={eff_wg}."))

    clamp_hit = bool(boost_res.fuel_cut or fuel_res.floor_clamped or fuel_res.saturated_u16 or spark_res.spark_u8 == spark_res.spklmt_u8 or eff_wg == boost_cal.max_wg_duty_u8)
    reward = (map_kpa / _MAP_SWEEP_MAX_KPA[map_sensor_bar]) * (0.5 + 0.5 * (spark_res.spark_deg_btdc / 45.0))
    penalty = (10.0 if boost_res.fuel_cut else 0.0) + (5.0 if spark_res.knock_retard_u8 > 0 else 0.0) + (100.0 if wrap_hit else 0.0) + (100.0 if overflow_hit else 0.0)
    return {
        "pt": pt, "fuel_res": fuel_res, "spark_res": spark_res, "boost_res": boost_res,
        "eff_pw": eff_pw, "eff_wg": eff_wg, "wrap_hit": wrap_hit, "overflow_hit": overflow_hit,
        "clamp_hit": clamp_hit, "violations": violations, "objective": float(penalty - reward),
    }


def _generate_and_eval_trials(
    sampler_norm: str, n_trials: int, seed: int, map_sensor_bar: int,
    fuel_cal: FuelCalibration, spark_cal: SparkCalibration, boost_cal: BoostCalibration,
    *, eff_rev_limit: int, eff_overboost_kpa: float,
) -> tuple[list[dict[str, Any]], float]:
    if sampler_norm == "OPTUNA":
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
        evals: list[dict[str, Any]] = []

        def _obj(trial: optuna.Trial) -> float:
            u = [trial.suggest_float(f"u{axis}", 0.0, 1.0) for axis in range(7)]
            ev = _evaluate_twin_point(
                _map_unit_sample(len(evals), u, map_sensor_bar), fuel_cal, spark_cal, boost_cal,
                eff_rev_limit=eff_rev_limit, eff_overboost_kpa=eff_overboost_kpa, map_sensor_bar=map_sensor_bar,
            )
            evals.append(ev)
            return ev["objective"]

        study.optimize(_obj, n_trials=n_trials, show_progress_bar=False)
        return evals, float(study.best_value)

    if sampler_norm == "SOBOL":
        m = max(1, math.ceil(math.log2(max(2, n_trials))))
        raw = qmc.Sobol(d=7, scramble=True, seed=seed).random_base2(m)[:n_trials]
    else:
        raw = qmc.LatinHypercube(d=7, seed=seed).random(n_trials)
    evals = [
        _evaluate_twin_point(
            _map_unit_sample(idx, [float(v) for v in row], map_sensor_bar), fuel_cal, spark_cal, boost_cal,
            eff_rev_limit=eff_rev_limit, eff_overboost_kpa=eff_overboost_kpa, map_sensor_bar=map_sensor_bar,
        )
        for idx, row in enumerate(raw)
    ]
    return evals, min(ev["objective"] for ev in evals)


def _select_hc11_indices(evals: list[dict[str, Any]], limit: int) -> list[int]:
    if limit <= 0 or not evals:
        return []
    n, cap, chosen = len(evals), min(len(evals), limit), []

    def _add(idx: int) -> None:
        if idx not in chosen and len(chosen) < cap:
            chosen.append(idx)

    for idx, ev in enumerate(evals):
        if ev["violations"]:
            _add(idx)
    for fn, key in ((max, "rpm"), (max, "map_kpa"), (max, "knock_raw"), (min, "batt_v"), (min, "rpm")):
        _add(fn(range(n), key=lambda i, k=key: evals[i]["pt"][k]))
    for idx in range(0, n, max(1, n // cap)):
        _add(idx)
    for idx in range(n):
        _add(idx)
    return chosen


def _verify_hc11_subset(
    assembled: AssembledRom, evals: list[dict[str, Any]], indices: list[int],
    fuel_cal: FuelCalibration, boost_cal: BoostCalibration,
    *, eff_rev_limit: int, eff_overboost_kpa: float, map_sensor_bar: int,
) -> tuple[int, float, int, int, list[dict[str, Any]]]:
    max_cycles, max_ms, extra_wraps, extra_overflows, violations = 0, 0.0, 0, 0, []
    for idx in indices:
        ev = evals[idx]
        pt, spark_res, boost_res = ev["pt"], ev["spark_res"], ev["boost_res"]
        res = execute_ram_case(
            assembled, rpm_hb=pt["rpm_hb"], map_raw=pt["map_raw"], baro_raw=pt["baro_raw"],
            batt_raw=pt["batt_raw"], knock_raw=pt["knock_raw"], fuel_trim_u8=fuel_cal.fuel_trim_u8, use_adc=False,
        )
        cycles, duration_ms = int(res.get("cycles", 0)), float(res.get("duration_ms", 0.0))
        max_cycles, max_ms, rpm, map_kpa = max(max_cycles, cycles), max(max_ms, duration_ms), pt["rpm"], pt["map_kpa"]
        if not res.get("ok", False) or cycles > MAX_MAIN_LOOP_CYCLES:
            cat = "LOOP_BUDGET_EXCEEDED" if cycles > MAX_MAIN_LOOP_CYCLES else "HC11_EXECUTION_ERROR"
            violations.append(_viol(idx, cat, rpm, map_kpa, f"HC11 cycles={cycles}, err={res.get('error_code')}"))
        mismatches = [
            f"{k}:hc11={res.get(k)}!=twin={exp}"
            for k, exp in (("pw_ticks_u16", ev["eff_pw"]), ("spark_u8", spark_res.spark_u8), ("wg_duty_u8", ev["eff_wg"]),
                           ("cutout_flags_u8", boost_res.cutout_flags_u8), ("dwell_ticks_u16", spark_res.dwell_ticks_u16))
            if int(res.get(k, -1)) != int(exp)
        ]
        if mismatches:
            violations.append(_viol(idx, "HC11_TWIN_MISMATCH", rpm, map_kpa, "Parity mismatch: " + ", ".join(mismatches)))
        flags, wg, pw, spk = int(res.get("cutout_flags_u8", 0)), int(res.get("wg_duty_u8", 0)), int(res.get("pw_ticks_u16", 0)), int(res.get("spark_u8", 0))
        if spk > spark_res.base_adv_u8 and not ev["wrap_hit"]:
            extra_wraps += 1
            violations.append(_viol(idx, "SPARK_UNDERFLOW_WRAP", rpm, map_kpa, f"HC11 spark wrapped: {spk} > {spark_res.base_adv_u8}"))
        if eff_rev_limit <= REV_LIMIT_CEILING_RPM and rpm >= eff_rev_limit and ((flags & 0x06) != 0x06 or wg != 0):
            violations.append(_viol(idx, "REV_LIMIT_VIOLATION", rpm, map_kpa, f"HC11 rev limit inactive at {rpm:.1f} RPM."))
        if eff_overboost_kpa <= BAR_OVERBOOST_CEILINGS[map_sensor_bar] and map_kpa > eff_overboost_kpa and ((flags & 0x05) != 0x05 or wg != 0):
            violations.append(_viol(idx, "OVERBOOST_CUT_VIOLATION", rpm, map_kpa, f"HC11 overboost cut inactive at {map_kpa:.1f} kPa."))
        if (((flags & 0x04) == 0 and pw < fuel_cal.min_pw_ticks) or wg > boost_cal.max_wg_duty_u8) and not ev["overflow_hit"]:
            extra_overflows += 1
            violations.append(_viol(idx, "PW_WG_OVERFLOW_OR_UNDERFLOW", rpm, map_kpa, f"HC11 PW/WG bounds: pw={pw}, wg={wg}"))
    return max_cycles, max_ms, extra_wraps, extra_overflows, violations


def _build_parquet_session(
    *, campaign_id: str, car_id: str, build_id: str, rom_sha256: str,
    map_sensor_bar: int, meta: dict[str, Any], evals: list[dict[str, Any]],
) -> dict[str, Any]:
    session_id = f"mc_{campaign_id}"
    rows = [{
        "ts_utc_ns": _BASE_TS_UTC_NS + i * _STEP_TS_NS, "session_id": session_id, "car_id": car_id, "build_id": build_id,
        "run_kind": "MONTE_CARLO_SWEEP", "campaign_id": campaign_id, "trial_idx": i, "dead_cell_quarantine": False,
        "raw_rpm_b": ev["pt"]["rpm_hb"], "raw_map_b": ev["pt"]["map_raw"], "raw_tps_b": ev["pt"]["tps_raw"],
        "raw_pw_u16": int(ev["eff_pw"]), "raw_spark_b": int(ev["spark_res"].spark_u8),
        "raw_act_b": ev["pt"]["act_raw"], "raw_cts_b": ev["pt"]["cts_raw"], "raw_batt_b": ev["pt"]["batt_raw"],
        "raw_knock_b": ev["pt"]["knock_raw"], "rpm": float(ev["pt"]["rpm"]), "map_kpa": float(ev["pt"]["map_kpa"]),
        "boost_psi": float((ev["pt"]["map_kpa"] - STANDARD_BARO_KPA) / PSI_TO_KPA), "tps_pct": float(ev["pt"]["tps_pct"]),
        "pw_ms": float(int(ev["eff_pw"]) * PW_TICK_MS), "spark_deg_btdc": float(int(ev["spark_res"].spark_u8) * 0.5),
        "act_c": float(ev["pt"]["act_c"]), "cts_c": float(ev["pt"]["cts_c"]), "batt_v": float(ev["pt"]["batt_v"]),
        "knock_counts": int(ev["pt"]["knock_raw"]),
    } for i, ev in enumerate(evals)]
    return {
        "session_id": session_id, "car_id": car_id, "build_id": build_id, "run_kind": "MONTE_CARLO_SWEEP",
        "rom_sha256": rom_sha256, "baro_kpa": STANDARD_BARO_KPA, "ecu_family": meta["ecu_family"], "map_bar": map_sensor_bar,
        "year": meta["year"], "model": meta["model"], "engine_profile_id": meta["engine_profile_id"],
        "transaxle_code": meta["transaxle_code"], "turbo_spec_id": meta["turbo_spec_id"],
        "pinout_verified": meta["pinout_verified"], "rows": rows,
    }


def _upsert_campaign_row(
    conn: sqlite3.Connection, *, campaign_id: str, car_id: str, rom_sha256: str,
    sampler: str, n_trials: int, status: str, objective_summary: str | None,
) -> None:
    conn.execute(
        "INSERT INTO optimization_campaigns (campaign_id, car_id, baseline_rom_sha256, issue_id, sampler, n_trials, status, objective_summary, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?) ON CONFLICT(campaign_id) DO UPDATE SET "
        "car_id=excluded.car_id, baseline_rom_sha256=excluded.baseline_rom_sha256, sampler=excluded.sampler, "
        "n_trials=excluded.n_trials, status=excluded.status, objective_summary=excluded.objective_summary",
        (campaign_id, car_id, rom_sha256, sampler, n_trials, status, objective_summary, _utc_now_iso()),
    )


def _persist_issues(
    conn: sqlite3.Connection, *, violations: list[dict[str, Any]], car_id: str,
    build_id: str, campaign_id: str, session_id: str | None,
) -> list[str]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for v in violations:
        grouped.setdefault(str(v["category"]), []).append(v)
    issue_ids: list[str] = []
    for category, items in grouped.items():
        issue_id = f"{campaign_id}:{category.lower()}"
        rpms = [float(i["rpm"]) for i in items if i.get("rpm") is not None]
        maps = [float(i["map_kpa"]) for i in items if i.get("map_kpa") is not None]
        desc = f"{len(items)} violation(s) in campaign {campaign_id}: " + "; ".join(str(i["detail"]) for i in items[:5])
        conn.execute(
            "INSERT INTO tuning_issues (issue_id, car_id, session_id, build_id, campaign_id, severity, category, status, "
            "title, description, rpm_min, rpm_max, map_min_kpa, map_max_kpa, created_at) VALUES (?, ?, ?, ?, ?, 'CRITICAL', ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(issue_id) DO UPDATE SET description=excluded.description, rpm_min=excluded.rpm_min, rpm_max=excluded.rpm_max, "
            "map_min_kpa=excluded.map_min_kpa, map_max_kpa=excluded.map_max_kpa",
            (issue_id, car_id, session_id, build_id, campaign_id, category, f"Monte Carlo {category} ({len(items)}x)",
             desc, min(rpms) if rpms else None, max(rpms) if rpms else None, min(maps) if maps else None, max(maps) if maps else None, _utc_now_iso()),
        )
        issue_ids.append(issue_id)
    return issue_ids


def _persist_sweep_outputs(
    *, db_path: Path | str, lake_dir: Path | str, campaign_id: str, car_id: str, build_id: str,
    rom_sha256: str, sampler_norm: str, n_trials: int, map_sensor_bar: int, meta: dict[str, Any],
    evals: list[dict[str, Any]], violations: list[dict[str, Any]], summary_stats: dict[str, Any], write_parquet: bool,
) -> tuple[list[str], list[str]]:
    conn = connect_catalog(db_path)
    try:
        with conn:
            upsert_vehicle(conn, {
                "car_id": car_id, "year": meta["year"], "model": meta["model"], "ecu_family": meta["ecu_family"],
                "engine_profile_id": meta["engine_profile_id"], "transaxle_code": meta["transaxle_code"],
                "turbo_spec_id": meta["turbo_spec_id"], "pinout_verified": meta["pinout_verified"],
            })
            _upsert_campaign_row(conn, campaign_id=campaign_id, car_id=car_id, rom_sha256=rom_sha256,
                                 sampler=sampler_norm, n_trials=n_trials, status="RUNNING", objective_summary=None)
    finally:
        conn.close()
    parquet_chunks, session_id = [], None
    if write_parquet:
        session = _build_parquet_session(campaign_id=campaign_id, car_id=car_id, build_id=build_id, rom_sha256=rom_sha256,
                                         map_sensor_bar=map_sensor_bar, meta=meta, evals=evals)
        session_id = session["session_id"]
        pq_res = write_session_parquet_chunks(session, lake_dir=lake_dir, db_path=db_path, chunk_seconds=30)
        parquet_chunks = [str(chunk["file_path"]) for chunk in pq_res.get("chunks", [])]
    conn = connect_catalog(db_path)
    try:
        with conn:
            _upsert_campaign_row(conn, campaign_id=campaign_id, car_id=car_id, rom_sha256=rom_sha256, sampler=sampler_norm,
                                 n_trials=n_trials, status="COMPLETED", objective_summary=json.dumps(summary_stats, sort_keys=True))
            issue_ids = _persist_issues(conn, violations=violations, car_id=car_id, build_id=build_id, campaign_id=campaign_id, session_id=session_id)
    finally:
        conn.close()
    return parquet_chunks, issue_ids


def run_monte_carlo_sweep(
    assembled: AssembledRom | None = None, *, profile_path: Path | str | None = None, profile: Mapping[str, Any] | None = None,
    cleanroom_src_dir: Path | str | None = None, sampler: str = "SOBOL", n_trials: int = 64, seed: int = 42,
    map_sensor_bar: int = 2, rev_limit_rpm: int | None = None, overboost_cut_kpa: float | None = None,
    verify_hc11: bool = True, hc11_sample_limit: int = 32, lake_dir: Path | str = Path("data/drives"),
    db_path: Path | str = Path("data/drives/catalog.sqlite"), build_id: str = "STOCK",
    campaign_id: str | None = None, write_parquet: bool = True,
) -> SweepSummary:
    """Run a deterministic Sobol, LHS, or Optuna sweep verifying Twin & HC11 invariants."""
    sampler_norm = sampler.strip().upper()
    if sampler_norm not in MC_SAMPLERS:
        raise ValueError(f"Unsupported sampler {sampler!r}; expected one of {MC_SAMPLERS}")
    if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials <= 0:
        raise ValueError(f"n_trials must be a positive integer, got {n_trials!r}")
    profile_dict = _load_profile(profile_path, profile)
    meta = _extract_vehicle_meta(profile_dict, map_sensor_bar)
    eff_bar = meta["map_sensor_bar"] if profile_dict and map_sensor_bar == 2 else map_sensor_bar
    if eff_bar not in BAR_OVERBOOST_CEILINGS:
        raise ValueError(f"Unsupported map_sensor_bar={eff_bar!r}; expected 1, 2, or 3")

    eff_cid = campaign_id or f"camp_{sampler_norm.lower()}_{seed}_{uuid.uuid4().hex[:8]}"
    req_rev = rev_limit_rpm if rev_limit_rpm is not None else meta["rev_limit_rpm"]
    req_ob = overboost_cut_kpa if overboost_cut_kpa is not None else meta["overboost_fuel_cut_kpa"]
    safe_rev = int(req_rev) if req_rev is not None and not isinstance(req_rev, bool) and math.isfinite(float(req_rev)) and 1000 <= int(req_rev) <= REV_LIMIT_CEILING_RPM else None
    safe_ob = float(req_ob) if req_ob is not None and not isinstance(req_ob, bool) and math.isfinite(float(req_ob)) and 0.0 < float(req_ob) <= BAR_OVERBOOST_CEILINGS[eff_bar] else None
    rom = assembled if assembled is not None else build_candidate_rom_bytes(
        profile_or_path=profile_dict, cleanroom_src_dir=cleanroom_src_dir, rev_limit_rpm=safe_rev, overboost_cut_kpa=safe_ob,
    )[1]

    fuel_cal, spark_cal, boost_cal = extract_calibrations_from_rom(
        rom, map_sensor_bar=eff_bar, rev_limit_rpm=safe_rev if rev_limit_rpm is not None else None,
        overboost_cut_kpa=safe_ob if overboost_cut_kpa is not None else None,
    )
    eff_rev_limit = int(req_rev) if req_rev is not None and not isinstance(req_rev, bool) and math.isfinite(float(req_rev)) else boost_cal.rev_limit_rpm
    eff_ob_raw = float(req_ob) if req_ob is not None and not isinstance(req_ob, bool) else boost_cal.overboost_cut_kpa
    ob_finite = math.isfinite(eff_ob_raw)
    eff_overboost_kpa = eff_ob_raw if ob_finite else boost_cal.overboost_cut_kpa
    violations: list[dict[str, Any]] = []
    if eff_rev_limit > REV_LIMIT_CEILING_RPM or eff_rev_limit < 1000:
        violations.append(_viol(None, "REV_LIMIT_CEILING_EXCEEDED", float(eff_rev_limit), None, f"rev_limit_rpm={eff_rev_limit} > {REV_LIMIT_CEILING_RPM}"))
    if not ob_finite or eff_ob_raw > BAR_OVERBOOST_CEILINGS[eff_bar] or eff_ob_raw <= 0.0:
        violations.append(_viol(None, "OVERBOOST_CUT_VIOLATION", None, eff_ob_raw if ob_finite else None, f"overboost_cut_kpa={eff_ob_raw} invalid (ceiling={BAR_OVERBOOST_CEILINGS[eff_bar]})"))

    evals, best_obj = _generate_and_eval_trials(sampler_norm, n_trials, seed, eff_bar, fuel_cal, spark_cal, boost_cal, eff_rev_limit=eff_rev_limit, eff_overboost_kpa=eff_overboost_kpa)
    wrap_cnt, ovf_cnt, clamp_cnt = sum(1 for e in evals if e["wrap_hit"]), sum(1 for e in evals if e["overflow_hit"]), sum(1 for e in evals if e["clamp_hit"])
    for ev in evals:
        violations.extend(ev["violations"])
    max_cyc, max_ms = 0, 0.0
    if verify_hc11 and hc11_sample_limit > 0:
        max_cyc, max_ms, ex_w, ex_o, hc11_viols = _verify_hc11_subset(
            rom, evals, _select_hc11_indices(evals, hc11_sample_limit), fuel_cal, boost_cal,
            eff_rev_limit=eff_rev_limit, eff_overboost_kpa=eff_overboost_kpa, map_sensor_bar=eff_bar,
        )
        wrap_cnt, ovf_cnt = wrap_cnt + ex_w, ovf_cnt + ex_o
        violations.extend(hc11_viols)

    stats = {"best_objective": best_obj, "overflow_count": ovf_cnt, "wrap_count": wrap_cnt, "safety_clamp_events": clamp_cnt, "max_loop_cycles": max_cyc, "violation_count": len(violations)}
    pq_chunks, issue_ids = _persist_sweep_outputs(
        db_path=db_path, lake_dir=lake_dir, campaign_id=eff_cid, car_id=meta["car_id"], build_id=build_id,
        rom_sha256=rom.sha256, sampler_norm=sampler_norm, n_trials=n_trials, map_sensor_bar=eff_bar,
        meta=meta, evals=evals, violations=violations, summary_stats=stats, write_parquet=write_parquet,
    )
    ok = ovf_cnt == 0 and wrap_cnt == 0 and max_cyc <= MAX_MAIN_LOOP_CYCLES and len(violations) == 0
    return SweepSummary(
        ok=ok, campaign_id=eff_cid, car_id=meta["car_id"], build_id=build_id, sampler=sampler_norm, n_trials=n_trials, seed=seed,
        parquet_chunks=pq_chunks, db_path=str(db_path), lake_dir=str(lake_dir), overflow_count=ovf_cnt, wrap_count=wrap_cnt,
        safety_clamp_events=clamp_cnt, max_loop_cycles=max_cyc, max_loop_ms=max_ms,
        max_rpm_seen=max(e["pt"]["rpm"] for e in evals), max_map_kpa_seen=max(e["pt"]["map_kpa"] for e in evals),
        violations=violations, issue_ids=issue_ids, best_objective=best_obj,
    )
