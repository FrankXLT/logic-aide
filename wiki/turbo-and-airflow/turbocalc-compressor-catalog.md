---
title: "TurboCalc Compressor Catalog & NIST SP 811 Axis Calibration"
slug: "turbocalc-compressor-catalog"
category: "turbo-and-airflow"
tier: "Tier 2"
provenance: "Distilled from read-only TurboCalc v5 beta 4 SQL/XML/DOCX catalog, SHA-256 deduplicated compressor map figures, and NIST SP 811 dry-air thermodynamic constants"
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
status: "verified"
last_verified: "2026-11-26"
tags: ["turbocalc", "compressor-map", "nist-sp811", "affine-calibration", "gt3076r", "chrysler-turbo"]
---

# TurboCalc Compressor Catalog & NIST SP 811 Axis Calibration

LogicAIDE imports the archival TurboCalc v5 beta 4 compressor catalog read-only into structured `profiles/turbo/catalog.json` and per-turbo `turbo_spec.json` artifacts so downstream airflow modeling (`WF-06`) never depends on raw legacy binaries or uncalibrated raster plots.

## 1. Live Imported Catalog Telemetry

| Catalog Metric | Verified Count | Scope & Reconciliation Notes |
| :--- | :---: | :--- |
| **Calibrated Turbos** | `145` | Spanning `6` manufacturers and `21` model families (`schema_version: "1.0.0"`). |
| **Uncalibrated Stubs** | `13` | Marked `axis_calibration_pending: true`; preserved safely without synthetic axis guessing. |
| **Deduplicated Map Figures** | `157` | Unique SHA-256 rasters consolidated from `167` source map files. |
| **BorgWarner Path Reconciliations** | `23` | Subdirectory path mismatches resolved deterministically against verified figure digests. |
| **Canonical Alias Links** | `2` | `garrett_t3_t4_50trim` -> `garrett_t04e_50_trim`; `garrett_t3_super60_t2` -> `garrett_t3_60_trim_super60`. |

### Chrysler OEM Wheel Baselines (`catalog.json`)

- **T03 Garrett (T-II)**: `42.0 mm` (`1.654"`) inducer / `60.0 mm` (`2.362"`) exducer, `.42` compressor A/R, `.48` A/R `T03` turbine (`48.0 mm` major dia).
- **TB03 Garrett (T-III)**: `45.0 mm` (`1.772"`) inducer / `60.0 mm` (`2.362"`) exducer, `.52` compressor A/R, `.48` A/R `T03` turbine (`48.0 mm` major dia).
- **TE04H Mitsu**: `38.0 mm` (`1.496"`) inducer / `53.0 mm` (`2.087"`) exducer, `T04` housing (`51.0 mm` turbine major dia).
- **VNT25 Garrett**: `36.0 mm` (`1.417"`) inducer, variable-nozzle turbine (`VNT`) lineage for `Turbo IV` `2.2L`/`2.5L` builds.

## 2. 4-Point Affine Pixel-to-Physical Calibration

Every calibrated `turbo_spec.json` maps top-left-origin image pixels `[x_p, y_p]^T` to corrected mass flow `m_corr` and pressure ratio `PR` using the exact 4-corner affine transform:

```text
[m_corr, PR]^T = A @ [x_p, y_p]^T + b

Top-Left  corner: (x0,     y0)     -> (m_min, pr_max)
Top-Right corner: (x0 + w, y0)     -> (m_max, pr_max)
Bot-Left  corner: (x0,     y0 + h) -> (m_min, pr_min)
Bot-Right corner: (x0 + w, y0 + h) -> (m_max, pr_min)
```

For rectangular axes aligned to pixel rows and columns:

```text
A = [[(m_max - m_min) / w,                    0],
     [                  0, -(pr_max - pr_min) / h]]

b = [m_min - x0 * (m_max - m_min) / w,
     pr_max + y0 * (pr_max - pr_min) / h]
```

Each `turbo_spec.json` publishes both `lb/min` (`A`, `b`) and SI `kg/s` (`A_kg_s`, `b_kg_s`) matrices verbatim so callers can evaluate surge, choke, and efficiency coordinates without re-deriving pixel offsets.

## 3. `NIST SP 811` Dry-Air Reference Conversions

