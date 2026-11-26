---
title: "Turbocharger & Airflow Engineering Overview"
slug: "turbo-and-airflow-overview"
category: "turbo-and-airflow"
tier: "Tier 2"
provenance: "Wave A verified engineering dataset distilled from TurboCalc v5 thermodynamics workbook, Chrysler OEM turbo maps, and G-head flowbench presets"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "verified"
last_verified: "2026-11-26"
tags: ["turbocalc", "wave-a", "airflow", "g-head", "injectors", "compressor-map"]
---

# Turbocharger & Airflow Engineering Overview

This category houses the **Wave A verified** thermodynamic and airflow reference baselines imported into LogicAIDE, connecting engine displacement, cylinder head flowbench curves, injector sizing, and compressor operating islands.

## Wave A Verified Dataset Scope

1. **TurboCalc v5 Engineering Catalog**:
   - Normalized intake charge density, pressure-ratio, mass-airflow (`lb/min`), and volumetric efficiency (`VE`) reference calculations imported via `logicaide turbo import-turbocalc`.
   - Extracted compressor/flowbench visualization figures and structured SQLite catalog records for deterministic profile validation.
2. **Chrysler OEM Turbocharger Baselines**:
   - Factory Garrett `T3` (`.48` / `.63` A/R), Mitsubishi `TE04H` (`Turbo II` / `Turbo IV` VNT), and `TD04` / `TD05` compressor flow boundaries and surge/choke envelopes.
   - Charge-air cooling pressure drop and compressor outlet temperature assumptions for `2.2L` and `2.5L` K-block applications.
3. **G-Head Flowbench & Injector Presets**:
   - `8V #445` swirl-port, `8V #782` fast-burn, and `16V` DOHC cylinder head intake/exhaust CFM flowbench presets (`0.100"`–`0.500"` lift).
   - Fuel injector flow-rating presets (`lb/hr` @ rated pressure) supporting `sqrt(P_rail / P_rated)` rail-pressure scaling and specific-gravity compensation via `logicaide turbo extract-presets`.

## Wave A Reference Articles

- [OEM Chrysler Turbos](oem-chrysler-turbos.md) — Factory Chrysler turbocharger wheel specs, housings, and swap envelopes
- [TurboCalc Compressor Catalog](turbocalc-compressor-catalog.md) — Live catalog telemetry, 4-point affine pixel-to-physical math, and `NIST SP 811` air density conversions
- [Cylinder Head Flowbench Atlas](cylinder-head-flowbench-atlas.md) — Verified intake/exhaust CFM flowbench profiles and injector scaling baselines

## Related Categories

- [Wiki Home](../Home.md)
- [Drivetrain & Swaps Overview](../drivetrain-and-swaps/drivetrain-and-swaps-overview.md)
- [ROM & SCI Protocol Overview](../rom-and-sci/rom-and-sci-overview.md)
