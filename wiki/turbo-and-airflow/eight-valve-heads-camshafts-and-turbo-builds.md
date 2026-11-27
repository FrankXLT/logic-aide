---
title: "8V Cylinder Heads, Camshafts & 2.2L/2.5L Turbo Builds"
slug: "eight-valve-heads-camshafts-and-turbo-builds"
category: "turbo-and-airflow"
tier: "Tier 2"
provenance: "Cleanroom distillation of TheDodgeGarage Cylinder Head & Camshaft Guides, Gus Mahon MeanMini/Acclaim build progressions, and Allpar (pre-2017 archive) 2.2L/2.5L engine specifications"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K"]
status: "verified"
last_verified: "2026-11-27"
tags: ["8v-heads", "445-head", "782-head", "camshafts", "roller-cam", "head-gaskets", "gus-mahon", "2.2l", "2.5l"]
---

# `8V` Cylinder Heads, Camshafts & `2.2L` / `2.5L` Turbo Builds

While `16V` `DOHC` conversions lift peak naturally aspirated flow, Chrysler's `8V` `SOHC` `2.2L` (`87.5 x 92.0 mm`, `2213 cc`, `1-3-4-2` firing order) and `2.5L` platforms routinely run `12-second` and `11-second` quarter-mile passes when cylinder head castings, valve lengths, cam-cap oiling, and boost-friendly roller profiles are matched properly (`TheDodgeGarage`, `Allpar (pre-2017 archive)`, `Gus Mahon`).

## `8V` Cylinder Head Castings & Compatibility Guards

| Casting & Years | Chamber Architecture | Applications (`TheDodgeGarage`) | Flow & Build Tradeoffs |
| :--- | :--- | :--- | :--- |
| **`4105445` (`#445`) / `4323287` (`#287`)** | Open "Bathtub" (`+6 cc` larger) | `1983–1985` `2.2L`; `1986–1994` `2.2L` carb/TBI | Flows `~10%` more air than stock `#782`; favors top-end power in lighter chassis (`G-Head` porting base). |
| **`4105782` (`#782`)** | "Swirl" / Fastburn (`-6 cc` smaller) | `1986+` `2.2L` fastburn, `1986–1990` `T-I`/`T-II`, `1986–1994` `2.5L` TBI, `1989–1992` `2.5L` Turbo | Higher port velocity and crisper off-boost throttle response for heavier cars; `12.6 @ 110 mph` (`~315 hp` @ `30 psi`) proven with backcut valves. |

### Five Mandatory `8V` Head & Valvetrain Checks

1. **Valve Stem Length Mismatch (`112 mm` vs. `114 mm`)**: `1986+` swirl/fastburn valves measure **`114 mm`** overall height vs. **`112 mm`** on `1982–1985` bathtub heads. Installing `114 mm` swirl valves in an early bathtub head holds the valves open (`0` compression).
2. **Turbo Exhaust Valves & `TS` Chamber Stamp**: Intake valves interchange between turbo and N/A heads, but **turbo exhaust valves** use superior high-temp alloy and stiffer springs (non-turbo exhaust valves fail in months under boost). Turbo `#782` castings carry **`TS`** (tapered shroud) stamped at `#4` end to suppress knock sensitivity.
3. **Reject `OSJ` Oversize-Journal Heads**: Heads stamped **`OSJ`** on the driver's end were factory-salvaged with oversize cam journals; installing a standard camshaft leaks oil pressure internally so lifters fail to pump up.
4. **Late-`1989` Cam Cap Oiling Cutout**: Chrysler phased in **roller cams** in `1988` (`75%` less idle friction, `35%` less cruise friction) and eliminated oil-squirter passages in cam caps `2`, `3`, and `4` in **late `1989`**. Never install a pre-`1989` **slider cam** into a late `1989+` head without machining cam-cap oiling slots, and **never mix slider and roller followers**.
5. **Cross-Drilled Cooling, Head Gaskets & Bolts**: `T-II`, `2.5L` Turbo, and `1991+` TBI castings are cross-drilled for coolant flow (safe to mix with non-cross-drilled blocks using `T-II` gaskets). Prefer Mopar gaskets (**`P4452005`** Carb/`T-I`, **`P4452006`** `T-II`/`2.5T`, **`P4452007`** O-ringed, **`P4452008`** O-ringed + cross-drilled). Torque `1983–1985` `10 mm` bolts to `35-45-45 + 1/4 turn` (`lb-ft`) and `1986–1994` `11 mm` bolts to `45-65-65 + 1/4 turn`. Milling `0.020–0.030 in` retards cam timing `2°`; backcutting valves (`20–21°` intake, `31–32°` exhaust) improves low-lift flow.

