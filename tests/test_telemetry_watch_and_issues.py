"""Tests for ``logicaide telemetry watch`` CLI, fixture replay, and tuning_issues helpers."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from click.testing import CliRunner
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import serial

from logicaide.cli import main
from logicaide.telemetry.issues import bookmark_tuning_issue, list_bookmarks, list_tuning_issues
from logicaide.telemetry.watch_cli import run_telemetry_watch


def test_cli_watch_no_persist_and_calm_cockpit_text(tmp_path: Path) -> None:
    """Verify --no-persist JSON mode and Calm Cockpit non-JSON summary with CSX profile."""
    runner = CliRunner()
    res_json = runner.invoke(
        main,
        ["telemetry", "watch", "--no-persist", "--max-frames", "8", "--rom-id", "smec_88_89_v19", "--json"],
    )
    assert res_json.exit_code == 0, res_json.output
    payload = json.loads(res_json.output)
    assert payload["persisted"] is False
    assert payload["parquet_chunks"] == []
    assert payload["frame_count"] == 8
    assert payload["handshake_ok"] is True
    assert payload["exit_reset_sent"] is True
    assert payload["rom_id"] == "smec_88_89_v19"

    lake_dir = tmp_path / "csx_lake"
    db_path = tmp_path / "csx.sqlite"
    res_text = runner.invoke(
        main,
        [
            "telemetry", "watch",
            "--profile", "profiles/examples/csx_89_neon_dohc.json",
            "--max-frames", "4",
            "--lake-dir", str(lake_dir),
            "--db-path", str(db_path),
        ],
    )
    assert res_text.exit_code == 0, res_text.output
    assert "csx_89_neon_dohc" in res_text.output
    assert "smec_88_89_v19" in res_text.output
    assert "Active channels:" in res_text.output
    assert "Parquet chunks" in res_text.output


def test_cli_watch_profile_drop_reprobe_bookmark_and_rollups(tmp_path: Path) -> None:
    """Verify drop->reprobe recovery, Parquet null-vs-live transition, bookmarking, and DuckDB rollups."""
    runner = CliRunner()
    lake_dir = tmp_path / "lake"
    db_path = tmp_path / "catalog.sqlite"

    res = runner.invoke(
        main,
        [
            "telemetry", "watch",
            "--profile", "profiles/examples/shadow_90_sbec_t2.json",
            "--max-frames", "8",
            "--drop-offset", "TpsVolts",
            "--reprobe", "TpsVolts",
            "--reprobe-at-frame", "3",
            "--bookmark-frame", "5",
            "--bookmark-note", "Boost taper check",
            "--lake-dir", str(lake_dir),
            "--db-path", str(db_path),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["car_id"] == "shadow_90_sbec_t2"
    assert payload["frame_count"] == 8
    assert payload["persisted"] is True
    assert payload["quarantined_cells"] == []
    assert any(ev["event"] == "REPROBE_STARTED" for ev in payload["events"])
    assert len(payload["bookmarks"]) == 1

    chunk_file = Path(payload["parquet_chunks"][0])
    assert chunk_file.is_file()
    table = pq.read_table(chunk_file)
    tps_vals = table["tps_pct"].to_pylist()
    assert tps_vals[:3] == [None, None, None]
    assert all(val is not None for val in tps_vals[3:])

    issue_id = payload["bookmarks"][0]["issue_id"]
    assert table["issue_id"].to_pylist()[5] == issue_id

    issues = list_tuning_issues(db_path, car_id="shadow_90_sbec_t2", session_id=payload["session_id"])
    bookmarks = list_bookmarks(db_path, session_id=payload["session_id"])
    assert len(issues) == 1 and issues[0]["issue_id"] == issue_id
    assert len(bookmarks) == 1 and issue_id in (bookmarks[0]["note"] or "")

    rollups_res = runner.invoke(
        main,
        ["telemetry", "rollups", "--lake-dir", str(lake_dir), "--session-id", payload["session_id"], "--json"],
    )
    assert rollups_res.exit_code == 0, rollups_res.output
    rollups_json = json.loads(rollups_res.output)
    assert rollups_json["bin_count"] >= 1


def test_cli_watch_fixture_replay_and_gps_prohibition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay GPS-stripped CSV/Parquet fixtures and reject fixtures containing GPS columns."""
    runner = CliRunner()
    csv_path = tmp_path / "clean_fixture.csv"
    csv_path.write_text(
        "ts_utc_ns,dead_cell_quarantine,rpm,map_kpa,tps_pct,pw_ms,spark_deg_btdc,act_c,cts_c,batt_v,afr\n"
        "1000000000,false,3100.0,135.0,45.0,8.2,22.5,33.0,89.0,14.1,12.8\n"
        "1050000000,false,3400.0,145.0,55.0,9.4,21.5,34.0,89.5,14.1,12.4\n",
        encoding="utf-8",
    )
    pq_fixture = tmp_path / "clean_fixture.parquet"
    pq.write_table(
        pa.Table.from_pydict({
            "ts_utc_ns": [1000000000, 1050000000],
            "dead_cell_quarantine": [False, False],
            "rpm": [3100.0, 3400.0],
            "map_kpa": [135.0, 145.0],
            "tps_pct": [45.0, 55.0],
            "pw_ms": [8.2, 9.4],
            "spark_deg_btdc": [22.5, 21.5],
            "act_c": [33.0, 34.0],
            "cts_c": [89.0, 89.5],
            "batt_v": [14.1, 14.1],
            "afr": [12.8, 12.4],
        }),
        pq_fixture,
    )

    for idx, fix in enumerate((csv_path, pq_fixture)):
        lake = tmp_path / f"lake_{idx}"
        db = tmp_path / f"cat_{idx}.sqlite"
        res = runner.invoke(
            main,
            [
                "telemetry", "watch", "--fixture", str(fix), "--max-frames", "2",
                "--lake-dir", str(lake), "--db-path", str(db), "--json",
            ],
        )
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        tbl = pq.read_table(payload["parquet_chunks"][0])
        assert tbl["rpm"].to_pylist() == pytest.approx([3100.0, 3400.0], abs=1.0)
        assert tbl["map_kpa"].to_pylist() == pytest.approx([135.0, 145.0], abs=1.5)

    gps_csv = tmp_path / "gps_leak.csv"
    gps_csv.write_text(
        "ts_utc_ns,rpm,map_kpa,lat,lon,gps_track\n"
        "1000000000,2000,70,42.33,-83.04,home\n",
        encoding="utf-8",
    )
    bad_res = runner.invoke(
        main,
        ["telemetry", "watch", "--fixture", str(gps_csv), "--no-persist", "--max-frames", "2"],
    )
    assert bad_res.exit_code != 0
    assert "GPS" in bad_res.output

    orig_open = serial.serial_for_url
    opened: list[object] = []
    closed: list[object] = []

    def _tracked_open(url: str, *args: object, **kwargs: object) -> object:
        handle = orig_open(url, *args, **kwargs)
        opened.append(handle)
        orig_close = handle.close

        def _tracked_close() -> None:
            closed.append(handle)
            orig_close()

        handle.close = _tracked_close  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(serial, "serial_for_url", _tracked_open)
    with pytest.raises(ValueError, match="GPS"):
        run_telemetry_watch(fixture_path=gps_csv, persist=False, max_frames=2)
    assert len(opened) == len(closed)


