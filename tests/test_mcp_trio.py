"""End-to-end tests for .mcp.json trio boot, MCP guards, and JSON-RPC validation."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from logicaide.telemetry.parquet_chunker import write_session_parquet_chunks

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = REPO_ROOT / "mcp_servers"
SHADOW_PROFILE = REPO_ROOT / "profiles" / "examples" / "shadow_90_sbec_t2.json"
EXPECTED_SERVERS: dict[str, int] = {"ecu-telemetry": 6, "hc11-emulator": 6, "chrysler-heritage": 7}


def _load_mcp(script_name: str, mod_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(mod_name, MCP_DIR / script_name)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rpc(req_id: int | str | None, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def _run_stdio(cmd: list[str], lines: list[str], timeout: int = 45) -> list[dict[str, Any]]:
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, input="\n".join(lines) + "\n",
        text=True, capture_output=True, check=True, timeout=timeout,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def _lake_row(
    ts_utc_ns: int, rpm: float, map_kpa: float, boost_psi: float, tps_pct: float, pw_ms: float,
    spark: float, act_c: float, cts_c: float, batt_v: float, afr: float | None = None,
    knock: int | None = None, quarantine: bool = False,
) -> dict[str, Any]:
    return {
        "ts_utc_ns": ts_utc_ns, "rpm": rpm, "map_kpa": map_kpa, "boost_psi": boost_psi,
        "tps_pct": tps_pct, "pw_ms": pw_ms, "spark_deg_btdc": spark, "act_c": act_c,
        "cts_c": cts_c, "batt_v": batt_v, "afr": afr, "knock_counts": knock,
        "dead_cell_quarantine": quarantine,
    }


def _seed_telemetry_lake(tmp_path: Path) -> tuple[Path, Path]:
    lake_dir, db_path = tmp_path / "lake", tmp_path / "catalog.sqlite"
    t0 = 1_700_000_000_000_000_000
    session = {
        "session_id": "sess_trio_01", "car_id": "shadow_90_sbec_t2", "build_id": "STOCK",
        "run_kind": "VEHICLE_SCI", "ecu_family": "SBEC_SINGLE_BOARD",
        "rows": [
            _lake_row(t0, 2250.0, 112.0, 1.5, 50.0, 4.2, 22.0, 25.0, 85.0, 14.0),
            _lake_row(t0 + 100_000_000, 2300.0, 115.0, 2.0, 52.0, 4.4, 21.5, 26.0, 85.0, 13.9),
            _lake_row(t0 + 200_000_000, 4300.0, 182.0, 11.7, 88.0, 11.2, 16.0, 45.0, 88.0, 13.7, 11.8, 2),
            _lake_row(t0 + 300_000_000, 3100.0, 135.0, 4.9, 20.0, 5.0, 20.0, 30.0, 86.0, 14.1, 14.2, 0, True),
        ],
    }
    write_session_parquet_chunks(session, lake_dir=lake_dir, db_path=db_path)
    return lake_dir, db_path


def _write_gps_contaminated_lake(tmp_path: Path) -> Path:
    gps_lake = tmp_path / "gps_lake"
    part = gps_lake / "run_kind=VEHICLE_SCI" / "car_id=gps_car" / "build_id=STOCK" / "session_id=sess_gps"
    part.mkdir(parents=True, exist_ok=True)
    table = pa.table({
        "ts_utc_ns": pa.array([1_700_000_000_000_000_000, 1_700_000_000_100_000_000], type=pa.int64()),
        "rpm": pa.array([2500.0, 4800.0], type=pa.float32()),
        "map_kpa": pa.array([110.0, 175.0], type=pa.float32()),
        "boost_psi": pa.array([1.2, 10.7], type=pa.float32()),
        "tps_pct": pa.array([40.0, 90.0], type=pa.float32()),
        "pw_ms": pa.array([4.5, 10.5], type=pa.float32()),
        "spark_deg_btdc": pa.array([22.0, 15.0], type=pa.float32()),
        "act_c": pa.array([25.0, 40.0], type=pa.float32()),
        "cts_c": pa.array([None, None], type=pa.float32()),
        "batt_v": pa.array([14.0, 13.8], type=pa.float32()),
        "afr": pa.array([None, 11.9], type=pa.float32()),
        "knock_counts": pa.array([None, 1], type=pa.uint16()),
        "dead_cell_quarantine": pa.array([False, False], type=pa.bool_()),
        "lat": pa.array([42.33, 42.34], type=pa.float64()),
        "lon": pa.array([-83.04, -83.05], type=pa.float64()),
        "gps_track": pa.array(["trk_1", "trk_2"], type=pa.string()),
    })
    pq.write_table(table, part / "chunk_0000.parquet", compression="zstd", compression_level=3)
    return gps_lake


def test_mcp_json_validity_and_offline_stdio_boot() -> None:
    cfg = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    servers = cfg["mcpServers"]
    assert set(servers) == set(EXPECTED_SERVERS)

    init_req = _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}})
    payload_lines = [json.dumps(init_req), json.dumps(_rpc(2, "tools/list", {}))]

    for name, expected_count in EXPECTED_SERVERS.items():
        entry = servers[name]
        assert entry["command"] == "uv"
        args = entry["args"]
        assert "--offline" in args and "--stdio" in args
        script_rel = next((a for a in args if a.endswith(".py")), "")
        assert script_rel and (REPO_ROOT / script_rel).is_file()

        responses = _run_stdio([entry["command"], *args], payload_lines)
        assert [r["id"] for r in responses] == [1, 2]
        assert responses[0]["result"]["serverInfo"]["name"] == name
        assert len(responses[1]["result"]["tools"]) == expected_count


def test_common_path_guards_and_json_helpers(tmp_path: Path) -> None:
    common = _load_mcp("_common.py", "mcp_common_test")
    real_parent = tmp_path / "real_parent"
    real_parent.mkdir()
    sym_parent = tmp_path / "sym_parent"
    sym_parent.symlink_to(real_parent)
    with pytest.raises(ValueError, match="symlink parent"):
        common.validate_path_arg("db_path", sym_parent / "file.sqlite")

    sym_anchor = tmp_path / "sym_anchor"
    sym_anchor.symlink_to(real_parent)
    with pytest.raises(ValueError, match="symlink anchor"):
        common.validate_path_arg("db_path", "file.sqlite", require_within=sym_anchor)

    assert common.json_safe({"x": float("inf"), "y": float("nan")}) == {"x": None, "y": None}
    assert common.display_repo_path(REPO_ROOT / "data" / "drives" / "catalog.sqlite") == "data/drives/catalog.sqlite"
    ext_path = tmp_path / "external.sqlite"
    assert common.display_repo_path(ext_path) == str(ext_path)


def test_ecu_telemetry_queries_and_lifecycle(tmp_path: Path) -> None:
    ecu = _load_mcp("ecu_telemetry.py", "ecu_telemetry_mcp")
    assert set(ecu.TOOL_REGISTRY) == {
        "list_vehicles", "query_load_cell_rollups", "get_drive_timeseries",
        "list_bookmarks", "create_or_update_tuning_issue", "export_gps_stripped_fixture",
    }

    missing_db = tmp_path / "does_not_exist.sqlite"
    empty_res = ecu.list_vehicles(db_path=missing_db)
    assert empty_res["count"] == 0 and empty_res["vehicles"] == [] and not missing_db.exists()
    empty_bmk = ecu.list_bookmarks(db_path=missing_db)
    assert empty_bmk["bookmark_count"] == 0 and empty_bmk["issue_count"] == 0 and not missing_db.exists()

    corrupt_db = tmp_path / "corrupt.sqlite"
    corrupt_db.write_bytes(b"")
    assert ecu.list_vehicles(db_path=corrupt_db)["status"] == "ERROR"

    lake_dir, db_path = _seed_telemetry_lake(tmp_path)
    veh = ecu.list_vehicles(db_path=db_path)
    assert veh["count"] == 1 and veh["vehicles"][0]["car_id"] == "shadow_90_sbec_t2"
    assert veh["vehicles"][0]["session_count"] == 1

    rollups_all = ecu.query_load_cell_rollups(lake_dir=lake_dir)
    assert rollups_all["summary"]["quarantined_rows"] == 1 and rollups_all["bin_count"] == 2
    trans = ecu.query_load_cell_rollups(
        lake_dir=lake_dir, rpm_min=2000, rpm_max=2500, map_min_kpa=100, map_max_kpa=120, tps_regime="TRANSITION",
    )
    assert trans["bin_count"] == 1
    assert trans["rollups"][0]["mean_afr"] is None and trans["rollups"][0]["total_knock_events"] is None
    assert trans["rollups"][0]["knock_polled_samples"] == 0
    assert ecu.query_load_cell_rollups(lake_dir=lake_dir, tps_regime="PART_THROTTLE")["bin_count"] == 0

    gps_lake = _write_gps_contaminated_lake(tmp_path)
    ts_page1 = ecu.get_drive_timeseries(lake_dir=gps_lake, offset=0, limit=1)
    assert ts_page1["total_matching_rows"] == 2 and ts_page1["returned_rows"] == 1
    assert ts_page1["peak_rpm"] == 4800.0
    assert "rpm" in ts_page1["polled_channels"] and "cts_c" in ts_page1["unpolled_channels"]
    row0 = ts_page1["rows"][0]
    assert row0["afr"] is None and row0["knock_counts"] is None and not ({"lat", "lon", "gps_track"} & set(row0))
    ts_page2 = ecu.get_drive_timeseries(lake_dir=gps_lake, offset=1, limit=1)
    assert ts_page2["returned_rows"] == 1 and ts_page2["rows"][0]["knock_counts"] == 1

    created = ecu.create_or_update_tuning_issue(
        db_path=db_path, car_id="shadow_90_sbec_t2", session_id="sess_trio_01",
        severity="CAUTION", category="FUEL_LEAN", title="Transition lean spot",
        note="Initial log observation", operating_point={"rpm": 2250, "map_kpa": 112},
        frame_ts_utc_ns=1_700_000_000_000_000_000, sample_idx=0, create_bookmark=True,
    )
    assert created["action"] == "created" and created["status"] == "OPEN" and created["bookmark_id"]
    updated = ecu.create_or_update_tuning_issue(
        db_path=db_path, issue_id=created["issue_id"], status="VERIFIED_FIXED", note="Added +4% WOT/trans fuel",
    )
    assert updated["action"] == "updated" and updated["status"] == "VERIFIED_FIXED"
    assert "Added +4% WOT/trans fuel" in updated["description"]
    bmk = ecu.list_bookmarks(db_path=db_path, session_id="sess_trio_01")
    assert bmk["bookmark_count"] == 1 and bmk["issue_count"] == 1


def test_ecu_telemetry_guards_export_and_stdio(tmp_path: Path) -> None:
    ecu = _load_mcp("ecu_telemetry.py", "ecu_telemetry_guards")
    lake_dir, db_path = _seed_telemetry_lake(tmp_path)
    gps_lake = _write_gps_contaminated_lake(tmp_path)

    assert ecu.create_or_update_tuning_issue(
        db_path=db_path, car_id="shadow_90_sbec_t2", operating_point={"rpm": 2000, "lat": 42.3},
    )["status"] == "ERROR"
    assert ecu.create_or_update_tuning_issue(db_path=db_path, car_id="shadow_90_sbec_t2", sample_idx=True)["status"] == "ERROR"
    sym_db = tmp_path / "sym_cat.sqlite"
    sym_db.symlink_to(db_path)
    for bad_db in (sym_db, "../escape.sqlite", "rom/inputs/evil.sqlite"):
        assert ecu.create_or_update_tuning_issue(db_path=bad_db, car_id="shadow_90_sbec_t2")["status"] == "ERROR"

    out_csv = tmp_path / "exports" / "slice.csv"
    exp_csv = ecu.export_gps_stripped_fixture(source=gps_lake, output_path=out_csv, fmt="csv")
    assert exp_csv["row_count"] == 2 and {"lat", "lon", "gps_track"}.issubset(set(exp_csv["stripped_gps_columns"]))
    with out_csv.open("r", encoding="utf-8", newline="") as fh:
        cols = next(csv.reader(fh))
    assert not ({"lat", "lon", "gps_track"} & set(cols))

    out_json = tmp_path / "exports" / "slice.json"
    exp_json = ecu.export_gps_stripped_fixture(source=gps_lake, output_path=out_json, fmt="json")
    assert exp_json["row_count"] == 2
    assert all(not ({"lat", "lon", "gps_track"} & set(r)) for r in json.loads(out_json.read_text(encoding="utf-8")))

    sym_out = tmp_path / "sym_out.csv"
    sym_out.symlink_to(tmp_path / "target.csv")
    for bad_out in (sym_out, "../escape.csv", "rom/stock_baselines/bad.csv"):
        assert ecu.export_gps_stripped_fixture(source=gps_lake, output_path=bad_out)["status"] == "ERROR"

    reqs = [
        _rpc(1, "tools/call", {"name": "list_vehicles", "arguments": {"db_path": str(db_path)}}),
        _rpc(2, "tools/call", {"name": "query_load_cell_rollups", "arguments": {"lake_dir": str(lake_dir), "tps_regime": "WOT"}}),
        _rpc(3, "tools/call", {"name": "get_drive_timeseries", "arguments": {"lake_dir": str(gps_lake), "limit": 2}}),
    ]
    resps = _run_stdio([sys.executable, str(MCP_DIR / "ecu_telemetry.py"), "--stdio"], [json.dumps(r) for r in reqs])
    assert [r["id"] for r in resps] == [1, 2, 3] and all(r["result"]["isError"] is False for r in resps)
    assert resps[0]["result"]["structuredContent"]["count"] == 1
    assert resps[1]["result"]["structuredContent"]["bin_count"] == 1
    assert resps[2]["result"]["structuredContent"]["returned_rows"] == 2


def test_hc11_emulator_manifest_roundtrip_parity_and_replay(tmp_path: Path) -> None:
    hc11 = _load_mcp("hc11_emulator.py", "hc11_emulator_mcp")
    assert set(hc11.TOOL_REGISTRY) == {
        "inspect_rom_manifest", "verify_roundtrip_sha256", "run_twin_parity",
        "run_single_replay", "run_monte_carlo_sweep", "evaluate_staging_safety_gate",
    }

    cat = hc11.inspect_rom_manifest()
    assert cat["manifest_count"] == 4
    sbec = hc11.inspect_rom_manifest(rom_id="sbec_90_93_v3_1")
    assert sbec["rom_id"] == "sbec_90_93_v3_1" and sbec["counts"]["verified_ram_count"] > 0
    hit = hc11.inspect_rom_manifest(rom_id="sbec_90_93_v3_1", sci_table="0xF6", offset="0x02")
    assert hit["matched"] is True and hit["var_name"] == "MapValue"
    miss = hc11.inspect_rom_manifest(rom_id="sbec_90_93_v3_1", offset="0xAA", var_name="TMADPCL")
    assert miss["matched"] is False and miss["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert hc11.inspect_rom_manifest(rom_id="sbec_90_93_v3_1", offset="0xFE99")["status"] == "NO_VERIFIED_SOURCE_MATCH"
    assert hc11.inspect_rom_manifest(offset="0x02")["status"] == "ERROR"
    assert hc11.inspect_rom_manifest(rom_id="sbec_90_93_v3_1", offset=True)["status"] == "ERROR"

    rt = hc11.verify_roundtrip_sha256(asm_path="rom/src/main.asm", splay_dir=tmp_path / "splay")
    assert rt["ok"] is True and rt["expected_sha256"] == rt["actual_sha256"]
    assert hc11.verify_roundtrip_sha256(asm_path="rom/src/main.asm", splay_dir="rom/inputs/bad_splay")["status"] == "ERROR"

    parity = hc11.run_twin_parity(asm_path="rom/src/main.asm")
    assert parity["ok"] is True and parity["max_lsb_diff"] == 0
    assert hc11.run_twin_parity(rev_limit_rpm=True)["status"] == "ERROR"

    replay_lake, replay_db = tmp_path / "replay_lake", tmp_path / "replay.sqlite"
    replay = hc11.run_single_replay(
        operating_points=[
            {"rpm": 2500.0, "map_kpa": 120.0, "tps_pct": 45.0, "act_c": 30.0, "batt_v": 14.0},
            {"rpm": 3500.0, "map_kpa": 160.0, "dead_cell_quarantine": True},
        ],
        car_id="shadow_90_sbec_t2", build_id="TRIO_TEST", session_id="sess_replay_01",
        lake_dir=replay_lake, db_path=replay_db, write_parquet=True,
    )
    assert replay["ok"] is True and replay["run_kind"] == "SINGLE_REPLAY"
    assert replay["parity_ok"] is True and replay["max_lsb_diff"] == 0 and replay["max_loop_cycles"] <= 22000
    assert replay["active_frames"] == 1 and replay["quarantined_frames"] == 1
    pq_tbl = pq.read_table(replay["parquet_chunks"][0])
    q_idx = pq_tbl["dead_cell_quarantine"].to_pylist().index(True)
    assert pq_tbl["pw_ms"].to_pylist()[q_idx] is None and pq_tbl["knock_counts"].to_pylist()[q_idx] is None

    clean_csv = tmp_path / "clean_stim.csv"
    clean_csv.write_text("rpm,map_kpa,tps_pct\n2800,115,35\n", encoding="utf-8")
    csv_rep = hc11.run_single_replay(fixture_path=clean_csv, lake_dir=replay_lake, db_path=replay_db, write_parquet=False)
    assert csv_rep["ok"] is True and csv_rep["frame_count"] == 1

    gps_csv = tmp_path / "gps_stim.csv"
    gps_csv.write_text("rpm,map_kpa,lat\n2800,115,42.3\n", encoding="utf-8")
    assert hc11.run_single_replay(
        operating_points=[{"rpm": 2500.0, "map_kpa": 120.0, "lat": 42.3}], lake_dir=replay_lake, db_path=replay_db,
    )["status"] == "ERROR"
    assert hc11.run_single_replay(fixture_path=gps_csv, lake_dir=replay_lake, db_path=replay_db)["status"] == "ERROR"
    assert hc11.run_single_replay(lake_dir="rom/inputs/lake", db_path=replay_db)["status"] == "ERROR"


def test_hc11_emulator_sweep_safety_gate_and_stdio(tmp_path: Path) -> None:
    hc11 = _load_mcp("hc11_emulator.py", "hc11_emulator_gate")
    sweep = hc11.run_monte_carlo_sweep(
        sampler="SOBOL", n_trials=8, seed=42, lake_dir=tmp_path / "mc_lake", db_path=tmp_path / "mc.sqlite",
    )
    assert sweep["ok"] is True and sweep["n_trials"] == 8 and sweep["overflow_count"] == 0
    assert hc11.run_monte_carlo_sweep(n_trials=True)["status"] == "ERROR"

    gate_lake, gate_db, stage_root = tmp_path / "gate_lake", tmp_path / "gate.sqlite", tmp_path / "staging"
    pass_res = hc11.evaluate_staging_safety_gate(
        profile_path=SHADOW_PROFILE, build_id="trio_pass_01", staging_root=stage_root,
        lake_dir=gate_lake, db_path=gate_db, n_trials=4, seed=42,
    )
    assert pass_res["status"] == "PASS" and pass_res["programmer_instructions_suppressed"] is False
    assert "minipro" in (pass_res["manual_flash_command"] or "")

    blocked_res = hc11.evaluate_staging_safety_gate(
        profile_path=SHADOW_PROFILE, build_id="trio_blocked_01", staging_root=stage_root,
        lake_dir=gate_lake, db_path=gate_db, n_trials=4, seed=42, rev_limit_rpm=9000,
    )
    assert blocked_res["status"] == "BLOCKED" and blocked_res["manual_flash_command"] is None
    assert blocked_res["programmer_instructions_suppressed"] is True
    assert "minipro" not in Path(blocked_res["report_path"]).read_text(encoding="utf-8")

    sym_stage = tmp_path / "sym_stage"
    sym_stage.symlink_to(stage_root)
    for bad_root in ("data/evil", "../escape", str(sym_stage), "rom/inputs/stage"):
        assert hc11.evaluate_staging_safety_gate(
            profile_path=SHADOW_PROFILE, staging_root=bad_root, lake_dir=gate_lake, db_path=gate_db, n_trials=4,
        )["status"] == "ERROR"

    reqs = [
        _rpc(1, "tools/call", {"name": "inspect_rom_manifest", "arguments": {"rom_id": "sbec_90_93_v3_1", "sci_table": "0xF6", "offset": "0x02"}}),
        _rpc(2, "tools/call", {"name": "run_twin_parity", "arguments": {"asm_path": "rom/src/main.asm"}}),
    ]
    resps = _run_stdio([sys.executable, str(MCP_DIR / "hc11_emulator.py"), "--stdio"], [json.dumps(r) for r in reqs])
    assert [r["id"] for r in resps] == [1, 2]
    assert resps[0]["result"]["structuredContent"]["var_name"] == "MapValue"
    assert resps[1]["result"]["structuredContent"]["max_lsb_diff"] == 0


def test_chrysler_heritage_mcp_guards() -> None:
    heritage = _load_mcp("chrysler_heritage.py", "chrysler_heritage_mcp")
    assert len(heritage.TOOL_REGISTRY) == EXPECTED_SERVERS["chrysler-heritage"]

    guarded_calls = [
        heritage.discover_heritage_leads(queue_dir="rom/inputs/x"),
        heritage.discover_heritage_leads(output_yaml="rom/stock_baselines/x.yaml"),
        heritage.sync_heritage_wiki(target_dir="rom/src/x", dry_run=False),
        heritage.search_heritage_docs("knock", db_path="../escape.sqlite"),
        heritage.lookup_sci_offset("MapValue", catalog_path="../bad.json"),
        heritage.lookup_sci_offset("MapValue", rom_id="../escape.json"),
    ]
    for payload in guarded_calls:
        assert payload["matched"] is False and payload["status"] == "NO_VERIFIED_SOURCE_MATCH"
        assert "error" in payload and payload["error"]

    rpc_res = heritage.handle_jsonrpc_request(
        _rpc(1, "tools/call", {"name": "sync_heritage_wiki", "arguments": {"target_dir": "rom/src/x", "dry_run": False}}),
    )
    assert rpc_res["result"]["isError"] is True
    assert "error" in rpc_res["result"]["structuredContent"]
    assert not (REPO_ROOT / "rom" / "inputs" / "x").exists()
    assert not (REPO_ROOT / "rom" / "stock_baselines" / "x.yaml").exists()
    assert not (REPO_ROOT / "rom" / "src" / "x").exists()


def test_corrupt_parquet_structured_errors_and_repo_relative_paths(tmp_path: Path) -> None:
    ecu = _load_mcp("ecu_telemetry.py", "ecu_telemetry_err")
    hc11 = _load_mcp("hc11_emulator.py", "hc11_emulator_err")

    bad_lake = tmp_path / "corrupt_lake"
    bad_part = bad_lake / "run_kind=VEHICLE_SCI" / "car_id=bad_car" / "build_id=STOCK" / "session_id=sess_bad"
    bad_part.mkdir(parents=True)
    bad_pq = bad_part / "chunk_0000.parquet"
    bad_pq.write_bytes(b"CORRUPT")

    rollup_err = ecu.handle_jsonrpc_request(
        _rpc(1, "tools/call", {"name": "query_load_cell_rollups", "arguments": {"lake_dir": str(bad_lake)}}),
    )
    ts_err = ecu.handle_jsonrpc_request(
        _rpc(2, "tools/call", {"name": "get_drive_timeseries", "arguments": {"lake_dir": str(bad_lake)}}),
    )
    replay_err = hc11.handle_jsonrpc_request(_rpc(3, "tools/call", {
        "name": "run_single_replay",
        "arguments": {
            "fixture_path": str(bad_pq), "lake_dir": str(tmp_path / "r_lake"),
            "db_path": str(tmp_path / "r.sqlite"), "write_parquet": False,
        },
    }))
    for resp in (rollup_err, ts_err, replay_err):
        assert resp["result"]["isError"] is True
        assert "error" in resp["result"]["structuredContent"]
    assert replay_err["result"]["structuredContent"]["error_type"] == "ArrowInvalid"

    home_prefix = "/ho" + "me/"
    if (REPO_ROOT / "data/drives/catalog.sqlite").is_file():
        veh = ecu.list_vehicles()
        assert veh["db_path"] == "data/drives/catalog.sqlite" and not veh["db_path"].startswith(home_prefix)
    if (REPO_ROOT / "rom/manifests/catalog.json").is_file():
        cat = hc11.inspect_rom_manifest()
        assert cat["catalog_path"] == "rom/manifests/catalog.json" and not cat["catalog_path"].startswith(home_prefix)


def test_jsonrpc_protocol_validation_errors(tmp_path: Path) -> None:
    ecu = _load_mcp("ecu_telemetry.py", "ecu_telemetry_rpc")
    hc11 = _load_mcp("hc11_emulator.py", "hc11_emulator_rpc")

    parse_resps = _run_stdio([sys.executable, str(MCP_DIR / "ecu_telemetry.py"), "--stdio"], ["{not-valid-json"])
    assert len(parse_resps) == 1 and parse_resps[0]["error"]["code"] == -32700

    assert ecu.handle_jsonrpc_request(["not-an-object"])["error"]["code"] == -32600  # type: ignore[arg-type]
    assert ecu.handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1})["error"]["code"] == -32600
    assert ecu.handle_jsonrpc_request(_rpc(2, "nonexistent/method", {}))["error"]["code"] == -32601

    assert ecu.handle_jsonrpc_request(_rpc(3, "tools/call", {"name": "no_such_tool", "arguments": {}}))["error"]["code"] == -32602
    assert ecu.handle_jsonrpc_request(_rpc(4, "tools/call", {"name": "list_vehicles", "arguments": {"bogus": 1}}))["error"]["code"] == -32602
    assert ecu.handle_jsonrpc_request(_rpc(5, "tools/call", {"name": "export_gps_stripped_fixture", "arguments": {"max_rows": 10}}))["error"]["code"] == -32602
    assert hc11.handle_jsonrpc_request(_rpc(6, "tools/call", {"name": "run_monte_carlo_sweep", "arguments": {"n_trials": True}}))["error"]["code"] == -32602
    assert ecu.handle_jsonrpc_request(_rpc(7, "tools/call", {"name": "get_drive_timeseries", "arguments": {"lake_dir": str(tmp_path), "limit": True}}))["error"]["code"] == -32602
