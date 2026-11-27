"""Tests for Hardware Deadbolt, SciTxGate, 62.5k loop:// polling, and Dead-Cell Quarantine."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from logicaide.rom.manifest import load_rom_manifest
from logicaide.telemetry.dead_cell import REASON_RUNTIME_TIMEOUT, REASON_STARTUP_TIMEOUT, STATE_ACTIVE, STATE_QUARANTINED
from logicaide.telemetry.deadbolt import (
    HardwareDeadboltError,
    NoVerifiedSourceMatchError,
    SciTxGate,
    open_read_only_serial,
    validate_serial_target,
)
from logicaide.telemetry.log_parsers import PSI_TO_KPA, STANDARD_BARO_KPA
from logicaide.telemetry.parquet_chunker import write_session_parquet_chunks
from logicaide.telemetry.rollups import query_load_cell_rollups
from logicaide.telemetry import sci_loopback
from logicaide.telemetry.sci_channels import (
    MAP_MIN_PSI,
    WatchedChannel,
    decode_channel_value,
    encode_channel_bytes,
    resolve_manifest_watchlist,
)
from logicaide.telemetry.sci_loopback import LOW_SPEED_BAUD, LoopbackUartPair, MockSciEcuResponder, OperatingPoint
from logicaide.telemetry.sci_poller import SciPoller, SciWatchConfig, resolve_offset_specs
from logicaide.telemetry.watch_cli import run_telemetry_watch


def _synthetic_manifest(*, sci_tables: list[str], entries: list[dict[str, Any]], sci_baud: int = 62500) -> dict[str, Any]:
    return {
        "rom_id": "custom_test_rom",
        "ecu_family": "SBEC_SINGLE_BOARD",
        "sci_baud": sci_baud,
        "sci_tables": sci_tables,
        "ram_watchlist": entries,
    }


def _persist_watch_result(res: Any, lake_dir: Path, db_path: Path) -> list[str]:
    sess = res.parsed_session
    payload = {
        "session_id": res.session_id,
        "car_id": res.car_id,
        "build_id": res.build_id,
        "run_kind": sess.run_kind,
        "ecu_family": res.ecu_family,
        "year": sess.year,
        "model": sess.model,
        "rom_sha256": res.rom_sha256 or ("0" * 64),
        "baro_kpa": sess.baro_kpa,
        "started_at_utc": sess.started_at_utc,
        "ended_at_utc": sess.ended_at_utc,
        "quarantined_cells": res.quarantined_cells,
        "rows": [asdict(s) for s in sess.samples],
    }
    report = write_session_parquet_chunks(payload, lake_dir=lake_dir, db_path=db_path)
    return [c["abs_path"] for c in report["chunks"]]


def test_hardware_deadbolt_transport_gate() -> None:
    """Enforce loop:// and memory:// allowlist and block physical/remote serial targets."""
    for ok_port in ("loop://", "loop://?logging=debug", "memory://"):
        assert validate_serial_target(ok_port) == ok_port

    ser = open_read_only_serial("loop://")
    try:
        ser.write(b"\x12")
        assert ser.read(1) == b"\x12"
    finally:
        ser.close()

    pair = LoopbackUartPair("loop://")
    pair.close()

    smec = load_rom_manifest("smec_88_89_v19")
    forbidden = (
        "/dev/tty" + "USB0",
        "/dev/ttyACM0",
        "/dev/ttyS0",
        "/dev/cu.usbserial-1410",
        "/dev/tty.usbmodem1",
        "COM3",
        r"\\.\COM12",
        "socket://localhost:9000",
        "rfc2217://host:7000",
        "hwgrep://0403:6001",
        "alt://loop://",
        "spy://loop://",
    )
    for bad_port in forbidden:
        with pytest.raises(HardwareDeadboltError, match=r"SECURITY\.md"):
            validate_serial_target(bad_port)
        with pytest.raises(HardwareDeadboltError, match=r"SECURITY\.md"):
            open_read_only_serial(bad_port)
        with pytest.raises(HardwareDeadboltError, match=r"SECURITY\.md"):
            LoopbackUartPair(bad_port)
        with pytest.raises(HardwareDeadboltError, match=r"SECURITY\.md"):
            SciPoller(smec, SciWatchConfig(car_id="DEADBOLT_TEST", port=bad_port))
        with pytest.raises(HardwareDeadboltError, match=r"SECURITY\.md"):
            run_telemetry_watch(port=bad_port, persist=False, max_frames=1)