def test_cli_watch_deadbolt_and_unverified_offset_errors(tmp_path: Path) -> None:
    """Reject physical/remote ports with SECURITY.md and unverified offsets with No Verified Source Match."""
    runner = CliRunner()
    for bad_port in ("/dev/tty" + "USB0", "COM3", "socket://127.0.0.1:9000"):
        res = runner.invoke(main, ["telemetry", "watch", "--no-persist", "--port", bad_port])
        assert res.exit_code != 0
        assert "SECURITY.md" in res.output

    # Synthetic catalog where 0xF2:0xFF is intentionally unverified/absent.
    man_dir = tmp_path / "test_unver"
    man_dir.mkdir(parents=True)
    (man_dir / "rom_manifest.json").write_text(
        json.dumps({
            "rom_id": "test_unver",
            "ecu_family": "SBEC_SINGLE_BOARD",
            "sci_baud": 62500,
            "sci_tables": ["0xF2"],
            "ram_watchlist": [
                {"sci_table": "0xF2", "offset": 0x79, "symbol": "EngineRpm_HB", "source_verified": True},
                {"sci_table": "0xF2", "offset": 0x7A, "symbol": "EngineRpm_LB", "source_verified": True},
                {"sci_table": "0xF2", "offset": 0x82, "symbol": "MapValue", "source_verified": True},
                {"sci_table": "0xF2", "offset": 0xFF, "symbol": "UnverifiedCell", "source_verified": False},
            ],
        }),
        encoding="utf-8",
    )
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(
        json.dumps({"manifests": [{"rom_id": "test_unver", "manifest_ref": "test_unver/rom_manifest.json"}]}),
        encoding="utf-8",
    )

    for flag in ("--drop-offset", "--extra"):
        res_syn = runner.invoke(
            main,
            [
                "telemetry", "watch", "--no-persist", "--max-frames", "1",
                "--catalog", str(cat_path), "--rom-id", "test_unver", flag, "0xF2:0xFF",
            ],
        )
        assert res_syn.exit_code != 0
        assert "No Verified Source Match" in res_syn.output

        res_sbec = runner.invoke(
            main,
            ["telemetry", "watch", "--no-persist", "--max-frames", "1", flag, "0xF2:0x34"],
        )
        assert res_sbec.exit_code != 0
        assert "No Verified Source Match" in res_sbec.output


