"""Calm Cockpit CLI and orchestration wrapper for read-only 62,500-baud SCI telemetry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import select
import sqlite3
import sys
from typing import Any
import uuid

import click

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB
from logicaide.profiles.compiler import DEFAULT_REPO_ROOT, DEFAULT_ROM_IDS, compile_profile
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH, load_rom_manifest
from logicaide.telemetry.deadbolt import (
    HardwareDeadboltError,
    validate_serial_target,
)
from logicaide.telemetry.issues import (
    VALID_BOOKMARK_SOURCES,
    VALID_ISSUE_SEVERITIES,
    bookmark_tuning_issue,
)
from logicaide.telemetry.log_parsers import STANDARD_BARO_KPA, TelemetrySample
from logicaide.telemetry.parquet_chunker import write_session_parquet_chunks
from logicaide.telemetry.sci_channels import (
    DEFAULT_TPS_SPAN_V,
    DEFAULT_TPS_ZERO_V,
    WatchedChannel,
    resolve_manifest_watchlist,
)
from logicaide.telemetry.sci_loopback import MockSciEcuResponder
from logicaide.telemetry.sci_poller import (
    SciPoller,
    SciWatchConfig,
    SciWatchResult,
    resolve_offset_specs,
)

DEFAULT_LAKE_DIR = Path("data/drives")
DEFAULT_WATCH_ROM_ID = "sbec_90_93_v3_1"


def _resolve_manifest_from_ref(
    rom_id: str, manifest_ref: str | None, catalog_path: Path | str | None,
) -> dict[str, Any]:
    if catalog_path is not None:
        return load_rom_manifest(rom_id, catalog_path=catalog_path)
    if manifest_ref and (ref_path := DEFAULT_REPO_ROOT / manifest_ref).is_file():
        return load_rom_manifest(ref_path)
    return load_rom_manifest(rom_id, catalog_path=DEFAULT_CATALOG_PATH)


def _resolve_watch_context(
    *,
    profile_path: Path | str | None, rom_id: str | None, car_id: str | None,
    build_id: str | None, map_sensor_bar: int | None, wideband_afr: bool | None,
    catalog_path: Path | str | None, db_path: Path | str, persist: bool,
) -> dict[str, Any]:
    if profile_path is not None:
        prof_path = Path(profile_path)
        report = compile_profile(
            prof_path, db_path=db_path, persist=(persist and not bool(car_id)), require_rom_manifest=True,
        )
        prof = json.loads(prof_path.read_text(encoding="utf-8"))
        ident, ecu = prof.get("identity_and_chassis", {}), prof.get("ecu_and_sensors", {})
        trans, turbo = prof.get("transaxle_and_gearing", {}), prof.get("turbo_and_intercooler", {})
        ecu_fam = str(ecu.get("ecu_family") or report["rom_manifest"].get("ecu_family") or "SBEC_SINGLE_BOARD")
        prof_rom_id = report["rom_manifest"].get("rom_id")
        res_rom = rom_id or prof_rom_id or DEFAULT_ROM_IDS.get(ecu_fam, DEFAULT_WATCH_ROM_ID)
        m_ref = None if (rom_id and rom_id != prof_rom_id) else report["rom_manifest"].get("manifest_ref")
        manifest = _resolve_manifest_from_ref(res_rom, m_ref, catalog_path)
        m_sha = str(manifest.get("rom_sha256") or (manifest.get("summary") or {}).get("rom_sha256") or "")
        return {
            "manifest": manifest, "car_id": car_id or str(ident.get("car_id") or report["car_id"]),
            "build_id": build_id or str(ident.get("build_id") or "STOCK"),
            "rom_id": str(manifest.get("rom_id") or res_rom),
            "ecu_family": str(manifest.get("ecu_family") or ecu_fam),
            "year": int(ident.get("year") or 1990), "model": str(ident.get("model") or "Chrysler Turbo"),
            "rom_sha256": m_sha or hashlib.sha256(res_rom.encode("utf-8")).hexdigest(),
            "map_sensor_bar": map_sensor_bar if map_sensor_bar is not None else int(ecu.get("map_sensor_bar") or 2),
            "wideband_afr": wideband_afr if wideband_afr is not None else bool(ecu.get("wideband_afr", True)),
            "tps_zero_v": float(ecu.get("tps_zero_v", DEFAULT_TPS_ZERO_V)),
            "tps_span_v": float(ecu.get("tps_span_v", DEFAULT_TPS_SPAN_V)),
            "baro_kpa": float(ecu.get("baro_kpa", STANDARD_BARO_KPA)),
            "transaxle_code": str(trans.get("transaxle_code") or "A555"),
            "turbo_spec_id": str(turbo.get("turbo_id") or "") or None,
            "pinout_verified": bool(ecu.get("pin11_continuity_verified", False) and ecu.get("pin2_3_25_45_verified", False)),
        }

    res_rom = rom_id or DEFAULT_WATCH_ROM_ID
    manifest = load_rom_manifest(res_rom, catalog_path=catalog_path or DEFAULT_CATALOG_PATH)
    ecu_fam = str(manifest.get("ecu_family") or "SBEC_SINGLE_BOARD")
    m_sha = str(manifest.get("rom_sha256") or (manifest.get("summary") or {}).get("rom_sha256") or "")
    return {
        "manifest": manifest, "car_id": car_id or f"BENCH_{res_rom.upper()}",
        "build_id": build_id or "STOCK", "rom_id": str(manifest.get("rom_id") or res_rom),
        "ecu_family": ecu_fam, "year": 1990, "model": f"Bench {ecu_fam}",
        "rom_sha256": m_sha or hashlib.sha256(res_rom.encode("utf-8")).hexdigest(),
        "map_sensor_bar": map_sensor_bar if map_sensor_bar is not None else 2,
        "wideband_afr": wideband_afr if wideband_afr is not None else True,
        "tps_zero_v": DEFAULT_TPS_ZERO_V, "tps_span_v": DEFAULT_TPS_SPAN_V, "baro_kpa": STANDARD_BARO_KPA,
        "transaxle_code": "A555", "turbo_spec_id": None, "pinout_verified": False,
    }


def _validate_watch_args(
    *,
    max_frames: int | None,
    duration_s: float | None,
    probe_timeout_ms: float,
    poll_interval_ms: float,
    reprobe_at_frame: int | None,
    bookmark_frame: int | None,
    bookmark_severity: str,
    bookmark_source: str,
) -> None:
    if bookmark_severity not in VALID_ISSUE_SEVERITIES:
        raise ValueError(f"Unsupported bookmark_severity {bookmark_severity!r}.")
    if bookmark_source not in VALID_BOOKMARK_SOURCES:
        raise ValueError(f"Unsupported bookmark_source {bookmark_source!r}.")
    if max_frames is not None and max_frames < 0:
        raise ValueError(f"max_frames must be >= 0, got {max_frames!r}.")
    if not math.isfinite(probe_timeout_ms) or probe_timeout_ms <= 0.0:
        raise ValueError(f"probe_timeout_ms must be finite and > 0, got {probe_timeout_ms!r}.")
    if not math.isfinite(poll_interval_ms) or poll_interval_ms < 0.0:
        raise ValueError(f"poll_interval_ms must be finite and >= 0, got {poll_interval_ms!r}.")
    if duration_s is not None and (not math.isfinite(duration_s) or duration_s <= 0.0):
        raise ValueError(f"duration_s must be finite and > 0, got {duration_s!r}.")
    if reprobe_at_frame is not None and reprobe_at_frame < 0:
        raise ValueError(f"reprobe_at_frame must be >= 0, got {reprobe_at_frame!r}.")
    if bookmark_frame is not None:
        if bookmark_frame < 0:
            raise ValueError(f"bookmark_frame must be >= 0, got {bookmark_frame!r}.")
        if max_frames is not None and bookmark_frame >= max_frames:
            raise ValueError(f"bookmark_frame ({bookmark_frame}) must be < max_frames ({max_frames}).")


def _spacebar_pressed() -> bool:
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0.0)
        return bool(ready and sys.stdin.read(1) in (" ", "\n", "\r"))
    except (OSError, ValueError):
        return False


def _sample_to_operating_point(sample: TelemetrySample) -> dict[str, Any]:
    fields = (
        "rpm", "map_kpa", "boost_psi", "tps_pct", "pw_ms",
        "spark_deg_btdc", "act_c", "cts_c", "batt_v", "afr", "knock_counts",
    )
    return {name: val for name in fields if (val := getattr(sample, name, None)) is not None}


def _make_bookmark_entry(
    *, frame_idx: int, sample: TelemetrySample, poller: SciPoller,
    note: str, severity: str, source: str,
) -> dict[str, Any]:
    return {
        "sample_idx": frame_idx,
        "ts_utc_ns": sample.ts_utc_ns,
        "operating_point": _sample_to_operating_point(sample),
        "active_offsets": [f"0x{fx:02X}:0x{off:02X}" for fx, off in poller.manager.active_keys()],
        "quarantined_offsets": [c.label for c in poller.manager.quarantined_entries()],
        "note": note,
        "severity": severity,
        "source": source,
    }


def _persist_watch_outputs(
    *, result: SciWatchResult, ctx: Mapping[str, Any], captured_bookmarks: list[dict[str, Any]],
    lake_dir: Path | str, db_path: Path | str,
) -> tuple[list[str], list[dict[str, Any]]]:
    rows = [asdict(sample) for sample in result.parsed_session.samples]
    prepared_bookmarks: list[dict[str, Any]] = []
    for bm in captured_bookmarks:
        issue_id = f"iss_{uuid.uuid4().hex[:16]}"
        idx = int(bm["sample_idx"])
        if 0 <= idx < len(rows):
            rows[idx]["issue_id"] = issue_id
        prepared_bookmarks.append({**bm, "issue_id": issue_id})

    session_payload = {
        "session_id": result.session_id, "car_id": result.car_id, "build_id": result.build_id,
        "run_kind": result.parsed_session.run_kind, "ecu_family": ctx["ecu_family"],
        "year": ctx["year"], "model": ctx["model"], "transaxle_code": ctx["transaxle_code"],
        "turbo_spec_id": ctx["turbo_spec_id"], "pinout_verified": ctx["pinout_verified"],
        "rom_sha256": result.rom_sha256, "baro_kpa": result.parsed_session.baro_kpa,
        "started_at_utc": result.parsed_session.started_at_utc,
        "ended_at_utc": result.parsed_session.ended_at_utc,
        "quarantined_cells": result.quarantined_cells, "rows": rows,
    }
    chunk_report = write_session_parquet_chunks(session_payload, lake_dir=lake_dir, db_path=db_path)
    parquet_paths = [str(chunk["file_path"]) for chunk in chunk_report["chunks"]]
    persisted_bookmarks = [
        bookmark_tuning_issue(
            db_path, result.car_id, session_id=result.session_id, build_id=result.build_id,
            frame_ts_utc_ns=bm["ts_utc_ns"], sample_idx=bm["sample_idx"],
            operating_point=bm["operating_point"], active_offsets=bm["active_offsets"],
            quarantined_offsets=bm["quarantined_offsets"], note=bm["note"],
            severity=bm["severity"], source=bm["source"], ecu_family=ctx["ecu_family"],
            rom_sha256=result.rom_sha256, issue_id=bm["issue_id"], run_kind=result.parsed_session.run_kind,
        )
        for bm in prepared_bookmarks
    ]
    return parquet_paths, persisted_bookmarks


def _execute_loopback_watch(
    *,
    ctx: Mapping[str, Any], channels: Sequence[WatchedChannel], config: SciWatchConfig,
    session_id: str | None, operating_points: Sequence[Any] | None, fixture_path: Path | str | None,
    drop_keys: list[tuple[int, int]], transient_drops: Mapping[tuple[int, int], Iterable[int]] | None,
    reprobe_keys: list[tuple[int, int]], reprobe_at_frame: int | None,
    bookmark_frame: int | None, bookmark_note: str | None, bookmark_severity: str,
    bookmark_source: str, interactive_spacebar: bool,
) -> tuple[SciWatchResult, list[dict[str, Any]]]:
    reprobe_schedule: dict[int, tuple[tuple[int, int], ...]] | None = None
    if reprobe_keys:
        target_reprobe_frame = (
            int(reprobe_at_frame) if reprobe_at_frame is not None
            else (1 if (config.max_frames is None or config.max_frames > 1) else 0)
        )
        if config.max_frames is not None and target_reprobe_frame >= config.max_frames:
            raise ValueError(
                f"reprobe_at_frame ({target_reprobe_frame}) must be < max_frames ({config.max_frames})."
            )
        reprobe_schedule = {target_reprobe_frame: tuple(reprobe_keys)}

    poller = SciPoller(ctx["manifest"], config, channels=channels)
    responder = MockSciEcuResponder(
        ctx["manifest"], channels, port=config.port, probe_timeout_ms=config.probe_timeout_ms,
        map_sensor_bar=ctx["map_sensor_bar"], baro_kpa=ctx["baro_kpa"],
        tps_zero_v=ctx["tps_zero_v"], tps_span_v=ctx["tps_span_v"], wideband_afr=ctx["wideband_afr"],
        operating_points=operating_points, fixture_path=fixture_path,
        drop_offsets=drop_keys, transient_drops=transient_drops,
    )

    poll_stdin = bool(interactive_spacebar and sys.stdin is not None and sys.stdin.isatty())
    captured_bookmarks: list[dict[str, Any]] = []

    def _before_reprobe(_frame_idx: int, keys: Sequence[tuple[int, int]]) -> None:
        for key in keys:
            responder.clear_drop_offset(key)

    def _on_frame(frame_idx: int, sample: TelemetrySample, cur_poller: SciPoller, _uart: Any) -> None:
        if (bookmark_frame is not None and frame_idx == bookmark_frame) or (poll_stdin and _spacebar_pressed()):
            captured_bookmarks.append(_make_bookmark_entry(
                frame_idx=frame_idx, sample=sample, poller=cur_poller,
                note=bookmark_note or "", severity=bookmark_severity, source=bookmark_source,
            ))

    with responder:
        watch_res = poller.run(
            responder.host_port, session_id=session_id,
            on_frame=_on_frame, reprobe_at_frame=reprobe_schedule, before_reprobe=_before_reprobe,
        )
    if bookmark_frame is not None and bookmark_frame >= len(watch_res.samples):
        raise ValueError(
            f"bookmark_frame ({bookmark_frame}) out of range for captured frame count ({len(watch_res.samples)})."
        )
    if bookmark_note is not None and bookmark_frame is None and not captured_bookmarks and watch_res.samples:
        last_idx = len(watch_res.samples) - 1
        captured_bookmarks.append(_make_bookmark_entry(
            frame_idx=last_idx, sample=watch_res.samples[last_idx], poller=poller,
            note=bookmark_note, severity=bookmark_severity, source=bookmark_source,
        ))
    return watch_res, captured_bookmarks


def run_telemetry_watch(
    *,
    rom_id: str | None = None, profile_path: Path | str | None = None, car_id: str | None = None,
    build_id: str | None = None, session_id: str | None = None, port: str = "loop://",
    sci_table: str | None = None, extra_specs: Sequence[str] = (), strict_table: bool = False,
    max_frames: int | None = 50, duration_s: float | None = None, probe_timeout_ms: float = 12.0,
    poll_interval_ms: float = 0.0, map_sensor_bar: int | None = None, wideband_afr: bool | None = None,
    drop_offsets: Sequence[str | tuple[int, int]] = (), reprobe_offsets: Sequence[str | tuple[int, int]] = (),
    reprobe_at_frame: int | None = None, fixture_path: Path | str | None = None,
    catalog_path: Path | str | None = None, lake_dir: Path | str = DEFAULT_LAKE_DIR,
    db_path: Path | str = DEFAULT_CATALOG_DB, persist: bool = True,
    bookmark_frame: int | None = None, bookmark_note: str | None = None,
    bookmark_severity: str = "CAUTION", bookmark_source: str = "SPACEBAR",
    interactive_spacebar: bool = False, operating_points: Sequence[Any] | None = None,
    transient_drops: Mapping[tuple[int, int], Iterable[int]] | None = None,
) -> dict[str, Any]:
    """Run read-only 62.5k SCI polling over ``loop://`` virtual loopback with Deadbolt & quarantine."""
    validated_port = validate_serial_target(port)
    _validate_watch_args(
        max_frames=max_frames, duration_s=duration_s, probe_timeout_ms=probe_timeout_ms,
        poll_interval_ms=poll_interval_ms, reprobe_at_frame=reprobe_at_frame,
        bookmark_frame=bookmark_frame, bookmark_severity=bookmark_severity, bookmark_source=bookmark_source,
    )

    ctx = _resolve_watch_context(
        profile_path=profile_path, rom_id=rom_id, car_id=car_id, build_id=build_id,
        map_sensor_bar=map_sensor_bar, wideband_afr=wideband_afr,
        catalog_path=catalog_path, db_path=db_path, persist=persist,
    )
    channels = resolve_manifest_watchlist(
        ctx["manifest"], sci_table=sci_table, extra_specs=tuple(extra_specs),
        wideband_afr=ctx["wideband_afr"], strict_table=strict_table,
    )
    drop_keys = resolve_offset_specs(
        drop_offsets, manifest=ctx["manifest"], channels=channels, preferred_table=sci_table,
    )
    reprobe_keys = resolve_offset_specs(
        reprobe_offsets, manifest=ctx["manifest"], channels=channels, preferred_table=sci_table,
    )
    config = SciWatchConfig(
        car_id=ctx["car_id"], build_id=ctx["build_id"], rom_id=ctx["rom_id"],
        ecu_family=ctx["ecu_family"], year=ctx["year"], model=ctx["model"],
        rom_sha256=ctx["rom_sha256"], sci_table=sci_table, extra_specs=tuple(extra_specs),
        strict_table=strict_table, port=validated_port, max_frames=max_frames,
        duration_s=duration_s, probe_timeout_ms=probe_timeout_ms, poll_interval_ms=poll_interval_ms,
        baro_kpa=ctx["baro_kpa"], map_sensor_bar=ctx["map_sensor_bar"],
        tps_zero_v=ctx["tps_zero_v"], tps_span_v=ctx["tps_span_v"], wideband_afr=ctx["wideband_afr"],
        drop_offsets=tuple(drop_keys), fixture_path=Path(fixture_path) if fixture_path is not None else None,
    )
    watch_res, captured_bookmarks = _execute_loopback_watch(
        ctx=ctx, channels=channels, config=config, session_id=session_id,
        operating_points=operating_points, fixture_path=fixture_path,
        drop_keys=drop_keys, transient_drops=transient_drops,
        reprobe_keys=reprobe_keys, reprobe_at_frame=reprobe_at_frame,
        bookmark_frame=bookmark_frame, bookmark_note=bookmark_note,
        bookmark_severity=bookmark_severity, bookmark_source=bookmark_source,
        interactive_spacebar=interactive_spacebar,
    )
    parquet_chunks, persisted_bookmarks = (
        _persist_watch_outputs(
            result=watch_res, ctx=ctx, captured_bookmarks=captured_bookmarks,
            lake_dir=lake_dir, db_path=db_path,
        )
        if persist else ([], [])
    )
    return {
        "session_id": watch_res.session_id, "car_id": watch_res.car_id,
        "build_id": watch_res.build_id, "rom_id": watch_res.rom_id,
        "ecu_family": watch_res.ecu_family, "rom_sha256": watch_res.rom_sha256,
        "port": watch_res.port,
        "sci_table": sci_table or (watch_res.channels[0].sci_table if watch_res.channels else None),
        "handshake_ok": watch_res.handshake_ok, "exit_reset_sent": watch_res.exit_reset_sent,
        "frame_count": watch_res.frame_count, "hz": round(watch_res.hz, 3),
        "rx_flush_count": watch_res.rx_flush_count,
        "quarantine_event_count": watch_res.quarantine_event_count,
        "persisted": bool(persist), "parquet_chunks": parquet_chunks, "db_path": str(db_path),
        "polled_channels": list(watch_res.parsed_session.polled_channels),
        "unpolled_channels": list(watch_res.parsed_session.unpolled_channels),
        "quarantined_cells": list(watch_res.quarantined_cells),
        "bookmarks": persisted_bookmarks, "events": list(watch_res.events),
        "latest_sample": _sample_to_operating_point(watch_res.samples[-1]) if watch_res.samples else None,
    }


