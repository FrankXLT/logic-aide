> **Scope**: Cleanroom Tier 2 Digest of `ChEM2` Calibration Concepts & `SMEC` Add-a-Pin Connector Modification (`SMEC_88_89` Hardware Lock)
> **Boundary**: Functional calibration architecture, `3-bar MAP` table scaling, table hierarchy, and strictly `SMEC_88_89`-locked Cavity 11 pin insertion with mandatory `SECURITY.md` Pin 11 `Z12` ground-short check; zero flash-write opcodes.

---
doc_id: "chem2-and-smec-add-a-pin-notes"
title: "ChEM2 Calibration Table Architecture & SMEC 60-Way Cavity 11 Add-a-Pin Safety Guide"
trust_tier: "Tier 2"
source_type: "ECU_CALIBRATION_AND_CONNECTOR_DIGEST"
source_citation: "ChEM2 User Documentation (2.0.0.7, MoparChem) & SMEC 60-Way Extra-Pin Install Notes (SMEC_88_89 Locked + SECURITY.md Gate)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "ChEM2.pdf p.1-22, flashable smec Add a pin.pdf p.1-4"
ecu_families: ["SMEC_DUAL_BOARD"]
ecu_scope: ["SMEC_88_89"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: ["CLDMAP", "HOTMAP", "WOTMAP", "TMPSWP", "MAPIDL", "MAPTBL", "WOTTBL", "PEFTBL", "AutoCal", "MAPLIM", "Z12", "D20", "D21", "C20"]
pins_referenced: ["60-way Pin 11", "60-way Pin 12", "60-way Pin 2", "60-way Pin 3", "60-way Pin 25", "60-way Pin 45", "Cavity 11", "Cavity 42"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# `ChEM2` Calibration Functional Map & `SMEC` Add-a-Pin (`SMEC_88_89` Only)

## 1. Mandatory Hardware & Safety Lock (`SECURITY.md`)

> [!CAUTION]
> **STRICT `SMEC_88_89` LOCK — Pin 11 `SBEC` `Z12` Ground-Short Isolation Gate**:
> 1. **Physical Add-a-Pin to `60-Way` `Cavity 11` applies ONLY to verified `SMEC_88_89` dual-board controllers.** On `SBEC_90_93` / `SBEC-II` harnesses, `60-way` **`Pin 11` can be bonded directly to `Z12` power/logic ground alongside `Pin 12`**. Applying `+12 V` / VPP to an unverified `Pin 11` causes a direct dead short.
> 2. **Mandatory DMM Pre-Check**: Always verify ECU hardware part number as `SMEC_88_89` and **measure resistance with a DMM ohmmeter between `Pin 11` and `Pin 12` (chassis/power ground)** before inserting or energizing any `Pin 11` terminal.
> 3. **Verify `Pins 2, 3, 25, and 45`**: Model-year `SMEC`/`SBEC` harnesses can swap `CTS`/`Knock` (`Pins 2/3`), omit `Pin 25` SCI TX, or assign `Pin 45` to `C20` A/C clutch instead of `D20` SCI RX.
> 4. **Zero Flash-Write Opcodes**: This digest documents connector assembly and calibration math structure only; no hardware flash-write protocols are included.

---

## 2. `60-Way` Connector Extra-Pin Installation Notes (`flashable smec Add a pin.pdf`)

When populating an unused factory cavity (`Cavity 11` for `SMEC` hardware pinout vs. `Cavity 42` shown in photo guides — never confuse the two):
- **Retainer Extraction**: Carefully pry the red secondary locking insert outward a little at a time from the mating face until it clears the gray housing.
- **Unpopulated Seal Boss Drilling**: From the wire-entry rear side of the gray `60-way` shell, slowly drill out the blank plastic plug cleanly using an appropriately sized bit without damaging internal latch tangs, then push any remaining plastic slug out through the mating face.
- **Donor Terminal Seating**: Harvest a matching Chrysler `60-way` crimped terminal + wire lead from a donor harness, verify **`Cavity 11`** position (count carefully from the notched end; guides illustrating `Cavity 42` use that cavity solely as a visual drilling example), insert from the rear until the locking tang clicks positively, and re-seat the red locking insert.

---

## 3. `ChEM2` Speed-Density Calibration Hierarchy (`1985–1993` Chrysler Controllers)

While hardware `Pin 11` notes lock strictly to `SMEC_88_89`, `ChEM2` software workflows model Chrysler speed-density baselines across `1985–1993` `LM`, `SMEC`, and `SBEC` (`2.2L` / `2.5L` native plus adapted `2.0L`/`2.4L` builds):

| Functional Subsystem | Key Tables / Constants | Cleanroom Calibration Mechanics |
| :--- | :--- | :--- |
| **3-Bar MAP Rescaling** | `2-Bar` (`~14.5 psi`) -> `3-Bar` (`~29 psi`), `MP1`–`MP5` | Expands MAP axis breakpoints across boost above `14.5 psig`; requires matching injector/load scaling and custom source assembly flag selection (`2-Bar` vs. `3-Bar`). |
| **Spark Advance Regime Switch** | `TMPSWP`, `CLDMAP`, `HOTMAP`, `WOTMAP` | Below coolant threshold `TMPSWP`: uses `CLDMAP` (retaining higher cold advance -> reduce high-boost cells carefully to avoid cold-boost knock). Above `TMPSWP`: `HOTMAP` governs idle/part-throttle and `WOTMAP` governs wide-open throttle (adding vacuum advance, pulling timing under boost). |
| **Running Fuel & Volumetric Efficiency** | `MAPIDL`, `MAPTBL`, `WOTTBL`, `PEFTBL` | `MAPIDL` governs idle/decel (not used in boost); `MAPTBL` governs part-throttle; `WOTTBL` sets WOT fueling envelope modulated dynamically across RPM by **`PEFTBL`** (**Pumping Efficiency Table**) to match cam/head VE changes (`3500–5000+ RPM`). |
| **Adaptive `AutoCal` & `O2` Loop** | `AutoCal` (`R1`, `MP1–MP5`, `MAPLIM`), Secondary Ramps (`3x3` RPM x MAP) | Closed-loop `O2` updates long-term `AutoCal` cells gated below `MAPLIM` and max RPM limits; secondary ramp matrices shape tip-in / cruise closed-loop authority. |
| **Transient & Cold Start** | Transient `TPS` / `MAP` accel enrichment; Baro Read | Separate `TPS` and `MAP` delta-triggered enrichment events handle rapid throttle opening; periodic ambient `Baro Read` updates altitude baseline. |
| **Engine Build Guidelines** | Injector flow rating, source control | Record table edits back into commented `.asm`/source baselines (e.g., bringing `1989` cruise/timing refinements to `1987 LM` builds) before socket/ROMULATOR bench simulation. |
