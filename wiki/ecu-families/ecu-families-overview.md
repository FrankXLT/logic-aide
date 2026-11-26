---
title: "Chrysler ECU Families Overview (LM, SMEC, SBEC & SBEC II)"
slug: "ecu-families-overview"
category: "ecu-families"
tier: "Tier 1"
provenance: "Cleanroom distillation of Chrysler factory powertrain diagnostic manuals and architectural hardware taxonomy"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "stub"
last_verified: "2026-11-26"
tags: ["ecu", "logic-module", "smec", "sbec", "6803", "68hc11"]
---

# Chrysler ECU Families Overview

This category tracks the architectural evolution across Chrysler 4-cylinder turbo and naturally aspirated engine controllers from early Logic Module (LM) units through single-board SBEC II controllers, organized along the Heritage Wave B–C ingestion roadmap.

## Controller Generation Matrix

| Controller Family | Model Years | MCU Core | Packaging | Wave Scope |
| :--- | :--- | :--- | :--- | :--- |
| **Logic Module (LM)** | `1984–1987` | `Motorola 6803` | Dual-module (LM + Power Module) | Wave B–C |
| **SMEC** | `1988–1989` | `Motorola 68HC11` | Dual-board potted/enclosed | Wave B |
| **SBEC** | `1990–1993` | `Motorola 68HC11` | Single-board 60-way | Wave B |
| **SBEC II** | `1994–1995` | `Motorola 68HC11` | Single-board 60-way (late bus) | Wave C |

## Wave B–C Documentation Roadmap

- **LM (`6803`) vs. SMEC (`68HC11`)**: Instruction set boundaries, dual-board power stage separation, and `7812.5` baud diagnostic transition.
- **SMEC vs. SBEC (`68HC11`)**: Calibration memory layout differences, fuel/spark table scaling, and harness bulkhead compatibility.
- **SBEC vs. SBEC II**: Late-model bus updates, idle air control revisions, and DOHC `2.0L ECC` / `2.4L EDZ` transition notes.

## Related Categories

- [Wiki Home](../Home.md)
- [ROM & SCI Protocol Overview](../rom-and-sci/rom-and-sci-overview.md)
- [Wiring & Diagnostics Overview](../wiring-and-diagnostics/wiring-and-diagnostics-overview.md)