def test_sci_tx_gate_and_lm_baud_rejection() -> None:
    """Allow only 0x12, 0xFE, and verified [FX, AA] reads; reject LM 7680-baud manifests."""
    smec = load_rom_manifest("smec_88_89_v19")
    sbec = load_rom_manifest("sbec_90_93_v3_1")
    smec_gate = SciTxGate(smec)
    sbec_gate = SciTxGate(sbec)

    for gate, valid in (
        (smec_gate, (b"\x12", b"\xFE", b"\xF2\x5F", b"\xF2\x67")),
        (sbec_gate, (b"\x12", b"\xFE", b"\xF2\x79", b"\xF6\x00")),
    ):
        for frame in valid:
            gate.assert_tx_allowed(frame)

    for bad_tx in (b"", b"\x13", b"\x17", b"\x23", b"\x41", b"\x51", b"\xF2\x5F\x00", b"\x41\x00\x01", b"\xF2"):
        with pytest.raises(HardwareDeadboltError):
            smec_gate.assert_tx_allowed(bad_tx)

    with pytest.raises(NoVerifiedSourceMatchError, match="No Verified Source Match"):
        smec_gate.assert_tx_allowed(b"\xF6\x00")
    with pytest.raises(NoVerifiedSourceMatchError, match="No Verified Source Match"):
        sbec_gate.assert_tx_allowed(b"\xF2\x34")

    unverified_manifest = _synthetic_manifest(
        sci_tables=["0xF2"],
        entries=[{"sci_table": "0xF2", "offset": 0x5F, "symbol": "ChargeTemp", "source_verified": False}],
    )
    unver_gate = SciTxGate(unverified_manifest)
    assert unver_gate.is_verified_read(0xF2, 0x5F) is False
    with pytest.raises(NoVerifiedSourceMatchError, match="No Verified Source Match"):
        unver_gate.assert_tx_allowed(b"\xF2\x5F")

    for lm_id in ("lm_86_87_v13", "lm_86_glhs_v1"):
        with pytest.raises(ValueError, match="62500"):
            resolve_manifest_watchlist(load_rom_manifest(lm_id))


def test_smec_polling_scaling_and_handshake_timeout() -> None:
    """Verify 7812->62500 handshake, 0xFE reset, SMEC 0xF2 scaling, and silent handshake timeout."""
    smec = load_rom_manifest("smec_88_89_v19")
    pt = OperatingPoint(
        rpm=3600.0, map_kpa=150.0, tps_pct=72.0, pw_ms=12.5,
        spark_deg_btdc=21.0, act_c=35.0, cts_c=90.0, batt_v=14.1, afr=12.4, knock_v=0.15,
    )
    cfg = SciWatchConfig(car_id="SMEC_89_TEST", rom_id="smec_88_89_v19", ecu_family="SMEC_DUAL_BOARD", max_frames=4)
    poller = SciPoller(smec, cfg)

    with MockSciEcuResponder(smec, poller.channels, operating_points=[pt]) as responder:
        res = poller.run(responder.host_port, session_id="SESS_SMEC_1")
        assert responder.uart_pair.ecu_baudrate == LOW_SPEED_BAUD
        assert responder.high_speed_active is False

    assert res.handshake_ok is True
    assert res.exit_reset_sent is True
    assert res.frame_count == 4
    s0 = res.samples[0]
    assert s0.rpm == pytest.approx(3600.0, abs=1.0)
    assert s0.map_kpa == pytest.approx(150.0, abs=1.5)
    assert s0.boost_psi == pytest.approx((150.0 - 101.325) / 6.894757, abs=0.25)
    assert s0.tps_pct == pytest.approx(72.0, abs=1.0)
    assert s0.pw_ms == pytest.approx(12.5, abs=0.05)
    assert s0.spark_deg_btdc == pytest.approx(21.0, abs=0.5)
    assert s0.act_c == pytest.approx(35.0, abs=1.5)
    assert s0.cts_c == pytest.approx(90.0, abs=2.0)
    assert s0.batt_v == pytest.approx(14.1, abs=0.15)
    assert s0.raw_o2_b is not None
    assert s0.afr == pytest.approx(12.4, abs=0.15)

    silent_pair = LoopbackUartPair("loop://")
    try:
        silent_poller = SciPoller(
            smec, SciWatchConfig(car_id="SILENT", probe_timeout_ms=10.0, max_frames=1),
        )
        with pytest.raises(TimeoutError, match="0x12 handshake failed"):
            silent_poller.enter_high_speed_mode(silent_pair.host_port)
    finally:
        silent_pair.close()


