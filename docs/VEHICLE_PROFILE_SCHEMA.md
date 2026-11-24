# Vehicle Build Profile Schema Specification (Draft 2020-12)

The canonical vehicle profile contract lives in [`profiles/schema/vehicle_build.schema.json`](../profiles/schema/vehicle_build.schema.json) using **JSON Schema Draft 2020-12** (`https://json-schema.org/draft/2020-12/schema`). All root and nested objects enforce `"additionalProperties": false` (with an optional root `"$schema"` string permitted for IDE validation), ensuring deterministic ingestion by the Python Behavioral Twin, `m68hc11` replay harness, and `load_cell_rollups` analytics.

Reference build examples:
- [`profiles/examples/csx_89_neon_dohc.json`](../profiles/examples/csx_89_neon_dohc.json) — 1989 Shelby CSX (`SMEC_DUAL_BOARD`, `2.2L_K` `.020`-over + `NEON_ECC_20_DOHC`, `HYBRID_MACHINING_REQUIRED`)
- [`profiles/examples/shadow_90_sbec_t2.json`](../profiles/examples/shadow_90_sbec_t2.json) — 1990 Dodge Shadow Turbo II (`SBEC_SINGLE_BOARD`, `2.2L_K` + `8V_782_SWIRL`, `BOLT_ON_NATIVE`)

---

## 1. Seven Required Profile Domains

| Domain Key | Responsibility | Key Fields & Constraints |
| :--- | :--- | :--- |
| `identity_and_chassis` | Vehicle catalog & aero/mass | `car_id`, `year` (`1984..1995`), `make`, `model`, `trim`, `chassis_code`, optional `vin_hash`, `curb_weight_kg`, `drag_area_cda_m2`, optional `notes` |
| `engine_bottom_end` | Block geometry & deck prep | `block_code` (`2.2L_K`, `2.5L_K`, `2.4L_EDZ`, `2.0L_ECC`), `nominal_bore_mm` (`87.5`), `overbore_in` (`>= 0`), `actual_bore_mm`, `stroke_mm`, `bore_center_spacing_mm` (`101.6`), `displacement_cc`, `compression_ratio`, `head_bolt_size_mm` (`10` \| `11`), `cross_drilled_block`, `piston_valve_reliefs`, `deck_modifications` |
| `cylinder_head_and_cam` | Head architecture & swap gate | `head_code`, `valves` (`8` \| `16`), `cam_architecture` (`SOHC` \| `DOHC`), `compatibility_tier` (`BOLT_ON_NATIVE` \| `HYBRID_MACHINING_REQUIRED`), `required_swap_mods`, optional `advisory_notes` |
| `transaxle_and_gearing` | Ratios, rollout & VSS | `transaxle_code` (`A525`, `A555`, `A568`, `NV_T350`, `A413`), `assembly_tag`, `forward_ratios` (`3..5` items), `final_drive_ratio`, `tire_spec` (`section_width_mm`, `aspect_ratio_pct`, `rim_diameter_in`, `loaded_radius_factor`, `rollout_circumference_in`), `vss_pulses_per_rev` |
| `fuel_system` | Fuel, injectors & pump curve | `fuel_grade`, `stoich_afr`, `specific_gravity`, `latent_heat_vap_kj_kg`, `regulator_topology`, `fmu_ratio`, `base_fuel_pressure_psi`, `rated_injector_pressure_psi`, `rated_injector_flow_lb_hr`, `effective_flow_lb_hr`, `injector_impedance`, `batoff_deadtime_ms`, `fuel_pump` |
| `turbo_and_intercooler` | Boost hardware & pressure drops | `turbo_id`, `turbo_spec_ref`, `wastegate_type`, `base_spring_psi`, `max_boost_ceiling_kpa`, `intercooler` (`type`, `effectiveness`, `pressure_drop_kpa`), `intake_tract_loss_kpa`, `bov_bypass_valve` |
| `ecu_and_sensors` | 68HC11 hardware & ROM bounds | `ecu_family`, `ecu_part_number`, `eprom_size_bytes` (`32768`), `rom_manifest_ref`, `pin11_continuity_verified`, `pin2_3_25_45_verified`, `fuel_table_architecture`, `iat_reference_c` (`60` \| `90`), `map_sensor_bar` (`1` \| `2` \| `3`), `rev_limit_rpm` (`<= 8160`), `overboost_fuel_cut_kpa`, `sci_baud` (`62500`) |

---

## 2. Mopar 3-Tier Block & Cylinder Head Compatibility Rules

The schema enforces Mopar block/head pairing via root-level `allOf` / `if` / `then` conditionals and forbids `"INCOMPATIBLE"` from appearing in `compatibility_tier`:

1. **Tier 1 — `BOLT_ON_NATIVE`**:
   - **Rule**: When `engine_bottom_end.block_code` is `2.2L_K` or `2.5L_K` and `cylinder_head_and_cam.head_code` is `8V_445_BATHTUB`, `8V_782_SWIRL`, `LOTUS_16V_T3`, or `MASERATI_16V_TC`, `compatibility_tier` MUST equal `"BOLT_ON_NATIVE"`.
   - Valve count and cam architecture are cross-checked (`8` / `SOHC` for `#445`/`#782`; `16` / `DOHC` for `LOTUS_16V_T3`/`MASERATI_16V_TC`).

2. **Tier 2 — `HYBRID_MACHINING_REQUIRED`**:
   - **Rule A (DOHC Head on `2.2L_K` / `2.5L_K` Block)**: When `engine_bottom_end.block_code` is `2.2L_K` or `2.5L_K` and `cylinder_head_and_cam.head_code` is `NEON_ECC_20_DOHC` or `EDZ_24_DOHC`:
     - `compatibility_tier` MUST equal `"HYBRID_MACHINING_REQUIRED"`.
     - `engine_bottom_end.piston_valve_reliefs` MUST be `true`.
     - `cylinder_head_and_cam.required_swap_mods` MUST contain all **7 mandatory swap checklist keys**:
       1. `oil_coolant_deck_passage_plugging`
       2. `external_head_oil_feed_and_pan_drainback`
       3. `head_bolt_stud_sleeve_alignment`
       4. `custom_timing_belt_tensioner_geometry`
       5. `piston_valve_reliefs`
       6. `smec_sbec_crank_cam_hall_sync`
       7. `ccw_peftbl_cam_peak_rotation`
     - Optional checklist keys (`10mm_11mm_bolt_gasket_note`, `edz_chassis_mount_swap`) may also be included.
   - **Rule B (Full `2.4L_EDZ` / `2.0L_ECC` Engine Swap into `1984–1995` SMEC/SBEC Car)**: When `block_code` is `2.4L_EDZ` or `2.0L_ECC` and `head_code` is `NEON_ECC_20_DOHC` or `EDZ_24_DOHC`, block/head deck oil-coolant passages match natively, while `compatibility_tier` MUST equal `"HYBRID_MACHINING_REQUIRED"` and `required_swap_mods` MUST require `smec_sbec_crank_cam_hall_sync`, `ccw_peftbl_cam_peak_rotation`, and `edz_chassis_mount_swap`.

3. **Tier 3 — `INCOMPATIBLE` (Hard Validation Rejection)**:
   - **Rule**: When `engine_bottom_end.block_code` is `2.4L_EDZ` (or `2.0L_ECC`) and `cylinder_head_and_cam.head_code` is `8V_445_BATHTUB`, `8V_782_SWIRL`, `LOTUS_16V_T3`, or `MASERATI_16V_TC`, the schema evaluates `"then": false` and **rejects the profile**.
   - `"INCOMPATIBLE"` is omitted from the `compatibility_tier` enum so no invalid pairing can pass validation by labeling itself incompatible.

---

## 3. Fuel System Scaling, Deadtime & Pump Curves

### 3.1 Pressure Square-Root & Specific-Gravity Scaling
Across an injector orifice of fixed discharge area, volumetric flow scales with $\sqrt{\Delta P / \rho}$ and mass flow ($\dot{m} = \rho Q$) scales with $\sqrt{\rho \cdot \Delta P}$:

$$Q_{\text{eff}} = Q_{\text{rated}} \sqrt{\frac{P_{\text{rail}}}{P_{\text{rated}}} \cdot \frac{\text{SG}_{\text{ref}}}{\text{SG}_{\text{fuel}}}}, \qquad \dot{m}_{\text{eff}} = \dot{m}_{\text{rated}} \sqrt{\frac{P_{\text{rail}}}{P_{\text{rated}}} \cdot \frac{\text{SG}_{\text{fuel}}}{\text{SG}_{\text{ref}}}}$$

For standard `93_E10` builds where injectors are rated in gasoline-equivalent mass flow ($\text{SG}_{\text{fuel}} \approx \text{SG}_{\text{ref}}$) and regulated by a `MANIFOLD_REFERENCED_1_TO_1` FPR holding constant differential $\Delta P = P_{\text{base}}$:
- **CSX `80 lb/hr` @ `43.5 psi` on `55.0 psi` rail**:
  $$\dot{m}_{\text{eff}} = 80.0 \times \sqrt{\frac{55.0}{43.5}} = 89.955\text{ lb}\cdot\text{hr}^{-1} \approx 89.96\text{ lb}\cdot\text{hr}^{-1}$$
