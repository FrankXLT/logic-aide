---
name: kickoff-interview
description: Structured vehicle, ECU board (SMEC vs SBEC), Pin 11 continuity, target boost/fuel, and drivetrain/turbo profile intake.
---

# Kickoff Interview Skill

Run this structured intake whenever onboarding a new Chrysler Turbo vehicle (`car_id`), starting a new calibration branch, or validating hardware profile compatibility.

## Step 1 — Capture Vehicle & Logic Module Identity

Collect and record the following fields (never guess missing values):
1. **`car_id` & Chassis**: Year, model (e.g., `1989 Daytona Shelby`, `1991 Spirit R/T`, `1988 Shadow ES`), and transmission type.
2. **ECU Architecture (`SMEC` vs `SBEC`)**:
   - Exact Logic Module / SMEC / SBEC Chrysler part number and EPROM ID (`27C256` `$8000`–`$FFFF` or 27C512 split).
   - **60-Way Pin 11 Continuity & Variant Harness Check**: Confirm physical ohmmeter isolation on **Pin 11** (`+12V`/`+21V` SMEC bootstrap vs. SBEC chassis ground — **never** instruct applying `+12V`/`+21V` without verified SMEC-vs-SBEC board ID and continuity clearance) and verify year/calibration assignments for **Pin 2 / Pin 3** (`CTS` vs. early Turbo `Knock`) and **Pin 25 / Pin 45** (`SCI TX/RX` vs. under-hood diagnostic connector).
   - **High-Speed SCI Interface**: Confirm 62,500 baud (`62.5k`) SCI adapter path (for human operator logging; AI remains offline).

## Step 2 — Drivetrain & Turbo Hardware Selection (`profiles/`)

Populate or select a `profiles/<car_id>.json` (`profiles/schema/vehicle_build.schema.json`, Draft 2020-12) + `turbo_spec.json` entry enforcing the **Mopar 3-Tier Drivetrain Compatibility Guard** (`4.00 in` / `101.6 mm` shared bore spacing, `87.5 mm` standard bore):
- **Engine Block**: `2.2L` / `2.5L` K-car iron block, `2.0L ECC`, or `2.4L` (`EDZ`) iron block (include `.020`/`.040` overbores if machined).
- **Cylinder Head & Compatibility Tier**:
  1. **`BOLT_ON_NATIVE`**: `2.2L`/`2.5L` K-block paired with `8V #445` Open/Bathtub, `8V #782` Swirl, `1991–1993` Lotus `16V` DOHC (`Turbo III`), or `1988–1991` Maserati `16V` DOHC (`Chrysler TC`), recording `10 mm` vs. `11 mm` head bolts and cross-drilled gasket alignment.
  2. **`HYBRID_MACHINING_REQUIRED`**: `2.2L`/`2.5L` K-block paired with `Neon ECC 2.0L DOHC` or `2.4L EDZ`/SRT-4 `16V` DOHC head (or full `2.4L EDZ` swaps into SMEC/SBEC cars). Must record the `ISA-101` amber **Swap Advisory Checklist**: oil/coolant deck passage plugging, external head oil feed & oil-pan drainback routing, head-bolt/stud sleeve alignment, custom timing-belt tensioner geometry, piston valve reliefs, SMEC/SBEC crank/cam Hall-effect trigger sync, and counter-clockwise `PEFTBL` cam-peak rotation.
  3. **`INCOMPATIBLE`**: Block profile compilation on non-viable pairs (e.g., `8V #445`/`#782` SOHC or K-car `Lotus`/`Maserati 16V` heads onto a `2.4L EDZ` block).
- **Transaxle & Gearing**: e.g., `A525`, `A555`, `A568`, `NV-T350`, `A413`, assembly tag, final drive, tire rollout.
- **Turbocharger & Intercooler**: Compressor map (`turbo_spec.json`), wastegate actuator base spring pressure (`kPa` / `psi`), intercooler core, and MAP sensor range (`1-bar` vs `2-bar` vs `3-bar`).

## Step 3 — Fuel & Target Mechanical Envelope

- **Fuel & Rail Scaling**: `93 E10` vs `E85` (specific gravity, stoichiometric AFR, `MANIFOLD_REFERENCED_1_TO_1` vs `FIXED_ATMOSPHERIC_RETURNLESS` vs `RISING_RATE_FMU`, `43.5 psi` vs Chrysler `55 psi` square-root flow scaling, `LOW_Z_PEAK_HOLD` vs `HIGH_Z_SATURATED`, `batoff_deadtime_ms` `8..15 V` + pressure slope + short-pulse knee, and voltage-derated pump curve).
- **ECU & Hard Clamps**: `CHRYSLER_2D_PEFTBL` vs `CHRYSLER_3D_T3`, `90 °C` vs `60 °C` IAT ref, target boost (`kPa`), redline (`<= 8160 RPM`), max knock retard, and overboost fuel-cut ceiling.

## Step 4 — Output Summary

Write or update the vehicle profile under `profiles/` and compile via `uv run logicaide profile compile` (once Milestone 1 is built), verifying stock baseline presence in `rom/stock_baselines/` (read-only) before running `diagnose-drive` or `stage-rom-candidate`.
