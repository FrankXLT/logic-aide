> **Scope**: Cleanroom Tier 2 Digest of Chrysler `1984–1994` Key-Dance Fault Code Retrieval, Switch Tests, Ignition Diagnostics, Spark Plug Reading (`Gus Mahon`), and `2.2T`/`2.5T` Troubleshooting
> **Boundary**: Distilled strictly from `MiniMopar` (`Russ W. Knize`), `TheDodgeGarage`, and `Allpar (pre-2017 archive)` cleanroom web archives; excludes unprinted code definitions, PII, and flash-write tokens.

---
doc_id: "mopar-fault-codes-and-ignition-diag"
title: "Chrysler Key-Dance Fault Codes, Ignition Diagnostics & 2.2T/2.5T Plug Reading"
trust_tier: "Tier 2"
source_type: "DIAGNOSTIC_PROCEDURE_AND_IGNITION"
source_citation: "MiniMopar Fault Codes (Russ W. Knize), TheDodgeGarage Ignition Guide & Gus Mahon Spark Plug Diagnostics, Allpar 2.2T Repairs"
warc_capture_date: "2026-11-27T00:00:00Z"
page_ref: "minimopar/fault; thedodgegarage/turbo_ignition, friends_gus_m; allpar/22-turbo-performance-repairs"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K"]
hex_symbols: ["0x4000"]
symbols_referenced: ["RN12YC", "RN11YC", "RN9YC", "MSD 6-A", "MSD 6-AL", "MSD 7-AL2", "MSD BTM6", "Code 11", "Code 12", "Code 33", "Code 55"]
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler Fault Code Retrieval, Ignition Diagnostics & `2.2T`/`2.5T` Troubleshooting

## 1. Key-Dance Fault Code Retrieval & Switch Tests (`MiniMopar`, `1984–1994`)

- **Key-Dance Sequence**: With engine off, cycle ignition `ON-OFF-ON-OFF-ON` within `5 seconds` (do **not** turn key to `START`). Count flashes of the `"Power Loss"`, `"Limited"`, or `"Check Engine"` lamp separated by short delays (`tens` digit, short pause, `ones` digit, long pause; `max 7 flashes` per digit).
- **Termination & Baseline Codes**:
  - **`Code 55`**: Terminates every fault sequence (`55` alone = no stored faults).
  - **`Code 33`**: Normal on vehicles **without** air conditioning.
  - **`Code 11` & `Code 12`**: Removing ECU/battery power for `~10 minutes` clears stored memory and generates `Code 11` + `Code 12`. `Code 11` clears as soon as the engine cranks; `Code 12` clears after `15` ignition on/off cycles. All other codes also persist for `15` on/off cycles.
- **Limp-In & Post-Code Switch Tests**: `1984–1987` `LM` units stay latched in limp-in until engine shutoff; `1988+` `SMEC`/`SBEC` units re-test failed sensors live and can exit limp-in while driving. After `Code 55`, activating driver switch inputs (brake pedal, A/C switch, cruise) illuminates the MIL while depressed and extinguishes it when released.
- **Year / Engine Split Applicability Index (`MiniMopar` Fault Index)**:
  - *All-model single definition*: `11, 12, 13, 14, 15, 16, 21, 24, 25, 33, 41, 43, 46, 47, 53, 55`.
  - *Year-split codes*: `17` (`85` / `86` / `87+`), `22` (`84` / `85+`), `31` & `34` (pre-`87` & all L-body / `87+` non-L-body), `32`, `35`, `42`, `44` (`84` / `85–87` / `88+`), `51` (`84` / `85` / `86+`), `52` (`85` / `86+`), `54` (`85` / `84, 86+`).
  - *Fuel / Turbo-split codes*: `23` (TBI / `84` Turbo / `85+` Turbo), `26` & `27` (TBI / MPI), `36` (MPI N/A / standard T-I, T-II, T-III / VNT T-IV), `37` (N/A auto / Turbo only), `45` (Turbo only).

---

## 2. Ignition System Diagnostics & `MSD` Integration (`TheDodgeGarage`)