def test_sbec_f6_strict_vs_enriched_custom_0xf4_and_3bar_map() -> None:
    """Verify SBEC 0xF6 strict vs 0xF2 enrichment, custom 0xF4 table, and 3-bar MAP scaling."""
    sbec = load_rom_manifest("sbec_90_93_v3_1")
    pt_hi = OperatingPoint(
        rpm=4800.0, map_kpa=250.0, tps_pct=95.0, pw_ms=17.0,
        spark_deg_btdc=17.5, act_c=44.0, cts_c=91.0, batt_v=13.9, afr=11.5,
    )

    enriched_cfg = SciWatchConfig(
        car_id="SBEC_ENRICH", sci_table="0xF6", strict_table=False, map_sensor_bar=3, max_frames=2,
    )
    enriched_poller = SciPoller(sbec, enriched_cfg)
    assert any(ch.sci_table == "0xF6" for ch in enriched_poller.channels)
    assert any(ch.sci_table == "0xF2" and ch.channel_name == "act_c" for ch in enriched_poller.channels)
    with MockSciEcuResponder(sbec, enriched_poller.channels, operating_points=[pt_hi], map_sensor_bar=3) as resp:
        res_enr = enriched_poller.run(resp.host_port, session_id="SESS_F6_ENR")
    assert res_enr.samples[0].act_c == pytest.approx(44.0, abs=1.5)
    assert res_enr.samples[0].batt_v == pytest.approx(13.9, abs=0.15)
    assert res_enr.samples[0].map_kpa == pytest.approx(250.0, abs=2.0)

    strict_cfg = SciWatchConfig(car_id="SBEC_STRICT", sci_table="0xF6", strict_table=True, max_frames=2)
    strict_poller = SciPoller(sbec, strict_cfg)
    assert all(ch.sci_table == "0xF6" for ch in strict_poller.channels)
    with MockSciEcuResponder(sbec, strict_poller.channels, operating_points=[pt_hi], map_sensor_bar=2) as resp:
        res_str = strict_poller.run(resp.host_port, session_id="SESS_F6_STR")
    assert res_str.samples[0].act_c is None
    assert res_str.samples[0].batt_v is None
    assert res_str.samples[0].afr is None
    assert res_str.samples[0].map_kpa == pytest.approx(202.677, abs=0.5)

    custom_f4 = _synthetic_manifest(
        sci_tables=["0xF4"],
        entries=[
            {"sci_table": "0xF4", "offset": 0x10, "symbol": "EngineRpm_HB", "source_verified": True},
            {"sci_table": "0xF4", "offset": 0x11, "symbol": "EngineRpm_LB", "source_verified": True},
            {"sci_table": "0xF4", "offset": 0x12, "symbol": "MapValue", "source_verified": True},
            {"sci_table": "0xF4", "offset": 0x13, "symbol": "TpsVolts", "source_verified": True},
            {"sci_table": "0xF4", "offset": 0x14, "symbol": "CoolantTemp", "source_verified": True},
        ],
    )
    f4_poller = SciPoller(custom_f4, SciWatchConfig(car_id="CUSTOM_F4", sci_table="0xF4", max_frames=2))
    pt_f4 = OperatingPoint(2500.0, 85.0, 30.0, 5.0, 26.0, 30.0, 88.0, 14.2)
    with MockSciEcuResponder(custom_f4, f4_poller.channels, operating_points=[pt_f4]) as resp:
        res_f4 = f4_poller.run(resp.host_port, session_id="SESS_F4")
    assert res_f4.samples[0].rpm == pytest.approx(2500.0, abs=1.0)
    assert res_f4.samples[0].map_kpa == pytest.approx(85.0, abs=1.0)


