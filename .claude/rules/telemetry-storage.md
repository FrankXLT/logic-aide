---
paths:
  - "telemetry/**"
  - "mcp_servers/**"
  - "issues/**"
  - "data/**"
---

# Telemetry Storage, Rollups & Privacy Rule

Applies whenever working with High-Speed SCI ingestion, DuckDB queries, Parquet lakes, `tuning_issues`, or FastMCP telemetry tools under `telemetry/`, `mcp_servers/`, `issues/`, or `data/`.

## Mandatory Invariants

1. **Unified `run_kind` Parquet Partitioning**:
   - Store all time-series logs in 30-second Zstd-compressed Parquet chunks under a single unified schema discriminated by `run_kind`:
     - `VEHICLE_SCI` (live or logged 62,500 baud High-Speed SCI car drives)
     - `SINGLE_REPLAY` (deterministic `m68hc11` stimulus replay)
     - `MONTE_CARLO_SWEEP` (`scipy.stats.qmc` Sobol / `Optuna` simulated runs)
   - Partition/index cleanly by `run_kind`, `car_id`, `build_id`, and `session_id` so DuckDB queries compare real drives and simulated builds seamlessly.
2. **Explicit `Not in Watchlist (null)` Semantics**:
   - When an ECU RAM offset or PID was not part of the active SCI polling watchlist for a drive, store and render it explicitly as `null` (`Not in Watchlist (null)`).
   - Never backfill or coerce unpolled `null` channels to `0`, `0.0`, or synthetic defaults.
3. **Dead-Cell Timeout Quarantine**:
   - On SCI frame timeout, checksum mismatch, or ECU bus stall, tag the affected interval with `dead_cell_quarantine = true` (and surface the Dead-Cell Quarantine indicator) rather than interpolating bogus values into `load_cell_rollups` (`250 RPM x 10 kPa x TPS x ACT`).
4. **GPS Privacy Stripping**:
   - Raw GPS coordinates (`lat`, `lon`, `altitude`, `gps_track`) may remain in local private drive logs (`data/drives/`, gitignored) if recorded, but MUST be stripped completely before exporting any `Stimulus Fixture` slice to `tests/fixtures/` or `staging/candidates/`.
5. **Module & Secret Hygiene**:
   - Keep modules `<= 500` lines (`650` ceiling) and never read `.env` or `secrets.env`.
6. **Manifest-Only SCI Offsets & Sequential Tests**:
   - Resolve `0xF2`/`0xF4` SCI RAM watchlist offsets exclusively from `rom_manifest.json` (`No Verified Source Match` if unverified — never hardcode across SMEC vs. SBEC calibration masks).
   - Execute tests sequentially via `uv run pytest -n 0` (`pyserial` `loop://` offline tests are permitted; physical `/dev/ttyUSB*`/`COM*` writes remain strictly denied).