| Component | Recommended Practice | Pitfalls & Failure Modes |
| :--- | :--- | :--- |
| **Spark Plugs & Gap** | Champion `RN12YC` (stock), `RN11YC` (colder), `RN9YC` (coldest; may foul on TBI). Gap `0.025–0.035 in` (`0.035 in` baseline). | High turbo cylinder pressure (`~1.85 hp/cu in`) misfires at `0.045 in` gap (dropping to `0.030 in` restores clean pulls). Avoid Bosch/Autolite Platinum (fail) and SplitFire (disrupt fast-burn swirl). |
| **Coils & Distributor** | Early oil-filled "can" coil preferred over thermostat-mounted resin coil; `MSD Blaster II/III` compatible. Blue Streak cap/rotor. | Cheap black caps crossfire. Inspect **plastic shutter-wheel disc** at base of `2.2`/`2.5` distributor—loosening with age causes misfire and poor idle. |
| **Plug Wires** | Spiral-core wires (Magnacore or Moroso universal). | **NEVER use solid-core plug wires**—EMI noise disrupts `LM`/`SMEC`/`SBEC` electronics. |
| **CD Boxes (`MSD`)** | Stock ignition suffices below `~325–340 hp`. Above that, use `MSD 6-A` / `6-AL` (`Blaster II/III`) or `MSD 7-AL2` (`HVC I/II`). | Remove ballast resistor & coil noise suppressor. Wire switched feed to a circuit that **stays hot during cranking** (most Chrysler accessory feeds drop in `START`). Factory ECU rev-limiter (`~6640 rpm`) overrides higher `MSD` pill limits. **Do NOT use `MSD BTM6` boost-retard** on factory turbo ECUs—causes double-retard with ECU boost timing pull. |

---

## 3. `Gus Mahon` Spark Plug Reading & `2.2T`/`2.5T` Field Diagnostics

- **Detonation & Lean Plug Reading (`Gus Mahon`, `TheDodgeGarage`)**:
  - **"Salt" specks**: Tiny molten droplets of aluminum fused onto the white center porcelain insulator = mild/inaudible detonation.
  - **"Pepper" specks**: Tiny dark carbon specks blasted off the piston crown and cylinder head.
  - **Rounded electrode edges**: Fresh plug corners should be sharp `90 deg`; rapid rounding after a few passes signals a lean mixture leading toward detonation (add fuel, pull timing, or reduce boost).
  - **Cylinder-to-cylinder distribution check**: Adding a small amount of `104+` octane booster leaves reddish residue on unleaded plugs—**reddest** plugs = richest cylinders; **whitest** plugs = leanest cylinders.
- **Mechanical & Vacuum Troubleshooting (`Allpar` Pre-2017 Archive & `TheDodgeGarage`)**:
  - **Boost & Vacuum Plumbing**: Turbo I wastegate actuators can stick open; brittle over-engine plastic vacuum lines crack (replace vapor-canister lines with fuel-rated hose and retain inline restriction orifices). A mechanical `Grainger` spring/ball pop-off valve regulates boost between wastegate spring base (`5–7 psi`) and backpressure blow-open (`~15–20+ psi` Garrett, `~17 psi` Mitsu).
  - **Drivetrain & Valvetrain Checks**: Repeated Daytona/LeBaron CV-joint breakage often traces to torn motor mounts flexing under torque. Cold piston/wrist-pin clacking (`T1` pressed pins) is distinct from rod knock/detonation. Inspect timing belt tensioner pulley rim wear (`~190k mi` check) so the belt cannot walk off.
  - **Historical `Gus Mahon` Reference Progression**: `2.5T` Caravan (`MeanMini`) progressed from high-`16s` stock to `13.18 ET @ 102.57 mph` (`Super 60`, `22 psi`, posi, `20%`-over injectors, balance shafts removed); `Acclaim` progressed from `16.99 @ 78.74 mph` (`7 psi`) to `12.25 @ 109.8 mph` (`27.5 psi`, `40#` injectors, alcohol injection, `2.65` transfer-gear final drive).