All volumetric and SI source maps normalize to mass flow (`lb/min` primary, `kg/s` secondary) using the **`NIST SP 811` dry-air reference state** (`rho_ref = 1.184 kg/m³` at `T_ref = 298.15 K`, `P_ref = 101.325 kPa`):

| Source Unit | Conversion to `kg/s` | Conversion to `lb/min` | Normalization Notes |
| :--- | :--- | :--- | :--- |
| **`kg/s`** | `1.0` | `x * 132.277357` | Exact `60 / 0.45359237` (`1 lb/min = 0.00755987 kg/s`). |
| **`m^3/s`** | `x * 1.184` | `x * 156.61639` | Standard `NIST SP 811` volumetric flow at `rho_ref = 1.184 kg/m³`. |
| **`m^3/min`** | `x * (1.184 / 60)` | `x * 2.610273` | Metric volumetric flow scaled by `rho_ref / 60`. |
| **`cfm` (Legacy TurboCalc)** | `x * 0.00052163` | `x * 0.069` | `legacy_cfm_to_lb_min_factor` preserved for `source_bounds` parity (e.g., `675 cfm = 46.575 lb/min`). |
| **`cfm` (`NIST 298.15 K`)** | `x * 0.00055879` | `x * 0.073915` | `nist_298k_volumetric_factor` (`0.02831685 m³/ft³ * 1.184 kg/m³ * 2.204623 lb/kg`). |

## 4. Representative Verified Turbo Comparison

| `turbo_id` | Family / Product | Airflow Range (`lb/min`) | PR Range | Key Envelope / Swap Notes |
| :--- | :--- | :---: | :---: | :--- |
| `garrett_gt3076r` | `GT-R Series` / `GT3076R` | `0.0 – 60.0` | `1.0 – 4.5` | `57.0/76.2 mm` (`56` trim, `0.60` A/R, `310–525` HP); surge `(8.0, 1.30) -> (39.0, 3.94)`, choke `(33.0, 1.30) -> (55.0, 3.25)`, efficiency spine `(18.0, 1.40) -> (40.0, 2.70)`. |
| `garrett_gt3071r` | `GT-R Series` / `GT3071R` | `0.0 – 60.0` | `1.0 – 4.5` | High-boost `2.2L`/`2.5L` DOHC street/track match with earlier spool than `GT3076R`. |
| `garrett_t04e_50_trim` | `T04E Series` / `50 Trim` | `2.0 – 50.0` | `1.0 – 3.6` | Canonical target for `garrett_t3_t4_50trim`; classic `T3/T4` hybrid swap for `300–400` WHP builds. |
| `garrett_t3_60_trim_super60` | `T3 Series` / `60 Trim - Super60` | `0.0 – 40.0` | `0.9 – 3.0` | Canonical target for `garrett_t3_super60_t2`; bolt-on upgrade over stock Chrysler `T03`/`TB03`. |
| `garrett_t3_60_trim` | `T3 Series` / `60 Trim` | `0.0 – 40.0` | `0.9 – 3.0` | Baseline `T3 60-trim` reference map (`PR 0.9–3.0`) for `8V` `#445`/`#782` K-block sizing. |
| `mitsubishi_td04_16t` | `TD04 Series` / `16T` | `0.0 – 46.575` | `1.0 – 3.4` | Imported from `0.0–675.0 cfm` map (`0.069` legacy factor); fast-spool `2.2L`/`2.5L` street swap. |

## 5. Read-Only Import-to-`WF-06` Pipeline

```mermaid
flowchart LR
  SRC["Read-Only TurboCalc Archive (SQL/XML/DOCX + Rasters)"] -->|"SHA-256 Dedup + 23 BW Path Fixes"| CAT["profiles/turbo/catalog.json (145 Calibrated, 13 Stubs)"]
  CAT -->|"4-Point Affine + NIST SP 811 Normalization"| SPEC["profiles/turbo/&lt;turbo_id&gt;/turbo_spec.json"]
  SPEC -->|"Load A, b, Envelope & Spine"| WF06["WF-06 Engine-Turbo Airflow Matcher"]
```

## Related Documentation

- [Turbocharger & Airflow Engineering Overview](turbo-and-airflow-overview.md)
- [Wiki Home](../Home.md)
