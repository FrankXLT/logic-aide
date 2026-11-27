"""Tests for telemetry log parsers, 30s Zstd Parquet chunker, DuckDB rollups, and CLI."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from click.testing import CliRunner
import pyarrow.parquet as pq
import pytest

from logicaide.cli import main as cli_main
from logicaide.heritage.bom_extractor import QuarantinedPathError
from logicaide.telemetry import (
    FORBIDDEN_GPS_COLUMNS,
    PARQUET_STATISTICS_COLUMNS,
    PSI_TO_KPA,
    STANDARD_BARO_KPA,
    TelemetrySample,
    discover_private_logs,
    export_stimulus_fixture,
    parse_telemetry_file,
    query_load_cell_rollups,
    summarize_telemetry_lake,
    write_session_parquet_chunks,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "telemetry"
INGEST_SCRIPT = REPO_ROOT / "scripts" / "ingest_private_logs.py"

PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/" + r"home/[A-Za-z0-9_.-]+"),
    re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:bu" + r"car|ste" + r"ve|mcc" + r"lurg|bas" + r"tage)\b", re.IGNORECASE),
)


def _load_ingest_script() -> Any:
    spec = importlib.util.spec_from_file_location("ingest_private_logs", INGEST_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lasky_realtimelog_physics_and_null_vs_zero(tmp_path: Path) -> None:
    """Verify 12.207 Hz timing, unit conversions, and SQL NULL vs 0 semantics."""
    sessions = parse_telemetry_file(FIXTURES_DIR / "lasky_realtimelog_v13_slice.csv")
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess.sample_count == 145
    assert sess.quarantined_sample_count == 0

    s0, s1 = sess.samples[0], sess.samples[1]
    dt_s = (s1.ts_utc_ns - s0.ts_utc_ns) / 1_000_000_000.0
    assert 1.0 / dt_s == pytest.approx(12.207, abs=0.01)
    assert s0.act_c == pytest.approx((81.1 - 32.0) * 5.0 / 9.0, abs=1e-5)
    assert s0.map_kpa == pytest.approx(STANDARD_BARO_KPA + (-14.9) * PSI_TO_KPA, abs=1e-5)
    assert s0.afr == pytest.approx(14.16, abs=1e-5)

    for sample in sess.samples[:5]:
        assert sample.knock_counts is None
        assert sample.pw_ms is None
        assert sample.spark_deg_btdc is None
        assert sample.batt_v is None

    lake_dir = tmp_path / "lake"
    write_session_parquet_chunks(sess, lake_dir=lake_dir, db_path=tmp_path / "cat.sqlite")
    rollups = query_load_cell_rollups(lake_dir, session_id=sess.session_id)
    assert rollups
    for row in rollups:
        assert row["total_knock_events"] is None
        assert row["knock_polled_samples"] == 0
        assert row["mean_pw_ms"] is None
        assert row["mean_spark_deg"] is None
        assert row["min_batt_v"] is None


def test_mpscandroid_egt_rail_quarantine_and_rollups(tmp_path: Path) -> None:
    """Verify 2275.31 degF EGT rail quarantine, raw CHRG act_c=None, and rollup exclusion."""
    sess = parse_telemetry_file(FIXTURES_DIR / "mpscandroid_2019_06_09_egt_rail_slice.csv")[0]
    assert sess.sample_count == 14
    assert sess.quarantined_sample_count == 14
    for sample in sess.samples:
        assert sample.dead_cell_quarantine is True
        assert sample.act_c is None
        assert sample.egt_c is None
        assert "OPEN_THERMOCOUPLE_EGT_RAIL" in (sample.quarantine_reason or "")

    lake_dir, db_path = tmp_path / "lake", tmp_path / "catalog.sqlite"
    write_session_parquet_chunks(sess, lake_dir=lake_dir, db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        q_json = conn.execute(
            "SELECT quarantined_cells_json FROM sessions WHERE session_id = ?",
            (sess.session_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    q_entries = json.loads(q_json)
    assert any(e["reason"] == "OPEN_THERMOCOUPLE_EGT_RAIL" and e["sample_count"] == 14 for e in q_entries)

    assert query_load_cell_rollups(lake_dir) == []
    summary = summarize_telemetry_lake(lake_dir, db_path=db_path)
    assert summary["quarantined_rows"] == 14
    assert summary["active_rows"] == 0
    assert summary["rollup_bin_count"] == 0


def test_ksmec_parquet_chunk_rotation_metadata_and_catalog(tmp_path: Path) -> None:
    """Verify 140-row >30s log rotates into >=2 Zstd-3 Parquet chunks with 33-col schema and stats."""
    sess = parse_telemetry_file(FIXTURES_DIR / "ksmec_mpN7D7P6_slice.csv")[0]
    assert sess.sample_count == 140
    assert (sess.samples[-1].ts_utc_ns - sess.samples[0].ts_utc_ns) / 1e9 > 30.0

    lake_dir, db_path = tmp_path / "lake", tmp_path / "catalog.sqlite"
    res = write_session_parquet_chunks(sess, lake_dir=lake_dir, db_path=db_path)
    assert res["chunk_count"] >= 2
    chunk_names = [Path(c["abs_path"]).name for c in res["chunks"]]
    assert chunk_names[:2] == ["chunk_0000.parquet", "chunk_0001.parquet"]

    required_non_null = {"ts_utc_ns", "session_id", "car_id", "build_id", "run_kind", "dead_cell_quarantine"}
    for chunk in res["chunks"]:
        pf = pq.ParquetFile(chunk["abs_path"])
        assert pf.metadata.num_columns == 33
        assert len(pf.schema_arrow) == 33
        for fname in required_non_null:
            assert pf.schema_arrow.field(fname).nullable is False
        rg = pf.metadata.row_group(0)
        names = pf.schema_arrow.names
        for col_idx in range(pf.metadata.num_columns):
            assert rg.column(col_idx).compression == "ZSTD"
        for stat_col in PARQUET_STATISTICS_COLUMNS:
            cm = rg.column(names.index(stat_col))
            assert cm.is_stats_set and cm.statistics is not None
            if stat_col != "act_c":
                assert cm.statistics.has_min_max is True

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT chunk_id, file_path, row_count, zstd_level, sha256 FROM parquet_chunks WHERE session_id = ? ORDER BY chunk_id",
            (sess.session_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == res["chunk_count"]
    assert sum(r[2] for r in rows) == 140
    assert all(r[3] == 3 for r in rows)
    assert rows[0][1].endswith("chunk_0000.parquet") and rows[1][1].endswith("chunk_0001.parquet")
    assert rows[0][4] == hashlib.sha256(Path(res["chunks"][0]["abs_path"]).read_bytes()).hexdigest()


def test_ecu_dlg_drops_zero_row_and_recovers_pw() -> None:
    """Verify .dlg drops initial all-zero row, recovers raw_pw_u16=425/pw_ms=1.700, keeps cts/act None."""
    sess = parse_telemetry_file(FIXTURES_DIR / "ecu_run6_slice.dlg")[0]
    assert sess.source_format == "ecu_dlg"
    assert sess.sample_count == 140
    first = sess.samples[0]
    assert first.raw_pw_u16 == 425
    assert first.pw_ms == pytest.approx(1.700, abs=1e-6)
    assert all(s.cts_c is None and s.act_c is None for s in sess.samples)


def test_lasky_normalized_slice_cleanroom_and_size() -> None:
    """Verify tracked normalized slice has <=150 rows, zero GPS columns, and zero PII."""
    slice_path = FIXTURES_DIR / "lasky_v13_normalized_slice.csv"
    text = slice_path.read_text(encoding="utf-8")
    for pattern in PII_PATTERNS:
        assert not pattern.search(text), f"Unexpected PII match in {slice_path.name}"
    with slice_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        rows = list(reader)
    assert 1 <= len(rows) <= 150
    assert not any(h in FORBIDDEN_GPS_COLUMNS or h.startswith("gps_") for h in headers)


def test_synthetic_parser_edge_cases(tmp_path: Path) -> None:
    """Verify Lasky split, 0-byte CSV, MM:SS.d rollover, all-zero drop, bus stall, and GPS reject."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_bytes(b"")
    assert parse_telemetry_file(empty_csv) == []

    lasky_multi = tmp_path / "multi_lasky.csv"
    lasky_multi.write_text(
        "Session: Session 1\ntime,LM1_O2,RPM,Charge_temp,MAP,TPS,CTS\n(sec),(AFR),(RPM),(degF),(PSI),(Volt),(degF)\n"
        "0.0,14.5,1000,80.0,-10.0,0.5,180.0\n\n"
        "Session: Session 2\ntime,LM1_O2,RPM,Charge_temp,MAP,TPS,CTS\n(sec),(AFR),(RPM),(degF),(PSI),(Volt),(degF)\n"
        "0.0,14.2,1200,82.0,-8.0,0.6,182.0\n",
        encoding="utf-8",
    )
    split = parse_telemetry_file(lasky_multi)
    assert [s.session_id for s in split] == ["multi_lasky__s1", "multi_lasky__s2"]

    wot_csv = tmp_path / "wot_rollover.csv"
    wot_csv.write_text(
        "TimeStamp,RPM,MAP3BAR,COOL,TPSV,AIR FUEL\n"
        "59:59.5,2000,1.0,180,1.0,12.5\n"
        "00:00.5,2500,5.0,181,3.8,12.2\n"
        "00:07.0,2600,5.2,181,3.8,12.1\n\n"
        "00:15.0,2700,5.4,181,3.8,12.0\n"
        "00:15.5,0,0,0,0,0\n",
        encoding="utf-8",
    )
    wot = parse_telemetry_file(wot_csv)[0]
    dts = [(b.ts_utc_ns - a.ts_utc_ns) / 1e9 for a, b in zip(wot.samples, wot.samples[1:])]
    assert dts[0] == pytest.approx(1.0, abs=1e-6)
    assert dts[1] == pytest.approx(6.5, abs=1e-6)
    assert "BUS_STALL" in (wot.samples[2].quarantine_reason or "")
    assert "BUS_STALL" not in (wot.samples[3].quarantine_reason or "")
    drop = wot.samples[4]
    assert drop.dead_cell_quarantine is True
    assert "ALL_ZERO_SENSOR_DROP" in (drop.quarantine_reason or "")
    assert all(v is None for v in (drop.rpm, drop.map_kpa, drop.tps_pct, drop.cts_c, drop.afr))

    gps_csv = tmp_path / "with_gps.csv"
    gps_csv.write_text("TimeStamp,RPM,MAP3BAR,COOL,TPSV,AIR FUEL,lat\n00:01.0,1000,0,180,0.5,14.7,42.3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="GPS"):
        parse_telemetry_file(gps_csv)


def test_gps_stripping_export_fixture_and_rollup_boundaries(tmp_path: Path) -> None:
    """Verify GPS stripping across CSV/JSON/Parquet exports and DuckDB rollup bin boundaries."""
    src_csv = tmp_path / "raw_with_gps.csv"
    pii_email = "driver@" + "example.com"
    src_csv.write_text(
        f"session_id,car_id,build_id,run_kind,ts_utc_ns,rpm,lat,gps_track,notes\n"
        f"s1,car1,STOCK,VEHICLE_SCI,1546300800000000000,1250,42.33,-83.04,{pii_email}\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "catalog.sqlite"
    for fmt, ext in (("csv", "csv"), ("json", "json"), ("parquet", "parquet")):
        out = tmp_path / f"fixture.{ext}"
        res = export_stimulus_fixture(src_csv, out, fmt=fmt, db_path=db_path, car_id="car1")
        assert res["gps_stripped"] is True
        assert sorted(res["stripped_gps_columns"]) == ["gps_track", "lat"]
        if fmt != "parquet":
            txt = out.read_text(encoding="utf-8")
            assert pii_email not in txt and "lat" not in txt and "gps_track" not in txt
        else:
            cols = pq.read_table(out).column_names
            assert "lat" not in cols and "gps_track" not in cols

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT gps_stripped FROM stimulus_fixtures").fetchall()
    finally:
        conn.close()
    assert len(rows) == 3 and all(r[0] == 1 for r in rows)

    base_ns = 1_546_300_800_000_000_000
    boundary_samples = [
        TelemetrySample(ts_utc_ns=base_ns + i * 1_000_000, rpm=rpm, map_kpa=mp, tps_pct=tps, act_c=act, knock_counts=knk)
        for i, (rpm, mp, tps, act, knk) in enumerate([
            (1249.9, 99.9, 2.9, 19.9, None),
            (1250.0, 100.0, 3.0, 20.0, 0),
            (1250.0, 100.0, 34.9, 39.9, None),
            (1250.0, 100.0, 35.0, 40.0, 2),
            (1250.0, 100.0, 74.9, 59.9, None),
            (1250.0, 100.0, 75.0, 60.0, None),
            (1250.0, 100.0, None, None, None),
        ])
    ]
    lake_dir = tmp_path / "boundary_lake"
    write_session_parquet_chunks(
        {"session_id": "b1", "car_id": "car1", "build_id": "STOCK", "run_kind": "VEHICLE_SCI", "rows": boundary_samples},
        lake_dir=lake_dir, db_path=None,
    )
    bins = query_load_cell_rollups(lake_dir)
    got = {
        (b["rpm_bin"], b["map_bin_kpa"], b["tps_regime"], b["charge_temp_bin"]): (
            b["sample_count"], b["total_knock_events"], b["knock_polled_samples"]
        )
        for b in bins
    }
    assert got[(1000, 90, "IDLE", "COLD_<20C")] == (1, None, 0)
    assert got[(1250, 100, "PART_THROTTLE", "WARM_20_40C")] == (2, 0, 1)
    assert got[(1250, 100, "TRANSITION", "HOT_40_60C")] == (2, 2, 1)
    assert got[(1250, 100, "WOT", "HEATSOAK_>=60C")] == (1, None, 0)
    assert got[(1250, 100, "UNPOLLED", "UNPOLLED")] == (1, None, 0)


def test_telemetry_cli_and_ingest_script(tmp_path: Path) -> None:
    """Verify Click telemetry CLI subcommands and scripts/ingest_private_logs.py without private_references."""
    src_dir = tmp_path / "raw_logs"
    src_dir.mkdir()
    for name in ("lasky_realtimelog_v13_slice.csv", "ksmec_mpN7D7P6_slice.csv", "ecu_run6_slice.dlg"):
        (src_dir / name).write_bytes((FIXTURES_DIR / name).read_bytes())

    lake_dir, db_path, out_fix = tmp_path / "cli_lake", tmp_path / "cli.sqlite", tmp_path / "cli_fix.csv"
    runner = CliRunner()

    r_ing = runner.invoke(cli_main, ["telemetry", "ingest", "--input", str(src_dir), "--lake-dir", str(lake_dir), "--db-path", str(db_path), "--car-id", "auto", "--json"])
    assert r_ing.exit_code == 0, r_ing.output
    ing = json.loads(r_ing.output)
    assert ing["files_processed"] == 3 and ing["session_count"] == 3 and ing["row_count"] == 425

    r_rol = runner.invoke(cli_main, ["telemetry", "rollups", "--lake-dir", str(lake_dir), "--json"])
    assert r_rol.exit_code == 0, r_rol.output
    rol = json.loads(r_rol.output)
    assert rol["summary"]["active_rows"] == 425 and rol["bin_count"] > 0

    r_exp = runner.invoke(cli_main, ["telemetry", "export-fixture", "--source", str(lake_dir), "--output", str(out_fix), "--max-rows", "25", "--format", "csv", "--db-path", str(db_path), "--json"])
    assert r_exp.exit_code == 0, r_exp.output
    exp = json.loads(r_exp.output)
    assert exp["row_count"] == 25 and exp["gps_stripped"] is True and out_fix.is_file()

    r_watch = runner.invoke(
        cli_main,
        ["telemetry", "watch", "--no-persist", "--max-frames", "5", "--json"],
    )
    assert r_watch.exit_code == 0, r_watch.output
    watch_payload = json.loads(r_watch.output)
    assert watch_payload["handshake_ok"] is True
    assert watch_payload["exit_reset_sent"] is True
    assert watch_payload["frame_count"] == 5
    assert watch_payload["persisted"] is False
    assert watch_payload["parquet_chunks"] == []

    script_mod = _load_ingest_script()
    s_lake, s_db = tmp_path / "script_lake", tmp_path / "script.sqlite"
    rc = script_mod.main(["--private-root", str(src_dir), "--lake-dir", str(s_lake), "--db-path", str(s_db), "--no-export-fixtures"])
    assert rc == 0
    assert summarize_telemetry_lake(s_lake, db_path=s_db)["total_rows"] == 425


def test_reingestion_idempotency_and_car_id_move(tmp_path: Path) -> None:
    """Verify stale chunks/catalog rows and old-partition files are pruned on re-ingestion."""
    base_ns = 1_546_300_800_000_000_000
    s_two = [
        TelemetrySample(ts_utc_ns=base_ns, rpm=1200.0, map_kpa=100.0, tps_pct=10.0, act_c=25.0),
        TelemetrySample(ts_utc_ns=base_ns + 35_000_000_000, rpm=1200.0, map_kpa=100.0, tps_pct=10.0, act_c=25.0),
    ]
    s_one = [TelemetrySample(ts_utc_ns=base_ns, rpm=1200.0, map_kpa=100.0, tps_pct=10.0, act_c=25.0)]

    lake_no_db = tmp_path / "lake_no_db"
    write_session_parquet_chunks({"session_id": "idem1", "car_id": "car_a", "rows": s_two}, lake_dir=lake_no_db, db_path=None)
    assert len(list(lake_no_db.rglob("*.parquet"))) == 2
    write_session_parquet_chunks({"session_id": "idem1", "car_id": "car_a", "rows": s_one}, lake_dir=lake_no_db, db_path=None)
    rem = list(lake_no_db.rglob("*.parquet"))
    assert len(rem) == 1 and rem[0].name == "chunk_0000.parquet"

    lake_db, db_path = tmp_path / "lake_db", tmp_path / "cat.sqlite"
    write_session_parquet_chunks({"session_id": "idem2", "car_id": "CHRYSLER_UNKNOWN", "rows": s_two}, lake_dir=lake_db, db_path=db_path)
    write_session_parquet_chunks({"session_id": "idem2", "car_id": "CHRYSLER_UNKNOWN", "rows": s_one}, lake_dir=lake_db, db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM parquet_chunks WHERE session_id = 'idem2'").fetchone()[0]
    finally:
        conn.close()
    assert cnt == 1 and len(list(lake_db.rglob("*.parquet"))) == 1

    write_session_parquet_chunks({"session_id": "idem2", "car_id": "ksmec_89", "rows": s_one}, lake_dir=lake_db, db_path=db_path)
    moved = list(lake_db.rglob("*.parquet"))
    assert len(moved) == 1 and "car_id=ksmec_89" in moved[0].as_posix()
    rollups = query_load_cell_rollups(lake_db, session_id="idem2")
    assert len(rollups) == 1 and rollups[0]["car_id"] == "ksmec_89" and rollups[0]["sample_count"] == 1


def test_security_quarantine_and_cleanroom_boundaries(tmp_path: Path) -> None:
    """Verify partition traversal rejection, PII/Deadbolt scrub, unknown session error, and path quarantine."""
    sample = [TelemetrySample(ts_utc_ns=1_546_300_800_000_000_000, rpm=1000.0, map_kpa=100.0)]
    for key in ("car_id", "build_id", "session_id"):
        for bad in ("../esc", "a/b", "x=y"):
            payload = {"session_id": "s1", "car_id": "c1", "build_id": "STOCK", "rows": sample}
            payload[key] = bad
            with pytest.raises(ValueError, match="Invalid Hive partition segment"):
                write_session_parquet_chunks(payload, lake_dir=tmp_path / "sec_lake", db_path=None)

    dirty_src = tmp_path / "dirty_notes.csv"
    bad_note = f"{'mc' + 'clurg'} {'st' + 'eve'} {'flash_cmd 0x' + '41'} {'Fl' + 'ash.frm'}"
    dirty_src.write_text(
        f"session_id,car_id,build_id,run_kind,ts_utc_ns,rpm,notes\n"
        f"s1,car1,STOCK,VEHICLE_SCI,1546300800000000000,1250,{bad_note}\n",
        encoding="utf-8",
    )
    out_json, db_path = tmp_path / "clean.json", tmp_path / "sec_cat.sqlite"
    export_stimulus_fixture(dirty_src, out_json, fmt="json")
    exported_txt = out_json.read_text(encoding="utf-8")
    for tok in ("mc" + "clurg", "st" + "eve", "0x" + "41", "Fl" + "ash.frm"):
        assert tok not in exported_txt
    assert "[REDACTED_NAME]" in exported_txt and "[REDACTED_CMD]" in exported_txt and "[REDACTED_TOOL]" in exported_txt

    lake_dir = tmp_path / "sec_lake2"
    write_session_parquet_chunks({"session_id": "real_s", "car_id": "car1", "rows": sample}, lake_dir=lake_dir, db_path=db_path)
    with pytest.raises(ValueError, match="Unknown source session_id"):
        export_stimulus_fixture(lake_dir, tmp_path / "missing.csv", db_path=db_path, session_id="ghost_s")

    priv = tmp_path / "priv"
    q1 = priv / "QUARANTINE_PII_DO_NOT_INGEST" / "raw.csv"
    q2 = priv / "PortMon" / "x.csv"
    ok = priv / "logs" / "ok.csv"
    for p in (q1, q2, ok):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("TimeStamp,RPM,MAP3BAR,COOL,TPSV,AIR FUEL\n00:01.0,1000,0,180,0.5,14.5\n", encoding="utf-8")
    for qp in (q1, q2):
        with pytest.raises(QuarantinedPathError):
            parse_telemetry_file(qp)
    assert discover_private_logs(priv) == [ok]


def test_parser_sensor_rails_monotonicity_and_mpscan_variants(tmp_path: Path) -> None:
    """Verify CTS/ACT/O2/neg-EGT rails, monotonic clamping, 15col ADV, blanks, and <60s second pull."""
    lasky_rail = tmp_path / "lasky_rail.csv"
    lasky_rail.write_text(
        "Session: Session 1\ntime,LM1_O2,RPM,Charge_temp,MAP,TPS,CTS\n(sec),(AFR),(RPM),(degF),(PSI),(Volt),(degF)\n"
        "2.0,14.5,1000,350.0,-10.0,0.5,350.0\n"
        "2.0,3.0,1100,-50.0,-10.0,0.6,-50.0\n"
        "1.5,28.0,1200,80.0,-10.0,0.6,180.0\n",
        encoding="utf-8",
    )
    lr = parse_telemetry_file(lasky_rail)[0]
    assert lr.samples[0].act_c is None and lr.samples[0].cts_c is None
    assert "ACT_SENSOR_RAIL" in (lr.samples[0].quarantine_reason or "")
    assert "CTS_SENSOR_RAIL" in (lr.samples[0].quarantine_reason or "")
    assert lr.samples[1].afr is None and "O2_SENSOR_RAIL" in (lr.samples[1].quarantine_reason or "")
    assert lr.samples[2].afr is None and lr.samples[1].ts_utc_ns == lr.samples[0].ts_utc_ns + 1
    assert lr.samples[2].ts_utc_ns == lr.samples[1].ts_utc_ns + 1

    mp15 = tmp_path / "mp15.csv"
    mp15.write_text(
        "TimeStamp,RPM,MAP3BAR,COOL,TPSV,AIR FUEL,ADV,EGT,VSPD\n"
        "06/09/2019 20:38:12.000,1500,-5.0,180,0.6,14.5,18.5,-60.0,45\n"
        "06/09/2019 20:38:12.500,1600,-4.0,180,0.7,14.4,,,\n",
        encoding="utf-8",
    )
    m = parse_telemetry_file(mp15)[0]
    assert m.source_format == "mpscan_android_15col"
    assert m.samples[0].spark_deg_btdc == pytest.approx(18.5)
    assert m.samples[0].egt_c is None and "OPEN_THERMOCOUPLE_EGT_RAIL" in (m.samples[0].quarantine_reason or "")
    assert m.samples[1].spark_deg_btdc is None and m.samples[1].egt_c is None and m.samples[1].vspd_mph is None

    wot_pull2 = tmp_path / "wot_short_reset.csv"
    wot_pull2.write_text(
        "TimeStamp,RPM,MAP3BAR,COOL,TPSV,AIR FUEL\n"
        "00:10.0,2000,1.0,180,1.0,12.5\n"
        "00:25.0,3000,5.0,181,3.8,12.1\n\n"
        "00:00.0,2200,2.0,182,1.2,12.4\n",
        encoding="utf-8",
    )
    wp = parse_telemetry_file(wot_pull2)[0]
    assert wp.samples[2].ts_utc_ns > wp.samples[1].ts_utc_ns
    assert (wp.samples[2].ts_utc_ns - wp.samples[1].ts_utc_ns) / 1e9 == pytest.approx(3575.0, abs=1e-6)

    bad_cols = tmp_path / "missing_cool_tpsv.csv"
    bad_cols.write_text("TimeStamp,RPM,MAP3BAR,AIR FUEL\n00:01.0,1000,0,14.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing required CSV telemetry columns"):
        parse_telemetry_file(bad_cols)
