> **Scope**: Cleanroom Tier 2 16V DOHC Neon (`2.0L_ECC`) Head on 89+ `2.2L_K`/`2.5L_K` Common Block Machining Digest (`HYBRID_MACHINING_REQUIRED`)
> **Boundary**: Distilled machining, NPT/AN plumbing, and 7-item compiler checklist mapping only; personal identities and private paths omitted.

---
doc_id: "dohc-ecc-kblock-hybrid-machining"
title: "16V DOHC Neon (ECC) Head on 89+ 2.2L/2.5L Common Block Hybrid Machining & Oiling Digest"
trust_tier: "Tier 2"
source_type: "HYBRID_MACHINING_DIGEST"
source_citation: "2.2L/2.5L Common Block to 16V DOHC Neon Head Hybrid Build Archive (Cleanroom Normalized)"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "Block/Head Plugging, -12AN Drainback, 0.098 in Restrictor & HYBRID_MACHINING_REQUIRED"
ecu_families: ["SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["SMEC_88_89", "SBEC_90_93"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC"]
hex_symbols: []
symbols_referenced: ["HYBRID_MACHINING_REQUIRED", "oil_coolant_deck_passage_plugging", "external_head_oil_feed_and_pan_drainback", "head_bolt_stud_sleeve_alignment", "custom_timing_belt_tensioner_geometry", "piston_valve_reliefs", "smec_sbec_crank_cam_hall_sync", "ccw_peftbl_cam_peak_rotation"]
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# 16V DOHC Neon (`2.0L_ECC`) Head on 2.2L/2.5L K-Block Hybrid Machining

## 1. Block & Head Machining / NPT-AN Deck Plugging

- **Block Front Oil Return Holes (3x)**: Drill `3/4"` (`~2.5"` deep), tap `1/2" NPT`, install cast-iron/stainless square-head plugs with Red Loctite, and deck flush.
- **Block Stock Oil Feed & CCV**: Drill/tap `1/4" NPT` and plug stock oil feed and stock CCV ports; drill new CCV hole matching the DOHC head.
- **Head Locating Dowels**: Drill `3/8"` deep for 2 head-locating dowel pins.
- **Block Rear `-12AN` Oil Pan Returns (2x)**: Drill/tap `2x 3/4" NPT` holes in rear of block for `3/4" NPT` to `-12AN` straight adapters (verify crank/rod internal clearance and trim threads if required).
- **Turbo Drain**: Remove stock drain tube and tap `1/2" NPT` for `-10AN` straight adapter.
- **16V DOHC Head Modifications**:
  - Plug 3 internal head oil return passages with `3/8" NPT` aluminum/stainless square-head plugs.
  - External oil feed: tap side of head above plugged EGR port for `-4AN` to `1/8" NPT` `90 deg` adapter fitted with a **`0.098"` carburetor jet oil restrictor**.
  - Head oil drains: `-12AN` to `3/4" NPT` `45 deg` (turbo side) and `-12AN` to `3/4" NPT` straight (power-steering side).

## 2. Five External Braided Oil Lines

| Line | Size / Length | End Fittings / Routing |
| :--- | :---: | :--- |
| Head-to-block drain #1 (turbo side) | `-12AN` (~12") | 1x straight, 1x `45 deg` |
| Head-to-block drain #2 (PS side) | `-12AN` (~8") | 2x `90 deg` swivel |
| External head oil feed | `-4AN` (~18") | Straight ends (`1/8" NPT` distribution block to head `0.098"` restrictor) |
| Turbo oil feed | `-4AN` (~24") | Straight ends (`1/8" NPT` `45 deg` at block to `1/8" NPT` `90/45 deg` at turbo) |
| Turbo oil drain | `-10AN` (~8") | Straight ends (turbo `1/2" NPT` `90 deg` to block `1/2" NPT` straight) |

## 3. Mapping to `HYBRID_MACHINING_REQUIRED` (7-Item Compiler Checklist)

| # | Compiler Checklist Token | Physical Hybrid Machining / Calibration Action |
| :---: | :--- | :--- |
| 1 | `oil_coolant_deck_passage_plugging` | Plug 3 front block returns (`1/2" NPT`, decked flush), stock feed/CCV (`1/4" NPT`), and 3 head returns (`3/8" NPT`). |
| 2 | `external_head_oil_feed_and_pan_drainback` | Route `-4AN` feed with **`0.098"` oil restrictor** and dual **`-12AN` oil pan/rear-block returns** (`3/4" NPT`) + `-10AN` turbo drain. |
| 3 | `head_bolt_stud_sleeve_alignment` | Install `3/8"` deep locating dowels, FWD Performance head studs, and alignment sleeves where 10mm/11mm bore steps apply. |
| 4 | `custom_timing_belt_tensioner_geometry` | Machine early timing-belt tensioner for belt tracking, fit adjustable cam gears, and use chopped Stratus DOHC intake (`4"` x `~16"` plenum; 2-pc Neon intake hits belt). |
| 5 | `piston_valve_reliefs` | Use custom or `2.5L Turbo III` forged pistons machined with 4 valve reliefs (`piston_valve_reliefs: true`). |
| 6 | `smec_sbec_crank_cam_hall_sync` | Configure distributor-to-Neon spark plug wires or Hall/DIS sync phasing (`README.txt` Hybrid variant / SMEC/SBEC crank-cam sync). |
| 7 | `ccw_peftbl_cam_peak_rotation` | Re-index `PEFTBL` (`PumpingEfficiency`) for higher-RPM DOHC airflow peak (do not scale `PEFTBL` by injector ratio). |
