> **Scope**: Cleanroom Tier 1 Chrysler Bulkhead Disconnect Pinouts (`1984 L-Body` 40-Way; `1987 G`, `1988 K/P/D`, `1989 G`, and `1989 S-Body` 50-Way)
> **Boundary**: Pinout cavity tables, circuit codes, wire gauges/colors, and year-to-year connector evolution only; excludes copyrighted FSM prose and write sequences.

---
doc_id: "chrysler-50way-bulkhead-pinouts"
title: "Chrysler Bulkhead Disconnect Pinout Matrix (1984 40-Way vs. 1987–1989 50-Way)"
trust_tier: "Tier 1"
source_type: "OEM_BULKHEAD_PINOUT_DIGEST"
source_citation: "Chrysler Electrical Wiring Diagrams: 84 L-Body (MZ 63, 8-612), 87 G-Body, 88 K/P/D & Aries/Reliant (K-PD 60, 8K-67), 89 G-Body (G-V 75/76, 89 8W-3), and 89 S-Body Minivan (8W-155)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "8-612 (40-Way), K-PD 60 / 8K-67, G-V 75/76 / 89 8W-3, G-V 93/94, 89 8W-155"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: ["Z1", "K3", "D40", "C20", "G7", "G20", "G6", "G60", "T21", "J1", "J2", "J10", "S2", "U3", "MX1", "MX2", "L1", "C13"]
pins_referenced: ["Cavity 1", "Cavity 11", "Cavity 12", "Cavity 17", "Cavity 18", "Cavity 24", "Cavity 31", "Cavity 33", "Cavity 37", "Cavity 38", "Cavity 41", "Cavity 42", "Cavity 47", "Cavity 49", "Cavity 50"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler Bulkhead Disconnect Pinout Matrix (`1984` `40-Way` vs. `1987–1989` `50-Way`)

Chrysler transitioned FWD firewall pass-through harnessing from a **`40-Way` bulkhead disconnect (`1984 L/M-Z`, sheet `MZ 63`)** to a unified **`50-Way` bulkhead disconnect (`1987–1989` `G`, `K/P/D`, and `S`-Body)**. Retaining identical outer indexing (`Small Index` at upper corner, `Large Index` at lower base, central bolt boss), the `50-Way` block carries critical powertrain and instrumentation signals across firewall pass-through cavities (`Z1` Fuel Pump, `K3` Check Engine / Power Loss Lamp, `T21` Tachometer, `D40` Brake Sense, `C20` A/C Clutch, and `J1`/`J2`/`J10` Ignition feeds).

## 1. Platform & Connector Evolution Summary

| Model / Year Source | Diagram Sheet ID | Cavity Count | Key Structural / Signal Milestones |
| :--- | :--- | :---: | :--- |
| **`1984 L/M-Z` (`84 L`)** | `8-612` (`MZ 63`) / `8-613` | **`40-Way`** | `6x7` center-bolt matrix (`1–40`); `Q20` alt. field `Cav 37`, `J2` `Cav 38`, `J1` `Cav 40`, `T11` tach `Cav 31`. **Not pin-compatible with `50-Way` connectors.** |
| **`1987 G-Body` / `88 K/P/D`** | `G-V 93/94` (`80-96`), `K-PD 60` (`8K-67`) | **`50-Way`** | Standardized layout: `L1` `Cav 1`, `G20`/`G6`/`G60` `Cav 17–19`, `Z1` Fuel Pump `Cav 24`, `T21` Tach `Cav 33`, `K3` MIL/Power Loss `Cav 38`, `D40` Brake Sense `Cav 41`, `J2` `Cav 47`, `J1`/`J10` `Cav 49/50`. |
| **`1989 G-Body` (`Daytona`)** | `G-V 75` / `G-V 76` (`89 8W-3`, `8G-90`/`86-91`) | **`50-Way`** | Adds `Z6` Fuel Flow `Cav 44`, `G31`/`G32` Ambient Temp `Cav 39/40`, and `Q2`/`Q20` Ignition feeds at `Cav 49/50`. |
| **`1989 S-Body` (`Mini`)** | `8W-155` (`89 8W-9`) | **`50-Way`** | Assigns `MX2`/`MX1` **`CCD Bus (-)/(+)`** at **`Cav 11` / `Cav 12`** (`1989` Caravan/Voyager electronic cluster & EATX). |

## 2. `1987–1989` `50-Way` Bulkhead Cavity Matrix (Instrument Panel <-> Engine)

| Cavity | `87 G` / `88 K/P/D` (`K-PD 60` / `Aries`) | `89 G-Body` (`G-V 75/76`) | `89 S-Body Mini` (`8W-155`) | Primary Subsystem |
| :---: | :--- | :--- | :--- | :--- |
| **`1`** | `L1` `12RD*` Battery Feed | `L1` `12RD*` Battery Feed | `L1` `12RD/WT*` Ign Switch Feed | Unswitched `B+` Supply |
| **`2`** | `—` | `L28` `14BR/YL*` Concealed Headlamp | `V3` `18BR/WT*` Wiper Low | Body / Wiper |
| **`3`** | `—` | `L31` `14DB/YL*` Concealed Headlamp | `Q7` `14RD/PK*` Battery Supply | Body / Van Conversion |
| **`4`** | `—` | `—` | `V4` `18RD/YL*` Wiper High | Wiper / Unused |
| **`5`** | `L4` `14VT*` Headlamp Low | `L4` `14VT*` / `L42` `16VT/RD*` | `L4` `14VT*` Headlamp Low | Exterior Lighting |
| **`6`** | `L3` `14RD/OR*` / `16RD/OR*` High | `L3` `14RD/OR*` / `16RD/OR*` High | `L3` `14RD/OR*` / `16RD/OR*` High | Exterior Lighting |
| **`7`** | `X30` `20DB/RD*` Speed Control | `G5` `20DB*` Fused Ign Feed (`89`: `G5`) | `X30` `22DB/RD*` Speed Control | Speed Control / Ign Feed |
| **`8`** | `X31` `20BR/RD*` Speed Control | `M1` `18PK` / `L22` Underhood Lamp | `X31` `22BR/RD*` Speed Control | Speed Control / Lamps |
| **`9`** | `X33` `20WT/RD*` Speed Control | `—` | `X33` `22WT` Speed Control | Speed Control |
| **`10`** | `X32` `20YL/RD*` Speed Control | `L34` `16LB` Fog Lamp Feed | `X32` `22YL/RD*` Speed Control | Speed Control / Fog |
| **`11`** | `—` | `—` | `MX2` `20WT/BK*` **`CCD Bus (-)`** | Diagnostic / Body Bus |
| **`12`** | `—` | `—` | `MX1` `20BK` **`CCD Bus (+)`** | Diagnostic / Body Bus |
| **`13–16`** | `—` | `—` | `—` (Cav 13–16) | Spare |
| **`17`** | `G20` `20VT/YL*` Water Temp Gauge | `G20` `22VT/YL*` Water Temp Gauge | `G20` `22VT/YL*` Temp Gauge | Cluster Instrumentation |
| **`18`** | `G6` `20GY` Oil Pressure Lamp | `G6` `22GY` Oil Pressure Lamp | `G6` `22GY` Oil Pressure Lamp | Cluster Instrumentation |
| **`19`** | `G60` `22GY/YL*` Oil Press Send | `G60` `22GY/YL*` Oil Press Send | `G60` `22GY/YL*` Oil Press Send | Cluster Instrumentation |
| **`20`** | `U3` `20OR/LG*` Fuel Pacer Lamp | `U3` `20OR/LG*` Fuel Pacer Lamp | `U3` `22OR/LG*` Up-Shift Lamp | Shift / Economy Lamp |
| **`21`** | `D6` `18LG` / `20LG` Left Turn | `D6` `18LG` Left Turn | `D6` `18LG` Left Turn | Turn Signals |
| **`22`** | `D5` `18TN` / `20TN` Right Turn | `D5` `18TN` Right Turn | `D5` `18TN` Right Turn | Turn Signals |
| **`23`** | `A3` `16PK` Hazard Flasher | `A3` `16PK/WT*` Hazard Flasher | `A3` `16PK/WT*` Hazard Flasher | Hazard Circuit |
| **`24`** | **`Z1` `14DG/BK*` Fuel Pump Feed** | **`Z1` `14DG/BK*` Fuel Pump Feed** | **`Z1` `14DG/BK*` Fuel Pump Feed** | **ASD -> Fuel Pump** |
| **`25`** | `—` | `—` | `S4` `18BR/YL*` Liftgate Rel (ATX) | Spare / Liftgate |
| **`26`** | `L7` `18BK/YL*` Park Lamps | `L7` `18BK/YL*` Park/Marker Lamps | `L7` `18BK/YL*` Parking Lamps | Exterior Lighting |
| **`27`** | `B1` `18WT` Back-Up Feed | `B1` `18WT` Back-Up Feed | `B1` `18WT` Back-Up Feed | Back-Up Switch |
| **`28`** | `B2` `18VT/BK*` Back-Up Lamp | `B2` `18VT/BK*` Back-Up Lamp | `B2` `18VT/BK*` Back-Up Lamp | Back-Up Lamps |
| **`29`** | `V10` `20BR` Washer Motor | `V10` `18BR` Washer Motor | `V10` `18BR` Washer Motor | Washer Pump |
| **`30`** | `—` (`87 G`: `V11` `20BK/TN*` Low Wash) | `V11` `22BK/TN*` Low Washer | `V11` `22BK/TN*` Low Washer | Washer Level |
| **`31`** | **`S2` `14YL` Ignition Start** | `P2` `22BK*` Park Brake Ind. | **`S2` `14YL` Ignition Start** | Start / Brake Note |
| **`32`** | `H2` `18DG/RD*` Horn | `H2` `14DG/RD*` Horn Feed | `H2` `18DG/RD*` Horns | Horn Relay |
| **`33`** | `—` (`87 G`: `T21` `22GY/LB*` Tach) | **`T21` `22`/`20GY/LB*` Tachometer** *(dash/engine)* | **`T21` `22GY/LB*` Tachometer** | **Cluster Tach Input** |
| **`34`** | `P5` `20GY/BK*` Brake Warn Lamp | `P5` `22GY/BK*` Brake Warn Lamp | `—` | Brake Warning |
| **`35`** | `V5` `18DB` Wiper Switch | `V5` `18DG` Wiper Switch | `—` | Wiper Motor |
| **`36`** | `V6`/`V1` `18DB` Wiper Motor | `V6`/`V1` `18DB` Wiper Switch | `—` | Wiper Motor |
| **`37`** | **`C20` `22DB/OR*` A/C Clutch** | **`C20` `18DB/OR*` / `C2` A/C Press** | `—` | **SMEC `1-45` A/CSense/Clutch** |
| **`38`** | **`K3` `20BK/PK*` Check Engine MIL** | **`K3` `22BK/PK*` Check Engine MIL** | `—` | **Check Engine / Power Loss** |
| **`39`** | `—` | `G31` `20VT/LG*` Ambient Temp | `—` | Overhead Console Temp |
| **`40`** | `—` | `G32` `22BK/LG*` Ambient Temp | `—` | Overhead Console Temp |
| **`41`** | **`D40` `18WT/TN*` Brake Sense** | **`D40` `18WT/PK*` Brake Sense** | `—` | **SMEC `1-29` Brake Input** |
| **`42`** | `—` | `G7` `22`/`20WT/OR*` Distance Sensor *(dash/engine)* | `—` | Cluster/Navigator Speed |
| **`43`** | `—` | `P51` `22BK/RD*` Brake Bulb Check | `—` | Ignition / Bulb Test |
| **`44`** | `—` | `Z6` `22LB*` Fuel Flow Sensor | `—` | Trip Computer Flow |
| **`45`** | `V3` `18BR*` Wiper Low | `V3` `18BR*` Wiper Low | `—` | Wiper System |
| **`46`** | `V4` `18RD/YL*` Wiper High | `V4` `18RD/YL*` Wiper High | `—` | Wiper System |
| **`47`** | **`J2` `14DB` Ignition Run/Start** | **`J2` `14DB` Ignition Run/Start** | **`J2` `14DB` Ignition Run/Start** | **ECU Run/Start Feed** |
| **`48`** | `C13` `12BK/RD*` Rear Defogger | `C13` `12BK/RD*` Rear Defogger | `C13` `12BK/RD*` Rear Defogger | Defogger Grid |
| **`49`** | **`J1` `12RD` Ignition Switch Feed** | `Q2` `14BK` Ignition Run-Start | **`J1` `12RD` Ignition Feed** | Main Power Feed |
| **`50`** | **`J10` `12PK*` Ignition Switch** | `Q20` `12BK/OR*` Ignition Run-Only | **`J10` `12PK/BK*` Ignition Feed** | Switched/Battery Feed |

> [!WARNING]
> Note cross-year shifts at **`Cav 31` (`S2` Start vs. `P2` Park Brake on `89 G`)**, **`Cav 33` (`T21` Tachometer absent on carb/early K-PD base sheets, populated on `87 G` / `89 G` / `89 Mini`)**, and **`Cav 49/50` (`J1/J10` vs. `Q2/Q20` on `89 G`)**. Always ohm-check circuits at both terminal ends before splicing an engine-swap harness.

## 3. `1984 L/M-Z` (`40-Way` `8-612`) Key Engine Circuits Comparison

| Cavity (`84 L` `40-Way`) | Circuit & Gauge | `84 L/M-Z` Assignment (`8-612` Engine Side) | Closest `50-Way` Counterpart |
| :---: | :--- | :--- | :--- |
| **`1–3`** | `X30`/`X31`/`X33` (`20 ga`) | Speed Control option circuits | `50-Way` `Cav 7, 8, 9` |
| **`4`** | `J10` `12PK*` | Ignition Switch | `50-Way` `Cav 50` |
| **`6`** | `R6` `12BK` | Splice — Charging Circuit, Alternator | Fusible link / `Cav 49` alt |
| **`12`** | `C2` `16DB/YL*` | A/C Low Pressure Switch | `50-Way` `Cav 37` (`C2`/`C20`) |
| **`14`** | `S2` `18YL` | Starter Relay — Ignition | `50-Way` `Cav 31` |
| **`17–18`** | `G2` `20VT` / `G6` `20GY` | Water Temp Gauge/Lamp & Oil Pressure Switch | `50-Way` `Cav 17, 18` |
| **`27`** | `G60` `20GY/YL*` | Oil Pressure Sending Unit (Rallye Cluster) | `50-Way` `Cav 19` |
| **`31`** | `T11` `20GY*` | Tachometer (Option) | `50-Way` `Cav 33` (`T21`) |
| **`37`** | `Q20` `12BR` | `(1.6)` Alternator Field Term | `50-Way` `Cav 50` (`Q20` on `89G`) |
| **`38`** | `J2` `14DB` | Splice Ignition Run Circuit | `50-Way` `Cav 47` |
| **`40`** | `J1` `12RD` | Ignition Switch | `50-Way` `Cav 49` |