def test_startup_quarantine_null_parquet_duckdb_and_reprobe(tmp_path: Path) -> None:
    """Startup 2-miss quarantine keeps act_c=None in Parquet/DuckDB until manual reprobe."""
    smec = load_rom_manifest("smec_88_89_v19")
    cfg = SciWatchConfig(
        car_id="SMEC_STARTUP_Q", rom_id="smec_88_89_v19", ecu_family="SMEC_DUAL_BOARD",
        probe_timeout_ms=10.0, max_frames=5,
    )
    poller = SciPoller(smec, cfg)

    with MockSciEcuResponder(smec, poller.channels, probe_timeout_ms=10.0, drop_offsets=((0xF2, 0x5F),)) as resp:
        res = poller.run(resp.host_port, session_id="SESS_START_Q")
        assert any(
            ev["event"] == "CELL_QUARANTINED" and ev["reason"] == REASON_STARTUP_TIMEOUT and ev["cell"] == "0xF2:0x5F"
            for ev in res.events
        )
        assert all(s.act_c is None and s.raw_act_b is None and s.dead_cell_quarantine is False for s in res.samples)

        pq_paths = _persist_watch_result(res, tmp_path / "lake", tmp_path / "catalog.sqlite")
        table = pq.read_table(pq_paths[0])
        assert all(v is None for v in table["act_c"].to_pylist())
        assert all(v is None for v in table["raw_act_b"].to_pylist())

        rollups = query_load_cell_rollups(tmp_path / "lake", session_id="SESS_START_Q")
        assert rollups and all(row["charge_temp_bin"] == "UNPOLLED" for row in rollups)

        poller.enter_high_speed_mode(resp.host_port)
        try:
            with pytest.raises(NoVerifiedSourceMatchError, match="No Verified Source Match"):
                poller.reprobe_offset(resp.host_port, 0xF4, 0x10)
            resp.clear_drop_offset((0xF2, 0x5F))
            cell = poller.reprobe_offset(resp.host_port, 0xF2, 0x5F)
            assert cell.state == STATE_ACTIVE
            restored = poller.poll_once(resp.host_port, res.samples[-1].ts_utc_ns + 1000)
            assert restored.act_c is not None and restored.raw_act_b is not None
        finally:
            poller.exit_high_speed_mode(resp.host_port)


def test_transient_vs_runtime_quarantine_stray_byte_flush_and_rollups(tmp_path: Path) -> None:
    """Verify 2-miss transient recovery and 3-miss runtime quarantine with delayed stray-byte RX flush."""
    smec = load_rom_manifest("smec_88_89_v19")
    pt = OperatingPoint(3200.0, 120.0, 55.0, 9.5, 23.0, 34.0, 89.0, 14.1, 12.6)

    # Part A: 2 transient misses recover cleanly without quarantine.
    tr_poller = SciPoller(smec, SciWatchConfig(car_id="SMEC_TR", probe_timeout_ms=10.0, max_frames=4))
    with MockSciEcuResponder(
        smec, tr_poller.channels, probe_timeout_ms=10.0, operating_points=[pt],
        transient_drops={(0xF2, 0x67): (1, 2)},
    ) as resp:
        tr_res = tr_poller.run(resp.host_port, session_id="SESS_TR")
    assert tr_res.quarantine_event_count == 0
    assert tr_res.rx_flush_count >= 2
    assert tr_res.samples[-1].tps_pct == pytest.approx(55.0, abs=1.0)

    # Part B: 3 consecutive misses with delayed stray byte 0xAA -> RUNTIME_TIMEOUT, zero 1-byte slip.
    rt_poller = SciPoller(smec, SciWatchConfig(car_id="SMEC_RT", probe_timeout_ms=10.0, max_frames=6))
    with MockSciEcuResponder(
        smec, rt_poller.channels, probe_timeout_ms=10.0, operating_points=[pt],
        transient_drops={(0xF2, 0x67): (1, 2, 3)}, stray_byte_drops={(0xF2, 0x67): 0xAA},
    ) as resp:
        rt_res = rt_poller.run(resp.host_port, session_id="SESS_RT")

    assert rt_res.rx_flush_count >= 3
    assert rt_poller.manager.get_cell((0xF2, 0x67)).state == STATE_QUARANTINED
    assert any(ev["event"] == "CELL_QUARANTINED" and ev["reason"] == REASON_RUNTIME_TIMEOUT for ev in rt_res.events)
    assert [s.dead_cell_quarantine for s in rt_res.samples] == [True, True, True, False, False, False]

    for idx in (3, 4, 5):
        s = rt_res.samples[idx]
        assert s.tps_pct is None and s.raw_tps_b is None
        assert s.map_kpa == pytest.approx(120.0, abs=1.5)
        assert s.cts_c == pytest.approx(89.0, abs=2.0)

    _persist_watch_result(rt_res, tmp_path / "lake", tmp_path / "catalog.sqlite")
    rollups = query_load_cell_rollups(tmp_path / "lake", session_id="SESS_RT")
    assert sum(row["sample_count"] for row in rollups) == 3
    assert all(row["tps_regime"] == "UNPOLLED" for row in rollups)