## `8V` Camshaft Matrix & Boost Dyno Findings (`TheDodgeGarage`)

| Camshaft & Part Number | Type | Lift (`in`) | Duration (`Int/Exh`) | Field Behavior & `20+ psi` Boost Findings |
| :--- | :--- | :---: | :---: | :--- |
| **`1984–1987` Turbo (`4293819`)** | Slider | `.430"` | `240° / 240°` | Longest factory turbo duration (`20°` overlap vs. `28°` on `244°` N/A cam). |
| **`1988` Turbo (`4387850`)** | Roller | `.430"` | `236° / 232°` | Early `1988` rollers retain lube slot on journal; drop-in with roller followers. |
| **`1989–1992` Turbo (`4387721`)** | Roller | `.430"` | `228° / 228°` | Crispest idle/throttle response; **`263.1 hp @ 5800`** / **`256.2 lb-ft @ 4600`** (`87 CSX` mule @ `14–17 psi`). |
| **`Mopar P4529314` / `FM 475` (`FM09034`)** | Slider | `.460"` | `240° / 240°` (`314`) | `FM 475` smooths boost transition (`6000–6300 rpm`), but lost to stock `1989` roller at `25 psi` track testing. |
| **`Super 60` (`P5249496`) / `Comp 22-124-4`** | Slider | `.499"` | `260°` / `268°` | `+20–25 hp` (`3000–6000 rpm`) below `18 psi`; poor emissions/low-end and short street life (`10–18k mi`). |
| **`TAFT S1` / `S2` / `S3`** | Re-ground Roller | `—` | Mild / `S-60` eq. / Race | `TAFT S2` (`14–17 psi` dyno) traded `-20 lb-ft` under `3500 rpm` for **`272.2 hp @ 6000`** (`+20 hp` @ `6500 rpm`). |

> [!TIP]
> **Boost-vs-Overlap Rule**: Above **`20 psi`**, factory **`1988–1992` roller cams** consistently outperform aggressive slider grinds (`Super 60` / `FM 475`) by minimizing overlap blow-through while preserving street idle and valvetrain durability.

## `2.2L` / `2.5L` Engine Benchmarks & `Gus Mahon` Strip Progression

- **Factory `2.2L` `SOHC` vs. `DOHC` (`Allpar (pre-2017 archive)`)**: `A515` `2.2L` `HPTBI` (`EDF`, `9.0:1` CR, `251.2 lb` dressed) vs. `A522` `2.2L` `Turbo III` (`EDS`, `8.3:1` CR, `364.0 lb` dressed; Lotus `16V` head requires careful timing-belt tensioning due to heavy valve springs). Note `86` `GLHS` and `87+` `T-II` use floating wrist pins and forged cranks.
- **`Gus Mahon` Build Progression Milestones (`TheDodgeGarage`)**:
  - **`MeanMini` Caravan (`2.5L` Turbo)**: Progressed from high-`16s` (`70s mph`) -> `15.11 @ 89.45 mph` (`15 psi` + Spearco intercooler) -> `13.949 @ 96.16 mph` (`18.5 psi`, open exhaust) -> **`13.18 @ 102.57 mph`** (`22 psi` `Super 60` turbo, `20%` over injectors, posi/LSD, balance shafts removed, `5th`/`6th` supplemental injectors).
  - **`Acclaim` (`2.5L` Turbo, `2990 -> 2665 lb`)**: Progressed from `16.99 @ 78.74 mph` (`7 psi` stock) -> `14.33 @ 94.88 mph` (`15 psi`, IC, alcohol injection, `5th` injector) -> `12.67 @ 107.23 mph` (`25.5 psi`, `Super 60`, `40#` injectors, posi) -> **`12.25 @ 109.8 mph`** (`27.5 psi`) / `12.27 @ 110.4 mph` (`2.65` transfer gears).

## Related Pages

- [Turbo & Airflow Overview](turbo-and-airflow-overview)
- [Cylinder Head Flowbench Atlas](cylinder-head-flowbench-atlas)
- [Chrysler OEM Turbocharger Reference](oem-chrysler-turbos)
- [16V DOHC ECC / EDZ Hybrid Swap & K-Block Machining Guide](dohc-ecc-hybrid-swap-guide)
- [Chrysler FWD Transaxles: A555, A568, A413](a555-a568-a413-transaxles)
- [Wiki Home](Home)
