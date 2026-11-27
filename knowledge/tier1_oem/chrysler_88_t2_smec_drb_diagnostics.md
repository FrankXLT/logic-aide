> **Scope**: Cleanroom Tier 1 Digest of `88 T2.pdf` (`PR6.27` 68HC11 MTABLE), `87List.pdf` (`T1.17 M1`), DRB-II Error Registers (`0x001E`–`0x0026`), and SCI `0x10`–`0x17` Diagnostic Routines
> **Boundary**: Verified calibration structure, Chrysler engineering margin conversions, DRB-II Code `11–55` bit masks, and read-only SCI lookup semantics; excludes flash-write opcodes and verbatim copyrighted assembly dumps.

---
doc_id: "chrysler-88-t2-smec-drb-diagnostics"
title: "Chrysler 1988 Turbo II (PR6.27) & 1987 (T1.17 M1) MTABLEs, ErrorBitRegisters & DRB-II Diagnostics"
trust_tier: "Tier 1"
source_type: "OEM_CALIBRATION_AND_DRB_DIGEST"
source_citation: "88 T2.pdf (Chrysler 68HC11 Ver 0.04 MTABLE PR6.27 + Engineering Notes), 87List.pdf (6801/11 Ver 3.0MR T1.17 M1), drbcodes.xls (DRB Routine A4DF), T-SMEC_v19.asm & T-SBEC_v3_1.asm"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "88 T2.pdf p.1-4, 87List.pdf p.1, drbcodes.xls A4DF, T-SMEC_v19 L451-522 / L4223"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: ["$001E", "$001F", "$0020", "$0021", "$0022", "$0023", "$0024", "$0025", "$0026", "0x10", "0x11", "0x12", "0x13", "0x14", "0x16", "0x17", "0xF2", "0xF6", "0xFE"]
symbols_referenced: ["ErrorBitRegister0", "ErrorBitRegister1", "ErrorBitRegister2", "ErrorBitRegister3", "ErrorBitRegisterStored0", "ErrorBitRegisterStored1", "ErrorBitRegisterStored2", "ErrorBitRegisterStored3", "ErrorCodesToDash_Table", "CLDMAP", "CLDLMT", "HOTMAP", "WOTMAP", "TMPSWF", "GOVNER", "SPKLMT", "FUELTR", "TRANSM"]
pins_referenced: ["60-way Pin 25", "60-way Pin 45", "Pin 31", "Pin 51"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler `1988 Turbo II` (`PR6.27`) & `1987` (`T1.17 M1`) `MTABLE`s + `DRB-II` Diagnostics

## 1. Ground-Truth Identity of `88 T2.pdf` & `87List.pdf`

Primary inspection confirms both factory printouts are Chrysler **`MTABLE` assembler calibration listings**, rather than consumer service booklets:

- **`88 T2.pdf` (`52 pages`)**: Header reads `MOTOROLA 68HC11 ASSEMBLER VER 0.04 11-08-87`, `COPYRIGHT 1986 CHRYSLER CORPORATION`, **`'88 TURBO II MTABLE - PR6.27`**. Early data words (`ADDR 0000..0016`) record end-item part numbers (`ENDIT1`, `ENDITM`, `BITSUM`), Huntsville tester code words (`CODE1`, `CODE2`), fault masks (`FLTMSK` for `FLTOLD+0..+3`), `IDMSK`, `IPLMSK`, `FUELTR` (`$0311`, toggle `8.140 ms` fuel monitor conversion), `FLWMNC`, and `TRANSM` (`$FF = AUTO`, `$00 = MANUAL`), alongside spark/boost symbols `CLDMAP` (`C00B`), `CLDLMT` (`C01A`), `HOTMAP` (`C01B`), `WOTMAP` (`C02E`), `TMPSWF` (`C04B`), `GOVNER` (`C07C`), and `SPKLMT` (`C0A1`).
- **Engineer Margin Conversion Factors (`88 T2.pdf` Page 2)**:
  - `1 torr = 0.019337 psi = 1.3332 mbar`
  - `1 psi = 68.947 mbar = 51.715 torr`
  - `1 mbar = 0.75006 torr = 0.01450 psi`
- **`87List.pdf` (`49 pages`)**: Header reads `6801/11 ASSEMBLER VER 3.0MR`, `SEPTEMBER 03, 1986`, **`'87 TURBO II TABLES - T1.17 M1`** (`CHRYSLER TURBO - 1987, SPEED DENSITY MULTI POINT INJECTION SYSTEM, M1 CALIBRATION TABLES, MANUAL TRANSMISSION (M5) INTERCOOLER`, pathname `USER.TB087.T117.SOL.T2.MTABLE`).

---

## 2. `DRB-II` Serial Diagnostic Commands (`0x10`–`0x17`, Routine `A4DF`)

Operating at `7812.5` baud (`8N1`, echoed) prior to any `0x12` high-speed `62,500`-baud switch:

| Command (`Hex`) | DRB Routine Role | Sub-Commands / Behavior (`drbcodes.xls` & Firmware) |
| :---: | :--- | :--- |
| **`0x10`** | **Read Error Codes** | Iterates sub-indexes `#07`..`#00` across Current (`ErrorBitRegister0..3`) and Stored (`ErrorBitRegisterStored0..3`), returning `0xFE` when no fault bits are latched. |
| **`0x11`** | **Error Bits** | Transmits raw latched fault-bit bytes associated with active error bits. |
| **`0x12`** | **Set High-Speed Baud** | Echoes `0x12`, sets `FastSerial` (`0x80`), awaits `TC` (`0x40`), and switches UART to `62,500` baud for zero-echo `0xF2` (`SMEC`/`SBEC`) or `0xF6` (`SBEC` `FastDataOutputAddressTable`) RAM polling. |
| **`0x13`** | **Actuator Test Mode (`ATM`)** | Sub-tests `#00` Stop All, `#01` Ignition Coil, `#04`–`#06` / `#1D` Injectors `#1–#4`, `#07` AIS, `#08` Rad Fan, `#09` A/C Clutch, `#0A` ASD Relay, `#0B` Purge, `#0C` S/C Servo, `#0D` Alt Field, `#0E` Tach Out, `#11`/`#1B`/`#20` Wastegate Solenoid, `#12` Baro Solenoid. |
| **`0x14`** | **Diagnostic Data / Live RAM** | Sub-tests `#01`–`#29` (e.g., `#05` ECT, `#07` TPS, `#0A` BattV, `#0B` MAP, `#10`/`#11` RPM, `#15` Spark Adv, `#16–#19` Knock Retards `#1–#4`, `#1A` Target Boost, `#26` WGDC; `0xF5` clears/ends) plus `#40`–`#47` realtime stream. |
| **`0x15` / `0x16`** | **Memory / Constants (`ECU ID`)** | `#15`: send memory location (`3`-byte mode); `#16`: transmit `ECU ID` / constant block. |
| **`0x17` / `0x23`** | **Erase / Clear Memory** | `#17`: Clear DTCs (`0xE0` cleared, `0xFF` error); `#23 #01..#04`: clear EEPROM, RAM, or `MINTHR` (`0xF0` OK, `0x01` error). |

---

## 3. `SMEC` vs. `SBEC` `ErrorBitRegister` Address Map & `Code 11–55` Matrix

Notice the `+1` zero-page base shift between `SMEC` (`$001E`–`$0025`) and `SBEC` (`$001F`–`$0026`)! Always query `rom_manifest.json` (`lookup_sci`) rather than hardcoding zero-page offsets across controller families:

| Register Group | `SMEC` (`T-SMEC_v19`) | `SBEC` (`T-SBEC_v3_1`) | `bit7`..`bit0` Fault Codes (MSB -> LSB) |
| :--- | :---: | :---: | :--- |
| **`ErrorBitRegister0`** (Current) | **`$001E`** | **`$001F`** | `b7`: **44** `Lost FJ2`, `b6`: **45** `Nothing / Overboost`, `b5`: **46** `Batt High`, `b4`: **47** `Batt Low`, `b3`: **51** `Stuck Lean`, `b2`: **52** `Stuck Rich`, `b1`: **53** `PROM Checksum`, `b0`: **54** `Dist Sync` |
| **`ErrorBitRegister1`** (Current) | **`$001F`** | **`$0020`** | `b7`: **33** `A/C Relay`, `b6`: **34** `Speed Ctrl`, `b5`: **35** `Rad Fan`, `b4`: **36** `Wastegate Sol`, `b3`: **37** `Baro Sol`, `b2`: **41** `Charging`, `b1`: **42** `ASD Relay`, `b0`: **43** `Ign Coil` |
| **`ErrorBitRegister2`** (Current) | **`$0020`** | **`$0021`** | `b7`: **22** `ECT Sensor`, `b6`: **23** `Charge Temp`, `b5`: **24** `TPS`, `b4`: **25** `AIS Motor`, `b3`: **26** `Inj Current`, `b2`: **27** `Inj Driver`, `b1`: **31** `Purge Sol`, `b0`: **32** `EGR Sol` |
| **`ErrorBitRegister3`** (Current) | **`$0021`** | **`$0022`** | `b7`: **11** `Not Cranked`, `b6`: **12** `Mem Lost`, `b5`: **13** `MAP Pneumatic`, `b4`: **14** `MAP Electrical`, `b3`: **15** `VSS`, `b2`: **16** `Batt Sense`, `b1`: **17** `Too Cool`, `b0`: **21** `O2 Sensor` |
| **`ErrorBitRegisterStored0..3`** | **`$0022`–`$0025`** | **`$0023`–`$0026`** | Exact mirror of Current bits `0..3`, decremented by `ErrorCodeUpdateKeyOnCount` (`$0026` SMEC / `$0027` SBEC). |

- **`ErrorCodesToDash_Table` & `Code 55`**: Key-on `3x` ignition cycling shifts nibbles from `ErrorCodesToDash_Table` (`0x12, 0x54..0x11`...) to flash two-digit MIL sequences (`K3` lamp), terminating every fault printout with end-of-sequence **`Code 55`**.
- **Diagnostic Threshold Guards (`T-SBEC` `Error_Code` Group)**: `ERRRPM_RPMLimitforErrorCodeSetting` (`0x38`), `ERRMAP_MAPLimitforErrorCodeSetting` (`0x2B`), `ERRTMP_CoolantTempLimitforErrorCodeSetting` (`0xA0`), `THRLOW` (`0x08`), `THRHIG` (`0xE4`), and `MPTHR` (`0x68`) gate when rational-range sensor checks mature and assert `b46_MajorFault` / `b5d_MajorFault` (`Power Loss` / `Check Engine` lamp).
