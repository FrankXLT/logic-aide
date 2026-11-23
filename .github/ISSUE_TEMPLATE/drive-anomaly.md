---
name: Drive Anomaly Report
about: Log a road pull anomaly, dead-cell fault, or SCI bookmark from a Turbo Chrysler drive
title: "[DRIVE] <car/ECU> - <brief symptom>"
labels: ["drive-anomaly", "telemetry"]
---

## Vehicle & Run Metadata
- **Car / Chassis Profile**: <!-- e.g., 1989 Daytona Shelby / 1991 Spirit R/T / 1990 Turbo Minivan -->
- **ECU Hardware**: <!-- SMEC | SBEC (include calibration/part number if known) -->
- **Run ID**: <!-- e.g., run_2026-11-23_01 -->
- **`tuning_issues` ID**: <!-- e.g., TI-0014 -->

## Telemetry Bookmark & Load Cell
- **`VEHICLE_SCI` Parquet Bookmark Timestamp**: <!-- e.g., 2026-11-23T19:42:11.350Z (or chunk + offset) -->
- **Load Cell (`RPM x MAP x TPS x ACT`)**:
  - `RPM`: <!-- e.g., 4250 RPM -->
  - `MAP`: <!-- e.g., 180 kPa (~11.5 psi boost) -->
  - `TPS Regime`: <!-- Idle | Cruise | Tip-In | WOT -->
  - `ACT (Charge Temp)`: <!-- e.g., 58 °C -->
- **Dead-Cell Probe Status**: <!-- Nominal | Quarantined (list sensor channels e.g., ACT stuck @ 0xFF) -->

## Observed Symptom & Stimulus Fixture
- **Observed Symptom**: <!-- Lean tip-in stumble, knock retard spike, overboost fuel cut, AFR drift, etc. -->
- **GPS-Stripped Stimulus Fixture Link**: <!-- Path under tests/fixtures/<slice>.parquet or .json (verify NO raw GPS!) -->
- **Notes / Heritage Cross-Reference**: <!-- Optional Tier 1/2/3 reference from knowledge/ -->
