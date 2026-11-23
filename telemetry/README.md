# `telemetry/` — 62.5k SCI Poller, Dead-Cell Probe & Load-Cell Lake

`telemetry/` captures live diagnostic data from 1988–1993 Chrysler SMEC/SBEC controllers over the `62.5 kbaud` SCI link and organizes multi-car road pulls into a fast analytical lake.

## Core Pipeline
- **Read-Only 62.5k SCI Poller**: Streams ECU parameters (`RPM`, `MAP`, `TPS`, `ECT`, `ACT`, `O2`, injector pulse width, spark advance, knock retard, battery voltage) without ever issuing write/flash commands.
- **Startup Dead-Cell Probe & Quarantine**: Runs at session start to detect flatlined, rail-stuck (`0x00`/`0xFF`), or physically implausible sensor channels and flags/quarantines corrupted cells so bad sensors never poison calibration sweeps.
- **30s Zstd Parquet Chunker**: Buffers incoming frames and writes resilient **30-second Zstd-compressed Parquet chunks** under `data/drives/` (gitignored), preserving `Spacebar` / touch `VEHICLE_SCI` anomaly bookmarks.
- **Phone IMU Merge**: Time-aligns smartphone accelerometer/gyroscope logs with SCI frames for load estimation and transient pull analysis.
- **`load_cell_rollups` Lake**: Aggregates multi-car drives in DuckDB/SQLite across standardized cells (`250 RPM x 10 kPa MAP x TPS Regime x Charge-Temp`) linked to [`issues/`](../issues/README.md).

## Interfaces
- CLI: `uv run logicaide telemetry ...`
- FastMCP: `ecu-telemetry` (`mcp_servers/`)
- Privacy: Raw GPS coordinates are **always stripped** before exporting slices to `tests/fixtures/` or `staging/candidates/`.
