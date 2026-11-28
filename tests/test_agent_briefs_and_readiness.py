"""Tests for Agent Briefs, GPS/PII scrubbing, workbench readiness, and new CLI commands."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from click.testing import CliRunner
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from logicaide.catalog.sqlite_store import connect_catalog, upsert_vehicle
from logicaide.cli import main
from logicaide.heritage.rag_index import HERITAGE_CORE_DDL, build_heritage_index
from logicaide.sim.replay import run_single_replay
from logicaide.telemetry.issues import (
    create_or_update_tuning_issue, get_tuning_issue, list_bookmarks, list_tuning_issues,
)
from logicaide.telemetry.parquet_chunker import CANONICAL_PARQUET_SCHEMA, write_session_parquet_chunks
from logicaide.telemetry.rollups import query_drive_timeseries, query_load_cell_rollups
from logicaide.workbench.briefs import (
    NOT_IN_WATCHLIST, build_candidate_build_brief, build_issue_brief,
    build_load_cell_brief, build_session_brief, format_brief_markdown, generate_agent_brief,
)
from logicaide.workbench.readiness import check_workbench_readiness, format_readiness_text

_EMAIL_SAMPLE = "driver" + "@example.com"
_MAC_PATH_SAMPLE = "/Users/" + "tester/drive.csv"
_POSIX_PATH_SAMPLE = "/ho" + "me/tester/drive.csv"
_CLEANROOM_SAMPLE = "Fl" + "ash.f" + "rm /de" + "v/tty" + "USB0"
_GPS_LAT, _GPS_LON = 42.3314, -83.0458
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_mcp_mod(name: str, rel_path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / rel_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_vehicle_and_session(
    tmp_path: Path, *, session_id: str = "sess_alpha", car_id: str = "daytona_89_sbec",
    ecu_family: str = "SBEC_SINGLE_BOARD", include_pii: bool = False,
) -> tuple[Path, Path]:
    """Create isolated SQLite catalog + Parquet lake with active, quarantined, and unpolled data."""
    lake_dir, db_path, base_ns = tmp_path / "lake", tmp_path / "catalog.sqlite", 1_700_000_000_000_000_000
    rows = [
        {"ts_utc_ns": base_ns, "dead_cell_quarantine": False, "rpm": 3550.0, "map_kpa": 162.0, "boost_psi": 8.8,
         "tps_pct": 85.0, "pw_ms": 10.0, "spark_deg_btdc": 18.0, "act_c": 35.0, "cts_c": 88.0, "batt_v": 14.0, "afr": None, "knock_counts": 2},
        {"ts_utc_ns": base_ns + 100_000_000, "dead_cell_quarantine": False, "rpm": 3620.0, "map_kpa": 168.0, "boost_psi": 9.67,
         "tps_pct": 92.0, "pw_ms": 13.0, "spark_deg_btdc": 16.0, "act_c": 36.0, "cts_c": 89.0, "batt_v": 13.6, "afr": None, "knock_counts": 3},
        {"ts_utc_ns": base_ns + 200_000_000, "dead_cell_quarantine": True, "rpm": 4100.0, "map_kpa": 180.0, "boost_psi": 11.4,
         "tps_pct": 95.0, "pw_ms": 15.0, "spark_deg_btdc": 14.0, "act_c": 38.0, "cts_c": 90.0, "batt_v": 13.5, "afr": None, "knock_counts": 0},
    ]
    notes = f"Contact {_EMAIL_SAMPLE} log {_MAC_PATH_SAMPLE} or {_POSIX_PATH_SAMPLE}" if include_pii else "Clean pull"
    write_session_parquet_chunks(
        {"session_id": session_id, "car_id": car_id, "build_id": "BUILD_T2", "run_kind": "VEHICLE_SCI",
         "ecu_family": ecu_family, "rom_sha256": "a" * 64,
         "quarantined_cells": [{"frame_idx": 2, "reason": "OPEN_THERMOCOUPLE_EGT_RAIL"}], "notes": notes, "rows": rows},
        lake_dir=lake_dir, db_path=db_path,
    )
    return lake_dir, db_path


def _seed_candidate_build(
    tmp_path: Path, *, build_id: str, status: str, car_id: str = "daytona_89_sbec",
) -> tuple[Path, Path]:
    """Seed an isolated SQLite candidate_builds row and staging gate_summary.json."""
    db_path, staging_root = tmp_path / "catalog.sqlite", tmp_path / "staging"
    build_dir = staging_root / build_id
    build_dir.mkdir(parents=True, exist_ok=True)
    rom_path = build_dir / f"{build_id}.bin"
    rom_path.write_bytes(b"\xFF" * 32)
    is_pass = status == "PASS"
    with connect_catalog(db_path) as conn:
        upsert_vehicle(conn, {"car_id": car_id, "year": 1989, "model": "Shelby Daytona", "ecu_family": "SBEC_SINGLE_BOARD",
                              "engine_profile_id": "2.5L_K+8V_782", "transaxle_code": "A555", "pinout_verified": 1})
        conn.execute(
            """INSERT INTO candidate_builds (
                build_id, car_id, campaign_id, baseline_sha256, candidate_sha256, staging_dir,
                vectors_ok, max_loop_cycles, twin_parity_ok, sweep_overflows, safety_gate_status, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, '2026-11-27T00:00:00+00:00')""",
            (build_id, car_id, "b" * 64, "c" * 64, str(build_dir),
             1 if is_pass else 0, 6420 if is_pass else 24000, 1 if is_pass else 0, 0 if is_pass else 2, status),
        )
    reasons = [] if is_pass else ["Loop budget exceeded 22000 cycles", "0-LSB parity mismatch"]
    (build_dir / "gate_summary.json").write_text(
        json.dumps({"build_id": build_id, "car_id": car_id, "status": status, "twin_parity_ok": is_pass,
                    "vectors_ok": is_pass, "max_loop_cycles": 6420 if is_pass else 24000,
                    "max_loop_ms": 3.21 if is_pass else 12.0, "sweep_overflows": 0 if is_pass else 2,
                    "baseline_sha256": "b" * 64, "candidate_sha256": "c" * 64,
                    "rom_path": str(rom_path), "blocking_reasons": reasons}),
        encoding="utf-8",
    )
    return db_path, staging_root


def _seed_heritage_corpus(root: Path) -> tuple[Path, Path, Path]:
    """Create minimal cleanroom knowledge/ and wiki/ trees plus built heritage_index.sqlite."""
    know_sub, wiki_dir = root / "knowledge" / "tier2_verified", root / "wiki"
    know_sub.mkdir(parents=True, exist_ok=True)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (know_sub / "sample_pinout.md").write_text(
        '> **Scope**: Synthetic cleanroom digest for sample-pinout\n'
        '> **Boundary**: Read-only test fixture for offline readiness validation only.\n\n'
        '---\ndoc_id: "sample-pinout"\ntitle: "Sample Pinout"\ntrust_tier: "Tier 2"\nsource_type: "FSM_NOTES"\n'
        'source_citation: "1989 FSM Cleanroom"\nwarc_capture_date: "2026-11-26T00:00:00Z"\npage_ref: "p.1"\n'
        'ecu_families: ["SBEC_SINGLE_BOARD"]\necu_scope: ["SBEC_SINGLE_BOARD"]\nengine_scope: ["2.5L_K"]\n'
        'hex_symbols: ["0x63"]\nsymbols_referenced: ["MapValue"]\npins_referenced: ["Pin 11"]\n'
        'mermaid_verification: "NONE"\ncleanroom_status: "DISTILLED_ATTRIBUTED"\nverified_by: "pytest"\n---\n\n'
        '# Sample Pinout\n\nPin 11 continuity check.\n',
        encoding="utf-8",
    )
    (wiki_dir / "_Sidebar.md").write_text("- [Home](Home.md)\n", encoding="utf-8")
    (wiki_dir / "Home.md").write_text(
        '---\ntitle: "Home"\nslug: "home"\ncategory: "portal"\ntier: "Tier 2"\nprovenance: "Cleanroom"\n'
        'ecu_scope: ["SBEC_SINGLE_BOARD"]\nengine_scope: ["2.5L_K"]\nstatus: "verified"\n'
        'last_verified: "2026-11-26"\ntags: ["portal"]\n---\n\n# Home\n\nSMEC and SBEC baseline notes.\n',
        encoding="utf-8",
    )
    her_db = root / "heritage_index.sqlite"
    build_heritage_index(knowledge_dir=root / "knowledge", wiki_dir=wiki_dir, db_path=her_db, verify_frontmatter=True)
    return root / "knowledge", wiki_dir, her_db


def _seed_rom_manifests(root: Path) -> tuple[Path, Path]:
    """Create a valid partitioned ROM manifest catalog and tags file in tmp_path."""
    rom_dir = root / "manifests"
    sub = rom_dir / "sbec_90_93_v3_1"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "rom_manifest.json").write_text(json.dumps({
        "rom_id": "sbec_90_93_v3_1", "ecu_family": "SBEC_SINGLE_BOARD",
        "ram_watchlist": [{"symbol": "MapValue", "offset_hex": "0x63", "source_verified": True}],
        "calibration_tables": [{"symbol": "FuelMain"}],
    }), encoding="utf-8")
    cat_path = rom_dir / "catalog.json"
    cat_path.write_text(json.dumps({"manifests": [{"rom_id": "sbec_90_93_v3_1", "manifest_ref": "sbec_90_93_v3_1/rom_manifest.json"}]}), encoding="utf-8")
    tags_path = root / "tags"
    tags_path.write_text("!_TAG_FILE_FORMAT\t2\n", encoding="utf-8")
    return cat_path, tags_path



def test_session_and_load_cell_briefs_api_and_cli(tmp_path: Path) -> None:
    """Verify session and load_cell Agent Briefs across Python API, CLI Markdown/JSON, and errors."""
    lake_dir, db_path = _seed_vehicle_and_session(tmp_path)
    issue = create_or_update_tuning_issue(
        db_path, issue_id="iss_knock_01", car_id="daytona_89_sbec", session_id="sess_alpha",
        severity="CRITICAL", title="High-MAP knock event", note="Check spark retard @ sample 1",
        rpm_min=3500.0, rpm_max=3700.0, map_min_kpa=160.0, map_max_kpa=170.0, create_bookmark=True,
    )
    sess_brief = generate_agent_brief("session", "sess_alpha", db_path=db_path, lake_dir=lake_dir)
    assert sess_brief["car_id"] == "daytona_89_sbec" and sess_brief["ecu_family"] == "SBEC_SINGLE_BOARD"
    assert (sess_brief["total_rows"], sess_brief["active_rows"], sess_brief["quarantined_rows"]) == (3, 2, 1)
    assert sess_brief["peak_rpm"] == pytest.approx(4100.0) and sess_brief["peak_map_kpa"] == pytest.approx(180.0)
    assert sess_brief["peak_boost_psi"] == pytest.approx(11.4, abs=0.05) and sess_brief["max_knock_counts"] == 3
    assert "afr" in sess_brief["unpolled_channels"] and NOT_IN_WATCHLIST in sess_brief["markdown"]
    assert len(sess_brief["bookmarks"]) == 1 and sess_brief["tuning_issues"][0]["issue_id"] == issue["issue_id"]
    assert [m["tool"] for m in sess_brief["mcp_follow_up"]] == ["get_drive_timeseries", "query_load_cell_rollups"]
    with pytest.raises(KeyError):
        build_session_brief("missing_session", db_path=db_path, lake_dir=lake_dir)

    cell_brief = generate_agent_brief("load_cell", 3500, 160, lake_dir=lake_dir, db_path=db_path)
    assert (cell_brief["rpm_bin"], cell_brief["map_bin_kpa"]) == (3500, 160)
    assert cell_brief["tps_regime"] == "WOT" and cell_brief["charge_temp_bin"] == "WARM_20_40C"
    assert cell_brief["mean_pw_ms"] == pytest.approx(11.5, abs=0.01) and cell_brief["mean_spark_deg"] == pytest.approx(17.0, abs=0.01)
    assert cell_brief["mean_afr"] is None and f"Mean AFR: `{NOT_IN_WATCHLIST}`" in cell_brief["markdown"]
    assert cell_brief["total_knock_events"] == 5 and cell_brief["overlapping_issues"][0]["issue_id"] == "iss_knock_01"
    for bad_rpm in (True, -250):
        with pytest.raises(ValueError):
            build_load_cell_brief(bad_rpm, 160, lake_dir=lake_dir, db_path=db_path)  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        build_load_cell_brief(9750, 300, lake_dir=lake_dir, db_path=db_path)

    runner = CliRunner()
    res_md = runner.invoke(main, ["brief", "session", "sess_alpha", "--db-path", str(db_path), "--lake-dir", str(lake_dir)])
    assert res_md.exit_code == 0 and "# Session Agent Brief — sess_alpha" in res_md.output
    res_json = runner.invoke(main, ["brief", "cell", "--rpm-bin", "3500", "--map-bin", "160",
                                    "--db-path", str(db_path), "--lake-dir", str(lake_dir), "--format", "json"])
    assert res_json.exit_code == 0 and json.loads(res_json.output)["mean_afr"] is None


def test_issue_and_candidate_build_briefs_and_deadbolt(tmp_path: Path) -> None:
    """Verify issue brief context and candidate_build PASS/BLOCKED Hardware Deadbolt gating."""
    lake_dir, db_path = _seed_vehicle_and_session(tmp_path)
    create_or_update_tuning_issue(
        db_path, issue_id="iss_lean_02", car_id="daytona_89_sbec", session_id="sess_alpha",
        severity="CAUTION", title="WOT boost transient", note="Verify fuel enrichment", sample_idx=1,
        rpm_min=3500.0, rpm_max=3700.0, map_min_kpa=160.0, map_max_kpa=170.0, create_bookmark=True,
    )
    iss_brief = build_issue_brief("iss_lean_02", db_path=db_path, lake_dir=lake_dir)
    assert iss_brief["issue_id"] == "iss_lean_02" and iss_brief["issue"]["severity"] == "CAUTION"
    assert iss_brief["linked_bookmarks"][0]["sample_idx"] == 1
    assert "Verify fuel enrichment" in iss_brief["linked_bookmarks"][0]["note"]
    assert iss_brief["surrounding_load_cells"] and iss_brief["nearby_frames"]
    with pytest.raises(KeyError):
        build_issue_brief("iss_missing", db_path=db_path, lake_dir=lake_dir)

    runner = CliRunner()
    iss_cli = runner.invoke(main, ["brief", "issue", "iss_lean_02", "--db-path", str(db_path), "--lake-dir", str(lake_dir), "--json"])
    assert iss_cli.exit_code == 0 and json.loads(iss_cli.output)["issue_id"] == "iss_lean_02"

    _, staging_root = _seed_candidate_build(tmp_path, build_id="bld_pass_1", status="PASS")
    pass_brief = generate_agent_brief("candidate_build", "bld_pass_1", db_path=db_path, staging_root=staging_root)
    assert pass_brief["twin_parity_ok"] is True and pass_brief["max_loop_cycles"] == 6420
    assert "Pin 11" in str(pass_brief["pin11_reminder"]) and "minipro" in str(pass_brief["manual_flash_command"]) and "minipro" in pass_brief["markdown"]

    _seed_candidate_build(tmp_path, build_id="bld_blk_1", status="BLOCKED")
    blk_cli = runner.invoke(main, ["brief", "build", "bld_blk_1", "--db-path", str(db_path), "--staging-root", str(staging_root), "--json"])
    assert blk_cli.exit_code == 0, blk_cli.output
    blk_payload = json.loads(blk_cli.output)
    assert blk_payload["safety_gate_status"] == "BLOCKED" and blk_payload["blocking_reasons"]
    assert blk_payload["pin11_reminder"] is None and blk_payload["manual_flash_command"] is None
    assert "DO NOT FLASH" in blk_payload["markdown"] and "minipro" not in blk_payload["markdown"]

    for bad_id in ("../escape", "bad/id"):
        with pytest.raises(ValueError):
            build_candidate_build_brief(bad_id, db_path=db_path, staging_root=staging_root)
    sym_root, sym_dir = tmp_path / "sym_staging", staging_root / "bld_sym_sum"
    sym_root.symlink_to(staging_root)
    sym_dir.mkdir()
    (tmp_path / "real_summary.json").write_text("{}", encoding="utf-8")
    (sym_dir / "gate_summary.json").symlink_to(tmp_path / "real_summary.json")
    for args in (("bld_pass_1", sym_root), ("bld_sym_sum", staging_root)):
        with pytest.raises(ValueError):
            build_candidate_build_brief(args[0], db_path=db_path, staging_root=args[1])


def test_mcp_follow_up_jsonrpc_execution(tmp_path: Path) -> None:
    """Validate and execute mcp_follow_up payloads across all 4 brief kinds via MCP JSON-RPC."""
    ecu_mod = _load_mcp_mod("ecu_telemetry_test", "mcp_servers/ecu_telemetry.py")
    hc11_mod = _load_mcp_mod("hc11_emulator_test", "mcp_servers/hc11_emulator.py")
    lake_dir, db_path = _seed_vehicle_and_session(tmp_path)
    create_or_update_tuning_issue(
        db_path, issue_id="iss_mcp_01", car_id="daytona_89_sbec", session_id="sess_alpha",
        severity="INFO", title="MCP check", rpm_min=3500.0, rpm_max=3700.0, map_min_kpa=160.0, map_max_kpa=170.0, create_bookmark=True,
    )
    _, staging_root = _seed_candidate_build(tmp_path, build_id="bld_mcp_1", status="PASS")
    rom_cat, _ = _seed_rom_manifests(tmp_path)

    sess_b = build_session_brief("sess_alpha", db_path=db_path, lake_dir=lake_dir)
    cell_b = build_load_cell_brief(3500, 160, lake_dir=lake_dir, db_path=db_path)
    iss_b = build_issue_brief("iss_mcp_01", db_path=db_path, lake_dir=lake_dir)
    bld_b = build_candidate_build_brief("bld_mcp_1", db_path=db_path, staging_root=staging_root)
    assert [m["tool"] for m in sess_b["mcp_follow_up"]] == ["get_drive_timeseries", "query_load_cell_rollups"]
    assert [m["tool"] for m in cell_b["mcp_follow_up"]] == ["query_load_cell_rollups", "get_drive_timeseries"]
    assert [m["tool"] for m in iss_b["mcp_follow_up"]] == ["list_bookmarks", "get_drive_timeseries"]
    assert [m["tool"] for m in bld_b["mcp_follow_up"]] == ["query_load_cell_rollups", "inspect_rom_manifest"]
    assert all("car_id" not in item["args"] for item in cell_b["mcp_follow_up"])
    ts_args = cell_b["mcp_follow_up"][1]["args"]
    assert ts_args["rpm_max"] == pytest.approx(3749.999) and ts_args["map_max_kpa"] == pytest.approx(169.999)

    inject = {"get_drive_timeseries": ("lake_dir", str(lake_dir)), "query_load_cell_rollups": ("lake_dir", str(lake_dir)),
              "list_bookmarks": ("db_path", str(db_path)), "inspect_rom_manifest": ("catalog_path", str(rom_cat))}
    for idx, item in enumerate(sess_b["mcp_follow_up"] + cell_b["mcp_follow_up"] + iss_b["mcp_follow_up"] + bld_b["mcp_follow_up"], 1):
        tool, args = item["tool"], dict(item["args"])
        k, v = inject[tool]
        args[k] = v
        mod = hc11_mod if tool == "inspect_rom_manifest" else ecu_mod
        resp = mod.handle_jsonrpc_request({"jsonrpc": "2.0", "id": idx, "method": "tools/call", "params": {"name": tool, "arguments": args}})
        assert "result" in resp and resp["result"]["isError"] is False and json.loads(resp["result"]["content"][0]["text"])


def test_candidate_build_fail_closed_and_cwd_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify P0 candidate_build fail-closed gating, summary/rom validation, and relative CWD flash path."""
    db_path, staging_root = _seed_candidate_build(tmp_path, build_id="bld_ok_false", status="PASS")
    sum_path = staging_root / "bld_ok_false" / "gate_summary.json"
    gs = json.loads(sum_path.read_text(encoding="utf-8"))
    for patch in ({"ok": False}, {"twin_parity_ok": False}):
        sum_path.write_text(json.dumps({**gs, **patch}), encoding="utf-8")
        blk = build_candidate_build_brief("bld_ok_false", db_path=db_path, staging_root=staging_root)
        assert blk["safety_gate_status"] == "BLOCKED" and blk["pin11_reminder"] is None and blk["manual_flash_command"] is None

    _seed_candidate_build(tmp_path, build_id="bld_db_blk", status="PASS")
    _seed_candidate_build(tmp_path, build_id="bld_db_twin", status="PASS")
    with connect_catalog(db_path) as conn:
        conn.execute("UPDATE candidate_builds SET safety_gate_status = 'BLOCKED' WHERE build_id = 'bld_db_blk'")
        conn.execute("UPDATE candidate_builds SET twin_parity_ok = 0 WHERE build_id = 'bld_db_twin'")
    for bid in ("bld_db_blk", "bld_db_twin"):
        blk = build_candidate_build_brief(bid, db_path=db_path, staging_root=staging_root)
        assert blk["safety_gate_status"] == "BLOCKED" and blk["pin11_reminder"] is None and blk["manual_flash_command"] is None

    sum_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        build_candidate_build_brief("bld_ok_false", db_path=db_path, staging_root=staging_root)
    outside_rom = tmp_path / "outside.bin"
    outside_rom.write_bytes(b"\x00" * 16)
    sum_path.write_text(json.dumps({**gs, "rom_path": str(outside_rom)}), encoding="utf-8")
    with pytest.raises(ValueError):
        build_candidate_build_brief("bld_ok_false", db_path=db_path, staging_root=staging_root)

    _seed_candidate_build(tmp_path, build_id="bld_cwd_1", status="PASS")
    monkeypatch.chdir(tmp_path)
    cwd_brief = build_candidate_build_brief("bld_cwd_1", db_path=db_path, staging_root=staging_root)
    assert cwd_brief["safety_gate_status"] == "PASS"
    assert "staging/bld_cwd_1/bld_cwd_1.bin" in str(cwd_brief["manual_flash_command"])
    assert "[REDACTED_PATH]" not in str(cwd_brief["manual_flash_command"]) and "[REDACTED_PATH]" not in cwd_brief["markdown"]


