---
title: "Wiring Bulkheads & Diagnostics Overview"
slug: "wiring-and-diagnostics-overview"
category: "wiring-and-diagnostics"
tier: "Tier 1"
provenance: "Cleanroom distillation of Chrysler Section 8W powertrain wiring diagrams and pinout verification procedures"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "verified"
last_verified: "2026-11-26"
tags: ["wiring", "50-way", "60-way", "pin-11", "diagnostics", "drb-ii", "ohmmeter"]
---

# Wiring Bulkheads & Diagnostics Overview

This portal documents verified Chrysler controller bulkhead connectors (`40-way`/`50-way` firewall disconnects, `50-way` Red/Blue Logic Module, `60-way` + `14-way` SMEC, and `60-way` unified SBEC interfaces), `DRB-II` `ErrorBitRegister` fault isolation, and pre-connect DMM checks.

> [!WARNING]
> **Mandatory Pin 11 Ohmmeter Isolation Check**: Before installing an `SMEC` into a `1990–1993` TBI `SBEC` harness—or swapping harnesses across `1988–1993` model years—**always isolate and verify `Pin 11` with an ohmmeter** (`ECU unplugged`, battery disconnected). `Pin 11` is blank/programmable on `SMEC_88_89` but grounded (`Z12`) alongside `Pin 12` on `SBEC_90_93`; unverified cross-plugging risks direct power/ground shorts.

## Verified Reference Articles

- [`60-Way SMEC & SBEC Connector Atlas (1988–1993)`](60-way-connector-atlas.md) — `1988 P-Body SMEC` (`60-Way` `1-xx` + `14-Way` `2-xx`) vs. `1990–1993 SBEC/SBEC-II`, highlighting `Pin 2/3`, `Pin 11/12`, `Pin 25/51`, and `Pin 45` (`C20` vs. `D20`).
- [`50-Way Bulkhead & 1984 40-Way Firewall Disconnect Atlas`](50-way-bulkhead-atlas.md) — Complete `1–50` cavity cross-reference (`87 G`, `88 K/P/D`, `89 G`, `89 S-Body Mini` `CCD Bus`) vs. `1984 L-Body` `40-Way` (`8-612`).
- [`Chrysler DRB-II Error Registers & Fault Isolation Trees (Codes 11–55)`](drb-ii-fault-trees.md) — Complete `Code 11`–`Code 55` map, `SMEC` (`$001E`–`$0025`) vs. `SBEC` (`$001F`–`$0026`) `+1` zero-page shift, `0x10`–`0x17` `DRB` subcommands, and Mermaid isolation tree.

## Related Categories

- [Wiki Home](../Home.md)
- [Pin 11 Safety Matrix](../ecu-families/pin-11-safety-matrix.md)
- [ECU Families Overview](../ecu-families/ecu-families-overview.md)
- [ROM & SCI Protocol Overview](../rom-and-sci/rom-and-sci-overview.md)
- [Hardware Bench Overview](../hardware-bench/hardware-bench-overview.md)