def test_issues_catalog_helpers_and_validations(tmp_path: Path) -> None:
    """Validate bookmark_tuning_issue, list_tuning_issues filters, list_bookmarks, and input guards."""
    db_path = tmp_path / "issues.sqlite"

    with pytest.raises(ValueError, match="GPS"):
        bookmark_tuning_issue(db_path, "CAR_A", operating_point={"latitude": 42.3, "rpm": 3200})
    with pytest.raises(ValueError, match="severity"):
        bookmark_tuning_issue(db_path, "CAR_A", severity="FATAL")
    with pytest.raises(ValueError, match="source"):
        bookmark_tuning_issue(db_path, "CAR_A", source="KEYBOARD")

    b1 = bookmark_tuning_issue(
        db_path, "CAR_A", session_id="SESS_1", frame_ts_utc_ns=1000, sample_idx=3,
        operating_point={"rpm": 4200, "map_kpa": 185.0},
        active_offsets=(0x79, 0x82), quarantined_offsets=("0xF2:0x86",),
        note="Knock spike near /home/driver/log.csv", title="WOT knock /Users/driver",
        severity="CRITICAL", category="KNOCK_EVENT", source="SPACEBAR",
    )
    b2 = bookmark_tuning_issue(
        db_path, "CAR_B", session_id=None, operating_point={"rpm": 1200, "map_kpa": 45.0},
        note="Cold idle note", severity="INFO", source="AUTO_ANOMALY",
    )

    assert b1["bookmark_id"] is not None
    assert b2["bookmark_id"] is None
    assert "/home/" not in b1["description"] and "/Users/" not in b1["title"]

    assert len(list_tuning_issues(db_path)) == 2
    assert len(list_tuning_issues(db_path, car_id="CAR_A")) == 1
    assert len(list_tuning_issues(db_path, session_id="SESS_1")) == 1
    assert len(list_tuning_issues(db_path, status="OPEN")) == 2
    assert len(list_tuning_issues(db_path, status="VERIFIED_FIXED")) == 0
    assert len(list_bookmarks(db_path)) == 1
    assert len(list_bookmarks(db_path, session_id="SESS_1")) == 1
    assert len(list_bookmarks(db_path, session_id="NONEXISTENT")) == 0