def test_gps_and_pii_scrubbing_in_briefs(tmp_path: Path) -> None:
    """Ensure GPS columns/coordinates, emails, and user home paths are scrubbed from JSON and Markdown."""
    lake_dir, db_path = _seed_vehicle_and_session(tmp_path, include_pii=True)
    gps_part = lake_dir / "run_kind=VEHICLE_SCI" / "car_id=daytona_89_sbec" / "build_id=BUILD_T2" / "session_id=sess_gps"
    gps_part.mkdir(parents=True, exist_ok=True)
    gps_schema = pa.schema([*CANONICAL_PARQUET_SCHEMA, pa.field("lat", pa.float64()), pa.field("lon", pa.float64()), pa.field("gps_track", pa.string())])
    pq.write_table(pa.Table.from_pylist([{
        "ts_utc_ns": 1_700_000_500_000_000_000, "session_id": "sess_gps", "car_id": "daytona_89_sbec", "build_id": "BUILD_T2",
        "run_kind": "VEHICLE_SCI", "campaign_id": None, "issue_id": None, "trial_idx": 0, "dead_cell_quarantine": False,
        "raw_rpm_b": 100, "raw_map_b": 150, "raw_tps_b": 200, "raw_pw_u16": 1000, "raw_spark_b": 36, "raw_act_b": 80,
        "raw_cts_b": 120, "raw_batt_b": 200, "raw_o2_b": None, "raw_knock_b": 0, "rpm": 3200.0, "map_kpa": 140.0,
        "boost_psi": 5.6, "tps_pct": 70.0, "pw_ms": 9.0, "spark_deg_btdc": 20.0, "act_c": 30.0, "cts_c": 88.0,
        "batt_v": 14.0, "afr": 12.5, "knock_counts": 0, "imu_grade_pct": None, "imu_lon_accel_mps2": None,
        "imu_lat_accel_mps2": None, "lat": _GPS_LAT, "lon": _GPS_LON, "gps_track": "route_secret",
    }], schema=gps_schema), gps_part / "chunk_0000.parquet")
    create_or_update_tuning_issue(
        db_path, issue_id="iss_pii_01", car_id="daytona_89_sbec", session_id="sess_gps",
        title=f"Log from {_EMAIL_SAMPLE}", note=f"Saved at {_MAC_PATH_SAMPLE} and {_POSIX_PATH_SAMPLE}",
        rpm_min=3100.0, rpm_max=3300.0, map_min_kpa=135.0, map_max_kpa=145.0, create_bookmark=True,
    )
    sess_gps = build_session_brief("sess_gps", db_path=db_path, lake_dir=lake_dir)
    iss_gps = build_issue_brief("iss_pii_01", db_path=db_path, lake_dir=lake_dir)
    md_direct = format_brief_markdown({
        "kind": "session", "session_id": f"sess_{_EMAIL_SAMPLE}", "lat": _GPS_LAT, "lon": _GPS_LON,
        "gps_track": "secret_track", "notes": f"{_MAC_PATH_SAMPLE} {_POSIX_PATH_SAMPLE}",
    })
    for blob in (json.dumps(sess_gps), sess_gps["markdown"], json.dumps(iss_gps), iss_gps["markdown"], md_direct):
        assert _EMAIL_SAMPLE not in blob and "/Users/" not in blob and "/ho" + "me/" not in blob
        assert str(_GPS_LAT) not in blob and str(_GPS_LON) not in blob and "gps_track" not in blob
        assert '"lat"' not in blob and '"lon"' not in blob

