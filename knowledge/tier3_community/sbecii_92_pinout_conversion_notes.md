> **Scope**: Cleanroom Tier 3 1991 SBEC vs. 1992 T1 SBEC-II 60-Way Pinout & Harness Conversion Notes (`SBEC_90_93`)
> **Boundary**: Community conversion notes flagged Tier 3; NEVER sole pinout authority without Factory Service Manual (FSM) corroboration.

---
doc_id: "sbecii-92-pinout-conversion-notes"
title: "1991 SBEC vs. 1992 T1 SBEC-II 60-Way Pinout & Harness Conversion Notes (Tier 3)"
trust_tier: "Tier 3"
source_type: "COMMUNITY_PINOUT_CONVERSION"
source_citation: "1991 FSM Supplement Cross-Reference & 1992 SBEC-II Community Conversion Notes (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "60-Way SBEC Pinout & 91->92 Harness Deltas"
ecu_families: ["SBEC_SINGLE_BOARD"]
ecu_scope: ["SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: ["A61", "A142", "Z12", "D21", "D20", "D01", "D02", "K54", "K56"]
pins_referenced: ["Pin 2", "Pin 3", "Pin 8", "Pin 11", "Pin 12", "Pin 25", "Pin 26", "Pin 45", "Pin 46", "Pin 48", "Pin 49", "Pin 50", "Pin 54", "Pin 56", "Pin 57"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "needs_fsm_corroboration"
---

# 1991 SBEC vs. 1992 T1 SBEC-II 60-Way Pinout & Conversion Notes

> **TIER 3 CORROBORATION WARNING**: **NEVER use this document as the sole pinout source without Factory Service Manual (FSM) corroboration** for the exact target model year and body chassis.
> - **Pin 11 `Z12` Ground-Short Warning (`SECURITY.md`)**: On SBEC harnesses, **Pin 11 (`Z12`) can be tied directly to power ground** alongside Pin 12. Always perform a **DMM ohmmeter continuity check** before applying `+12V`/`+21V` to Pin 11.
> - **Pins 2, 3, 25, and 45 FSM Check**: Verify model-year assignments for Pins `2`/`3` (`CTS` vs. `Knock`) and Pins `25`/`45` (`SCI TX`/`RX` vs. early SMEC `C20` A/C clutch) against the exact FSM before probing.

## 1. 1991 -> 1992 Harness Conversion Deltas

- **Remove from 1991 harness**: Pin `26` (`CCD+`), Pin `46` (`CCD-`), Pin `48` (Cruise SET), Pin `49` (Cruise Feed), Pin `50` (Cruise RESUME), and Pin `54`/`56` (Turbo Solenoid; `54` repurposes as part-throttle unlock / auto on 1992).
- **Pin Swap**: Move Pin `8` (`A61` Fuel Pump; empty in 91 non-supplement) <-> Pin `57` (`A142` `+12V` ASD sense on 92).
- **Cruise Incompatibility**: 1992 uses a single-wire 3-step voltage-ladder cruise switch; 3-wire 1991 cruise switches are incompatible without rewiring.
- **Chassis Bus Note**: P-body (Shadow/Sundance) lacks CCD bus wiring unless V6/41TE equipped; G/J/AA bodies use CCD/Serial bus for Traveler, digital dash, and factory alarm.

## 2. 60-Way Connector Pinout (1991 FSM Wiring Supplement Reference)

| Pin | Circuit | Gauge/Color | Function |
| :---: | :---: | :---: | :--- |
| 1 | K01 | 20 DG/RD | MAP Sensor |
| 2 | K02 | 20 TN/BK | Coolant Temp Sensor *(verify vs. FSM)* |
| 3 | A14 | 16 RD/WT | Fuel Pump Auto Shutdown *(verify vs. FSM)* |
| 4 | K04 | 18 BK/LB | Signal Reference (Logic) |
| 5 | Z11 | 16 BK/WT | Signal Ground |
| 6 | K06 | 18 VT/WT | 5V Output MAP & TPS |
| 7 | K07 | 18 OR | 8V Supply |
| 8 | A61 | 16 DG/BK | Fuel Pump *(empty in 91 non-supplement; swap to Pin 57 on 92)* |
| 9 | A21 | 14 DB | Ignition Start/Run |
| 10 | — | — | Unused |
| 11 | Z12 | 16 BK/TN | Power Ground **(DMM check before any +12V probe)** |
| 12 | Z12 | 16 BK/TN | Power Ground |
| 13 | K14 | 18 LB/BR | Fuel Injector #4 |
| 14 | K13 | 18 YL/WT | Fuel Injector #3 |
| 15 | K12 | 18 TN | Fuel Injector #2 |
| 16 | K11 | 18 WT/DB | Fuel Injector #1 |
| 17–18 | — | — | Unused |
| 19 | K19 | 18 BK/GY | Ignition Voltage Driver |
| 20 | K20 | 18 DG | Voltage Regulator |
| 21 | — | — | Unused |
| 22 | K22 | 18 OR/DB | Throttle Position Sensor |
| 23 | — | — | Unused |
| 24 | K24 | 18 GY/BK | Distributor Cap Pick Up |
| 25 | D21 | 20 PK | SBEC Diagnostic (`SCI TX`) |
| 26 | D01 | 20 VT/BR | Serial Bus + (`CCD+`) |
| 27 | C20 | 18 BR | A/C Damped Pressure Switch |
| 28 | — | — | Unused |
| 29 | K29 | 20 WT/PK | Brake Sense Signal |
| 30 | T24 | 20 BR/LB | Starter Relay Ground |
| 31 | C27 | 20 DB/PK | Radiator Fan Relay |
| 32 | G03 | 20 BK/PK | Check Engine Lamp |
| 33 | V36 | 20 TN/RD | Speed Control |
| 34 | C13 | 20 DB/OR | A/C Clutch Relay |
| 35 | K35 | 20 GY/YL | EGR Solenoid |
| 36 | K36 | 20 LG/BK | Wastegate Solenoid |
| 37–38 | — | — | Unused |
| 39 | K39 | 18 GY/RD | Auto Idle Speed (#3) |
| 40 | K40 | 18 BR/WT | Auto Idle Speed (#1) |
| 41 | K41 | 18 BK/DG | Oxygen Sensor |
| 42 | K42 | 18 BK/LG | Detonation Sensor |
| 43 | G21 | 20 GY/LB | Tachometer Signal |
| 44 | K44 | 18 TN/YL | Dist. Sync Pickup |
| 45 | D20 | 20 LG | SBEC Receive (`SCI RX`) |
| 46 | D02 | 20 WT/BK | Serial Bus - (`CCD-`) |
| 47 | G07 | 20 WT/OR | Vehicle Distance Sensor |
| 48 | V31 | 20 BR/RD | Speed Control Switch SET |
| 49 | V32 | 20 YL/RD | Speed Control Feed |
| 50 | V33 | 20 WT/LG | Speed Control RESUME |
| 51 | K51 | 20 DB/YL | Auto Shutdown (ASD) |
| 52 | K52 | 20 PK/BK | Purge Solenoid |
| 53 | V35 | 20 LG/RD | Speed Control Vent |
| 54 | K54 | 20 LG/WT | Turbo Solenoid (92: PT unlock / auto) |
| 55 | K55 | 20 LB | Baro-Read Solenoid |
| 56 | — | — | Unused *(91 non-supplement: K56 20 LG/OR Turbo Solenoid)* |
| 57–58 | — | — | Unused *(92 uses Pin 57 for A142 +12V ASD Sense)* |
| 59 | K59 | 18 VT/BK | Auto Idle Speed (#4) |
| 60 | K60 | 18 YL/BK | Auto Idle Speed (#2) |