def _emit_watch_summary(result: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    click.echo(
        f"{result['car_id']} | {result['rom_id']} | {result['port']} "
        f"| frames={result['frame_count']} ({result['hz']:.1f} Hz) "
        f"| rx_flush={result['rx_flush_count']} | quarantined={len(result['quarantined_cells'])}"
    )
    click.echo(f"Active channels: {', '.join(result['polled_channels']) or 'none'}")
    if result["quarantined_cells"]:
        click.echo("Quarantined cells: " + ", ".join(c.get("label", "?") for c in result["quarantined_cells"]))
    for bm in result["bookmarks"]:
        click.echo(f"Bookmark {bm['issue_id']}: [{bm['severity']}] {bm['title']}")
    if result["parquet_chunks"]:
        click.echo(f"Parquet chunks ({len(result['parquet_chunks'])}): {', '.join(result['parquet_chunks'])}")


def register_telemetry_watch_command(telemetry_group: click.Group) -> None:
    """Register the read-only ``logicaide telemetry watch`` subcommand."""

    @telemetry_group.command(name="watch")
    @click.option("--rom-id", default=None, help="Partitioned ROM manifest identifier (defaults to sbec_90_93_v3_1).")
    @click.option("--profile", "profile_path", type=click.Path(path_type=Path), default=None, help="Vehicle build JSON profile.")
    @click.option("--car-id", default=None, help="Override vehicle car_id.")
    @click.option("--build-id", default=None, help="Override calibration build_id.")
    @click.option("--session-id", default=None, help="Explicit telemetry session_id.")
    @click.option("--port", default="loop://", show_default=True, help="Offline loop:// virtual loopback transport URL.")
    @click.option("--sci-table", default=None, help="Preferred SCI table (e.g. 0xF2 or 0xF6).")
    @click.option("--extra", "extra_specs", multiple=True, help="Additional verified SCI symbol or 0xFX:0xAA spec.")
    @click.option("--strict-table", is_flag=True, help="Restrict channels strictly to --sci-table.")
    @click.option("--max-frames", type=int, default=50, show_default=True, help="Maximum SCI frames to poll.")
    @click.option("--duration-s", type=float, default=None, help="Optional maximum polling duration in seconds.")
    @click.option("--probe-timeout-ms", type=float, default=12.0, show_default=True, help="Per-cell RX timeout in ms.")
    @click.option("--poll-interval-ms", type=float, default=0.0, show_default=True, help="Delay between frames in ms.")
    @click.option("--map-sensor-bar", type=click.Choice(["1", "2", "3"]), default=None, help="MAP sensor rating (1, 2, or 3 bar).")
    @click.option("--wideband/--narrowband", "wideband_afr", default=None, help="Use wideband AFR or narrowband O2 volts.")
    @click.option("--drop-offset", "drop_offsets", multiple=True, help="Simulate unresponsive cell (e.g. 0xF2:0x67 or TpsVolts).")
    @click.option("--reprobe", "reprobe_offsets", multiple=True, help="Cell to manually re-probe during session.")
    @click.option("--reprobe-at-frame", type=int, default=None, help="0-based frame index at which to run --reprobe.")
    @click.option("--fixture", "fixture_path", type=click.Path(path_type=Path), default=None, help="GPS-stripped CSV/Parquet fixture.")
    @click.option("--catalog", "catalog_path", type=click.Path(path_type=Path), default=None, help="ROM manifest catalog.json path.")
    @click.option("--lake-dir", type=click.Path(path_type=Path), default=DEFAULT_LAKE_DIR, show_default=True, help="Parquet lake root.")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True, help="SQLite catalog DB path.")
    @click.option("--persist/--no-persist", default=True, show_default=True, help="Persist Parquet chunks and SQLite catalog rows.")
    @click.option("--bookmark-frame", type=int, default=None, help="0-based frame index to bookmark into tuning_issues.")
    @click.option("--bookmark-note", default=None, help="Bookmark note for tuning_issues.")
    @click.option("--bookmark-severity", type=click.Choice(sorted(VALID_ISSUE_SEVERITIES)), default="CAUTION", show_default=True)
    @click.option("--spacebar/--no-spacebar", "interactive_spacebar", default=False, show_default=True, help="Poll non-blocking stdin TTY for live bookmarks.")
    @click.option("--json", "json_output", is_flag=True, help="Emit watch session report as JSON.")
    def telemetry_watch(
        rom_id: str | None, profile_path: Path | None, car_id: str | None, build_id: str | None,
        session_id: str | None, port: str, sci_table: str | None, extra_specs: tuple[str, ...],
        strict_table: bool, max_frames: int | None, duration_s: float | None, probe_timeout_ms: float,
        poll_interval_ms: float, map_sensor_bar: str | None, wideband_afr: bool | None,
        drop_offsets: tuple[str, ...], reprobe_offsets: tuple[str, ...], reprobe_at_frame: int | None,
        fixture_path: Path | None, catalog_path: Path | None, lake_dir: Path, db_path: Path,
        persist: bool, bookmark_frame: int | None, bookmark_note: str | None, bookmark_severity: str,
        interactive_spacebar: bool, json_output: bool,
    ) -> None:
        """Stream read-only 62.5k SCI telemetry frames over loop:// with Dead-Cell Quarantine."""
        try:
            result = run_telemetry_watch(
                rom_id=rom_id, profile_path=profile_path, car_id=car_id, build_id=build_id,
                session_id=session_id, port=port, sci_table=sci_table, extra_specs=extra_specs,
                strict_table=strict_table, max_frames=max_frames, duration_s=duration_s,
                probe_timeout_ms=probe_timeout_ms, poll_interval_ms=poll_interval_ms,
                map_sensor_bar=int(map_sensor_bar) if map_sensor_bar is not None else None,
                wideband_afr=wideband_afr, drop_offsets=drop_offsets, reprobe_offsets=reprobe_offsets,
                reprobe_at_frame=reprobe_at_frame, fixture_path=fixture_path, catalog_path=catalog_path,
                lake_dir=lake_dir, db_path=db_path, persist=persist, bookmark_frame=bookmark_frame,
                bookmark_note=bookmark_note, bookmark_severity=bookmark_severity,
                interactive_spacebar=interactive_spacebar,
            )
        except (HardwareDeadboltError, ValueError, FileNotFoundError, KeyError, OSError, sqlite3.Error) as exc:
            raise click.ClickException(str(exc)) from exc
        _emit_watch_summary(result, as_json=json_output)


__all__ = [
    "DEFAULT_LAKE_DIR",
    "DEFAULT_WATCH_ROM_ID",
    "register_telemetry_watch_command",
    "run_telemetry_watch",
]