def test_workbench_readiness_and_stale_rebuild(tmp_path: Path) -> None:
    """Verify readiness statuses: missing/partial/strict, corrupt 0-byte, symlink, and stale auto-rebuild."""
    runner, empty_root = CliRunner(), tmp_path / "empty_ws"
    empty_root.mkdir()
    e_kw = dict(db_path=empty_root / "catalog.sqlite", lake_dir=empty_root / "lake",
                rom_catalog_path=empty_root / "manifests" / "catalog.json", tags_path=empty_root / "tags",
                heritage_db_path=empty_root / "heritage_index.sqlite", knowledge_dir=empty_root / "knowledge", wiki_dir=empty_root / "wiki")
    missing_rep = check_workbench_readiness(**e_kw)
    assert missing_rep["overall_status"] == "degraded" and missing_rep["stores"]["catalog_sqlite"]["status"] == "missing"
    assert missing_rep["stores"]["telemetry_lake"]["status"] == "empty" and missing_rep["recommended_actions"]
    assert "DEGRADED" in format_readiness_text(missing_rep)
    assert runner.invoke(main, ["readiness", "--db-path", str(e_kw["db_path"]), "--lake-dir", str(e_kw["lake_dir"]),
                                "--rom-catalog", str(e_kw["rom_catalog_path"]), "--tags-path", str(e_kw["tags_path"]),
                                "--heritage-db", str(e_kw["heritage_db_path"]), "--knowledge-dir", str(e_kw["knowledge_dir"]),
                                "--wiki-dir", str(e_kw["wiki_dir"]), "--strict"]).exit_code == 2

    connect_catalog(empty_root / "empty_cat.sqlite").close()
    (empty_root / "empty_rom_cat.json").write_text('{"manifests": []}', encoding="utf-8")
    her_empty = empty_root / "her_empty.sqlite"
    with sqlite3.connect(her_empty) as hconn:
        hconn.executescript(HERITAGE_CORE_DDL)
    assert check_workbench_readiness(**{**e_kw, "db_path": empty_root / "empty_cat.sqlite",
                                        "rom_catalog_path": empty_root / "empty_rom_cat.json", "heritage_db_path": her_empty})["overall_status"] == "partial"

    zero_db = tmp_path / "zero.sqlite"
    zero_db.write_bytes(b"")
    corrupt_rep = check_workbench_readiness(**{**e_kw, "db_path": zero_db, "rom_catalog_path": empty_root / "empty_rom_cat.json", "heritage_db_path": zero_db})
    assert corrupt_rep["overall_status"] == "degraded" and corrupt_rep["stores"]["catalog_sqlite"]["status"] == "error"
    assert corrupt_rep["stores"]["heritage_index"]["status"] == "error"

    sym_db = tmp_path / "sym_catalog.sqlite"
    sym_db.symlink_to(empty_root / "empty_cat.sqlite")
    with pytest.raises(ValueError):
        check_workbench_readiness(db_path=sym_db)
    assert runner.invoke(main, ["readiness", "--db-path", str(sym_db)]).exit_code != 0

    ws = tmp_path / "ready_ws"
    lake_dir, db_path = _seed_vehicle_and_session(ws)
    know_dir, wiki_dir, her_db = _seed_heritage_corpus(ws)
    rom_cat, tags_path = _seed_rom_manifests(ws)
    r_kw = dict(db_path=db_path, lake_dir=lake_dir, rom_catalog_path=rom_cat, tags_path=tags_path,
                heritage_db_path=her_db, knowledge_dir=know_dir, wiki_dir=wiki_dir)
    ready_rep = check_workbench_readiness(**r_kw)
    assert ready_rep["overall_status"] == "ready" and ready_rep["stores"]["heritage_index"]["status"] == "ready"

    past = time.time() - 20.0
    os.utime(her_db, (past, past))
    (know_dir / "tier2_verified" / "extra_pin.md").write_text(
        (know_dir / "tier2_verified" / "sample_pinout.md").read_text(encoding="utf-8").replace("sample-pinout", "extra-pin"), encoding="utf-8")
    stale_rep = check_workbench_readiness(**r_kw, auto_rebuild=False)
    assert stale_rep["overall_status"] == "degraded" and stale_rep["stores"]["heritage_index"]["status"] == "stale"
    reasons = stale_rep["stores"]["heritage_index"]["stale_reasons"]
    assert any("source_newer:Home.md" in r for r in reasons) and any("missing_doc_ids:extra-pin" in r for r in reasons)

    rebuilt_cli = runner.invoke(main, ["readiness", "--db-path", str(db_path), "--lake-dir", str(lake_dir), "--rom-catalog", str(rom_cat),
                                       "--tags-path", str(tags_path), "--heritage-db", str(her_db), "--knowledge-dir", str(know_dir),
                                       "--wiki-dir", str(wiki_dir), "--auto-rebuild", "--json"])
    assert rebuilt_cli.exit_code == 0, rebuilt_cli.output
    rebuilt_json = json.loads(rebuilt_cli.output)
    assert rebuilt_json["rebuilt"] is True and rebuilt_json["stores"]["heritage_index"]["status"] == "ready"


