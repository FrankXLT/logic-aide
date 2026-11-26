"""Wave C cleanroom parsers for Chrysler SMEC/SBEC/LM empirical telemetry logs."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, NamedTuple, Sequence

from logicaide.heritage.bom_extractor import check_quarantine, is_quarantined_path
from logicaide.heritage.discovery import scrub_pii
from logicaide.rom.asm_parsers import scrub_cleanroom_text
from logicaide.rom.manifest_specs import find_ecu_root

STANDARD_BARO_KPA = 101.325
PSI_TO_KPA = 6.894757293168361
RELATIVE_LOG_BASE_EPOCH_NS = 1_546_300_800_000_000_000  # 2019-01-01T00:00:00Z
EGT_RAIL_THRESHOLD_F = 2200.0
BUS_STALL_THRESHOLD_S = 5.0
TEMP_MIN_F, TEMP_MAX_F = -40.0, 300.0
AFR_MIN, AFR_MAX = 5.0, 25.0

FORBIDDEN_GPS_COLUMNS: frozenset[str] = frozenset({
    "lat", "lon", "lng", "latitude", "longitude",
    "altitude", "alt", "gps_track", "elevation", "gps_lat", "gps_lon",
})
ALL_ENGINEERED_CHANNELS: tuple[str, ...] = (
    "rpm", "map_kpa", "boost_psi", "tps_pct", "pw_ms",
    "spark_deg_btdc", "act_c", "cts_c", "batt_v", "afr", "knock_counts",
)
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SESSION_HDR_RE = re.compile(r"^Session:\s*Session\s+(\d+)\b", re.IGNORECASE)
_ABS_TS_FORMATS: tuple[str, ...] = ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S")


@dataclass(slots=True, frozen=True)
class TelemetrySample:
    """Single normalized telemetry sample aligned to canonical Parquet lake columns."""

    ts_utc_ns: int
    dead_cell_quarantine: bool = False
    raw_rpm_b: int | None = None
    raw_map_b: int | None = None
    raw_tps_b: int | None = None
    raw_pw_u16: int | None = None
    raw_spark_b: int | None = None
    raw_act_b: int | None = None
    raw_cts_b: int | None = None
    raw_batt_b: int | None = None
    raw_o2_b: int | None = None
    raw_knock_b: int | None = None
    rpm: float | None = None
    map_kpa: float | None = None
    boost_psi: float | None = None
    tps_pct: float | None = None
    pw_ms: float | None = None
    spark_deg_btdc: float | None = None
    act_c: float | None = None
    cts_c: float | None = None
    batt_v: float | None = None
    afr: float | None = None
    knock_counts: int | None = None
    imu_grade_pct: float | None = None
    imu_lon_accel_mps2: float | None = None
    imu_lat_accel_mps2: float | None = None
    egt_c: float | None = None
    vspd_mph: float | None = None
    quarantine_reason: str | None = None


@dataclass(slots=True)
class ParsedTelemetrySession:
    """Parsed telemetry session ready for Zstd Parquet chunking and catalog persistence."""

    session_id: str
    source_format: str
    source_rel_path: str
    car_id: str = "CHRYSLER_UNKNOWN"
    build_id: str = "STOCK"
    run_kind: str = "VEHICLE_SCI"
    ecu_family: str = "SMEC_DUAL_BOARD"
    year: int = 1989
    model: str = "Chrysler Turbo"
    baro_kpa: float = STANDARD_BARO_KPA
    started_at_utc: str = ""
    ended_at_utc: str = ""
    sample_count: int = 0
    quarantined_sample_count: int = 0
    tps_zero_v: float | None = None
    tps_span_v: float | None = None
    polled_channels: tuple[str, ...] = ()
    unpolled_channels: tuple[str, ...] = ()
    gps_stripped: bool = True
    quarantined_cells: list[dict[str, Any]] = field(default_factory=list)
    samples: list[TelemetrySample] = field(default_factory=list)
    notes: str | None = None

    @property
    def rows(self) -> list[TelemetrySample]:
        """Expose ``samples`` as ``rows`` for ``write_session_parquet_chunks``."""
        return self.samples


@dataclass(slots=True)
class _RawRow:
    """Intermediate uncalibrated telemetry row prior to MINTHR and quarantine passes."""

    ts_ns: int
    had_blank_separator: bool
    polled_driving_values: tuple[float, ...]
    rpm: float | None = None
    boost_psi: float | None = None
    tps_v: float | None = None
    raw_pw_u16: int | None = None
    pw_ms: float | None = None
    spark_deg_btdc: float | None = None
    act_f: float | None = None
    cts_f: float | None = None
    afr: float | None = None
    egt_f: float | None = None
    vspd_mph: float | None = None


_RawSession = NamedTuple("_RawSession", [
    ("session_id", str), ("source_format", str), ("polled_channels", tuple[str, ...]),
    ("ecu_family", str), ("year", int), ("model", str), ("raw_rows", list[_RawRow]),
])


def _scrub_text(text: str) -> str:
    return scrub_cleanroom_text(scrub_pii(text))


def _clean_source_rel_path(path: Path, private_root: Path | None = None) -> str:
    resolved = path.resolve()
    if "ECU" in resolved.parts:
        return _scrub_text("/".join(resolved.parts[resolved.parts.index("ECU"):]))
    if private_root is not None:
        try:
            return _scrub_text(resolved.relative_to(Path(private_root).resolve()).as_posix())
        except ValueError:
            pass
    return _scrub_text(path.name)


def _assert_no_gps_columns(headers: Sequence[str]) -> None:
    for col in headers:
        norm = col.strip().lower()
        if norm in FORBIDDEN_GPS_COLUMNS or norm.startswith("gps_"):
            raise ValueError(f"Forbidden GPS column detected in telemetry header: {col!r}")


def _opt_float(cell: str) -> float | None:
    s = cell.strip()
    return float(s) if s else None


def _calibrate_minthr_tps(valid_tps_volts: Sequence[float]) -> tuple[float | None, float | None]:
    if not valid_tps_volts:
        return None, None
    min_v, max_v = min(valid_tps_volts), max(valid_tps_volts)
    tps_zero_v = min_v if (0.15 <= min_v <= 1.0) else 0.5
    return tps_zero_v, max(3.8, (max_v - tps_zero_v) if max_v >= 3.5 else 3.8)


def _ns_to_iso_utc(ts_utc_ns: int) -> str:
    sec, rem_ns = divmod(ts_utc_ns, 1_000_000_000)
    return datetime.fromtimestamp(sec, tz=timezone.utc).replace(microsecond=rem_ns // 1_000).isoformat()


def _parse_abs_timestamp_ns(text: str) -> int:
    clean = text.strip()
    for fmt in _ABS_TS_FORMATS:
        try:
            delta = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc) - _EPOCH_UTC
            return ((delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1_000
        except ValueError:
            continue
    raise ValueError(f"Unsupported telemetry timestamp: {text!r}")


def _record_quarantine_hit(
    agg: dict[tuple[str, str], dict[str, Any]], *,
    reason: str, channel: str, detail: str, idx: int, ts_ns: int, dt_s: float | None = None,
) -> None:
    key = (reason, channel)
    entry = agg.get(key)
    if entry is None:
        entry = {
            "reason": reason, "channel": channel, "sample_count": 0, "span_count": 0,
            "first_sample_idx": idx, "last_sample_idx": idx,
            "first_ts_utc_ns": ts_ns, "last_ts_utc_ns": ts_ns, "detail": detail,
        }
        if dt_s is not None:
            entry["max_dt_s"] = round(dt_s, 6)
        agg[key] = entry
    if entry["sample_count"] == 0 or idx > entry["last_sample_idx"] + 1:
        entry["span_count"] += 1
    entry["sample_count"] += 1
    entry["last_sample_idx"] = idx
    entry["last_ts_utc_ns"] = ts_ns
    if dt_s is not None and dt_s > entry.get("max_dt_s", 0.0):
        entry["max_dt_s"] = round(dt_s, 6)


def _evaluate_row_quarantine(
    raw: _RawRow, *, all_zero_drop: bool, dt_s: float | None,
    idx: int, ts_ns: int, agg: dict[tuple[str, str], dict[str, Any]],
) -> tuple[bool, bool, bool, bool, list[str]]:
    reasons: list[str] = []
    if dt_s is not None and not raw.had_blank_separator and dt_s > BUS_STALL_THRESHOLD_S:
        reasons.append("BUS_STALL")
        _record_quarantine_hit(
            agg, reason="BUS_STALL", channel="bus_timing",
            detail=f"Consecutive non-blank-separated sample gap dt_s > {BUS_STALL_THRESHOLD_S}s",
            idx=idx, ts_ns=ts_ns, dt_s=dt_s,
        )
    if all_zero_drop:
        reasons.append("ALL_ZERO_SENSOR_DROP")
        _record_quarantine_hit(
            agg, reason="ALL_ZERO_SENSOR_DROP", channel="all_polled",
            detail="All-zero sensor drop across polled driving channels (all polled channels nulled)",
            idx=idx, ts_ns=ts_ns,
        )
        return False, False, False, False, reasons

    egt_rail = raw.egt_f is not None and (raw.egt_f >= EGT_RAIL_THRESHOLD_F or raw.egt_f < -50.0)
    cts_rail = raw.cts_f is not None and (raw.cts_f < TEMP_MIN_F or raw.cts_f > TEMP_MAX_F)
    act_rail = raw.act_f is not None and (raw.act_f < TEMP_MIN_F or raw.act_f > TEMP_MAX_F)
    o2_rail = raw.afr is not None and (raw.afr <= 0.0 or raw.afr < AFR_MIN or raw.afr > AFR_MAX)
    for triggered, code, ch, detail in (
        (egt_rail, "OPEN_THERMOCOUPLE_EGT_RAIL", "egt_c", "Open-thermocouple EGT rail >= 2200.0 degF (nulled egt_c)"),
        (cts_rail, "CTS_SENSOR_RAIL", "cts_c", "Out-of-range coolant temperature rail (nulled cts_c)"),
        (act_rail, "ACT_SENSOR_RAIL", "act_c", "Out-of-range charge temperature rail (nulled act_c)"),
        (o2_rail, "O2_SENSOR_RAIL", "afr", "Out-of-range O2/AFR sensor rail (nulled afr)"),
    ):
        if triggered:
            reasons.append(code)
            _record_quarantine_hit(agg, reason=code, channel=ch, detail=detail, idx=idx, ts_ns=ts_ns)
    return egt_rail, cts_rail, act_rail, o2_rail, reasons


def _f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def _finalize_session(
    raw_sess: _RawSession, *, source_rel_path: str, car_id: str, build_id: str, run_kind: str, baro_kpa: float,
) -> ParsedTelemetrySession:
    """Apply per-session MINTHR TPS calibration, Dead-Cell Quarantine, and SI conversions."""
    raw_rows = raw_sess.raw_rows
    zero_drops = [bool(r.polled_driving_values) and all(v == 0.0 for v in r.polled_driving_values) for r in raw_rows]
    valid_tps = [r.tps_v for r, d in zip(raw_rows, zero_drops) if not d and r.tps_v is not None and 0.0 <= r.tps_v <= 5.5]
    tps_zero_v, tps_span_v = _calibrate_minthr_tps(valid_tps)
    samples: list[TelemetrySample] = []
    q_agg: dict[tuple[str, str], dict[str, Any]] = {}
    prev_ts_ns: int | None = None
    quarantined_count = 0

    for idx, (raw, is_drop) in enumerate(zip(raw_rows, zero_drops)):
        dt_s = ((raw.ts_ns - prev_ts_ns) / 1_000_000_000.0) if prev_ts_ns is not None else None
        ts_ns = raw.ts_ns if (prev_ts_ns is None or raw.ts_ns > prev_ts_ns) else (prev_ts_ns + 1)
        egt_rail, cts_rail, act_rail, o2_rail, reasons = _evaluate_row_quarantine(
            raw, all_zero_drop=is_drop, dt_s=dt_s, idx=idx, ts_ns=ts_ns, agg=q_agg,
        )
        reason_str = ",".join(reasons) if reasons else None
        if is_drop:
            sample = TelemetrySample(ts_utc_ns=ts_ns, dead_cell_quarantine=True, quarantine_reason=reason_str)
        else:
            tps_pct = (
                max(0.0, min(100.0, ((raw.tps_v - tps_zero_v) / tps_span_v) * 100.0))
                if (raw.tps_v is not None and tps_zero_v is not None and tps_span_v is not None) else None
            )
            sample = TelemetrySample(
                ts_utc_ns=ts_ns, dead_cell_quarantine=bool(reasons), raw_pw_u16=raw.raw_pw_u16,
                rpm=raw.rpm, map_kpa=(baro_kpa + raw.boost_psi * PSI_TO_KPA) if raw.boost_psi is not None else None,
                boost_psi=raw.boost_psi, tps_pct=tps_pct, pw_ms=raw.pw_ms, spark_deg_btdc=raw.spark_deg_btdc,
                act_c=None if (raw.act_f is None or act_rail) else _f_to_c(raw.act_f),
                cts_c=None if (raw.cts_f is None or cts_rail) else _f_to_c(raw.cts_f),
                afr=None if (raw.afr is None or o2_rail) else raw.afr, knock_counts=None,
                egt_c=None if (raw.egt_f is None or egt_rail) else _f_to_c(raw.egt_f),
                vspd_mph=raw.vspd_mph, quarantine_reason=reason_str,
            )
        quarantined_count += int(sample.dead_cell_quarantine)
        samples.append(sample)
        prev_ts_ns = ts_ns

    unpolled = tuple(ch for ch in ALL_ENGINEERED_CHANNELS if ch not in raw_sess.polled_channels)
    return ParsedTelemetrySession(
        session_id=_scrub_text(raw_sess.session_id), source_format=raw_sess.source_format,
        source_rel_path=source_rel_path, car_id=car_id, build_id=build_id, run_kind=run_kind,
        ecu_family=raw_sess.ecu_family, year=raw_sess.year, model=raw_sess.model, baro_kpa=baro_kpa,
        started_at_utc=_ns_to_iso_utc(samples[0].ts_utc_ns) if samples else "",
        ended_at_utc=_ns_to_iso_utc(samples[-1].ts_utc_ns) if samples else "",
        sample_count=len(samples), quarantined_sample_count=quarantined_count,
        tps_zero_v=tps_zero_v, tps_span_v=tps_span_v, polled_channels=raw_sess.polled_channels,
        unpolled_channels=unpolled, gps_stripped=True, quarantined_cells=list(q_agg.values()), samples=samples,
    )


def _parse_lasky_sessions(lines: list[str], stem: str) -> list[_RawSession]:
    """Parse multi-session Lasky ``Realtimelog*.csv`` blocks into raw sessions."""
    blocks: list[tuple[int, list[_RawRow]]] = []
    cur_session_num, cur_rows, cur_hdr, had_blank = 1, [], None, False

    for line in lines:
        stripped = line.strip()
        if not stripped or set(stripped) <= {","}:
            had_blank = True
            continue
        if sess_match := _SESSION_HDR_RE.match(stripped):
            if cur_rows:
                blocks.append((cur_session_num, cur_rows))
            cur_session_num, cur_rows, cur_hdr, had_blank = int(sess_match.group(1)), [], None, False
            continue
        cells = [c.strip() for c in line.split(",")]
        if cells[0] == "time" and "LM1_O2" in cells:
            _assert_no_gps_columns(cells)
            cur_hdr, had_blank = cells, False
            continue
        if cells[0].startswith("(") or cur_hdr is None:
            had_blank = False
            continue
        idx = {col: pos for pos, col in enumerate(cur_hdr) if col}
        t_s, o2, rpm = float(cells[idx["time"]]), float(cells[idx["LM1_O2"]]), float(cells[idx["RPM"]])
        ct_f, map_psi = float(cells[idx["Charge_temp"]]), float(cells[idx["MAP"]])
        tps_v, cts_f = float(cells[idx["TPS"]]), float(cells[idx["CTS"]])
        cur_rows.append(_RawRow(
            ts_ns=RELATIVE_LOG_BASE_EPOCH_NS + round(t_s * 1_000_000_000), had_blank_separator=had_blank,
            polled_driving_values=(o2, rpm, ct_f, map_psi, tps_v, cts_f),
            rpm=rpm, boost_psi=map_psi, tps_v=tps_v, act_f=ct_f, cts_f=cts_f, afr=o2,
        ))
        had_blank = False
    if cur_rows:
        blocks.append((cur_session_num, cur_rows))

    polled = ("rpm", "map_kpa", "boost_psi", "tps_pct", "act_c", "cts_c", "afr")
    return [_RawSession(f"{stem}__s{n}", "lasky_realtimelog", polled, "LM_PM", 1987, "Chrysler Turbo (LM)", r) for n, r in blocks]


def _parse_tabular_csv_rows(rows: list[list[str]], stem: str) -> list[_RawSession]:
    """Parse MPScan Android (14/15-col), K SMEC 13-col, and WOT MM:SS.d CSV logs."""
    headers = [h.strip() for h in rows[0]]
    _assert_no_gps_columns(headers)
    idx = {name: pos for pos, name in enumerate(headers) if name}
    ts_col = "Date/Time" if "Date/Time" in idx else "TimeStamp"
    afr_col = "AIR FUE" if "AIR FUE" in idx else "AIR FUEL"
    if any(col not in idx for col in (ts_col, "RPM", "MAP3BAR", "COOL", afr_col, "TPSV")):
        raise ValueError(f"Missing required CSV telemetry columns in {stem}")

    has_adv, is_relative_mmss = "ADV" in idx, False
    prev_raw_mmss_s: float | None = None
    rollover_offset_s, had_blank = 0.0, False
    raw_rows: list[_RawRow] = []

    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            had_blank = True
            continue
        ts_text = row[idx[ts_col]].strip()
        if "/" in ts_text:
            ts_ns = _parse_abs_timestamp_ns(ts_text)
        else:
            is_relative_mmss = True
            m_str, s_str = ts_text.split(":", 1)
            raw_s = int(m_str) * 60.0 + float(s_str)
            if prev_raw_mmss_s is not None and ((prev_raw_mmss_s - raw_s) > 60.0 or (had_blank and raw_s < prev_raw_mmss_s)):
                rollover_offset_s += 3600.0
            prev_raw_mmss_s = raw_s
            ts_ns = RELATIVE_LOG_BASE_EPOCH_NS + round((raw_s + rollover_offset_s) * 1_000_000_000)

        rpm, boost_psi = float(row[idx["RPM"]]), float(row[idx["MAP3BAR"]])
        cool_f, afr, tps_v = float(row[idx["COOL"]]), float(row[idx[afr_col]]), float(row[idx["TPSV"]])
        # MPScan Android CHRG is raw 0-5V (1..4V), NOT degF; keep act_c = None.
        raw_rows.append(_RawRow(
            ts_ns=ts_ns, had_blank_separator=had_blank, polled_driving_values=(rpm, boost_psi, cool_f, afr, tps_v),
            rpm=rpm, boost_psi=boost_psi, tps_v=tps_v,
            spark_deg_btdc=_opt_float(row[idx["ADV"]]) if has_adv else None, act_f=None, cts_f=cool_f, afr=afr,
            egt_f=_opt_float(row[idx["EGT"]]) if "EGT" in idx else None,
            vspd_mph=_opt_float(row[idx["VSPD"]]) if "VSPD" in idx else None,
        ))
        had_blank = False
    if not raw_rows:
        return []
    fmt = ("k_smec_13col" if ts_col == "Date/Time" else "wot_log_mmss" if is_relative_mmss
           else "mpscan_android_15col" if has_adv else "mpscan_android_14col")
    polled: tuple[str, ...] = ("rpm", "map_kpa", "boost_psi", "tps_pct", "cts_c", "afr") + (("spark_deg_btdc",) if has_adv else ())
    return [_RawSession(stem, fmt, polled, "SMEC_DUAL_BOARD", 1989, "Chrysler SMEC Turbo", raw_rows)]


def _parse_dlg_rows(lines: list[str], stem: str) -> list[_RawSession]:
    """Parse 3-line-preamble tab-delimited ``.dlg`` logs, dropping the initial all-zero row."""
    if len(lines) < 4:
        return []
    headers = [h.strip() for h in lines[2].split("\t")]
    _assert_no_gps_columns(headers)
    idx = {name: pos for pos, name in enumerate(headers) if name}
    if not {"Time", "RPM", "MAP", "A/F", "TPS", "Pulsewidth"} <= set(idx):
        raise ValueError(f"Unexpected .dlg header columns: {headers!r}")

    raw_rows: list[_RawRow] = []
    had_blank, first_data_seen = False, False
    for line in lines[3:]:
        if not line.strip():
            had_blank = True
            continue
        vals = [float(c.strip()) for c in line.split("\t")]
        if not first_data_seen:
            first_data_seen = True
            if all(v == 0.0 for v in vals):
                had_blank = False
                continue
        t_s, rpm, boost_psi = vals[idx["Time"]], vals[idx["RPM"]], vals[idx["MAP"]]
        afr, tps_v, dlg_pw = vals[idx["A/F"]], vals[idx["TPS"]], vals[idx["Pulsewidth"]]
        raw_pw_u16 = max(0, min(65535, round(dlg_pw * 65535.0 / 49152.0)))
        raw_rows.append(_RawRow(
            ts_ns=RELATIVE_LOG_BASE_EPOCH_NS + round(t_s * 1_000_000_000), had_blank_separator=had_blank,
            polled_driving_values=(rpm, boost_psi, afr, tps_v, dlg_pw), rpm=rpm, boost_psi=boost_psi,
            tps_v=tps_v, raw_pw_u16=raw_pw_u16, pw_ms=raw_pw_u16 * 0.004, afr=afr,
        ))
        had_blank = False
    if not raw_rows:
        return []
    polled = ("rpm", "map_kpa", "boost_psi", "tps_pct", "raw_pw_u16", "pw_ms", "afr")
    return [_RawSession(stem, "ecu_dlg", polled, "SMEC_DUAL_BOARD", 1989, "Chrysler Turbo (DLG)", raw_rows)]


def parse_telemetry_file(
    path: str | Path, *, car_id: str = "CHRYSLER_UNKNOWN", build_id: str = "STOCK",
    run_kind: str = "VEHICLE_SCI", baro_kpa: float = STANDARD_BARO_KPA, private_root: str | Path | None = None,
) -> list[ParsedTelemetrySession]:
    """Parse one empirical Chrysler telemetry log (`.csv` or `.dlg`) into normalized sessions."""
    file_path = check_quarantine(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Telemetry log file not found: {file_path}")
    if file_path.stat().st_size == 0:
        return []
    lines = file_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    if not any(line.strip() for line in lines):
        return []
    rel_path = _clean_source_rel_path(file_path, Path(private_root) if private_root is not None else None)
    clean_stem = _scrub_text(file_path.stem)
    first_nonempty = next((ln.strip() for ln in lines if ln.strip()), "")

    if file_path.suffix.lower() == ".dlg" or (len(lines) >= 3 and "\t" in lines[2]):
        raw_sessions = _parse_dlg_rows(lines, clean_stem)
    elif _SESSION_HDR_RE.match(first_nonempty) or first_nonempty.startswith("time,LM1_O2"):
        raw_sessions = _parse_lasky_sessions(lines, clean_stem)
    else:
        csv_rows = list(csv.reader(lines))
        first_header = [h.strip() for h in csv_rows[0]] if csv_rows else []
        _assert_no_gps_columns(first_header)
        if len(csv_rows) < 2:
            return []
        if first_header[0] not in {"TimeStamp", "Date/Time"}:
            raise ValueError(f"Unsupported telemetry log format in {rel_path}: header={first_header[:6]!r}")
        raw_sessions = _parse_tabular_csv_rows(csv_rows, clean_stem)
    return [_finalize_session(rs, source_rel_path=rel_path, car_id=car_id, build_id=build_id,
                              run_kind=run_kind, baro_kpa=baro_kpa) for rs in raw_sessions]


def _is_supported_telemetry_file(path: Path) -> bool:
    lower_parts = {part.lower() for part in path.parts}
    if "mpr" in lower_parts or "uberdata" in path.as_posix().lower():
        return False
    suffix = path.suffix.lower()
    if suffix != ".csv":
        return suffix == ".dlg"
    if path.stat().st_size == 0:
        return "logs" in lower_parts or "lasky" in lower_parts
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        first = handle.readline().strip()
    return (bool(_SESSION_HDR_RE.match(first)) or first.startswith("time,LM1_O2")
            or first.startswith("TimeStamp,RPM,") or first.startswith("Date/Time,RPM,"))


def discover_private_logs(private_root: str | Path = Path("private_references"), *, deduplicate: bool = False) -> list[Path]:
    """Discover empirical telemetry log paths (`.csv` and `.dlg`) under ``private_root``."""
    root_path = Path(private_root)
    if not root_path.exists():
        return []
    ecu_root = find_ecu_root(root_path)
    scan_root = ecu_root if ecu_root.is_dir() else root_path
    discovered, seen_hashes = [], set()
    for cand in sorted(scan_root.rglob("*"), key=lambda p: p.as_posix()):
        if is_quarantined_path(cand) or not cand.is_file() or not _is_supported_telemetry_file(cand):
            continue
        if deduplicate:
            digest = hashlib.sha256(cand.read_bytes()).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
        discovered.append(cand)
    return discovered
