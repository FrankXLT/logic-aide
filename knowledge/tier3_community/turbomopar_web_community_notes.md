> **Scope**: Cleanroom Tier 3 Community Diagnostics & Tooling Notes (`Turbo-Mopar` Threads & `MPSciLink` / `MPFlash` Documentation)
> **Boundary**: Community diagnostic threads and `MPSciLink` operational notes flagged Tier 3; NEVER sole authority without Factory Service Manual (`FSM`) and component datasheet corroboration. Omits PII and flash-write tokens.

---
doc_id: "turbomopar-web-community-notes"
title: "Turbo-Mopar Community Diagnostic Threads, Dual-Cal Switching & MPSciLink Notes (Tier 3)"
trust_tier: "Tier 3"
source_type: "COMMUNITY_DIAGNOSTICS_AND_TOOLING"
source_citation: "Turbo-Mopar (pre-2017 Wayback archive) Community Threads & MPSciLink Documentation"
warc_capture_date: "2026-11-27T00:00:00Z"
page_ref: "turbomopar/threads (cel-no-codes, lm-32kb-switch, turbonator-code53); mpscilink (mpflash, operational-info)"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: ["0x0000", "0x4000"]
symbols_referenced: ["28C256", "27C256", "A14", "Code 12", "Code 13", "Code 42", "Code 53", "Z1", "HEP", "SDS", "BB60", "MPSciLink", "MPFlash", "MPScandroid"]
pins_referenced: ["Pin 1", "Pin 2", "Pin 27"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "needs_fsm_corroboration"
---

# `Turbo-Mopar` Community Diagnostics, Dual-Cal Chip Notes & `MPSciLink`

> **TIER 3 CORROBORATION WARNING**: **NEVER use this document as the sole authority without Factory Service Manual (`FSM`) and component datasheet corroboration** for the exact target model year, ECU family, and memory IC.
> - **JEDEC `28C256` `A14` Pin Warning**: Forum advice on `32 KB` dual-calibration bank switching conflates `Pin 1` vs `Pin 2` (`28C256` `A14` is `Pin 1` and `A12` is `Pin 2`, whereas `27C256` places `VPP` on `Pin 1` and `A14` on `Pin 27`). Always verify **`A14` `Pin 1` vs `Pin 2` against the exact IC datasheet and `FSM` / ECU schematic** before lifting pins or wiring a switch.

---

## 1. `CEL` Flash + Fuel Cut Under Boost With No Stored Codes (`Turbo-Mopar`, 2008)

- **Reported Symptom**: `CEL` flashes on and fuel cuts abruptly around `3–5 psi` boost with **no stored fault codes** (`Code 12` clears normally, while unplugging MAP correctly triggers `Code 13`), accompanied by `100–150 RPM` tachometer dip during the cutout and occasional high idle (`~4000 RPM` on clutch depression).
- **Community Consensus (`MiniMopar`, `ShelGame`)**:
  - A stock calibration illuminates `CEL` without storing a fault code only when the ECU believes the engine has stopped running—pointing directly to momentary loss of the **Hall Effect Pickup (`HEP`)** distributor reference signal (confirmed by the tach needle dropping during cutout, unlike overboost/over-rev fuel cut where tach tracks true RPM), a sag in the **`8V` sensor supply feed**, or a soft ground flexing under drivetrain torque.
  - **Speed/Distance Sensor (`SDS`) Power & Pull-Up Deltas**: On `LM`/`SMEC`, the `SDS` pull-up comes from the logic board; `3-wire SDS` units (`~1990`) share the `8V` `HEP` feed before later models move to `5V` `TPS`-style supply. Verify `SDS` continuity (`~5V` pull-up toggling cleanly to ground across `8` pulses per rotation) and rule out `SDS`-triggered staging-mod threshold (`~2–3 mph`) or custom `BB60` `AIS`/cold-start idle tables.

---

## 2. Dual-Calibration Switching on a `32 KB` `LM` Chip (`Turbo-Mopar`, 2008)

- **Concept**: Two `16 KB` calibrations burned onto one `32 KB` (`256 Kbit`) chip—lower bank at `0x0000` (`16 KB`) and upper bank at `0x4000` (`16 KB`).
- **Community Switch Circuit**: Pull the highest-order address line (`MSB`) to `+5V` through a `~2 kOhm` resistor, and wire a toggle switch from that address pin to ground:
  - Switch **ON** (grounded = logic `0`): selects lower `16 KB` calibration (`0x0000`).
  - Switch **OFF** (pulled high = logic `1`): selects upper `16 KB` calibration (`0x4000`).
- **Mandatory Hardware Check**: Thread contributors gave conflicting guesses (`Pin 2` vs `Pin 1` for `32 KB` vs `64 KB` chips). **NEVER** guess the pin from forum memory—confirm `A14` (`Pin 1` vs `Pin 2` vs `Pin 27`) on the exact JEDEC `28C256` / `27C256` datasheet and `FSM` before modifying the Logic Module carrier.

---

## 3. `Code 53` (Internal ECM) + `Code 42` (`ASD` `Z1`) & Stalling (`Turbo-Mopar`, 2011)

- **Symptom & Triage**: Intermittent `Code 53` paired with `Code 42` (`ASD` sense circuit `Z1`), random stalling without warning, brief radiator fan activation on restart, and recovery when swapped to a stock ECU.
- **Community Guidance (`ShelGame`, `Turbo-Mopar`)**:
  - Custom binaries edited via legacy `D-Cal` can trigger checksum/internal `Code 53` (whereas clean builds from `MP Tune` avoid this).
  - However, intermittent `Code 53` + `Code 42` combined with spontaneous stalling and fan cycling—cured immediately by an ECU swap—indicates **failing ECU hardware** (power/ground solder joints, logic board fault, or failing EPROM socket) rather than a calibration artifact.

---

## 4. `MPSciLink` / `MPFlash` / `MPScandroid` Diagnostic Operational Notes (`MPSciLink`)

- **Tooling Overview**: `MWM Engineering` (`MPSuite`) hardware/software line (`MPSciLink` Arduino-based Bluetooth `SCI` interface, `MPFlash`, and `MPScandroid` Android/Nextion interface) for Chrysler/Dodge/Plymouth/Jeep `SCI` diagnostics.
- **Bluetooth & Link Verification (`MPSciLink` Operational Guide)**:
  1. Open `MPScandroid` -> `Menu` -> `MPSciLink Version`. A pop-up displays the firmware version running on the `MPSciLink` Arduino; **no version displayed** indicates a Bluetooth pairing/link fault.
  2. From the Main Menu, select `[Miscellaneous]` -> `[Nextion Tasks]` -> `[Module Settings]` to inspect live **`SD` card, `BT` link, ECU communication status, and baud rates**.