def test_16bit_hb_lb_torn_snapshot_partner_skip_and_resolve_offset_specs() -> None:
    """Verify 16-bit startup retry avoids torn snapshots, LB quarantine skips HB, and resolve_offset_specs."""
    smec = load_rom_manifest("smec_88_89_v19")
    pt0 = OperatingPoint(2200.0, 80.0, 20.0, 5.0, 25.0, 30.0, 88.0, 14.1, 14.4)
    pt1 = OperatingPoint(4800.0, 165.0, 90.0, 15.0, 19.0, 42.0, 90.0, 14.0, 11.8)

    p_torn = SciPoller(smec, SciWatchConfig(car_id="TORN_CHK", probe_timeout_ms=10.0, max_frames=2))
    with MockSciEcuResponder(
        smec, p_torn.channels, probe_timeout_ms=10.0, operating_points=[pt0, pt1],
        transient_drops={(0xF2, 0x5E): (0,)},
    ) as resp:
        r_torn = p_torn.run(resp.host_port, session_id="SESS_TORN")
    assert r_torn.quarantine_event_count == 0
    assert r_torn.samples[0].rpm == pytest.approx(2200.0, abs=1.0)
    assert r_torn.samples[1].rpm == pytest.approx(4800.0, abs=1.0)

    p_skip = SciPoller(smec, SciWatchConfig(car_id="PAIR_SKIP", probe_timeout_ms=10.0, max_frames=6))
    hb_at_q: list[int] = []
    with MockSciEcuResponder(
        smec, p_skip.channels, probe_timeout_ms=10.0, operating_points=[pt0],
        transient_drops={(0xF2, 0x5E): (1, 2, 3)},
    ) as resp:
        def _cap(idx: int, _sample: Any, _poller: Any, _uart: Any) -> None:
            if idx == 2:
                hb_at_q.append(resp.request_counts[(0xF2, 0x5D)])

        r_skip = p_skip.run(resp.host_port, session_id="SESS_SKIP", on_frame=_cap)
        assert p_skip.manager.is_quarantined((0xF2, 0x5E)) is True
        assert resp.request_counts[(0xF2, 0x5D)] == hb_at_q[0]
    assert all(s.rpm is None and s.raw_rpm_b is None for s in r_skip.samples[3:])

    sbec = load_rom_manifest("sbec_90_93_v3_1")
    wl = resolve_manifest_watchlist(sbec, extra_specs=("EngineRpm_LB",))
    assert sum(1 for ch in wl if ch.channel_name == "rpm" or ch.symbol in ("EngineRpm_HB", "EngineRpm_LB")) == 1
    assert resolve_offset_specs(("EngineRpm_LB",), channels=wl, manifest=sbec) == [(0xF2, 0x7A)]
    assert resolve_offset_specs(("rpm",), channels=wl, gate=SciTxGate(sbec)) == [(0xF2, 0x79), (0xF2, 0x7A)]
    with pytest.raises(ValueError, match="requires either gate or manifest"):
        resolve_offset_specs(("rpm",), channels=wl)


