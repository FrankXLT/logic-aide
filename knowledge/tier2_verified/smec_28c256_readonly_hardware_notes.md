> **Scope**: Cleanroom Tier 2 SMEC `28C256` Read-Only Hardware Modification & Safety Notes (`SMEC_88_89` ONLY)
> **Boundary**: Hardware wiring, BOM, mechanical clearance, and electrical safety warnings only; flash-write opcodes omitted per Hardware Deadbolt.

---
doc_id: "smec-28c256-readonly-hardware-notes"
title: "SMEC (1988–1989) 28C256-150I EEPROM Hardware Modification & Pin 11 Safety Notes"
trust_tier: "Tier 2"
source_type: "ECU_HARDWARE_MOD_DIGEST"
source_citation: "1988–1989 SMEC 28C256 Hardware Adapter & SECURITY.md Safety Reference (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "SMEC_EEPROM_vs2 Hardware BOM, Logic Wiring & Electrical Deadbolt Checks"
ecu_families: ["SMEC_DUAL_BOARD"]
ecu_scope: ["SMEC_88_89"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: ["28C256-150I", "74HC00", "1N914", "Z12", "C20", "D21", "D20"]
pins_referenced: ["60-way Pin 11", "60-way Pin 12", "60-way Pin 2", "60-way Pin 3", "60-way Pin 25", "60-way Pin 45", "PIA-1", "PIA-20", "PIA-21", "PIA-25"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# SMEC (`SMEC_88_89`) `28C256` Read-Only Hardware Modification Notes

> **STRICT SCOPE LOCK**: Applies **ONLY** to `SMEC_88_89` (`SMEC_DUAL_BOARD`) hardware. Never apply Pin 11 `+12V` download wiring to an `SBEC` (`SBEC_90_93`) harness.

## 1. Mandatory Pre-Power Safety Checks (`SECURITY.md`)

> **CRITICAL WARNING — Pin 11 SBEC `Z12` Ground-Short Hazard**
> On `SMEC_88_89` harnesses, 60-way **Pin 11 is unpopulated/open**. On `SBEC_90_93` harnesses, 60-way **Pin 11 can be tied directly to `Z12` power/logic ground** alongside Pin 12. Applying `+12V` (or `+21V`) to Pin 11 on an SBEC harness without lifting/isolating Pin 11 causes an **immediate direct ground short**, risking melted harness wiring, blown fuses, or destroyed ECU PCB traces.
> 1. Visually and by part number confirm `SMEC_88_89` dual-board hardware before connecting any Pin 11 switch.
> 2. **Verify Pin 11 continuity with a DMM ohmmeter** against chassis/power ground (Pin 12) before energizing `+12V`.
> 3. **Verify Pins 2, 3, 25, and 45 against the exact model-year FSM**: `Pin 2`/`Pin 3` swap `CTS` vs. `Knock` across model years, and early 1988–89 Turbo SMECs omit Pin 25 or assign Pin 45 to `C20` A/C clutch instead of SCI RX (`D20`).

## 2. Mechanical & Health Hazards

- **Toxic Potting Fumes**: Stock SMEC conformal potting compound emits hazardous fumes when heated with a soldering iron—use active ventilation and avoid inhalation.
- **Power Module Clearance**: Stacking an EEPROM socket + `28C256` + `74HC00` directly on top of the EEPROM interferes mechanically with the Power Module housing. Mount `74HC00` (`U1`) inverted on the **PIA** when using a socket, and keep the SPDT relay low-profile.

## 3. Hardware BOM (Zero Flash-Write Opcodes)

| Qty | Component | Specification / Role |
| :---: | :--- | :--- |
| 1 | Atmel `28C256-150I` | Industrial temp EEPROM, **must be `<= 200 ns`** access time; pins **1, 20, 22, 27** lifted/isolated from PCB pads |
| 1 | `74HC00` (`U1`) | Quad 2-input NAND gate for `OE*` / `CE*` / `WE*` gating |
| 1 | `0.1 uF` ceramic cap | Decoupling across `U1` `VCC` (`Pin 14`) and `GND` (`Pin 7`) |
| 1 | `12 VDC` SPDT relay | Miniature single-side-stable (e.g., `1A @ 30 VDC`, `515 ohm` coil) |
| 1 | `1N914` diode | Flyback suppression across relay coil (cathode = black band) |
| 1 | `1 kOhm` resistor | Pull-up from relay NC pin to EEPROM pin 28 (`VCC`) |
| 1 | 30 AWG Teflon wire + SPST switch | Wire-wrap interconnect; jumper across 22 V Zener on Pin 11 trace; fused/switched `+12V` to 60-way Pin 11 |

## 4. `74HC00` & Relay Pin Wiring Summary

| Source Signal / Pin | Destination | Notes |
| :--- | :--- | :--- |
| `PIA-20` (`VCC`) / `PIA-1` (`GND`) | `U1-14` / `U1-7` + `0.1 uF` cap | Logic gate power & decoupling |
| EEPROM PCB pad 27 | EEPROM pin 1 (`A14`) | Re-route lifted `A14` trace |
| `PIA-21` (`R/W`) | `U1-1, 2, 13` | `U1-3` connects to `U1-9` |
| `PIA-25` (`E` clock) | `U1-10, 12` | Gates active bus cycle (`E` high) |
| `U1-8` / Relay NC -> `1 kOhm` -> Pin 28 | Relay NO / Relay Common -> EEPROM pin 27 (`WE*`) | Write-protect pull-up when relay de-energized |
| `U1-11` | EEPROM pin 22 (`OE*`) | Output enable gating |
| Board `A15` via -> `U1-4, 5` | `U1-6` -> EEPROM pin 20 (`CE*`) | Bypasses stock mystery chip blocking `CS*` |
| Relay coil flyback (`1N914`) | Anode -> EEPROM pin 14 (`GND`); Cathode -> Pin 11 via | Short 22 V Zener on Pin 11 line; engage switch before key-on, turn OFF before driving |
