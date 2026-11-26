> **Scope**: Cleanroom Tier 3 1987–1989 Daytona Analog-to-Digital Dash 26-Pin Conversion Pinout (`LM_PM_84_87` / `SMEC_88_89`)
> **Boundary**: Community cluster conversion table flagged Tier 3; NEVER sole pinout authority without Factory Service Manual (FSM) corroboration.

---
doc_id: "daytona-digital-dash-26pin"
title: "1987–1989 Daytona Analog-to-Digital Dash 26-Pin Connector Conversion Pinout (Tier 3)"
trust_tier: "Tier 3"
source_type: "COMMUNITY_CLUSTER_PINOUT"
source_citation: "1987–1989 Daytona Digital Instrument Cluster Swap Archive (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "26-Pin Digital Dash Connector & Analog Harness Mapping"
ecu_families: ["LM_PM_84_87", "SMEC_DUAL_BOARD"]
ecu_scope: ["LM_PM_84_87", "SMEC_88_89"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: []
pins_referenced: ["Digital Dash Pin 1..26", "Bulkhead P5 Pin 31", "Bulkhead P5 Pin 34"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "needs_fsm_corroboration"
---

# 1987–1989 Daytona Digital Dash 26-Pin Conversion Pinout

> **TIER 3 CORROBORATION WARNING**: **NEVER use this document as the sole pinout source without Factory Service Manual (FSM) corroboration** for the exact vehicle year and cluster part number. Confirm constant `+12V`, switched `+12V`, and ground continuity with a DMM prior to connecting the cluster.

## 26-Pin Digital Dash Connector Table

| Pin | Function | Digital Dash Wire Color | Analog Equivalent / Harness Notes |
| :---: | :--- | :--- | :--- |
| 1 | Headlamp High Beam Lamp | Red/Orange | Red/Orange |
| 2 | Right Turn Signal | Tan | Tan |
| 3 | Left Turn Signal | Green | Green |
| 4 | Tachometer | Blue/Grey | Grey/Blue |
| 5 | Engine Oil Pressure (Switch) | Grey | Grey |
| 6 | Distance Sensor | White/Orange | White/Orange |
| 7 | Display Dimming | Yellow/Orange | From light switch / radio harness (`YL/OR`) |
| 8 | Air Bag | Black/Brown | Black/Brown |
| 9 | Water Temperature | Purple/Yellow | Purple/Yellow |
| 10 | Instrument Panel Illumination | Orange | Orange |
| 11 | Driver Door Switch | Black/Blue | `BK/LB` at light switch (illuminates decorative lines on door open, not odometer) |
| 12 | Not Used | — | — |
| 13 | Not Used | — | — |
| 14 | Power Loss / Check Engine | Black/Pink | Blue/Pink |
| 15 | Not Used | — | — |
| 16 | Fuel Pacer Lamp | Green/Orange | Unused on analog cluster |
| 17 | Seat Belt Warning Lamp | Red/Blue | Blue/Red |
| 18 | Brake Warning | Grey/Black | Black/Grey (Bulkhead P5 Pin 34 `GY/BK` brake fluid level) |
| 19 | Parking Brake Indicator | Black/White | Bulkhead P5 Pin 31 `BK` parking brake switch |
| 20 | Ground | Black/Green | Ground |
| 21 | Fuel Gauge Sending Unit | Blue | Blue |
| 22 | Engine Oil Gauge | Grey/Yellow | Grey/Yellow |
| 23 | Ground | Black/Orange | Black/Orange |
| 24 | Park Warning Lamp | Black/Blue | Voice alert / Ignition switch `BK/RD` parklamp test |
| 25 | +12V Ignition Feed | Blue/White | Blue/White |
| 26 | Constant +12V Battery Feed | Red | Constant +12V (`RD` at headlight switch) |

## Cluster Fitment Note

- **Body Fitment Warning**: The **LeBaron GTS / Lancer** digital instrument cluster is longer and narrower and does **not** fit Daytona or LeBaron J-body dash openings.