def test_channel_codec_boundaries_and_defensive_guards() -> None:
    """Verify TPS clamping, 1-bar MAP, function_set endpoints, null 16-bit pair, None bounds, and NB o2_v."""
    tps_ch = WatchedChannel(channel_name="tps_pct", sci_table="0xF2", fx_byte=0xF2, high_offset=0x67, symbol="TpsVolts")
    assert decode_channel_value(tps_ch, 0) == pytest.approx(0.0)
    assert decode_channel_value(tps_ch, 255) == pytest.approx(100.0)

    map_ch = WatchedChannel(channel_name="map_kpa", sci_table="0xF2", fx_byte=0xF2, high_offset=0x63, symbol="MapValue")
    assert decode_channel_value(map_ch, 0, map_sensor_bar=1) == pytest.approx(
        STANDARD_BARO_KPA + MAP_MIN_PSI * PSI_TO_KPA, abs=0.05,
    )
    assert decode_channel_value(map_ch, 255, map_sensor_bar=1) == pytest.approx(STANDARD_BARO_KPA, abs=0.05)

    act_ch = WatchedChannel(
        channel_name="act_c", sci_table="0xF2", fx_byte=0xF2, high_offset=0x5F, symbol="ChargeTemp",
        units="degF", function_set=[{"volts": 0.5, "value": 212.0}, {"volts": 4.5, "value": 32.0}],
    )
    assert decode_channel_value(act_ch, 0) == pytest.approx(100.0, abs=0.05)
    assert decode_channel_value(act_ch, 255) == pytest.approx(0.0, abs=0.05)

    rpm_ch = WatchedChannel(
        channel_name="rpm", sci_table="0xF2", fx_byte=0xF2, high_offset=0x5D, low_offset=0x5E, symbol="EngineRpm_HB",
    )
    assert decode_channel_value(rpm_ch, (0x20, None)) is None

    cts_ch = WatchedChannel(
        channel_name="cts_c", sci_table="0xF2", fx_byte=0xF2, high_offset=0x6A, symbol="CoolantTemp",
        settings={"ecu_min": None, "ecu_max": None},
    )
    assert isinstance(decode_channel_value(cts_ch, 128), float)

    nb_manifest = _synthetic_manifest(
        sci_tables=["0xF2"],
        entries=[{"sci_table": "0xF2", "offset": 0x8B, "symbol": "RawAirFuelSensInput", "source_verified": True}],
    )
    nb_wl = resolve_manifest_watchlist(nb_manifest, wideband_afr=False)
    nb_ch = next(ch for ch in nb_wl if ch.channel_name == "o2_v")
    assert nb_ch.symbol == "RawAirFuelSensInput"
    assert encode_channel_bytes(nb_ch, 2.5) == bytes([128])


def test_duration_stop_zero_frames_and_init_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify max_frames=0, duration_s bounded stop, and LoopbackUartPair cleanup on partial open failure."""
    smec = load_rom_manifest("smec_88_89_v19")

    p_zero = SciPoller(smec, SciWatchConfig(car_id="ZERO_FRAMES", max_frames=0))
    with MockSciEcuResponder(smec, p_zero.channels) as resp:
        r_zero = p_zero.run(resp.host_port, session_id="SESS_ZERO")
    assert r_zero.handshake_ok is True
    assert r_zero.frame_count == 0
    assert r_zero.exit_reset_sent is True

    p_dur = SciPoller(smec, SciWatchConfig(car_id="DUR_STOP", max_frames=None, duration_s=0.05, poll_interval_ms=10.0))
    with MockSciEcuResponder(smec, p_dur.channels) as resp:
        r_dur = p_dur.run(resp.host_port, session_id="SESS_DUR")
    assert 1 <= r_dur.frame_count <= 20
    assert r_dur.exit_reset_sent is True

    class _DummyHandle:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    first = _DummyHandle()
    calls = 0

    def _flaky_open(_port: str, *, baudrate: int = 7812, timeout: float = 0.012) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            return first
        raise OSError("second handle failed")

    monkeypatch.setattr(sci_loopback, "open_read_only_serial", _flaky_open)
    with pytest.raises(OSError, match="second handle failed"):
        LoopbackUartPair("loop://")
    assert first.closed is True
