# `profiles/` — Modular Mopar Drivetrain Matrix & Turbo Compressor Maps

`profiles/` defines the physical vehicle combinations and turbocharger thermodynamics used by the Python Behavioral Twin, `m68hc11` load calculations, and Quiet UI workbench selectors.

## Modular Drivetrain Compatibility Matrix
| Dimension | Supported Variants | Physical Compatibility Guard |
| :--- | :--- | :--- |
| **Engine Block** | `2.2L` (K-car), `2.5L` (long-stroke), `2.4L` (DOHC-ready iron block) | Determines displacement & deck/coolant architecture |
| **Cylinder Head** | `8V SOHC` (Turbo I/II), `2.2L Lotus 16V` (Turbo III), `Neon ECC 2.0L DOHC` | **Strict Guard**: `2.2L` / `2.5L` blocks pair ONLY with `8V SOHC` or `2.2L Lotus 16V`. `Neon ECC` DOHC swaps pair **exclusively** with the `2.4L` iron block. |
| **Transaxle** | `A525`, `A555`, `A568` (5-speed manual), `NV-T350`, `A413` (3-speed auto) | Supplies per-gear ratios & final drive for RPM/speed/load modeling |
| **Vehicle Chassis** | Daytona Shelby, Shelby CSX, Turbo Shadow, Spirit R/T, Omni GLH/GLHS, Turbo Minivans | Supplies mass, aero drag, & tire circumference |

## `turbo_spec.json` — 4-Point Digitized Compressor Maps
- Stores digitized compressor map islands (`Pressure Ratio` vs `Corrected Mass Flow / lb·min⁻¹` and adiabatic efficiency contours) using **4-point reference calibration** per map.
- Enables the thermodynamic layer to overlay logged `RPM x MAP x ACT` road pulls directly onto the compressor map, flagging **surge margin**, **choke boundary**, and intercooler/charge-temp thermal efficiency (`NIST SP 811` units).
