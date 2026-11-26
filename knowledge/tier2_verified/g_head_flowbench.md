> **Scope**: Cleanroom Tier 2 Cylinder Head Flowbench & 2.5L VE Reference (`2.2L_K` / `2.5L_K` / `3.0L 12V` / `Stealth DOHC` @ `28 inH2O` / `6.97 kPa`)
> **Boundary**: Distilled anonymous bench measurements only; personal contributor identities and private source paths omitted per cleanroom policy.

---
doc_id: "g-head-flowbench-nist-sp811"
title: "Chrysler 2.2L/2.5L G-Head, Swirl Head & Intake Flowbench Digest (CFM & L/s)"
trust_tier: "Tier 2"
source_type: "BENCH_FLOWSHEET_DIGEST"
source_citation: "Community Flowbench & 2.5L VE Reference Archive (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "28 inH2O (6.97 kPa) Flowbench Series"
ecu_families: ["SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: []
symbols_referenced: []
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler 2.2L / 2.5L Cylinder Head & Intake Flowbench Digest

All volumetric flow figures are normalized at **`28.0 inH2O` (`6.97 kPa`)** using **NIST SP 811** conversion (`1 CFM = 0.47194745 dm3/s = L/s`).

## 2.5L Swirl, G-Head & 3.0L 12V Flowbench Comparison

### Intake Flow (`CFM / L/s @ 28 inH2O`)

| Lift (in) | Stock Swirl (CFM / L/s) | Community Ported G-Head (2.5L Bench) (CFM / L/s) | Community Ported Swirl Head (CFM / L/s) | LW Swirl Head (CFM / L/s) | Stock 3L 12v (CFM / L/s) | Community Ported 3.0L 12V (CFM / L/s) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0.10 | 47.52 / 22.427 | 68.31 / 32.239 | 54.70 / 25.816 | 69.56 / 32.829 | 44.50 / 21.002 | 44.50 / 21.002 |
| 0.20 | 74.25 / 35.042 | 112.86 / 53.264 | 107.80 / 50.876 | 119.88 / 56.577 | 103.95 / 49.059 | 103.95 / 49.059 |
| 0.30 | 100.98 / 47.657 | 149.99 / 70.787 | 153.00 / 72.208 | 165.00 / 77.871 | 147.01 / 69.381 | 151.47 / 71.486 |
| 0.40 | 118.80 / 56.067 | 175.23 / 82.699 | 177.20 / 83.629 | 191.40 / 90.331 | 161.86 / 76.389 | 172.26 / 81.298 |
| 0.50 | 126.00 / 59.465 | 187.00 / 88.254 | 195.40 / 92.219 | — | 183.00 / 86.366 | — |
| 0.60 | — | — | 210.50 / 99.345 | — | — | — |

### Exhaust Flow (`CFM / L/s @ 28 inH2O`)

| Lift (in) | Stock Swirl (CFM / L/s) | Community Ported G-Head (2.5L Bench) (CFM / L/s) | Community Ported Swirl Head (CFM / L/s) | LW Swirl Head (CFM / L/s) | Stock 3L 12v (CFM / L/s) | Community Ported 3.0L 12V (CFM / L/s) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 0.10 | 47.25 / 22.300 | 63.00 / 29.733 | 52.80 / 24.919 | 60.90 / 28.742 | 31.50 / 14.866 | 31.50 / 14.866 |
| 0.20 | 78.75 / 37.166 | 94.50 / 44.599 | 96.80 / 45.685 | 106.56 / 50.291 | 78.75 / 37.166 | 75.40 / 35.585 |
| 0.30 | 94.50 / 44.599 | 124.25 / 58.639 | 129.20 / 60.976 | 149.60 / 70.603 | 97.65 / 46.086 | 110.25 / 52.032 |
| 0.40 | 110.25 / 52.032 | 141.75 / 66.899 | 160.00 / 75.512 | 161.40 / 76.172 | 103.95 / 49.059 | 133.87 / 63.180 |
| 0.50 | 116.55 / 55.005 | 151.50 / 71.500 | 174.90 / 82.544 | — | 103.95 / 49.059 | — |
| 0.60 | — | — | 184.50 / 87.074 | — | — | — |

## G-Head Porting Progression & DOHC Flowbench Comparison

### Intake Flow (`CFM / L/s @ 28 inH2O`)

| Lift (in) | Stock Head (CFM / L/s) | KMP Flowbench Corrected (CFM / L/s) | G-Head Bowl & Short Turn Porting (CFM / L/s) | Stock Stealth DOHC (CFM / L/s) | G-Head Full Rework (Big Valves) (CFM / L/s) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 0.10 | 44.50 / 21.002 | 48.50 / 22.889 | 44.50 / 21.002 | 93.00 / 43.891 | 68.31 / 32.239 |
| 0.20 | 103.95 / 49.059 | 109.95 / 51.891 | 103.95 / 49.059 | 179.00 / 84.479 | 112.86 / 53.264 |
| 0.30 | 147.01 / 69.381 | 155.01 / 73.157 | 151.47 / 71.486 | 230.00 / 108.548 | 149.99 / 70.787 |
| 0.40 | 161.86 / 76.389 | 181.86 / 85.828 | 172.26 / 81.298 | 262.00 / 123.650 | 175.68 / 82.912 |
| 0.50 | 183.00 / 86.366 | 207.50 / 97.929 | 194.00 / 91.558 | 256.00 / 120.819 | 187.00 / 88.254 |

### Exhaust Flow (`CFM / L/s @ 28 inH2O`)

| Lift (in) | Stock Head (CFM / L/s) | KMP Flowbench Corrected (CFM / L/s) | G-Head Bowl & Short Turn Porting (CFM / L/s) | Stock Stealth DOHC (CFM / L/s) | G-Head Full Rework (Big Valves) (CFM / L/s) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 0.10 | 31.50 / 14.866 | 30.50 / 14.394 | 31.50 / 14.866 | 80.00 / 37.756 | 63.00 / 29.733 |
| 0.20 | 78.75 / 37.166 | 88.75 / 41.885 | 75.40 / 35.585 | 164.00 / 77.399 | 94.50 / 44.599 |
| 0.30 | 97.65 / 46.086 | 125.65 / 59.300 | 110.25 / 52.032 | 200.00 / 94.389 | 124.25 / 58.639 |
| 0.40 | 103.95 / 49.059 | 133.95 / 63.217 | 133.87 / 63.180 | 216.00 / 101.941 | 141.75 / 66.899 |
| 0.50 | 103.95 / 49.059 | 135.95 / 64.161 | 148.05 / 69.872 | 219.00 / 103.356 | 151.50 / 71.500 |

## G-Head Full Intake Manifold Flow Comparison

### Intake Flow (`CFM / L/s @ 28 inH2O`)

| Lift (in) | G-Head Only (CFM / L/s) | G-Head with 1pc Intake (CFM / L/s) | G-Head with mild 1pc Intake (CFM / L/s) | G-Head with 2pc Intake (CFM / L/s) | G-Head with mild 2pc Intake (CFM / L/s) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 0.10 | 46.20 / 21.804 | 44.10 / 20.813 | 49.70 / 23.456 | 49.40 / 23.314 | 49.40 / 23.314 |
| 0.20 | 93.20 / 43.986 | 85.20 / 40.210 | 88.00 / 41.531 | 89.00 / 42.003 | 89.00 / 42.003 |
| 0.30 | 128.10 / 60.456 | 108.90 / 51.395 | 110.60 / 52.197 | 113.40 / 53.519 | 114.80 / 54.180 |
| 0.40 | 150.50 / 71.028 | 121.50 / 57.342 | 122.50 / 57.814 | 126.00 / 59.465 | 128.80 / 60.787 |
| 0.50 | 161.50 / 76.220 | 130.38 / 61.533 | 130.20 / 61.448 | 133.70 / 63.099 | 136.50 / 64.421 |

## G-Head Lower vs. Complete 2-Piece Intake Flow

### Intake Flow (`CFM / L/s @ 28 inH2O`)

| Lift (in) | Head Only (CFM / L/s) | 2pc Lower (CFM / L/s) | mild 2pc Lower (CFM / L/s) | 2pc Intake (CFM / L/s) | mild 2pc Intake (CFM / L/s) |
| :---: | :---: | :---: | :---: | :---: | :---: |
| 0.10 | 46.20 / 21.804 | 46.10 / 21.757 | 51.20 / 24.164 | 49.40 / 23.314 | 49.40 / 23.314 |
| 0.20 | 93.20 / 43.986 | 88.30 / 41.673 | 92.30 / 43.561 | 89.00 / 42.003 | 89.00 / 42.003 |
| 0.30 | 128.10 / 60.456 | 116.90 / 55.171 | 120.40 / 56.822 | 113.40 / 53.519 | 114.80 / 54.180 |
| 0.40 | 150.50 / 71.028 | 132.40 / 62.486 | 135.20 / 63.807 | 126.00 / 59.465 | 128.80 / 60.787 |
| 0.50 | 161.50 / 76.220 | 144.40 / 68.149 | 146.10 / 68.952 | 133.70 / 63.099 | 136.50 / 64.421 |

## 2.5L 4-Cylinder Volumetric Efficiency (VE) Reference Sweep

- **Baseline**: `2.5 L`, `4 cyl`, `52.3 lb/hr @ 55.0 psi`, `45.0 psi` static (`1.0:1` regulator).

| RPM | MAP (psi) | IAT (degF) | PW (ms) | AFR (:1) | Fuel Pres (psi) | Duty Cycle | Calculated VE |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| 1000 | 0.0 | 85 | 3.00 | 14.7 | 45.0 | 0.025 | 0.3604 |
| 2000 | 1.0 | 90 | 8.00 | 14.0 | 46.0 | 0.133 | 0.8745 |
| 3000 | 12.0 | 108 | 12.46 | 13.0 | 57.0 | 0.311 | 0.8548 |
| 4000 | 15.0 | 120 | 16.00 | 11.0 | 60.0 | 0.533 | 0.8748 |
| 5000 | 15.0 | 130 | 15.97 | 11.0 | 60.0 | 0.665 | 0.8882 |
| 6000 | 15.0 | 130 | 15.00 | 11.0 | 60.0 | 0.750 | 0.8342 |