def test_watch_reprobe_at_frame_zero_and_paired_16bit_drop_reprobe(tmp_path: Path) -> None:
    """Verify reprobe-at-frame 0 recovers startup-quarantined cells and paired 16-bit drop/reprobe."""
    runner = CliRunner()
    res = runner.invoke(
        main,
        [
            "telemetry", "watch", "--no-persist", "--drop-offset", "EngineRpm_LB",
            "--reprobe", "EngineRpm_LB", "--reprobe-at-frame", "0", "--max-frames", "1", "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert any(
        ev["event"] == "CELL_QUARANTINED" and ev.get("reason") == "STARTUP_PROBE_TIMEOUT"
        for ev in payload["events"]
    )
    assert any(ev["event"] == "REPROBE_STARTED" for ev in payload["events"])
    assert payload["quarantined_cells"] == []
    assert payload["latest_sample"]["rpm"] is not None

    dropped = run_telemetry_watch(
        drop_offsets=("EngineRpm_LB",), max_frames=2,
        lake_dir=tmp_path / "drop_lake", db_path=tmp_path / "drop.sqlite",
    )
    assert dropped["latest_sample"].get("rpm") is None
    assert pq.read_table(dropped["parquet_chunks"][0])["rpm"].to_pylist() == [None, None]
    assert dropped["quarantined_cells"]

    rec_sym = run_telemetry_watch(
        drop_offsets=("EngineRpm_LB",), reprobe_offsets=("EngineRpm_LB",),
        reprobe_at_frame=1, persist=False, max_frames=3,
    )
    assert rec_sym["quarantined_cells"] == []
    assert rec_sym["latest_sample"]["rpm"] is not None

    rec_chan = run_telemetry_watch(
        drop_offsets=("rpm",), reprobe_offsets=("rpm",),
        reprobe_at_frame=1, persist=False, max_frames=3,
    )
    assert rec_chan["quarantined_cells"] == []
    assert rec_chan["latest_sample"]["rpm"] is not None


def test_watch_validation_rejections_and_profile_override_no_template_insert(tmp_path: Path) -> None:
    """Verify invalid watch bounds fail cleanly and --car-id override skips template vehicle insert."""
    runner = CliRunner()
    bad_arg_sets = (
        ["--bookmark-frame", "-1"],
        ["--bookmark-frame", "5", "--max-frames", "5"],
        ["--reprobe", "TpsVolts", "--reprobe-at-frame", "5", "--max-frames", "5"],
        ["--duration-s", "nan"],
        ["--poll-interval-ms", "-1"],
    )
    for bad_args in bad_arg_sets:
        bad_res = runner.invoke(main, ["telemetry", "watch", "--no-persist", *bad_args])
        assert bad_res.exit_code != 0, f"Expected failure for {bad_args}"

    lake_dir = tmp_path / "ov_lake"
    db_path = tmp_path / "ov.sqlite"
    ok_res = runner.invoke(
        main,
        [
            "telemetry", "watch",
            "--profile", "profiles/examples/shadow_90_sbec_t2.json",
            "--rom-id", "smec_88_89_v19",
            "--car-id", "OVERRIDE_CAR",
            "--max-frames", "2",
            "--lake-dir", str(lake_dir),
            "--db-path", str(db_path),
            "--json",
        ],
    )
    assert ok_res.exit_code == 0, ok_res.output
    payload = json.loads(ok_res.output)
    assert payload["rom_id"] == "smec_88_89_v19"
    assert payload["car_id"] == "OVERRIDE_CAR"

    with sqlite3.connect(db_path) as conn:
        car_ids = {row[0] for row in conn.execute("SELECT car_id FROM vehicles").fetchall()}
    assert "shadow_90_sbec_t2" not in car_ids


def test_issues_nested_gps_empty_car_id_and_whitespace_issue_id(tmp_path: Path) -> None:
    """Verify nested GPS keys, empty car_id, and whitespace issue_id fallback in bookmark_tuning_issue."""
    db_path = tmp_path / "edge_issues.sqlite"

    with pytest.raises(ValueError, match="GPS"):
        bookmark_tuning_issue(db_path, "CAR_A", operating_point={"nested": {"lat": 42.3}})
    with pytest.raises(ValueError):
        bookmark_tuning_issue(db_path, "")

    bm = bookmark_tuning_issue(db_path, "CAR_A", issue_id="   ")
    assert bm["issue_id"].startswith("iss_") and len(bm["issue_id"]) > 4

