---
name: ROM Feature or Table Change
about: Propose a 68HC11 SMEC/SBEC table adjustment, routine patch, or Python Behavioral Twin change
title: "[ROM] <symbol/table> - <summary>"
labels: ["rom-change", "simulation"]
---

## Target ROM Symbol & Behavioral Twin
- **`rom_manifest.json` Symbol / Address**: <!-- e.g., PEFTBL ($C400), SPKTBL ($C800) — never guess hex offsets! -->
- **Target ECU / Baseline Calibration**: <!-- SMEC | SBEC + baseline identifier -->
- **Python Behavioral Twin Module**: <!-- e.g., rom/models/fuel_twin.py -->
- **Linked `tuning_issues` / `optimization_campaigns` ID**: <!-- e.g., TI-0014 / OC-0005 -->

## Sobol Monte Carlo Campaign & Timing Bounds
- **Campaign Parameter Bounds**:
  - <!-- Parameter 1: [min, max] (e.g., PEFTBL cells 4000-5500 RPM @ 170-200 kPa: -5% to +8%) -->
  - <!-- Parameter 2: [min, max] -->
- **Stimulus Fixture(s)**: <!-- GPS-stripped 1-3s slice(s) under tests/fixtures/ -->
- **Timing & Safety Gates**:
  - [ ] Worst-case simulated loop duration verified `< 11 ms` in `m68hc11`
  - [ ] Python Behavioral Twin bit-exact parity verified against assembled 68HC11 `.asm`
  - [ ] 8-bit carry/overflow and mechanical boost/spark clamps verified
  - [ ] `$FFC0`–`$FFFF` interrupt/reset vectors unchanged or verified valid

## Motivation & Expected Outcome
<!-- Explain what road anomaly or thermodynamic constraint this change addresses and how success will be measured in Monte Carlo trace diffs. -->
