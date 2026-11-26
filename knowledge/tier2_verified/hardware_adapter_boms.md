> **Scope**: Cleanroom Tier 2 Hardware Adapter BOMs, Pinouts & Firmware Constants (`SMEC_88_89` / `SBEC_90_93` / `LM_PM_84_87`)
> **Boundary**: Distilled electronics BOMs, pinouts, and safety clamps only; personal/order metadata and flash-write opcodes omitted.

---
doc_id: "hardware-adapter-boms-cleanroom"
title: "SST27SF020 EEPROM Adapter, Knock/SCI 4N25/LM2917 Board & DIS Adaptor BOMs"
trust_tier: "Tier 2"
source_type: "HARDWARE_BOM_AND_FIRMWARE_DIGEST"
source_citation: "Cleanroom Electronics BOM, DIS PIC Firmware & Knock Board Arduino Archive"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "Wave C Hardware Presets (3 Assemblies)"
ecu_families: ["LM_PM_84_87", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_PM_84_87", "SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K", "2.4L_EDZ"]
hex_symbols: []
symbols_referenced: ["SST27SF020-70-3C-PHE", "NTE74LS193", "4N25", "1N4733A-TR", "LM2917", "SN75176BD", "PIC12F629-E/SN", "PIC16F690", "VB921", "STP10NK80ZFP"]
pins_referenced: ["DB15-1..15", "Arduino-D2..D6", "Arduino-A0..A1", "60-way Pin 11"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Cleanroom Hardware Adapter BOMs & Interface Reference

> **Safety (`SECURITY.md`)**: Enforce `1N4733A-TR` `5.1V` Zener clamping and `4N25` optocoupler isolation on SCI/Arduino interfaces; never wire unprotected 3.3V/5V USB-TTL lines to `+12V`/`+21V` bootstrap pins.

## 1. SST27SF020 + 74LS193 EEPROM Socket Adapter (`sst27sf020_74ls193_socket_adapter`)

| Qty | Part Number | Description |
| :---: | :--- | :--- |
| 10 | `1-390261-4` | IC Sockets 16P ECONOMY TIN |
| 20 | `CCF0710K0JKE36` | 1/4WATT Metal Film Resistors 1/4watt 10Kohms 5% Rated to 1/2watt |
| 5 | `NTE74LS193` | replacement Digital ICs SYNC BIN CNTR DIP-16 |
| 5 | `4832-6004-CP` | DIP Sockets 32P DUAL WIPE DIPSKT |
| 5 | `SST27SF020-70-3C-PHE` | Memory - MTP 256K X 8 70ns |

## 2. Knock / SCI 4N25 + 1N4733A + LM2917 Board (`knock_sci_4n25_1n4733a_lm2917_board`)

- **F-to-V**: `LM2907`/`LM2917` @ `66 Hz/V` (`12V` max); `Serial` `115200` baud; LCD `9600` baud.
- **Arduino Nano/Uno Pins**: `ignEvent=D2`, `knockDiscrete=D3`, `clearKnock=D4`, `LEDPin=D13`, `rpmPin=A0`, `knockPin=A1`, LCD `D5 (RX), D6 (TX)`.

| Qty | Part Number | Description |
| :---: | :--- | :--- |
| 10 | `273-4.7K-RC` | 1/2W 1% Metal Film Resistors 4.7Kohms 1% 50PPM |
| 10 | `CCF0710K0GKE36` | 1/4WATT Metal Film Resistors 1/4watt 10Kohms 2% Rated to 1/2watt |
| 10 | `MF1/4DC1001F` | 1/4Watt Metal Film Resistors 1K 1% 100PPM |
| 10 | `MF1/4LCT52R332J` | 1/4Watt Metal Film Resistors 1/4 WATT 3.3K OHM 5% |
| 10 | `MF1/4DC1003F` | 1/4Watt Metal Film Resistors 100K 1% 100PPM |
| 10 | `273-470-RC` | 1/2W 1% Metal Film Resistors 470ohms 1% 50PPM |
| 10 | `C330C105M5R5CA` | Conformally Coated Radial Ceramic Capacitors 50volts 1.0uF 20% X7R |
| 10 | `140-50Z5-203M-RC` | Ceramic Disc Capacitors 50V .02uF Z5U 20% Tol |
| 10 | `2N3904CTA` | Small Signal Transistors NPN Trans Gen Purp Collector=Middle Pin |
| 10 | `LM7812ACT` | Amplifiers REGULATOR 12V 1A 4% |
| 10 | `LM7805ACT` | Linear Regulators 3A 2% OUTPUT TOL 3-TERMINAL |
| 10 | `MF1/2DCT52R4701F` | 1/2Watt Metal Film Resistors 4.7Kohms 1% 100PPM |
| 10 | `140-50Q5-103Z-RC` | Ceramic Disc Capacitors 50V .01uF Z5V +80%/-20% Tol |
| 10 | `140-50P2-102K-RC` | Ceramic Disc Capacitors 50V 1000pF Y5P 10% Tol |
| 10 | `4N25` | Optocouplers Phototransistor Out |
| 10 | `1N4733A-TR` | Zener Diodes 5.1 Volt 1.0 Watt |
| 10 | `MF1/2DCT52R3900F` | 1/2Watt Metal Film Resistors 1/2 WATT 390 OHM 1% |
| 10 | `1N4001` | Rectifiers Vr/50V Io/1A T/R |
| 10 | `140-50N5-470J-TB-RC` | Class 1 Ceramic Disc Capacitors - Tape/Box 50V 47pF NPO 5% Tol |
| 10 | `5WF103MECAI` | Ceramic Disc Capacitors 25volts .01uF 20% |
| 10 | `CCF071K00JNR36` | 1/4WATT Metal Film Resistors 1/4watt 1Kohms 5% Rated to 1/2watt |
| 10 | `MF1/4LLT52R511J` | 1/4Watt Metal Film Resistors 510ohms 5% |
| 10 | `CMF603K9200FHEK` | 1/2WATT Metal Film Resistors 1/2watt 3.92Kohms 1% |
| 10 | `293-1.0-RC` | 1/2W 5% Carbon Film Resistors 1ohms 5% |
| 2 | `SN75176BD` | RS-485 Interface ICs Diff Bus Trans |
| 20 | `2N4401TFR` | Small Signal Transistors NPN Transistor General Purpose |

### Knock Board 34-Byte EEPROM Map

| Offset | Symbol | Type | Purpose |
| :---: | :--- | :--- | :--- |
| `0..15` | `rpmLookup[8]` | `uint16[8]` | RPM lookup breakpoints |
| `16..31` | `knockLookup[8]` | `uint16[8]` | Knock voltage threshold |
| `32` | `numCylinders` | `uint8` | Engine cylinder count |
| `33` | `alertLength` | `uint8` | Discrete hold duration (s) |

## 3. DIS Adaptor PIC + VB921 Waste-Spark (`dis_adaptor_pic_vb921_waste_spark`)

- **Base Timing & Sync**: `9 deg BTDC` (5-20-08 Non-NGC & NGC; 11-9-07 Non-NGC `10 deg BTDC`); `1-3-4-2` firing order (`1+4` & `2+3` paired coils); spark disabled when `lockcnt >= 3`.

| Qty | Part Number | Description |
| :---: | :--- | :--- |
| 10 | `STP10NK80ZFP` | MOSFET N-CH 800V 9A TO-220F |
| 60 | `MMBT2222AFSCT-ND` | TRANSISTOR GP NPN AMP SOT-23 |
| 60 | `MMBT4403FSCT-ND` | TRANSISTOR GP PNP AMP SOT-23 |
| 50 | `RHM1.0KERCT-ND` | RES 1.0K OHM 1/4W 5% 1206 SMD |
| 10 | `RHM220ERCT-ND` | RES 220 OHM 1/4W 5% 1206 SMD |
| 10 | `RHM1.0MERCT-ND` | RES 1.0M OHM 1/4W 5% 1206 SMD |
| 5 | `MC7805BDTRKGOSCT-ND` | IC REG VOLT POS 1A 5V DPAK |
| 5 | `PCE4074CT-ND` | CAP 22UF 50V ELECT TG SMD |
| 10 | `399-1249-1-ND` | CAP .10UF 50V CERAMIC X7R 1206 |
| 5 | `PIC12F629-E/SN-ND` | IC MCU CMOS 8BIT 1K FLASH 8-SOIC |

### DIS Adaptor DB15 Pinout (`5-20-08` / `11-9-07`)

| DB15 Pin | Signal (`5-20-08`) | `11-9-07` Delta |
| :---: | :--- | :--- |
| 1 | Simulated HEP out Gray (Turbo only) | Same |
| 2 | NC | +8 volt HEP supply (In) |
| 3 | Simulated HEP out Black (Turbo & TBI/Carb) | Same |
| 4 | +5 volt Sensor supply (Out) | Same |
| 5 | +Battery | Same |
| 6 | 1+4 Coil out | Same |
| 7 | 2+3 Coil out | Same |
| 8 | Ground | Same |
| 9 | HEP in Gray (Turbo only) or 2.4 Crank in | Same |
| 10 | HEP in Black or 2.4 Cam in | Same |
| 11 | Ground | Same |
| 12 | Coil - in (Do NOT leave coil connected) | Same |
| 13 | 1+4 Coil out | Same |
| 14 | 2+3 Coil out | Same |
| 15 | Ground | Same |
