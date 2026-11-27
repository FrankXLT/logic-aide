> **Scope**: Cleanroom Tier 1 IC Datasheets Digest (`DM74LS193`, `SST27SF512/010/020`, `LM2907/LM2917`, and `NJM4151`)
> **Boundary**: Electrical limits, DIP/PLCC pin configurations, frequency-to-voltage equations, and read-side socket/pin-isolation notes only; excludes device-programmer write sequences per Hardware Deadbolt.

---
doc_id: "ic-datasheets-74ls193-sst27sf-lm2907-njm4151"
title: "IC Datasheet Reference: DM74LS193 Counter, SST27SFxx Memory, LM2907/2917 F-to-V, and NJM4151 V/F"
trust_tier: "Tier 1"
source_type: "IC_DATASHEET_DIGEST"
source_citation: "Fairchild DM74LS193 (DS006406), SST SST27SF512/010/020 (S71152-12-000), National Semiconductor LM2907/LM2917 (TL/H/7942), New Japan Radio NJM4151 (2004-10-25)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "74ls193 p.1-7, S71152 p.1-5, LM2907N p.1-5, NJM4151_E p.1-6"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
hex_symbols: []
symbols_referenced: ["DM74LS193", "SST27SF512", "SST27SF010", "SST27SF020", "LM2907", "LM2917", "NJM4151", "VPP", "OE#", "CE#", "Z12"]
pins_referenced: ["Pin 1", "Pin 2", "Pin 3", "Pin 4", "Pin 5", "Pin 6", "Pin 7", "Pin 8", "Pin 11", "Pin 14", "Pin 16", "Pin 22", "Pin 24", "Pin 28", "Pin 32"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Hardware Bench & Conditioning IC Reference (`74LS193`, `SST27SF`, `LM2907`, `NJM4151`)

## 1. `DM74LS193` Synchronous `4-Bit` Up/Down Binary Counter (`DS006406`)

Used in bank-switching, address-decoding, and bench pulse-divider fixtures (`16-lead PDIP N16E` / `SOIC M16A`):

| Pin | Function | Role in Timing / Address Fixtures |
| :---: | :--- | :--- |
| **`1, 15, 10, 9`** | `DATA B, DATA A, DATA C, DATA D` | Asynchronous parallel preset inputs (loaded when `LOAD` `Pin 11` goes LOW) |
| **`3, 2, 6, 7`** | `QA, QB, QC, QD` | `4-bit` master-slave flip-flop outputs (`LSB -> MSB`, clocked `LOW->HIGH`) |
| **`4` / `5`** | `COUNT DOWN` / `COUNT UP` | Dual clock inputs (hold inactive clock `HIGH` while pulsing active input) |
| **`11` / `14`** | `LOAD` (active-low) / `CLEAR` (active-high) | `CLEAR` high forces `QA–QD` low independently of clocks/load |
| **`13` / `12`** | `BORROW` / `CARRY` (active-low pulse outputs) | Direct ripple-free multi-stage cascading without external gating |
| **`16` / `8`** | `VCC` / `GND` | Standard `+5 V` TTL rail and ground |

---

## 2. `SST27SF512` / `SST27SF010` / `SST27SF020` Read-Side Pinout & Isolation (`S71152`)

Many-Time Programmable (`MTP`) SuperFlash replacements for UV-erasable `27C256`/`27C512` (`SMEC` / `SBEC` socket adapters):

- **Package & Capacity Variants**:
  - **`SST27SF512` (`512 Kbit` = `64 KB`, `A15–A0`)**: `28-Pin` `PDIP` (`600 mil`) drops into `28-pin` `27C512` footprint; **`Pin 22` is `OE#/VPP`** (`11.4–12 V` only off-vehicle during external programmer use).
  - **`SST27SF010` (`1 Mbit` = `128 KB`, `A16–A0`) & `SST27SF020` (`2 Mbit` = `256 KB`, `A17–A0`)**: **`32-Pin`** `PDIP` / `PLCC` / `TSOP`. On `32-Pin`, **`Pin 1` is `VPP`**, **`Pin 2` (`A16`)**, **`Pin 30` (`A17` on `020`, `NC` on `010`)**, **`Pin 31` (`PGM#`)**, **`Pin 22` (`CE#`)**, **`Pin 24` (`OE#`)**, **`Pin 32` (`VDD` = `4.5–5.5 V`)**, **`Pin 16` (`VSS`)**.
- **28-Pin ECU Socket Alignment Rule (`SECURITY.md` + `DATA_MODEL_AND_MCP.md`)**: When fitting a `32-pin` `SST27SF010/020` adapter or Ostrich `28/32-pin` header into a **`28-pin` `SMEC`/`SBEC` ECU socket**, align the bottom `28` pins (**matching ECU `Pin 14` = `VSS` to IC `Pin 16` = `VSS`**, leaving top `4` pins overhanging above socket `Pin 1` and `Pin 28` unused/jumpered to tie `VCC` and address bank selects safely). Always verify `Pin 11` harness isolation and seat orientation (`notch toward Pin 1`) prior to keying on.
- **Hardware Identification & Read-Only Guardrail**: Silicon ID read (`A9 = VH` = `11.4–12V`, off-board only) returns Manufacturer `0xBF` at `0000H`, with Device IDs `0xA4` (`SF512`), `0xA5` (`SF010`), `0xA6` (`SF020`). In-vehicle ECU operation holds `OE#/VPP` at TTL levels only (`VPP = VDD` or `VSS`)—never energize `11.4–12 V` `VPP` on a live harness or `SBEC` `Z12`-grounded `Pin 11`.

---

## 3. `LM2907` / `LM2917` Frequency-to-Voltage Tachometer Converter (`TL/H/7942`)

Forms the tachometer-conditioning front end (`66–67 Hz/V`) feeding `Knock_Board` Arduino `A0` (`0–5 V` input):

- **Key Ratings (`LM2907N.pdf` p.2–5)**:
  - **Max Supply Voltage (`VCC`)**: `28 V` (`LM2917` integrates an internal **`7.56 V` active shunt Zener regulator** across power pins; use **`R_DROP = 470 ohm`** series resistor for `9–16 V` automotive rails so `~3 mA` bias regulates within `160 mV`).
  - **Input Stage**: `LM2907-8`/`LM2917-8` ground-references one comparator input for single-ended `VR` / zero-crossing pickup (`±28 V` swing protection); `14-pin` variants expose differential tachometer inputs (`20 mV–30 mV` hysteresis).
- **Transfer & Ripple Equations**:
  - **Output Voltage**: $$V_O = V_{CC} \cdot f_{IN} \cdot C_1 \cdot R_1 \cdot K \quad (K \approx 1.0)$$
  - **Standard Reference (`15 V`, `C1 = 0.01 uF`, `R1 = 100 kOhm`)**: yields `~67 Hz/V` (`1 V @ 67 Hz` -> `~1.0 V / 1005 RPM` on 4-cyl `2 pulses/rev`); adjust `R1` or `VCC` (`7.56 V` regulated) so `f_max` remains `< 5 V` at `A0` with `5.1 V` Zener (`1N4733A-TR`) protection.
  - **Max Frequency & Ripple**:
    $$f_{MAX} = \frac{I_2}{C_1 \cdot V_{CC}}, \qquad V_{RIPPLE\,(pk-pk)} = \frac{V_{CC}}{2} \cdot \frac{C_1}{C_2} \cdot \left(1 - \frac{V_{CC} \cdot f_{IN} \cdot C_1}{I_2}\right)$$
  - Keep `C1 > 500 pF` for charge-pump accuracy and `V_O / R1 <= I3` (`I2, I3` typically `~180 uA` @ `12 V`).

---

## 4. `NJM4151` Precision Voltage-to-Frequency / Frequency-to-Voltage IC (`DIP8` / `DMP8`)

Alternative bipolar `V/F` and `F/V` interface (`8 V` to `22 V` supply, `1.0 Hz` to `100 kHz` operating range):

| `NJM4151` Pin | Function | Circuit Notes (`2004-10-25` Datasheet) |
| :---: | :--- | :--- |
| **`1`** | Current Source Out | Switched precision current pulses integrated across passive `R_B C_B` in `F/V` mode (`Fig. 5`) |
| **`2`** | Scale Factor (`R_S`) | Sets switched current magnitude (`~140 uA` nominal) |
| **`3`** | Logic Output | Open-collector frequency pulse output (`15 V` logic compatible) |
| **`4` / `8`** | `GND` / `V+` | Single-supply `8–22 V` rail (`0.01 uF` bypass recommended at `Pin 6` `>10 kHz`) |
| **`5`** | One-Shot `R_O, C_O` | Sets constant one-shot pulse width: `T = 1.1 * R_O * C_O` (`F/V` input pulse must be `< T`) |
| **`6` / `7`** | Threshold / Input Voltage | Comparator differential inputs; in `F/V` mode (`Fig. 5`, `1%` linearity), AC-coupled negative pulses trigger `Pin 6` (`NJM311`/`NJM2901` comparator recommended to square sinusoidal pickups; precision op-amp `Fig. 4`/`6` achieves `0.05%` linearity & `10 ms` response). |
