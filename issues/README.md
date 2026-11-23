# `issues/` — Tuning Issues & Optimization Campaigns

`issues/` bridges empirical road telemetry (`telemetry/`) with deterministic ROM simulation (`emulator/`, `rom/`) and staged candidate binaries (`staging/candidates/`).

## Core Tracking Entities
- **`tuning_issues`**: Structured records for road anomalies, dead-cell sensor faults, or drivability targets.
  - Captures: `issue_id` (e.g., `TI-0014`), vehicle profile, ECU type (`SMEC`/`SBEC`), `run_id`, `VEHICLE_SCI` Parquet bookmark timestamp, `RPM x MAP x TPS x ACT` load cell, dead-cell status, and path to the GPS-stripped `1–3 s` stimulus fixture.
- **`optimization_campaigns`**: Structured records for Python Twin + `m68hc11` Sobol/Optuna experiments addressing one or more `tuning_issues`.
  - Captures: `campaign_id` (e.g., `OC-0005`), linked `issue_id`, target `rom_manifest.json` symbols, Sobol/LHS parameter bounds, `Optuna` objective metrics, `< 11 ms` cycle-budget verification, and resulting `staging/candidates/<build_id>/` artifact path.

## Traceability Invariant
Every staged candidate ROM in `staging/candidates/` must trace cleanly back through an `optimization_campaigns` record to a documented `tuning_issues` entry or explicit calibration goal.
