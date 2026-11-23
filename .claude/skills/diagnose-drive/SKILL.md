---
name: diagnose-drive
description: Triage Spacebar bookmarks in tuning_issues, query DuckDB load_cell_rollups, isolate unpolled null vs sensor anomalies, and export GPS-stripped Stimulus Fixture slices.
---

# Diagnose Drive Skill

Use this workflow to investigate a vehicle drive (`VEHICLE_SCI`) from `Spacebar` / touch bookmarks in `tuning_issues` down to reproducible, GPS-stripped `Stimulus Fixture` slices.

## Step 1 — Inspect `tuning_issues` Bookmarks

1. List open bookmarks via `uv run logicaide telemetry issues --car <car_id>` (or `ecu-telemetry` FastMCP).
2. Capture each bookmark's `session_id`, `timestamp_utc`, operator note (e.g., `"Tip-in lean stumble at 3200 RPM"`), and `+/- 15 s` window bounds.

## Step 2 — Query DuckDB `load_cell_rollups`

1. Query `load_cell_rollups` filtered by `run_kind = 'VEHICLE_SCI'`, `car_id`, and `session_id` across the **`250 RPM x 10 kPa MAP x TPS Regime x ACT`** grid.
2. Compare observed AFR, pulse width (`ms`), spark advance (`deg BTDC`), and knock counts against baseline expectations or prior `SINGLE_REPLAY` / `MONTE_CARLO_SWEEP` runs.

## Step 3 — Isolate Watchlist `null` vs Real Anomalies vs Quarantine

Before blaming calibration tables, classify every anomalous channel:
- **`Not in Watchlist (null)`**: Channel was omitted from that drive's 62.5k High-Speed SCI watchlist. Do **not** treat `null` as `0.0` or a sensor short.
- **Dead-Cell Timeout Quarantine**: Check `dead_cell_quarantine = true` flags indicating SCI packet drop or bus stall; exclude quarantined ticks from statistical rollups and flag hardware cable/baud diagnostics.
- **Confirmed Sensor/Calibration Anomaly**: Verify physical plausibility (`MAP kPa`, `TPS %`, `ACT °C`, `ECT °C`, `Battery V`) and cross-reference `rom_manifest.json` symbols (`rom-disassembly-analyst`) or `knowledge/` (`chrysler-heritage`).

## Step 4 — Export GPS-Stripped `Stimulus Fixture`

1. Export the targeted `+/- 15 s` window using `uv run logicaide telemetry export-fixture --issue <issue_id> --out tests/fixtures/<fixture_name>.parquet`.
2. Explicitly verify that `lat`, `lon`, `altitude`, and `gps_track` columns are absent from the exported fixture before handing it to `hc11-emulator-tester` (`SINGLE_REPLAY`).