- **Shadow `+40%` (`46 lb/hr`) @ `43.5 psi` on `55.0 psi` rail**:
  $$\dot{m}_{\text{eff}} = 46.0 \times \sqrt{\frac{55.0}{43.5}} = 51.724\text{ lb}\cdot\text{hr}^{-1} \approx 51.72\text{ lb}\cdot\text{hr}^{-1}$$

When switching to `E85` ($\text{SG} \approx 0.785$, $\text{AFR}_{\text{stoich}} \approx 9.8$), `stoich_afr`, `specific_gravity`, and `latent_heat_vap_kj_kg` update both mass-flow scaling and port evaporative charge cooling ($\Delta T_{\text{evap}}$). If `regulator_topology` is `RISING_RATE_FMU`, `fmu_ratio` MUST be a number `> 1`.

### 3.2 Battery Deadtime (`batoff_deadtime_ms`)
Injector opening latency depends on system bus voltage, rail differential pressure, and low-pulse non-linearity:
- `voltage_points_v` & `deadtime_ms`: Paired arrays covering at least **`8..15 V`** (e.g., `[8, 9, 10, 11, 12, 13, 14, 15]`) matching the 68HC11 `BATOFF` / deadtime lookup axis.
- `pressure_slope_ms_per_psi`: Linear deadtime increase per `psi` of differential rail pressure above the nominal characterization pressure (relevant when running `55.0 psi` vs `43.5 psi` or rising-rate FMUs).
- `short_pulse_knee_ms` & `min_linear_pw_ms`: Marks the non-linear ballistic region (`PW < short_pulse_knee_ms`) and minimum controllable linear pulse width (`min_linear_pw_ms`) flagged during idle/decel sweeps.

### 3.3 Voltage-Derated Fuel Pump Curves (`fuel_pump`)
Factory wiring harnesses suffer `1.5–2.5 V` resistive drop under load; rewired relay feeds (`rewired: true`) recover pump terminal voltage near alternator bus voltage (`13.5–14.4 V`). `fuel_pump` pairs nominal `flow_lph_at_13_5v` with `voltage_derated_curve` (`{voltage_v, pressure_psi, flow_lph}`) so Monte Carlo voltage-sag sweeps (`10.5 V`–`14.4 V`) detect fuel-rail starvation margins before lean excursion occurs.

---

## 4. ECU Architecture, IAT Reference & `8160 RPM` Ceiling

| Parameter | Options / Constraint | Engineering Rationale |
| :--- | :--- | :--- |
| `fuel_table_architecture` | `CHRYSLER_2D_PEFTBL` \| `CHRYSLER_3D_T3` | Standard `SMEC`/`SBEC` 8V/Turbo calibrations index `PEFTBL` (`RPM x MAP`); Turbo III / custom `T3` ROMs add 3D airflow/load structures (`CHRYSLER_3D_T3`). Hybrid CCW DOHC swaps require rotating/re-indexing `PEFTBL` cells to track higher-RPM DOHC VE peaks. |
| `iat_reference_c` | `60` \| `90` | Chrysler SMEC/SBEC charge-temp compensation tables normalize `ACT`/`IAT` density around calibration-specific reference temperatures: **`90 °C`** on earlier 1988–89 SMEC builds vs. **`60 °C`** on 1990+ SBEC calibrations. Mismatching the ROM's IAT reference biases hot-restart and heat-soak AFRs. |
| `rev_limit_rpm` | `1000..8160` (`maximum: 8160`) | Chrysler 68HC11 calibrations store RPM thresholds in an 8-bit `uint8` register scaled at **`32 RPM/bit`** ($255 \times 32 = \mathbf{8{,}160\text{ RPM}}$). Values above `8160` overflow 8-bit representation and are rejected by the schema. |
| `overboost_fuel_cut_kpa` | `1-bar <= 105`, `2-bar <= 205`, `3-bar <= 315` | Prevents unreachable overboost cut targets above the selected `map_sensor_bar` saturation ceiling (`~105 kPa` / `~205 kPa` / `~315 kPa` absolute). |
| `eprom_size_bytes` / `sci_baud` | `32768` / `62500` | Enforces 32 KB `27C256` (`$8000`–`$FFFF`) ROM image size and high-speed Chrysler `0xF2`/`0xF4` SCI streaming rate (`62,500` baud). |
| Harness Verification Flags | `pin11_continuity_verified`, `pin2_3_25_45_verified` | Records human DMM verification prior to bench/road connection (Pin 11 `Vpp` vs. SBEC ground; Pins 2/3 `Knock`/`CTS` and Pins 25/45 `SCI TX/RX` per FSM). |
