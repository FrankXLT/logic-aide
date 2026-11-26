---
title: "Hardware Bench, Emulation & Tooling Overview"
slug: "hardware-bench-overview"
category: "hardware-bench"
tier: "Tier 2"
provenance: "Cleanroom engineering bench notes for offline ROM emulation, knock conditioning, and DIP EEPROM adapter BOMs"
ecu_scope: ["SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "stub"
last_verified: "2026-11-26"
tags: ["hardware-bench", "ostrich2", "knock-box", "eeprom", "dip-tooling", "deadbolt"]
---

# Hardware Bench, Emulation & Tooling Overview

This category covers physical bench-test fixtures, adapter bills of materials (BOMs), and offline validation tooling used alongside LogicAIDE's read-only simulation pipeline.

> [!CAUTION]
> **Hardware Deadbolt Reminder**: LogicAIDE agents and automated scripts are strictly read-only regarding physical programmers, emulators (`ostrich2`, `minipro`), and live USB-serial / COM port interfaces. Bench artifacts are staged offline under `staging/candidates/<build_id>/` with SHA-256 verification and safety-gate sign-off before any manual operator bench transfer.

## Planned Bench Modules

- **Ostrich 2.0 Emulator Read-Only Staging**:
  - `28-pin` / `32-pin` DIP ribbon orientation, socket seating height constraints inside SMEC/SBEC housings, and power-isolation sequencing.
  - Offline binary staging checklist (`$8000`–`$FFFF` checksum + `$FFC0`–`$FFFF` reset/interrupt vector verification).
- **Knock Box Conditioning**:
  - Piezoelectric knock sensor resonant frequency filtering (`2.2L` / `2.5L` vs. `2.4L` bore acoustics), shielded coax routing, and logic-level interface conditioning.
- **EEPROM DIP Tooling & Adapter BOMs**:
  - `27C256` / `28C256` / `27C512` `PLCC-32` and `DIP-28` socket/header pinouts, ZIF test-socket standoffs, and ESD-safe bench power supply current-limiting guidelines.

## Related Categories

- [Wiki Home](../Home.md)
- [ROM & SCI Protocol Overview](../rom-and-sci/rom-and-sci-overview.md)
- [Wiring & Diagnostics Overview](../wiring-and-diagnostics/wiring-and-diagnostics-overview.md)
