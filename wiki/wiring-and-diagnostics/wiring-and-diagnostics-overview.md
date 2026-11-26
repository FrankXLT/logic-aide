---
title: "Wiring Bulkheads & Diagnostics Overview"
slug: "wiring-and-diagnostics-overview"
category: "wiring-and-diagnostics"
tier: "Tier 1"
provenance: "Cleanroom distillation of Chrysler Section 8W powertrain wiring diagrams and pinout verification procedures"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "stub"
last_verified: "2026-11-26"
tags: ["wiring", "52-way", "60-way", "pin-11", "diagnostics", "ohmmeter"]
---

# Wiring Bulkheads & Diagnostics Overview

This category documents verified Chrysler controller bulkhead connectors (`52-way` Logic Module, `60-way` SMEC/SBEC, and `14-way`/`60-way` Power Module interfaces), sensor ground topology, and bench-before-plug diagnostic checks.

> [!WARNING]
> **Mandatory Pin 11 Ohmmeter Isolation Check**: Before installing an `SMEC` into a `1990–1993` TBI `SBEC` harness—or swapping harnesses across `1988–1993` model years—**always isolate and verify `Pin 11` with an ohmmeter** (`ECU unplugged`, battery disconnected). `Pin 11` assignment diverges between SMEC and certain `1990–1993` `60-way` harnesses; unverified cross-plugging risks direct power/ground faults into controller drivers.

## Planned Reference Sections

- **`52-Way` Logic Module & Power Module (`1984–1987`)**: Dual-connector harness routing, ASD relay sense, and Hall-effect pickup grounds.
- **`60-Way` SMEC vs. SBEC (`1988–1993`)**: Cavity-by-cavity pinout cross-reference, `SCI` `RX`/`TX` (`62.5k` bus) cavities, and `Pin 11` / `Pin 12` / `Pin 22` / `Pin 25` / `Pin 45` isolation checks.
- **Sensor Return & Shielding**: `5V` sensor reference integrity, MAP/TPS/CTS ground splices, and noise-floor isolation for knock and crank/cam sensors.

## Related Categories

- [Wiki Home](../Home.md)
- [ECU Families Overview](../ecu-families/ecu-families-overview.md)
- [Hardware Bench Overview](../hardware-bench/hardware-bench-overview.md)
