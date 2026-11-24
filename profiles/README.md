# `profiles/` — Modular Mopar Drivetrain Matrix, Schema & Compressor Maps

`profiles/` defines the Draft 2020-12 vehicle build manifest (`schema/vehicle_build.schema.json`), curated example builds (`examples/`), and turbocharger thermodynamics (`turbo_spec.json`) used by the Python Behavioral Twin, `m68hc11` load calculations, and Quiet UI workbench selectors.

## Modular Drivetrain 3-Tier Compatibility Matrix
All Chrysler `2.2L`, `2.5L`, `2.0L Neon ECC/ECB`, and `2.4L EDZ/SRT-4` blocks share **`4.00 in` (`101.6 mm`) bore-center spacing** and `87.5 mm` standard bore.

| Dimension | Supported Variants | 3-Tier Compatibility & Notes |
| :--- | :--- | :--- |
| **Engine Block** | `2.2L` K-car, `2.5L` long-stroke, `2.0L ECC`, `2.4L EDZ`/SRT-4 | Supports `.020` / `.040` overbores; `10 mm` (`1981–1987`) vs. `11 mm` (`1988+`) K-block bolts |
| **Cylinder Head** | `8V #445` Open, `8V #782` Swirl, `1991–93 Lotus 16V`, `1988–91 Maserati 16V`, `Neon ECC 2.0L DOHC`, `2.4L EDZ/SRT-4 DOHC` | **`BOLT_ON_NATIVE`**: `2.2L`/`2.5L` + `#445`/`#782`/Lotus/Maserati.<br>**`HYBRID_MACHINING_REQUIRED`**: `2.2L`/`2.5L` + `ECC`/`EDZ` DOHC (and `2.4L EDZ` SMEC/SBEC swaps) with 7-item amber Swap Advisory.<br>**`INCOMPATIBLE`**: e.g., `8V #445`/`#782` SOHC or K-car `16V` heads on `2.4L EDZ` block. |
| **Transaxle** | `A525`, `A555`, `A568`, `NV-T350` (5-spd manual), `A413` (3-spd auto) | Supplies per-gear ratios, assembly tags & final drive for RPM/speed/load modeling |
| **Vehicle Chassis** | Daytona Shelby, Shelby CSX, Turbo Shadow, Spirit R/T, Omni GLH/GLHS, Chrysler TC, Turbo Minivans | Supplies mass, aero drag, & tire circumference |

## Schema & Examples
- **`schema/vehicle_build.schema.json`**: JSON Schema Draft 2020-12 enforcing all 7 vehicle subsystems, fuel pressure/SG scaling, `batoff_deadtime_ms` (`8..15 V` + pressure slope + short-pulse knee), voltage-derated pump curves, `<= 8160 RPM` rev limit, and 3-tier compatibility.
- **`examples/csx_89_neon_dohc.json`**: `1989` Shelby CSX SMEC hybrid (`2.2L` + Neon `ECC` `16V` DOHC, `HYBRID_MACHINING_REQUIRED`, `A555` `3.85`, `80 lb/hr` @ `43.5 psi` on `55 psi` `1:1` rail, rewired Walbro `255 HP`).
- **`examples/shadow_90_sbec_t2.json`**: `1990` Dodge Shadow SBEC (`2.2L` Turbo II `#782` Swirl, `BOLT_ON_NATIVE`, `A555`, `+40%` injectors on `55 psi` rail).

## `turbo_spec.json` — 4-Point Digitized Compressor Maps
- Stores digitized compressor map islands (`Pressure Ratio` vs `Corrected Airflow / lb·min⁻¹` and adiabatic efficiency contours) using **4-point reference calibration** per map.
- Enables the thermodynamic layer to overlay logged `RPM x MAP x ACT` road pulls directly onto the compressor map, flagging **surge margin**, **choke boundary**, and intercooler/charge-temp thermal efficiency (`NIST SP 811` units).
