---
title: "ROM Architecture & SCI Protocol Overview"
slug: "rom-and-sci-overview"
category: "rom-and-sci"
tier: "Tier 1"
provenance: "Cleanroom distillation of Chrysler SCI diagnostic bus specifications and 68HC11 memory-map contracts"
ecu_scope: ["SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "stub"
last_verified: "2026-11-26"
tags: ["rom", "sci", "0xF2", "0xF4", "0xF6", "behavioral-twin", "deadbolt"]
---

# ROM Architecture & SCI Protocol Overview

This category documents Motorola `68HC11` ROM structure (`$8000`–`$FFFF`), `7812.5` to `62,500` baud Serial Communications Interface (SCI) polling, manifest-driven RAM watchlists, and deterministic Python Behavioral Twins.

> [!CAUTION]
> **Hardware Deadbolt**: LogicAIDE enforces strictly read-only SCI polling (`0xF2`, `0xF4`, `0xF6`) and deterministic offline `m68hc11` simulation. Live vehicle ECU write/flash sequences and modifications to stock ROM baselines (`rom/inputs/`, `rom/stock_baselines/`) are prohibited.

## Core Topics Planned

- **SCI Mode Polling (`0xF2` / `0xF4` / `0xF6`)**:
  - `0x12` handshake at `7812.5` baud transitioning to `62,500` baud zero-echo telemetry frames.
  - Strictly manifest-driven RAM offset resolution via `rom_manifest.json` (`ram_watchlist`), preventing cross-mask address assumptions between SMEC and SBEC binaries.
  - Dead-cell quarantine semantics (`12 ms` timeout, `null` vs. `0` preservation).
- **Behavioral Twins & Timing Safety**:
  - Bit-exact Python fixed-point models (`fuel_twin`, `spark_twin`, `boost_twin`) verified against `$8000`–`$FFFF` emulator replays.
  - Interrupt vector integrity (`$FFC0`–`$FFFF`) and `< 11.0 ms` (`<= 22,000` E-cycles @ `2.0 MHz`) main-loop ceiling proofs.

## Related Categories

- [Wiki Home](../Home.md)
- [ECU Families Overview](../ecu-families/ecu-families-overview.md)
- [Hardware Bench Overview](../hardware-bench/hardware-bench-overview.md)
