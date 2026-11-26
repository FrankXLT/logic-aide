---
title: "LogicAIDE Living Heritage Wiki"
slug: "home"
category: "portal"
tier: "Tier 1"
provenance: "Cleanroom distillation of Chrysler factory diagnostic supplements and verified technical archives"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "verified"
last_verified: "2026-11-26"
tags: ["heritage", "wiki", "portal", "mopar", "68hc11"]
---

# LogicAIDE Living Heritage Wiki

Welcome to the cleanroom Chrysler `2.2L` / `2.5L` / `2.0L` / `2.4L` powertrain knowledge base for **LogicAIDE**. Every article is attributed by trust tier (`Tier 1` factory manuals & verified harness pinouts, `Tier 2` engineering calculators & bench measurements, `Tier 3` community cross-checks) and normalized for deterministic RAG retrieval.

> [!CAUTION]
> **Hardware Deadbolt Boundary**: LogicAIDE is strictly **read-only** with respect to live vehicle ECU flashing and stock ROM baselines (`rom/inputs/`, `rom/stock_baselines/`). All emulator, SCI, and calibration workflows stage verified artifacts offline only.

## Knowledge Categories

| Category | Status | Trust Tier | Overview |
| :--- | :--- | :--- | :--- |
| **ECU Families** | `stub` | `Tier 1` | [ECU Families Overview](ecu-families/ecu-families-overview.md) |
| **ROM & SCI Protocol** | `stub` | `Tier 1` | [ROM & SCI Overview](rom-and-sci/rom-and-sci-overview.md) |
| **Turbo & Airflow** | `verified` | `Tier 2` | [Turbo & Airflow Overview](turbo-and-airflow/turbo-and-airflow-overview.md) |
| **Drivetrain & Swaps** | `stub` | `Tier 1` | [Drivetrain & Swaps Overview](drivetrain-and-swaps/drivetrain-and-swaps-overview.md) |
| **Wiring & Diagnostics** | `stub` | `Tier 1` | [Wiring & Diagnostics Overview](wiring-and-diagnostics/wiring-and-diagnostics-overview.md) |
| **Hardware Bench** | `stub` | `Tier 2` | [Hardware Bench Overview](hardware-bench/hardware-bench-overview.md) |

### Wave A Verified Reference Articles

- [OEM Chrysler Turbos](turbo-and-airflow/oem-chrysler-turbos.md) — Factory Garrett `T03`/`TB03`/`VNT25` and Mitsubishi `TE04H`/`TD04LR` baselines
- [TurboCalc Compressor Catalog](turbo-and-airflow/turbocalc-compressor-catalog.md) — `145` calibrated turbos, 4-point affine equations, and `NIST SP 811` conversions
- [Cylinder Head Flowbench Atlas](turbo-and-airflow/cylinder-head-flowbench-atlas.md) — `#445`, `#782`, `G-Head`, and `16V` DOHC flowbench curves

## Topic Map

```mermaid
flowchart TD
    home["LogicAIDE Living Heritage Wiki (Home)"]
    ecu["ECU Families (LM 6803 / SMEC 68HC11 / SBEC)"]
    rom["ROM & SCI Protocol (0xF2 / 0xF4 / 0xF6)"]
    turbo["Turbo & Airflow (TurboCalc v5 / G-Head)"]
    drive["Drivetrain & Swaps (4.00 in Bore-Center Matrix)"]
    wiring["Wiring & Diagnostics (52/60-Way & Pin 11)"]
    bench["Hardware Bench (Ostrich 2.0 / Knock / DIP)"]

    home --> ecu
    home --> rom
    home --> turbo
    home --> drive
    home --> wiring
    home --> bench
    ecu --> rom
    ecu --> wiring
    turbo --> drive
    rom --> bench
```

## Navigation Chrome

- Sidebar index: [`_Sidebar.md`](_Sidebar.md)
- Trust-tier & safety footer: [`_Footer.md`](_Footer.md)
