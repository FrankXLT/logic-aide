---
title: "Drivetrain & Engine Swap Compatibility Overview"
slug: "drivetrain-and-swaps-overview"
category: "drivetrain-and-swaps"
tier: "Tier 1"
provenance: "Cleanroom distillation of Chrysler 4.00 in bore-center block/head geometry and transaxle bellhousing specifications"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "stub"
last_verified: "2026-11-26"
tags: ["drivetrain", "swaps", "4.00-bore-center", "a604", "a413", "nvg555"]
---

# Drivetrain & Engine Swap Compatibility Overview

Chrysler `2.2L`, `2.5L`, `2.0L ECC`/`ECB`, and `2.4L EDZ`/SRT-4 blocks share **`4.00 in` (`101.6 mm`) bore-center spacing** (`87.5 mm` standard bore). LogicAIDE enforces a strict 3-tier mechanical compatibility guard in `profiles/schema/vehicle_build.schema.json` before compiling any vehicle profile.

## 3-Tier Mopar Compatibility Matrix

| Tier Classification | Block & Head Combinations | Compiler Behavior |
| :--- | :--- | :--- |
| **`BOLT_ON_NATIVE`** | `2.2L` / `2.5L` K-block with `8V #445`, `8V #782`, `1991–1993` Lotus `16V` (`Turbo III`), or `1988–1991` Maserati `16V` DOHC (`Chrysler TC`). | Compiles cleanly with native oil/coolant passages. |
| **`HYBRID_MACHINING_REQUIRED`** | `2.2L` / `2.5L` K-block with `Neon ECC 2.0L DOHC` or `2.4L EDZ`/SRT-4 `16V` DOHC head, or full `2.4L EDZ` swap into SMEC/SBEC chassis. | Emits mandatory 7-item amber Swap Advisory Checklist. |
| **`INCOMPATIBLE`** | `8V #445`/`#782` SOHC or K-car `Lotus`/`Maserati 16V` heads paired onto a `2.4L EDZ` block. | Hard compilation block (`INCOMPATIBLE`). |

## Transaxle & Torque Capacity Notes (Planned)

- **`A413` (`31TH`) 3-Speed Automatic**: Mechanical governor/kickdown linkage, torque converter stall matching, and flexplate spacing.
- **`A604` (`41TE`) Ultradrive 4-Speed**: Electronic transaxle controller (`EATX`) vs. SMEC/SBEC communication bus and clutch pack torque limits.
- **`NVG555` / `A520` / `A555` / `A568` Manual**: Bellhousing bolt patterns (`K-block` vs. `EDZ`), Getrag gearset strength, and axle spline interfaces.

## Related Categories

- [Wiki Home](../Home.md)
- [Turbo & Airflow Overview](../turbo-and-airflow/turbo-and-airflow-overview.md)
- [Wiring & Diagnostics Overview](../wiring-and-diagnostics/wiring-and-diagnostics-overview.md)