def test_readiness_manifest_traversal_and_issues_readonly(tmp_path: Path) -> None:
    """Verify manifest_ref/rom_id traversal blocking and issues.py read-only non-creation/non-mutation."""
    ws = tmp_path / "trav_ws"
    lake_dir, db_path = _seed_vehicle_and_session(ws)
    know_dir, wiki_dir, her_db = _seed_heritage_corpus(ws)
    rom_cat, tags_path = _seed_rom_manifests(ws)
    for bad_entry in (
        {"rom_id": "sbec_bad", "manifest_ref": "../outside.json"},
        {"rom_id": "sbec_abs", "manifest_ref": "/etc/passwd"},
        {"rom_id": "../escape", "manifest_ref": "sbec_90_93_v3_1/rom_manifest.json"},
    ):
        rom_cat.write_text(json.dumps({"manifests": [bad_entry]}), encoding="utf-8")
        rep = check_workbench_readiness(db_path=db_path, lake_dir=lake_dir, rom_catalog_path=rom_cat, tags_path=tags_path,
                                        heritage_db_path=her_db, knowledge_dir=know_dir, wiki_dir=wiki_dir)
        assert rep["stores"]["rom_manifests"]["status"] == "error" and rep["overall_status"] == "degraded"

    iss_db = tmp_path / "missing_issues.sqlite"
    assert get_tuning_issue("iss_1", iss_db) is None and list_tuning_issues(iss_db) == [] and list_bookmarks(iss_db) == []
    assert not iss_db.exists()

    zero_db = tmp_path / "zero_issues.sqlite"
    zero_db.write_bytes(b"")
    empty_sqlite = tmp_path / "empty_tables.sqlite"
    with sqlite3.connect(empty_sqlite) as conn:
        conn.execute("CREATE TABLE dummy (id INTEGER)")
    for target in (zero_db, empty_sqlite):
        for fn in (lambda p=target: get_tuning_issue("iss_1", p), lambda p=target: list_tuning_issues(p), lambda p=target: list_bookmarks(p)):
            with pytest.raises(ValueError):
                fn()
    assert zero_db.stat().st_size == 0


