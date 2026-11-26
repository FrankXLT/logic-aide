> **Scope**: Cleanroom Tier 2 Injector Flow Scaling & 2-Bar to 3-Bar MAP Conversion (`LM_PM_84_87` / `SMEC_88_89` / `SBEC_90_93`)
> **Boundary**: Distilled calibration scaling math and 29-item MAP dependency table only; contributor identities and private paths omitted.

---
doc_id: "injector-and-3bar-map-scaling"
title: "Chrysler Turbo Injector Scaling (S = Q_stock / Q_new) & 3-Bar MAP 29-Item Dependency Digest"
trust_tier: "Tier 2"
source_type: "CALIBRATION_SCALING_DIGEST"
source_citation: "1987 ChEM / D-Cal Injector & 3-Bar MAP Conversion Reference (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "Injector Flow Rates, AutoCal $50-$5F & 29 MAP-Indexed Calibration Items"
ecu_families: ["LM_PM_84_87", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_PM_84_87", "SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: ["$50", "$5C", "$5F", "$7F", "$80", "$B0", "$EF", "0x7F", "0xFF"]
symbols_referenced: ["CLDMAP", "WOTMAP", "HOTMAP", "KNKOFS", "RETMAX", "STFBAR", "MAPLIM", "MAPTBL", "WOTTBL", "MAPIDL", "HMPMUL", "BAROCP", "BARAIS", "DCLMN2", "UPRLIM", "LWRLIM", "MAPPT", "MPSHOF", "FANMAP", "BAROHI", "BAROLO", "BARDFT", "BATOFF", "PEFTBL", "FUELTR", "STRTFL", "PRSHOT", "POSSLP"]
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Injector Flow Scaling & 3-Bar MAP Conversion Digest

## 1. Stock & Mopar Performance Injector Flow Rates (@ 55 psi)

| Application / Part # | Flow Rate (`lb/hr`) |
| :--- | :---: |
| 1985–1988 2.2L Turbo I / Mopar `P4452803` ("803s") | `27.0` |
| 1987–1988 2.2L Turbo II | `32.0` |
| 1988–1993 2.5L Turbo I / 1989–1993 2.2L TII/TIII/TIV / Mopar `P4452804` ("804s") | `34.85` |
| Mopar Performance +20% (`P4529495`) | `42.0` |
| Mopar Performance +40% "Super 60" (`P5249452`) | `52.0` |

## 2. Injector Scaling Rules (`S = Q_stock / Q_new`)

- **Core Formula**: `S = Q_stock / Q_new` (e.g., `+20%` -> `1 / 1.2 = 83.3333%`; `+40%` -> `1 / 1.4 = 71.4285%`; `27 -> 42.5 lb/hr` -> `63.5294%`).
- **Fuel Pressure Basis**: Stock Chrysler Turbo FPR holds `55 psi` manifold-referenced differential pressure; `43 psi`-rated injectors must be flow-tested at `55 psi` or paired with a `43 psi` FPR.
- **Tables to Scale**: `FuelBaseline` (`WOTTBL`), `FuelIdle` (`MAPIDL`), `FuelPart` (`MAPTBL`), `FuelChangeFromRPMSNoThrottle`, `FuelMonitorScaling` (`FUELTR`), `StartFuelFromEngineTemp` (`STRTFL`), `StartPrimeFuelShot` (`PRSHOT`), `Table75FromTPS`, and `TransientThrottleEnrich` (`POSSLP`, minor).
- **Do NOT Scale (`BATOFF` & `PEFTBL`)**:
  - `PumpingEfficiency` (`PEFTBL`): Post-calculation volumetric efficiency multiplier, **not** an injector pulsewidth table.
  - `FuelBatteryOffset` (`BATOFF`): Injector actuation deadtime vs. battery voltage (depends on injector coil/pintle physics and rail pressure, not linear flow rate; source deadtime curve from `Super 60` / matched preset).
- **AutoCal Verification (`$50`–`$5F`)**: First 13 bytes hold learned fuel trims (`$5C` = idle cell; signed 8-bit: `>= $80` pulls fuel, `<= $7F` adds fuel). Target `$B0`–`$EF` in closed-loop cells so open-loop WOT trends slightly rich.

## 3. 2-Bar to 3-Bar MAP Scaling (`2/3 = 66.6666%`)

- **Sensor Span (`0–5V` / `0–255` ADC)**: 2-bar stock `-14.7..+14.7 psi` -> 3-bar `-14.7..+29.4 psi`.
- **Axis Scaling**: Multiply MAP-indexed X-axes and MAP constants by **`2/3` (`66.6666%`)**, then update display range to `-14.7..+29.4 psi`. Combine with injector Y-scaling (`S`) on dual-scaled fuel tables.

### Exhaustive 29-Item 1987 T2 MAP-Dependent Table & Constant Checklist

| # | Symbol / Table Name | Code / Token | Notes |
| :---: | :--- | :--- | :--- |
| 1 | `AdvanceFromMapCold` | `CLDMAP` | Cold MAP spark advance X-axis |
| 2 | `AdvanceFromMapWarmFull` | `WOTMAP` | Warm WOT MAP spark advance X-axis |
| 3 | `AdvanceFromMapWarmPart` | `HOTMAP` | Warm part-throttle MAP spark advance X-axis |
| 4 | `RetardFactorFromBarometricPressure` | — | Barometric spark retard multiplier |
| 5 | `UseTPSRetardAboveThisMAP` | — | MAP threshold enabling TPS retard |
| 6 | `KnockOffset` | `KNKOFS` | Knock detection MAP threshold |
| 7 | `MaximumRetardFromMap` | `RETMAX` | Maximum MAP-based knock retard |
| 8 | `StartFuelBaro` | `STFBAR` | Crank/start baro fuel compensation |
| 9 | Primary O2 Ramp lower & upper boundaries | — | Closed-loop primary O2 MAP window |
| 10 | Secondary O2 Ramp lower & upper boundaries | — | Closed-loop secondary O2 MAP window |
| 11 | AutoCal MAP Cell boundaries 1–5 | — | Learned fuel trim MAP cell breakpoints |
| 12 | `NoCellUpdateAboveThisMAP` | `MAPLIM` | AutoCal learning upper MAP cutoff |
| 13 | `FuelFromMAPPart` | `MAPTBL` | Part-throttle fuel MAP X-axis (+ Y if injectors changed) |
| 14 | `Table23_FromRpms` | — | RPM/MAP transition lookup |
| 15 | `Fuel_Baseline` | `WOTTBL` | WOT fuel MAP X-axis (+ Y if injectors changed) |
| 16 | `Fuel_Idle` | `MAPIDL` | Idle fuel MAP X-axis (+ Y if injectors changed) |
| 17 | `LoadEnrichment2` | `HMPMUL` | High-MAP enrichment multiplier |
| 18 | `BaroCompWarmup` | `BAROCP` | Warmup barometric fuel compensation |
| 19 | `TestedAgainstMAPDuringTPSProcessing` | — | TPS processing MAP gate |
| 20 | `AisBaroCompensation` | `BARAIS` | AIS idle motor baro offset |
| 21 | `NoAISControlIfMapBelowThis` | `DCLMN2` | AIS decel/low-MAP disable threshold |
| 22 | `UpperLimitOnMPCORR` | `UPRLIM` | Upper MAP correction clamp |
| 23 | `LowerLimitOnMPCORR` | `LWRLIM` | Lower MAP correction clamp |
| 24 | `MapPointForWastegateControlCells` | `MAPPT` | Wastegate control MAP reference |
| 25 | `OverboostShutoff` | `MPSHOF` | Overboost fuel-cut (`0xFF` disables; keep armed per `SECURITY.md`) |
| 26 | `FanMap` | `FANMAP` | Cooling fan MAP threshold (`0x7F` disables) |
| 27 | `BaroReadMaxValid` | `BAROHI` | Baro-read upper sanity bound |
| 28 | `BaroReadMinValid` | `BAROLO` | Baro-read lower sanity bound |
| 29 | `BaroReadDefault` | `BARDFT` | Fallback baro value on out-of-range read |
