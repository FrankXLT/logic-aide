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

Populate or select a `profiles/<car_id>.json` + `turbo_spec.json` entry enforcing the **Physical Drivetrain Compatibility Guard**:
- **Engine Block**: `2.2L` / `2.5L` K-car iron block OR `2.4L` (`EDZ`) iron block.
- **Cylinder Head**:
  - `2.2L`/`2.5L` blocks pair ONLY with `8V SOHC` (Swirl/Fast-Burn) or `2.2L Lotus 16V Turbo III` heads.
  - **BLOCK INVALID COMBO**: Never pair a vintage `2.2L`/`2.5L` K-car block with a Neon `ECC` 2.0L DOHC cylinder head (`ECC`/`EDZ` DOHC swaps require the `2.4L` iron block).
- **Transaxle & Gearing**: e.g., `A555`, `A568`, `A413`, `A670`, final drive, tire diameter.
- **Turbocharger & Intercooler**: Compressor map (`turbo_spec.json`), wastegate actuator base spring pressure (`kPa` / `psi`), intercooler core, and MAP sensor range (`2-bar` vs `3-bar`).

## Step 3 — Fuel & Target Mechanical Envelope

- **Fuel**: `93 E10` pump gasoline vs `E85` (specify stoichiometric AFR, injector size in `lb/hr` at rated rail pressure, and base fuel pressure in `kPa`).
- **Targets & Hard Clamps**: Target boost (`kPa` gauge/absolute), redline (`RPM`), maximum allowable knock retard, and overboost fuel-cut ceiling.

## Step 4 — Output Summary

Write or update the vehicle profile under `profiles/` and verify stock baseline presence in `rom/stock_baselines/` (read-only) before running `diagnose-drive` or `stage-rom-candidate`.