def test_rollups_nonfinite_pii_and_brief_cli_errors(tmp_path: Path) -> None:
    """Verify rollups Infinity/NaN & PII scrubbing and brief CLI duckdb.Error handling."""
    lake_dir, k_db = tmp_path / "nan_lake", tmp_path / "k_cat.sqlite"
    part = lake_dir / "run_kind=VEHICLE_SCI" / "car_id=daytona_89_sbec" / "build_id=BUILD_T2" / "session_id=sess_nan"
    part.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{
        "ts_utc_ns": 1_700_000_000_000_000_000, "session_id": "sess_nan", "car_id": "daytona_89_sbec", "build_id": "BUILD_T2",
        "run_kind": "VEHICLE_SCI", "campaign_id": f"camp {_EMAIL_SAMPLE} {_POSIX_PATH_SAMPLE} {_CLEANROOM_SAMPLE}",
        "issue_id": None, "trial_idx": 0, "dead_cell_quarantine": False, "raw_rpm_b": 100, "raw_map_b": 150, "raw_tps_b": 200,
        "raw_pw_u16": 1000, "raw_spark_b": 36, "raw_act_b": 80, "raw_cts_b": 120, "raw_batt_b": 200, "raw_o2_b": None,
        "raw_knock_b": 0, "rpm": 3550.0, "map_kpa": 162.0, "boost_psi": 8.8, "tps_pct": 85.0, "pw_ms": float("inf"),
        "spark_deg_btdc": 18.0, "act_c": float("nan"), "cts_c": 88.0, "batt_v": 14.0, "afr": None, "knock_counts": 1,
        "imu_grade_pct": None, "imu_lon_accel_mps2": None, "imu_lat_accel_mps2": None,
    }], schema=CANONICAL_PARQUET_SCHEMA), part / "chunk_0000.parquet")
    rollups, ts_res = query_load_cell_rollups(lake_dir, session_id="sess_nan"), query_drive_timeseries(lake_dir, session_id="sess_nan")
    assert rollups and rollups[0]["mean_pw_ms"] is None and ts_res["rows"][0]["pw_ms"] is None and ts_res["rows"][0]["act_c"] is None
    for blob in (json.dumps(rollups, allow_nan=False), json.dumps(ts_res, allow_nan=False)):
        assert _EMAIL_SAMPLE not in blob and "/ho" + "me/" not in blob and "Fl" + "ash.f" + "rm" not in blob and "/de" + "v/tty" + "USB0" not in blob

    bad_lake = tmp_path / "bad_lake"
    bad_part = bad_lake / "run_kind=VEHICLE_SCI" / "car_id=c1" / "build_id=b1" / "session_id=s_bad"
    bad_part.mkdir(parents=True, exist_ok=True)
    (bad_part / "chunk_0000.parquet").write_bytes(b"NOT_A_PARQUET_FILE")
    runner = CliRunner()
    for args in (["brief", "session", "s_bad", "--db-path", str(k_db), "--lake-dir", str(bad_lake)],
                 ["brief", "cell", "--rpm-bin", "3500", "--map-bin", "160", "--db-path", str(k_db), "--lake-dir", str(bad_lake)]):
        res = runner.invoke(main, args)
        assert res.exit_code != 0 and "Traceback" not in res.output and "Error:" in res.output


