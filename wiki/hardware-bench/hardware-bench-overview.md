---
title: "Hardware Bench, Emulation & Tooling Overview"
slug: "hardware-bench-overview"
category: "hardware-bench"
tier: "Tier 2"
provenance: "Cleanroom engineering bench notes for offline ROM emulation, knock conditioning, and DIP EEPROM adapter BOMs"
ecu_scope: ["SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "verified"
last_verified: "2026-11-26"
tags: ["hardware-bench", "ostrich2", "knock-box", "eeprom", "dip-tooling", "deadbolt"]
---

# Hardware Bench, Emulation & Tooling Overview

This category covers physical bench-test fixtures, adapter bills of materials (BOMs), and offline validation tooling used alongside LogicAIDE's read-only simulation pipeline.

> [!CAUTION]
> **Hardware Deadbolt & Pin 11 Isolation Reminder**: LogicAIDE agents and automated scripts are strictly read-only regarding physical programmers, emulators (`ostrich2`, `minipro`), and live USB-serial / COM port interfaces. Bench artifacts are staged offline under `staging/candidates/<build_id>/` with SHA-256 verification and safety-gate sign-off before any manual operator bench transfer. Before probing or connecting any `60-way` harness, perform the ignition-`OFF` DMM isolation check: `Pin 11` to `Pin 12` and chassis ground MUST read `OL` (`> 10 MOhm`) to rule out an `SBEC` `Z12` direct ground short (see [Pin 11 Safety Matrix](../ecu-families/pin-11-safety-matrix.md)).

## Verified Bench Modules

- **SMEC Socket & Ostrich 2.0 Read-Only Staging**:
  - `SST27SF020` bank-switched `28-pin` vs. `32-pin` adapter alignment, `Atmel 28C256-150I` + `74HC00` control-signal wiring, Power Module mezzanine clearance, and potting fume ventilation.
  - Offline binary staging checklist (`$8000`–`$FFFF` checksum + `$FFC0`–`$FFFF` reset/interrupt vector verification).
- **SCI, Knock Conditioning & Waste-Spark DIS Hardware**:
  - Galvanic `4N25` optical isolation, `1N4733A-TR` `5.1 V` Zener clamping, `LM2907` tachometer `F-to-V` conditioning, and Arduino `Knock_Board` telemetry math.
  - `PIC12F629` / `PIC16F690` Waste-Spark DIS Adaptor (`1-3-4-2` coil-pair state machine and `VB921` dwell/spark hardware notes).

## Verified Wave C Reference Articles

- [SMEC 28C256 & Bank-Switched Socket Read-Only Hardware Notes](smec-28c256-read-only-socket-notes.md) — `SECURITY.md` Pin 11 / variant pin pre-connect gates, `SST27SF020` bank-switch and Ostrich 2.0 alignment, and `28C256-150I` + `74HC00` adapter BOMs.
- [SCI, Knock Box Conditioning & Waste-Spark DIS Adaptor Hardware Notes](knock-and-dis-conditioning.md) — `4N25` / `1N4733A-TR` / `LM2907` SCI & Knock Conditioning BOMs, `Knock_Board` firmware math, and `PIC12F629` / `PIC16F690` Waste-Spark DIS Adaptor notes.

## Related Categories

- [Wiki Home](../Home.md)
- [ROM & SCI Protocol Overview](../rom-and-sci/rom-and-sci-overview.md)
- [Wiring & Diagnostics Overview](../wiring-and-diagnostics/wiring-and-diagnostics-overview.md)
