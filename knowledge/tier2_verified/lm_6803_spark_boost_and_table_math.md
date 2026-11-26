> **Scope**: Cleanroom Tier 2 1987 6803 Logic Module Spark Advance, Boost Control & Table Point Math (`LM_PM_84_87` / `SMEC_88_89` cross-refs)
> **Boundary**: Distilled disassembly math, ROM/RAM addresses, and binary table geometry only; contributor identities and private paths omitted.

---
doc_id: "lm-6803-spark-boost-and-table-math"
title: "1987 6803 Logic Module Spark Advance ($EFD2-$F0AE), Boost ($F269-$F2C9) & Table Extension Math"
trust_tier: "Tier 2"
source_type: "DISASSEMBLY_MATH_DIGEST"
source_citation: "1987 Turbo II 6803 Disassembly & Calibration Table Layout Notes (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "CalcAdvance ($EF90-$F0AE), CalcDesiredBoost ($F269-$F2C9), Adding Table Points"
ecu_families: ["LM_PM_84_87", "SMEC_DUAL_BOARD"]
ecu_scope: ["LM_PM_84_87", "SMEC_88_89"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: ["$0072", "$0073", "$0081", "$008F", "$0094", "$0096", "$0098", "$009A", "$00A7", "$00A9", "$00BA", "$00C6", "$00CA", "$2004", "$8000", "$8010", "$8023", "$C00B", "$C01B", "$C02E", "$C04B", "$C04C", "$C057", "$C062", "$C06E", "$C07C", "$C0A1", "$C0A8", "$C1DC", "$C492", "$C4A1", "$C4AC", "$C4BB", "$C4C6", "$C4D1", "$C4D8", "$D58D", "$EDD4", "$EF90", "$EFD2", "$F0AE", "$F269", "$F2C0", "$F2C9"]
symbols_referenced: ["CalcAdvance", "CalcDesiredBoost", "LookupTable", "AdvanceFromMapCold", "AdvanceFromMapWarmPart", "AdvanceFromMapWarmFull", "AdvanceFromRPM1", "Table069", "Table070", "Table071", "Table072", "Table073", "Table074"]
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# 1987 6803 Logic Module Spark, Boost & Table Extension Math

## 1. Ignition Advance Routine (`CalcAdvance`, `$EF90`–`$F0AE`, Spark Core `$EFD2`–`$F0AE`)

### Key RAM Variables & Calibration Tables

| Symbol / Address | Role / Description |
| :--- | :--- |
| `$0072` (`L0072`) | Final encoded spark advance output |
| `$0073` (`BarometricPressure`) | Ambient barometric pressure reading |
| `$0081` (`L0081`) | Status flags (`bit 7` = WOT/mode; `bit 0` = Park/Neutral via `lsrb`) |
| `$008F` (`EngineRPMhi`) | 16-bit RPM word (`$008F:$0090`); shifted `EngineRPMhi * 2` for table lookups |
| `$0094` / `$0098` / `$0096` / `$009A` | `MAPpressure`, `TPS`, `LowestEverTPS`, `CoolantTemp` |
| `$00B6`–`$00BA` / `$00CA` / `$2004` | `TempVar00..04`, auxiliary load/timer `L00CA`, status `L2004` (`$08` = moving) |
| `$C00B` (`TableS001`) / `$C01A` | Cold MAP advance table (clamped to max `$C01A`) |
| `$C01B` (`MapAdvanceFULL`) / `$C02E` (`MAPAdvancePart`) | Warm full-throttle & warm part-throttle MAP advance tables |
| `$C049` / `$C04A` / `$C04B` (`TemperatureSwitch`) | Excess-MAP multiplier above `MaxMAP` (`$C1DC`), sign flag, cold/warm switch (`$A6`) |
| `$C04C` / `$C057` / `$C062` / `$C06D` | `Table004(RPM*2)`, `Table005(Baro)`, `Table006(RPM*2)` altitude retard (floor `$06`) |
| `$C06E` (`Table007`) | TPS retard (`TPS - LowestEverTPS`) active when `MAP > $93` (~5 psi), `RPM > $27`, `LowestEverTPS > $50` |
| `$C07C` (`Table008`) / `$C0A1` (`Table012`) / `$C0A8` (`Table013`) | Mechanical RPM advance, max advance ceiling vs. RPM, cold low-RPM coolant adder |
| `$D58D` (`LookupTable`) | Shared 2D interpolated table lookup subroutine |

### Control Flow & Final Encoding (`$F0A1`–`$F0AC`)

1. Clear `TempVar00`/`TempVar01` (`std $B6`) and branch on `CoolantTemp` vs. `TemperatureSwitch` (`$C04B = $A6`).
2. **Cold (`< $A6`)**: Lookup `$C00B(MAP)`, clamp to `$C01A`, add `$C0A8(CoolantTemp)` if `RPM <= $C0AF` and `L2004 & $08 == 0`, jump to `$F05A`.
3. **Warm (`>= $A6`)**: Lookup `$C01B` (`MapAdvanceFULL`), optionally blend/clamp with `$C02E` (`MAPAdvancePart`) `+/-` scaled excess MAP `(L00CA + MAPpressure - MaxMAP) * $C049`.
4. **Altitude-Compensated RPM Advance**: `Table004(RPM*2) - (Table006(RPM*2) * Table005(Baro))`, floored at `$C06D` (`$06`); take `min(TempVar00, result)`.
5. Add `Table008(RPM*2)` (clamp `>= 0`), subtract `Table007(TPS - LowestEverTPS)` when thresholds pass (clamp `>= 0`), and clamp `<= Table012(RPM*2)`.
6. **Final Encoding**: `B = (-B + $A6) & $FF`, `D = B * $5B`, `D = D * 2`, round up `A` if bit 7 of `B` is set, store `A` into `$0072`.

## 2. Boost Limit Routine (`CalcDesiredBoost`, `$F269`–`$F2C9`)

| 87 LM Address | 89 SMEC Ref | Symbol / Input | Function |
| :---: | :---: | :--- | :--- |
| `$C492` | `$FA52` | `Table069` (`EngineRPMhi << 1`) | Initial max relative boost target vs. RPM |
| `$C4A1` | `$C558` | `Table070` (`$00A9`) | Min clamp vs. `EstimateOfCylinderTemp_Min00` |
| `$C4AC` | `$C536` | `Table071` (`$009A`) | Min clamp vs. `CoolantTemp` |
| `$C4BB` | `$C572` | `Table072` (`$005D`) | Min clamp vs. `ChargeTempVolts` |
| `$C4D8` | — | `LC4D8` (`$3F`, ~7 psi) | Park/Neutral cap when `L0081` bit 0 == 0 |
| `$C4D1` | `$C597` | `Table074` (`$0069 << 3`) | Vehicle speed cap when `L2004 & $08 != 0` |
| `$C4C6` | `$C58C` | `Table073` (`$00C6`) | Subtract boost reduction vs. Over-5-PSI timer |

- Sequential min clamps run via `$F2C0` (`LookupAndPutSmallestAnswerInTempVar04` into `$00BA`).
- Finally, add `BarometricPressure` (`$0073`) and saturate at `$FF` to emit desired absolute MAP target (`TempVar3` at `$00A7`). Higher altitude reduces absolute MAP target accordingly.

## 3. Adding Table Points to 1987 Calibration Bins

- **ROM Offset Rule**: Calibration ROM is mapped at `$8000` base (`file_offset = cpu_addr - $8000`; e.g., `$8010` -> `$0010`).
- **4-Byte Table Layout**:
  - Byte `0`: Point count (e.g., `0x03` -> `0x04`).
  - Each point entry is **4 bytes**: `[X_compare, Y_return, slope_hi, slope_lo]`.
  - When appending a higher-X point (e.g., extending `AdvanceFromMapCold` past ~15 psi to `X = 0xA0`, `Y = 0xF6` for ~29 psi): update the prior final point's `0000` slope to the new segment slope, append `[new_X, new_Y, 0x00, 0x00]`, and increment Byte `0`.
- **Padding & Relocation Constraint**:
  - Unused calibration space is `0xFF`-padded; adding 1 point requires at least **4 unused `0xFF` bytes** before the next object (e.g., `$8023 = 0x0E` after `AdvanceFromMapCold`).
  - `AdvanceFromMapWarmFull` sits directly before `AdvanceFromRPM1` with zero `0xFF` slack. Extending `AdvanceFromMapWarmFull` requires relocating `AdvanceFromRPM1` to free ROM (example: `$EDD4`, file offset `$6DD4`) and updating its `ldx #...` pointer in code.