def test_replay_knock_counts_guard(tmp_path: Path) -> None:
    """Verify run_single_replay rejects invalid knock_counts and clamps 99999 -> 65535 in uint16 Parquet."""
    k_lake, k_db = tmp_path / "k_lake", tmp_path / "k_cat.sqlite"
    rep = run_single_replay(operating_points=[{"rpm": 3000, "map_kpa": 140, "knock_counts": 99999}], lake_dir=k_lake, db_path=k_db, write_parquet=True)
    pq_tbl = pq.read_table(rep.parquet_chunks[0])
    assert pq_tbl.schema.field("knock_counts").type == pa.uint16() and pq_tbl["knock_counts"].to_pylist() == [65535]
    for bad_kc in (-1, True, float("inf")):
        with pytest.raises(ValueError):
            run_single_replay(operating_points=[{"rpm": 3000, "map_kpa": 140, "knock_counts": bad_kc}], lake_dir=k_lake, db_path=k_db, write_parquet=False)


def test_cli_sim_replay_splay_and_telemetry_issues(tmp_path: Path) -> None:
    """Verify `sim replay`, `sim splay`, and `telemetry issues list|create|update` CLI commands."""
    runner, lake_dir, db_path = CliRunner(), tmp_path / "sim_lake", tmp_path / "sim_cat.sqlite"
    res_def = runner.invoke(main, ["sim", "replay", "--no-parquet", "--max-frames", "4", "--lake-dir", str(lake_dir), "--db-path", str(db_path), "--json"])
    assert res_def.exit_code == 0 and json.loads(res_def.output)["parity_ok"] is True
    clean_csv, gps_csv, splay_dir, iss_db = tmp_path / "clean_replay.csv", tmp_path / "gps_replay.csv", tmp_path / "splayed_rom", tmp_path / "issues.sqlite"
    clean_csv.write_text("rpm,map_kpa,tps_pct,batt_v\n2400,105,35,14.1\n3600,155,85,14.0\n", encoding="utf-8")
    gps_csv.write_text("rpm,map_kpa,lat,lon\n2400,105,42.3,-83.0\n", encoding="utf-8")
    res_fix = runner.invoke(main, ["sim", "replay", "--fixture", str(clean_csv), "--lake-dir", str(lake_dir), "--db-path", str(db_path), "--json"])
    assert res_fix.exit_code == 0 and json.loads(res_fix.output)["frame_count"] == 2
    res_gps = runner.invoke(main, ["sim", "replay", "--fixture", str(gps_csv), "--no-parquet"])
    assert res_gps.exit_code != 0 and "GPS" in res_gps.output
    assert runner.invoke(main, ["sim", "splay", "--splay-dir", str(splay_dir), "--json"]).exit_code == 0 and (splay_dir / "main.asm").is_file()
    res_empty = runner.invoke(main, ["telemetry", "issues", "list", "--db-path", str(iss_db)])
    assert res_empty.exit_code == 0 and "No tuning issues found." in res_empty.output
    res_create = runner.invoke(main, ["telemetry", "issues", "create", "--car-id", "csx_89_t2", "--session-id", "sess_t1",
                                      "--issue-id", "iss_cli_01", "--severity", "CAUTION", "--title", "Tip-in lean spike",
                                      "--note", "Inspect AE table", "--rpm-min", "2800", "--rpm-max", "3200",
                                      "--map-min-kpa", "120", "--map-max-kpa", "140", "--create-bookmark", "--db-path", str(iss_db), "--json"])
    assert res_create.exit_code == 0 and json.loads(res_create.output)["action"] == "created"
    res_list = runner.invoke(main, ["telemetry", "issues", "list", "--db-path", str(iss_db), "--car-id", "csx_89_t2", "--json"])
    assert res_list.exit_code == 0 and json.loads(res_list.output)["count"] == 1
    res_upd = runner.invoke(main, ["telemetry", "issues", "update", "--issue-id", "iss_cli_01", "--status", "VERIFIED_FIXED",
                                   "--severity", "INFO", "--note", "Fixed in build 2", "--db-path", str(iss_db), "--json"])
    assert res_upd.exit_code == 0 and json.loads(res_upd.output)["status"] == "VERIFIED_FIXED"
    assert runner.invoke(main, ["telemetry", "issues", "update", "--issue-id", "iss_missing", "--status", "OPEN", "--db-path", str(iss_db)]).exit_code != 0
