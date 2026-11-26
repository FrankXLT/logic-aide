> **Scope**: Cleanroom Tier 2 1987 Turbo II Logic Module (LM) 50-Way Red & Blue Pinout (`LM_PM_84_87` / `2.2L_K`)
> **Boundary**: Distilled OEM wiring pinout & circuit assignments only; personal contributor identities and private paths omitted.

---
doc_id: "lm-87-t2-red-blue-50way-pinout"
title: "1987 Turbo II Logic Module (LM) 50-Way Red & Blue Connector Pinout"
trust_tier: "Tier 2"
source_type: "ECU_PINOUT_DIGEST"
source_citation: "1987 Shelby Daytona Turbo II Logic Module Wiring Reference (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "50-Way LM Red (25-Pin) & Blue (25-Pin) Connectors"
ecu_families: ["LM_PM_84_87"]
ecu_scope: ["LM_PM_84_87"]
engine_scope: ["2.2L_K"]
hex_symbols: []
symbols_referenced: ["K8", "K16", "Y1", "K3", "R31", "K15", "K14", "G7", "N7", "DK21", "DK20", "K19", "N6", "K5", "N5", "K6", "J11", "K9", "Y7", "N11", "K4", "K7", "K10", "N12", "K13"]
pins_referenced: ["Red 1..25", "Blue 1..25", "Red 11 (SCI TX)", "Red 13 (SCI RX)"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# 1987 Turbo II Logic Module (LM) 50-Way Red & Blue Pinout

> **Cross-Check Notice**: Always confirm wire colors and pin positions against the exact model-year Chrysler Factory Service Manual (FSM) before probing or powering a harness.

## Wiring Color Code Legend

- `BK` = Black, `BR` = Brown, `DB` = Dark Blue, `DG` = Dark Green, `GY` = Grey
- `LB` = Light Blue, `LG` = Light Green, `OR` = Orange, `PK` = Pink, `RD` = Red
- `TN` = Tan, `VT` = Violet, `WT` = White, `YL` = Yellow, `*` = Tracer

## Red Connector (25 Pins)

| Pin | Circuit | Gauge | Color | Function |
| :---: | :---: | :---: | :---: | :--- |
| 1 | K8 | 20 | VT/WT* | 5.0V power supply to MAP sensor |
| 2 | K16 | 22 | VT/YL* | Injector control signal |
| 3 | Y1 | 20 | GY/WT* | Injector on signal |
| 4 | K3 | 22 | BK/PK* | Power loss / check engine lamp output |
| 5 | R31 | 22 | DG/OR* | Voltage regulation signal |
| 6 | K15 | 22 | YL | Anti dwell signal |
| 7 | K14 | 18 | DB/WT* | Ignition "start"/"run" feed from power module (J2) |
| 8 | K14 | 18 | DB/WT* | Ignition "start"/"run" feed from power module (J2) |
| 9 | G7 | 22 | WT/OR* | Speed/distance sensor signal |
| 10 | N7 | 18 | GY/BK* | Ignition reference sensor signal |
| 11 | DK21 | 20 | PK | Serial communications interface (TX) |
| 12 | T21 | 22 | GY/LB* | Tachometer gauge signal |
| 13 | DK20 | 20 | LG | Serial communications interface (RX) |
| 14 | Z6 | 22 | LB/BK* | Navigator fuel monitor signal |
| 15 | Y4 | 20 | LB | Baro-read solenoid output |
| 16 | N4 | 18 | BK/YL* | AIS motor output (winding 2) |
| 17 | K19 | 20 | DB/YL* | ASD relay output to power module |
| 18 | N1 | 18 | GY/RD* | AIS motor output (winding 2) |
| 19 | Y6 | 20 | LG/BK* | Wastegate solenoid output |
| 20 | N3 | 18 | VT/BK* | AIS motor output (winding 1) |
| 21 | C27 | 20 | DB/PK* | Radiator fan relay output |
| 22 | N2 | 18 | BR/WT* | AIS motor output (winding 1) |
| 23 | N6 | 18 | OR | 8V power supply from power module |
| 24 | K5 | 18 | BK/WT* | Signal ground source from fuel rail bolt |
| 25 | N5 | 18 | BK/LB* | Signal ground to sensors |

## Blue Connector (25 Pins)

| Pin | Circuit | Gauge | Color | Function |
| :---: | :---: | :---: | :---: | :--- |
| 1 | K6 | 18 | OR/WT* | 5.0V power supply to TPS |
| 2 | J11 | 20 | RD/WT* | Direct battery feed (via power module) |
| 3 | N13 | 20 | DB/OR* | A/C cutout relay output |
| 4 | X36 | 20 | TN/RD* | Speed control solenoid vacuum output |
| 5 | S6 | 20 | GY/YL* | Canister purge solenoid output |
| 6 | X35 | 20 | LG/RD* | Speed control solenoid vent output |
| 7 | K9 | 18 | LB/RD* | Power ground source from fuel rail bolt |
| 8 | K9 | 18 | LB/RD* | Power ground source from fuel rail bolt |
| 9 | X31 | 22 | BR/RD* | Speed control switch set signal |
| 10 | X33 | 22 | WT | Speed control switch resume signal |
| 11 | C2 | 18 | BR | A/C damped pressure switch signal |
| 12 | S4 | 20 | BR/YL* | Park/neutral safety switch signal |
| 13 | D40 | 18 | WT/PK* | Brake switch signal |
| 14 | — | — | — | Not used |
| 15 | X32 | 22 | YL/RD* | Speed control switch on/off signal |
| 16 | — | — | — | Not used |
| 17 | Y7 | 18 | TN/YL* | Fuel injector sync sensor signal |
| 18 | N11 | 18 | BK | Oxygen sensor signal |
| 19 | K4 | 20 | DG/RD* | MAP sensor signal |
| 20 | K22 | 22 | RD/BK* | Battery temperature sensor signal |
| 21 | K7 | 18 | OR/DB* | Throttle position sensor (TPS) signal |
| 22 | J11 | 20 | RD/WT* | Voltage regulation sense signal |
| 23 | K10 | 20 | TN/BK* | Coolant temperature sensor signal |
| 24 | N12 | 18 | BK/LG* | Detonation sensor signal |
| 25 | K13 | 18 | BK/RD* | Air charge temperature sensor signal |
