> **Scope**: Cleanroom Tier 1 Chrysler P-Body (`Shadow` / `Sundance`) `1987` `LM`+`PM` vs. `1988` `SMEC` Turbo Engine Wiring Digest
> **Boundary**: Pin assignments, circuit codes, wire colors/gauges, sensor/actuator splice paths, and model-year hazard checks only; zero copyrighted FSM prose.

---
doc_id: "chrysler-87-88-pbody-turbo-wiring"
title: "Chrysler 1987 (LM/PM) & 1988 (SMEC) P-Body 2.2L Turbo Engine Wiring Pinouts"
trust_tier: "Tier 1"
source_type: "OEM_ENGINE_WIRING_DIGEST"
source_citation: "1987 Dodge Shadow 2.2L SOHC Turbo EFI Wiring (Parts 1-5) & 1988 P/PD Chrysler Wiring Diagrams (8N-12..8N-27, P-PD 63/64)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "87P Fig. 18-19 (Parts 1-5), 88P 8N-14..8N-27 (P-PD 5..18) & 8N-72..8N-73 (P-PD 63/64)"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: ["Z1", "K3", "K4", "K5", "K7", "K8", "K9", "K10", "K13", "K14", "K15", "K16", "K19", "K22", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N11", "N12", "N13", "Y1", "Y4", "Y5", "Y6", "Y7", "Y11", "Y12", "DK20", "DK21", "DK5", "R3", "R31", "C20", "C27", "D40", "G7", "S4", "T21", "J1", "J2", "J5", "J9"]
pins_referenced: ["1-1", "1-2", "1-3", "1-4", "1-5", "1-7", "1-8", "1-9", "1-10", "1-12", "1-13", "1-17", "1-18", "1-19", "1-20", "1-22", "1-23", "1-26", "1-29", "1-30", "1-31", "1-32", "1-34", "1-38", "1-39", "1-41", "1-45", "1-47", "1-48", "1-50", "1-51", "1-52", "1-53", "1-54", "1-56", "1-57", "1-58", "1-59", "1-60", "2-1", "2-3", "2-4", "2-5", "2-6", "2-7", "2-8", "2-9", "2-10", "2-11", "2-14"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler `1987` (`LM`+`PM`) & `1988` (`SMEC`) `P-Body` `2.2L` Turbo Wiring Reference

Between the `1987` and `1988` P-body (`Dodge Shadow` / `Plymouth Sundance`) model years, Chrysler migrated `2.2L` Turbo fuel/ignition control from the **two-box `6803` Logic Module (`1-xx` Red / `2-xx` Blue `25-way` connectors) + Power Module (`10-way #1` / `12-way #2`)** to the integrated **under-hood `68HC11` Single Module Engine Controller (`SMEC`: `1-xx` `60-Way` Connector + `2-xx` `14-Way` Connector)**.

> [!CAUTION]
> **Mandatory Pin-Verification Checks (`SECURITY.md`)**:
> 1. **`60-Way` `Pin 11` Hazard**: Keep `SMEC` `60-way` `Pin 11` strictly isolated on `SBEC` conversions (`SBEC` bonds `Pin 11` to `Z12` ground).
> 2. **Cross-Year Sensor / SCI Swaps (`Pins 2, 3, 25, 45`)**: Look closely at **`1-2` (`N12` Knock on `88` P-body `8N-21`)** vs. **`2-24` (`N12` Knock on `87` P-body `LM`)** vs. `1989+` `CTS`/`Knock` swaps on **`Pin 2` / `Pin 3`**, and note that **`88 P-Body` `SMEC` routes SCI TX (`DK21`) to `1-51` and SCI RX (`DK20`) to `1-31`** (whereas later `1989–1993` `60-way` `SBEC` standardization uses **`Pin 25` `D21` / `Pin 45` `D20`**, while `88 P-Body` **`1-45` carries `C20` A/C Pressure Switch**!).

---

## 1. `1988` `P-Body` `SMEC` `60-Way` Connector (`1-xx`, Sheet `8N-21..27`)

Mapped directly from `1988 P/P,D` Turbo wiring (`P-PD 12`–`P-PD 18`) mounted on the left fender side shield:

| SMEC Pin | Circuit Code & Wire Spec | Target Subsystem / Component | Notes & Routing |
| :---: | :--- | :--- | :--- |
| **`1-1`** | `K4` `20DG/RD*` | `MAP` Sensor Supply (`+5V` ref / input feed) | Front of right shock tower |
| **`1-2`** | `N12` `18BK/LG*` | **Detonation (Knock) Sensor** | Rear of valve cover, right side (`8N-21`) |
| **`1-3`** | `K10` `20TN*` | **Engine Coolant Temp Sensor (`ECT`)** | Top left of engine (`N5` splice return) |
| **`1-4`** | `N5` `18BK/LB*` | **5V Sensor Reference Ground Return** | Spliced to `MAP`, `TPS`, `ECT`, `Dist Sync/Ref`, `VSS` |
| **`1-5`** | `K5` `16BK*` | **Power / Diagnostic Logic Ground** | Ring terminal at fuel rail mounting bolt + Diag Conn. |
| **`1-7`** | `X33` `20WT/LG*` | Speed Control Resume / Switch | Steering column switch |
| **`1-8`** | `X32` `20YL/RD*` | Speed Control On/Off Sense | Splice/servo interface (`P-PD 18`) |
| **`1-9`** | `X31` `20BR/RD*` | Speed Control Set | Steering column switch |
| **`1-10`** | `Z1` `16DG/BK*` | **ASD Output Sense / Coil+ / Injector+ Feed** | Spliced via `Z1` to coil `+`, `O2` heater, fuel pump |
| **`1-12`** | `K14` `18DB*` | **Auto Shutdown (`ASD`) Relay Pull-In Drive** | Switched ground to ASD relay coil (`P-PD 14`) |
| **`1-13`** | `K8` `20VT/WT*` | **`+5 V` Reference Supply (`MAP` & `TPS`)** | Splice `K8` (`MAP` center + `TPS` ref) |
| **`1-17`** | `N2` `18BR*` | `AIS` Stepper Phase (`N2`) | Throttle body `AIS` 4-pin black conn. |
| **`1-18`** | `N4` `18YL/BK*` | `AIS` Stepper Phase (`N4`) | Throttle body `AIS` 4-pin black conn. |
| **`1-19`** | `N1` `18GY/RD*` | `AIS` Stepper Phase (`N1`) | Throttle body `AIS` 4-pin black conn. |
| **`1-20`** | `N3` `18VT/BK*` | `AIS` Stepper Phase (`N3`) | Throttle body `AIS` 4-pin black conn. |
| **`1-22`** | `K7` `18OR/DB*` | **Throttle Position Sensor (`TPS`) Signal** | Top of throttle body injector |
| **`1-23`** | `N11` `18BK/DG*` | **Heated `O2` Sensor Signal** | Center rear exhaust manifold (`J9` shield gnd) |
| **`1-26`** | `Y7` `18TN/YL*` | **Distributor Sync (`Cam`) Pickup** | Gray connector, right front of engine (`N5`/`N6`) |
| **`1-29`** | `D40` `18WT/PK*` | **Brake Lamp / Speed Control Sense** | Thru `50-Way` `Cav 41` to stop lamp switch |
| **`1-30`** | `S4` `20BR/YL*` | **Park / Neutral / Clutch Interlock Sense** | Starter relay interlock (`P-PD 5` / `15`) |
| **`1-31`** | **`DK20` `20LG`** | **`SCI` Diagnostic `RX` (`Host -> SMEC`)** | **6-Pin Black Diag Conn. (left shock tower)** (`8N-22`) |
| **`1-32`** | `Y1` `20GY/WT*` | **`A/C` / Radiator Fan Relay / Misc Solenoid Out** | Output bank (`P-PD 16`) |
| **`1-33`** | `K16` `20VT/YL*` | **Solenoid / Relay Control** | Output bank (`P-PD 16`) |
| **`1-34`** | `K15` `20YL` | **Solenoid / Relay Control** | Output bank (`P-PD 16`) |
| **`1-38`** | `Y4` `20LB` | **Baro-Read Solenoid Control** | Right shock tower (`J2` fed) |
| **`1-39`** | `Y6` `20LG/BK*` | **Purge / EGR Control Solenoid** | Front of right shock tower (`P-PD 13`) |
| **`1-41`** | `J1` `16RD` | **Unswitched Keep-Alive Battery (`+12 V`)** | Direct fusible link from battery `+` |
| **`1-45`** | **`C20` `18BR`** | **`A/C` Compressor Clutch / Switch Sense** | **Occupied on `88 P-Body` (NOT `SCI RX`!)** |
| **`1-47`** | `N7` `18GY*` | **Distributor Reference (`RPM` / Crank) Pickup** | Black connector, right front of engine |
| **`1-48`** | `G7` `20WT/OR*` | **Vehicle Distance (`VSS`) Sensor** | Rear of transmission, black 2-pin (`N5` ground) |
| **`1-50`** | `T21` `20GY/LB*` | **Tachometer Output to Instrument Cluster** | Thru `#22 ENG` splice to right PCB conn. (`K` pin) |
| **`1-51`** | **`DK21` `20PK`** | **`SCI` Diagnostic `TX` (`SMEC -> Host`)** | **6-Pin Black Diag Conn. (left shock tower)** (`8N-22`) |
| **`1-52`** | `N6` `18OR` | **Sensor Supply (`+8 V` Hall Pickups)** | Feeds Dist. Sync & Ref sensors (`N6` splice) |
| **`1-53`** | `X36` `20TN/RD*` | Speed Control Vent Valve Control | Servo under battery tray |
| **`1-54`** | `K1` `20PK*` | **Wastegate Control Solenoid (`WGDC`)** | Front of right shock tower (`J2` fed) |
| **`1-56`** | `N13` `20DB/OR*` | `A/C` Heater/Control Interface | A/C heater system (`P-PD 12`) |
| **`1-57`** | `C27` `20DB/PK*` | `A/C` Control Interface | A/C heater system (`P-PD 12`) |
| **`1-58`** | `K19` `20DB/YL*` | `ASD` Coil / Diagnostic Status Feedback | Connected to `ASD` relay & 6-pin diag conn. |
| **`1-59`** | `K3` `20BK/PK*` (`22BK/PK*`) | **Power Loss / Check Engine (`MIL`) Lamp** | `#23 ENG` to left cluster connector (`8N-25`) |
| **`1-60`** | `X35` `20LG/RD*` | Speed Control Vacuum Valve Control | Servo under battery tray |

---

## 2. `1988` `P-Body` `SMEC` `14-Way` Power-Stage Connector (`2-xx`, Sheet `8N-21..25`)

| 14-Way Pin | Circuit Code & Wire Spec | Subsystem / Function | Hardware Destination |
| :---: | :--- | :--- | :--- |
| **`2-1`** | `N6` `18OR` | `+8 V` Hall-Sensor Regulator Branch | Spliced to `1-52` & Dist. Sync/Ref (`8N-23`) |
| **`2-3`** | `K14` `18DB*` | **`ASD` Relay Coil Return / Parallel Feed** | Spliced to `1-12` (`K14` splice, `8N-23`) |
| **`2-4`** | `DK21` `20PK` | **`SCI` `TX` Parallel Link** | Tied with `1-51` to 6-pin Diag Conn. (`8N-22`) |
| **`2-5`** | `Y1` `20GY/WT*` | Output Driver / Radiator Fan Path | Parallel/jumper link with `1-32` (`8N-25`) |
| **`2-6`** | `J9` `16BK` | **Injector / Power Ground** | Ring terminal at fuel rail bolt (`8N-22`) |
| **`2-7`** | `J9` `16BK` | **Injector / Power Ground** | Ring terminal at fuel rail bolt (`8N-22`) |
| **`2-8`** | `K16` `20VT/YL*` | Solenoid / Relay Driver | Linked with `1-33` (`8N-25`) |
| **`2-9`** | **`Y11` `18WT`** | **Injector Bank `#1` + `#2` Driver (Low-Side)** | Top of engine injector harness (`Z1` `+12V` feed) |
| **`2-10`** | **`Y12` `18TN`** | **Injector Bank `#3` + `#4` Driver (Low-Side)** | Top of engine injector harness (`Z1` `+12V` feed) |
| **`2-11`** | `R31` `20DG/OR*` | **Alternator Field / Voltage Control** | Charging system (`P-PD 4`, `8N-21`) |
| **`2-12`** | `J5` `18BK/YL*` | **Ignition Coil `-` Switching Driver** | Drives ignition coil primary (`8N-23`) |
| **`2-13`** | `K15` `20YL` | Solenoid / Relay Driver | Linked with `1-34` (`8N-25`) |
| **`2-14`** | `R3` `18DG` | **Alternator Field Drive (`+`)** | Bosch / Corporate `40/90` alternator (`8N-13`/`23`) |
| **`2-15` / `2-21`** | `K9` `18LB/RD*` + `K5` `16BK*` | **Diagnostic Connector & Power Ground** | Look closely at **`1-16` / `2-15` (`K9` `18LB/RD*`)** and **`2-2` (`K5` `16BK*`)** on `8N-22` bottom bus |

*(Verification note on `8N-22`: the bottom bus shows **`1-5` (`K5`), `2-2` (`K5`), `1-15` (`K9`), `1-16` (`K5`/`K9`), `2-6`, `2-7` (`J9`)**—verify exact pin cavity against `S-HK 87`/`88` or continuity before re-pinning `14-Way` grounds.)*

---

## 3. `1987` `P-Body` `LM`+`PM` Two-Box Bridge Summary (`Fig. 18–19`, Parts 1–5)

On `1987` Turbo `P-Body` (`Dodge Shadow L4-135 2.2L`), functions split between the **Logic Module (`LM`, right cowl: `1-xx` Red / `2-xx` Blue `25-Way`)** and the under-hood **Power Module (`PM`, left side shield: `10-Way #1` + `12-Way #2`)**:

| `1987` `LM` / `PM` Pin | Circuit & Spec | Signal Assignment | `1988` `SMEC` Equivalent |
| :---: | :--- | :--- | :---: |
| **`LM 1-1` / `LM 2-19`** | `K8` `20VT/WT*` / `K4` `20DG/RD*` | `MAP` `+5V` Ref (`1-1`) & `MAP` Signal (`2-19`, via `PM #12-7` link) | `1-13` / `1-1` |
| **`LM 1-2`..`1-6`** | `K16` (`1-2`), `Y1` (`1-3`), `K3` (`1-4`), `R31` (`1-5`), `K15` (`1-6`) | Relays / Power Loss Lamp (`K3`) / Alt Sense (`R31`) via `PM` | `1-33,32,59`, `2-11`, `1-34` |
| **`LM 1-7` / `1-8` / `1-9` / `1-10`** | `K14` (`1-7,1-8`), `G7` `VSS` (`1-9`), `N7` `Dist Ref` (`1-10`) | `ASD` Command (`K14`), Distance (`G7`), Reference (`N7`) | `1-12/2-3`, `1-48`, `1-47` |
| **`LM 1-11` / `1-13` / `1-24`** | **`DK21` `20PK`** (`1-11`), **`DK20` `20LG`** (`1-13`), **`K5` `16BK*`** (`1-24`) | **Diagnostic Conn. `SCI TX` / `SCI RX` / Logic Ground** | `1-51`, `1-31`, `1-5` |
| **`LM 1-12` / `1-15` / `1-17` / `1-18` / `1-22` / `1-23` / `1-25`** | `T21` Tach (`1-12`), `Y4` Baro (`1-15`), `K19` (`1-17`), `N1` `AIS` (`1-18`), `N2` `AIS` (`1-22`), `N6` (`1-23`), `N5` (`1-25`) | Cluster Tach, Baro Sol, Diag Feedback, `AIS`, `+8V`, `5V` Gnd | `1-50`, `1-38`, `1-58`, `1-19`, `1-17`, `1-52`, `1-4` |
| **`LM 2-1` / `2-21`** | `K6` `18OR/WT*` (`2-1`), `K7` `18OR/DB*` (`2-21`) | `TPS` `+5V` Ref (`K6`) & `TPS` Wiper Signal (`K7`) | `1-13`, `1-22` |
| **`LM 2-2`..`2-25` (Key Signals)** | `J11` (`2-2,2-22`), `Y6` Purge (`2-5`), `K9` Diag (`2-7,2-8`), `N11` `O2` (`2-18`), `K22` Ign Trigger (`2-20`), `K10` `ECT` (`2-23`), **`N12` Knock (`2-24`)**, `K13` Charge Temp (`2-25`) | Note **`2-24` (`N12` Knock)** and **`2-25` (`K13` Charge Temp)** + `PM` `10-Way` (`Y11 #5`, `J1 #4`, `K14 #3`, `J2 #2`, `J5 #1 Coil-`, `Z1 #6`, `Y12 #7`, `R3 #8`, `K22 #9`, `J9 #10`) | `1-23`, `1-2`, `1-3`, `2-12`, `2-9`, `2-10` |

## 4. `1988` `P-Body` Engine-to-Instrument-Panel `37-Cavity` Firewall Disconnects (`P-PD 63` / `64`)

- **Cavities `1–18` (20-Pin Rectangular)**: `Cav 2` `L1` `12RD*` Battery, `Cav 4` `Q2` Ign Run/Acc, `Cav 5` `Q20` Ign Run-Only, `Cav 7/8` `D3`/`D4` Brake Switch, `Cav 10` `J2` `14DB` Ign Run/Start, `Cav 12` **`Z1` `14DG/BK*` Fuel Pump/Coil**, `Cav 18` `C2`/`C20` A/C Clutch.
- **Cavities `19–34` (16-Pin Block)**: `Cav 19` `G5` Speed Ctrl, `Cav 22` **`T21` `22GY/LB*` Tachometer**, `Cav 23` **`K3` `22BK/PK*` Power Loss Lamp**, `Cav 26` `U3` Fuel Pacer/Shift Lamp, `Cav 27/28` `G6`/`G20` Oil Pressure & Water Temp.
- **Cavities `35–37` (3-Pin Block)**: `Cav 35` `L34` Fog Lamp, `Cav 37` `L4` Headlamp Low Beam.
