# Modular Mopar Drivetrain & Turbo Thermodynamics Specification

All drivetrain builders, compressor-map overlays, and charge-cooling calculations follow **`NIST SP 811`** quantity calculus (`Quantity / Unit`, e.g., `Pressure / kPa`, `Mass Flow / lb·min⁻¹` or `kg·s⁻¹`, `Charge Temp / °C`) and **`ISA-101`** calm-nominal / amber-crimson excursion alerting (`.quiet-ui.md`).

---

## 1. Modular Mopar Drivetrain Schema (`profiles/`)

### 1.1 Engine Blocks (`profiles/blocks.json` / `vehicle_build.schema.json`)

All Chrysler `2.2L`, `2.5L`, `2.0L` Neon `ECC`/`ECB`, and `2.4L` `EDZ`/SRT-4 inline-4 blocks share **`4.00 in` (`101.6 mm`) bore-center spacing** and **`87.5 mm` standard bore** (supporting `.020` / `.040` machined overbores):

| Block Code | Bore $\times$ Stroke (`mm`) | Displacement (`cm³` / `L`) | Architecture & Notes |
| :--- | :---: | :---: | :--- |
| **`2.2L` K-Block** | $87.5 \times 92.0$ | $2{,}213\text{ cm}^3$ (`2.2 L`) | Cast-iron open-deck inline-4 (`10 mm` head bolts `1981–1987`; `11 mm` bolts `1988+` common block); Turbo II/III castings include cross-drilled block/head oil-coolant passages. |
| **`2.5L` K-Block** | $87.5 \times 104.0$ | $2{,}501\text{ cm}^3$ (`2.5 L`) | Long-stroke cast-iron K-block sharing `87.5 mm` bore, `101.6 mm` bore centers, and K-deck geometry with `2.2L`. |
| **`2.0L` `ECC` Block** | $87.5 \times 83.0$ | $1{,}996\text{ cm}^3$ (`2.0 L`) | Neon short-stroke block sharing `4.00 in` bore spacing and `87.5 mm` bore (`ECC` DOHC / `ECB` SOHC). |
| **`2.4L` `EDZ` Block** | $87.5 \times 101.0$ | $2{,}429\text{ cm}^3$ (`2.4 L`) | Tall-deck thin-wall cast-iron block (Cloud/PT/SRT-4 architecture) sharing `4.00 in` bore centers; distinct oil-pan balance-shaft carrier & deck passages vs. K-blocks. |

### 1.2 Cylinder Heads & 3-Tier Mopar Compatibility Matrix (`profiles/heads.json`)

| Cylinder Head | Valves / Cam | `2.2L` / `2.5L` K-Block | `2.4L` `EDZ` Block | Engineering & Swap Notes |
| :--- | :--- | :---: | :---: | :--- |
| **`8V SOHC` Open (`#445` Bathtub)** | 8V SOHC | **`BOLT_ON_NATIVE`** | **`INCOMPATIBLE`** | Early 1983–87 chamber; verify `10 mm` vs. `11 mm` head bolts, cross-drilled gasket alignment, and inconel exhaust valves on boosted builds. |
| **`8V SOHC` Swirl (`#782` Fast-Burn)** | 8V SOHC | **`BOLT_ON_NATIVE`** | **`INCOMPATIBLE`** | Smaller fast-burn chamber (`~6 cm³` tighter) improving swirl & knock margin (`Turbo I`/`II`); verify `11 mm` head-bolt torque & cross-drilled passages. |
| **`1991–1993` Lotus `16V` (`Turbo III`)** | 16V DOHC | **`BOLT_ON_NATIVE`** | **`INCOMPATIBLE`** | `Spirit R/T` & `Daytona IROC R/T` Lotus-engineered K-footprint head (`11 mm` bolts, cross-drilled cooling/oil gasket verification). |
| **`1988–1991` Maserati `16V` (`Chrysler TC`)** | 16V DOHC | **`BOLT_ON_NATIVE`** | **`INCOMPATIBLE`** | Chrysler TC by Maserati K-footprint 16V head (`11 mm` head bolts and cross-drilled block/head oil-coolant gasket checks). |
| **Neon `ECC` (`2.0L`) / `2.4L` `EDZ`/SRT-4 DOHC** | 16V DOHC | **`HYBRID_MACHINING_REQUIRED`** | **`HYBRID_MACHINING_REQUIRED`** *(chassis swap)* / Native Deck | Shared `4.00 in` (`101.6 mm`) bore centers permit DOHC-on-K hybrid builds and full `2.4L EDZ` swaps into SMEC/SBEC cars when **all 7 Swap Advisory Checklist items** are satisfied (see below). |

#### Mopar 3-Tier Validation Rules (`profiles/schema/vehicle_build.schema.json`)
1. **`BOLT_ON_NATIVE`**: Permitted directly with `10 mm` vs. `11 mm` head-bolt sizing and cross-drilled block/head gasket verification notes (`2.2L`/`2.5L` + `8V #445`, `8V #782`, `1991–1993` Lotus `16V`, or `1988–1991` Maserati `16V`).
2. **`HYBRID_MACHINING_REQUIRED` (`ISA-101` Amber Swap Advisory Checklist)**: Triggered when pairing a `2.2L` or `2.5L` K-block with a Neon `ECC` `2.0L` DOHC or `2.4L` `EDZ`/SRT-4 `16V` DOHC head (or installing a full `2.4L EDZ` engine into a `1988–1993` SMEC/SBEC car). Compilation requires explicit confirmation of:
   - Oil and coolant deck passage plugging between K-block and `ECC`/`EDZ` head surfaces.
   - External head oil feed line and external oil-pan drainback routing.
   - Head-bolt / stud sleeve alignment (`10 mm` / `11 mm` bolt/dowel geometry).
   - Custom timing-belt tensioner and idler geometry.
   - Piston valve reliefs cleared for 4-valve intake/exhaust pockets.
   - SMEC/SBEC crank/cam Hall-effect trigger synchronization.
   - Counter-clockwise `PEFTBL` cam-peak rotation (accounting for shifted DOHC volumetric-efficiency cam peak in `PEFTBL`).
3. **`INCOMPATIBLE` (Hard Compilation Block)**: Genuinely non-viable mechanical pairings—such as fitting an older `8V #445` or `#782` SOHC K-head (or K-footprint `Lotus`/`Maserati 16V` head) onto a `2.4L EDZ` block—halt `logicaide profile compile` with an explicit mechanical explanation.

### 1.3 Transaxle & Final Drive Library (`profiles/transaxles.json`)

| Transaxle | Type | Forward Gear Ratios (`1st / 2nd / 3rd / 4th / 5th`) | Reference Final Drive (`FD`) | Notes |
| :--- | :--- | :---: | :---: | :--- |
| **`A525`** | 5-spd Manual | `3.20 / 2.08 / 1.36 / 1.00 / 0.72` | `3.05 / 3.56 / 3.85` | Early light-duty manual; differential case flex under high boost often braced with Mopar girdle (`P4349444`). |
| **`A555`** | 5-spd Getrag HD | `3.25 / 2.08 / 1.36 / 1.03 / 0.72` | `3.85` | Heavy-duty heat-treated case, Getrag coarse-pitch gearset, 4-pinion differential (`Turbo II`). |
| **`A568`** | 5-spd Getrag/NPG | `3.52 / 2.08 / 1.35 / 0.97 / 0.73` | `3.50 / 3.85 / 3.94` | Reinforced successor to `A555` pairing dual-cone synchros with high-torque gearsets (`Spirit R/T`, `Daytona IROC`). |
| **`NV-T350`** | 5-spd Manual (`A578`) | `3.50 / 1.95 / 1.36 / 0.97 / 0.72` | `3.55 / 3.94` | Neon / `2.4L` swap manual (`3.55` SOHC vs. `3.94` DOHC R/T final drives). |
| **`A413`** | 3-spd TorqueFlite (`31TH`) | `2.74 / 1.54 / 1.00` | `3.02 / 3.22 / 3.56` | 3-speed FWD automatic; overall axle ratio set by transfer shaft + ring gear pair (`3.02` common on Turbo builds). |

> **Assembly-Tag Verification Note**: Chrysler mixed internal gearsets across model years and assembly numbers. Factory FSM / MiniMopar tables also record `A525` wide-ratio (`3.29/1.89/1.21/0.88/0.72`) & close-ratio (`3.29/2.08/1.45/1.04/0.72`), `A555` (`3.00/1.89/1.28/0.94/0.71`), `A568` (`1990–91` `3.00/1.89/1.28/0.94/0.71` vs. `1992–93` `3.31/2.06/1.36/0.97/0.71`), and `A413` OEM planetary (`2.69/1.55/1.00`) vs. A904-lineage (`2.74/1.54/1.00`) ratios. Always verify the stamped **Transaxle Assembly Tag** when calibrating road-speed inference.

### 1.4 Tire Rollout & RPM-to-MPH Mapping
Given tire section width $W\text{ (mm)}$, aspect ratio $A\text{ (\%)}$, rim diameter $D_{\text{rim}}\text{ (in)}$, loaded rolling radius factor $k_{\text{load}} \approx 0.97$ (or directly measured rollout circumference $C_{\text{tire, in}}$), gear ratio $G_i$, and final drive $G_{\text{FD}}$:
$$C_{\text{tire, in}} = \pi \cdot k_{\text{load}} \left(D_{\text{rim}} + \frac{2 \cdot W \cdot (A / 100)}{25.4}\right)$$
$$v_{\text{mph}} = \frac{\text{RPM} \cdot C_{\text{tire, in}} \cdot 60}{G_i \cdot G_{\text{FD}} \cdot 63{,}360}, \qquad v_{\text{km/h}} = v_{\text{mph}} \cdot 1.609344$$
Used to infer engaged gear $G_i$ during `VEHICLE_SCI` drives lacking a direct gear sensor and to cross-check speedometer/VSS scaling.

---

## 2. 4-Point `turbo_spec.json` Compressor-Map Digitizer

LogicAIDE converts static raster compressor maps into machine-readable `profiles/turbo/<turbo_id>/turbo_spec.json` artifacts via a **4-point affine axis calibration**:

1. **4 User Axis Clicks**:
   - Two horizontal mass-flow landmarks on the $x$-axis: $(x_1, y_{x1}) \leftrightarrow (\dot{m}_{\text{corr},1}, PR_{\text{base}})$ and $(x_2, y_{x2}) \leftrightarrow (\dot{m}_{\text{corr},2}, PR_{\text{base}})$ in $\text{lb·min}^{-1}$ or $\text{kg·s}^{-1}$.
   - Two vertical pressure-ratio landmarks on the $y$-axis: $(x_{y1}, y_1) \leftrightarrow (\dot{m}_{\text{base}}, PR_1)$ and $(x_{y2}, y_2) \leftrightarrow (\dot{m}_{\text{base}}, PR_2)$.
2. **Affine Pixel-to-Data Transform**:
   Solves the $2 \times 3$ affine matrix $M$ mapping raster pixel $(x_p, y_p)$ directly to $(\dot{m}_{\text{corr}}, PR)$ even under minor scan skew/rotation:
   $$\begin{bmatrix} \dot{m}_{\text{corr}} \\ PR \end{bmatrix} = \mathbf{A} \begin{bmatrix} x_p \\ y_p \end{bmatrix} + \mathbf{b}$$
3. **Vectorized Map Geometry**:
   - **`efficiency_islands`**: closed polygons labeled with compressor adiabatic efficiency $\eta_c \in [0.50, 0.78]$.
   - **`surge_line`**: piecewise polyline $PR_{\text{surge}}(\dot{m}_{\text{corr}})$ marking unstable flow reversal on the left boundary.
   - **`choke_boundary`**: piecewise polyline $\dot{m}_{\text{choke}}(PR)$ (and max shaft speed contour) marking sonic choking on the right boundary.

---

## 3. Compressible-Flow & Charge-Cooling Equations (`NIST SP 811`)

### 3.1 Pressure Ratio & Corrected Mass Flow
Let $P_{\text{amb}}$ be ambient barometric pressure ($\text{kPa}$), $\Delta P_{\text{intake}}$ air-filter/inlet tract loss ($\text{kPa}$), $P_{\text{manifold}}$ absolute intake manifold pressure (`map_kpa`), and $\Delta P_{\text{IC}}$ charge-pipe + intercooler pressure drop ($\text{kPa}$):
$$P_1 = P_{\text{amb}} - \Delta P_{\text{intake}}, \qquad P_2 = P_{\text{manifold}} + \Delta P_{\text{IC}}, \qquad PR = \frac{P_2}{P_1} = \frac{P_{\text{manifold}} + \Delta P_{\text{IC}}}{P_{\text{amb}} - \Delta P_{\text{intake}}}$$

Engine airflow demand ($\dot{m}_{\text{actual}}$, 4-stroke) and compressor-map corrected flow (referenced to map standard $T_{\text{ref}}, P_{\text{ref}}$, typically $298.15\text{ K}$ and $101.325\text{ kPa}$):
$$\dot{m}_{\text{actual}} = \frac{V_d \cdot (\text{RPM} / 120) \cdot P_{\text{manifold}} \cdot \eta_v}{R_{\text{air}} \cdot T_3}, \qquad \dot{m}_{\text{corr}} = \dot{m}_{\text{actual}} \cdot \frac{\sqrt{T_1 / T_{\text{ref}}}}{P_1 / P_{\text{ref}}}$$

### 3.2 Compressor Discharge & Intercooler Outlet Temperatures
With dry-air specific heat ratio $\gamma \approx 1.40$ ($\frac{\gamma - 1}{\gamma} \approx 0.2857$), compressor inlet temperature $T_1\text{ (K)}$, and interpolated map efficiency $\eta_c(\dot{m}_{\text{corr}}, PR)$:
$$T_2 = T_1 \left(1 + \frac{PR^{(\gamma - 1)/\gamma} - 1}{\eta_c}\right)$$
With intercooler thermal effectiveness $\varepsilon \in [0, 1]$ and ambient sink $T_{\text{amb}}\text{ (K)}$:
$$T_3 = T_2 - \varepsilon (T_2 - T_{\text{amb}})$$

### 3.3 Fuel Evaporative Charge Cooling ($\Delta H_{\text{vap}}$): `93 E10` vs. `E85`
Port fuel vaporization removes enthalpy from the intake port/cylinder charge prior to intake valve closing:

| Fuel Grade | Stoichiometric `AFR` | Latent Heat $\Delta H_{\text{vap}}$ (`kJ·kg⁻¹` fuel) | Mixture Cooling Potential (`kJ·kg⁻¹` stoich charge) | Operational Impact (`2.2L`/`2.5L` Turbo) |
| :--- | :---: | :---: | :---: | :--- |
| **`93 E10`** | $\approx 14.1:1$ | $\sim 350\text{–}370$ | $\sim 23\text{–}25$ | Baseline charge cooling; sensitive to `ACT > 55 °C` heat-soak knock at `> 15 psi` boost. |
| **`E85`** *(85% EtOH)* | $\approx 9.8:1$ | $\sim 840\text{–}920$ (ethanol) / $\sim 770\text{–}840$ (blend) | $\sim 72\text{–}85$ | $\sim 3\text{–}4\times$ higher evaporative cooling per kg charge (higher still at `0.78–0.85 λ` boost enrichment). |

Given effective fuel vaporization fraction $f_{\text{evap}} \in [0, 1]$ inside the port/cylinder and air constant-pressure specific heat $c_{p,\text{air}} \approx 1.005\text{ kJ·kg}^{-1}\text{·K}^{-1}$:
$$\Delta T_{\text{evap}} = \frac{\Delta H_{\text{vap}} \cdot f_{\text{evap}}}{\text{AFR}_{\text{actual}} \cdot c_{p,\text{air}}}, \qquad T_{\text{charge}} = T_3 - \Delta T_{\text{evap}}$$
Because charge density scales inversely with absolute temperature ($\rho \propto 1 / T_{\text{charge}}$), switching from `93 E10` to `E85` yields a measurable **volumetric efficiency / trapped-mass gain** alongside substantial end-gas knock margin, requiring corresponding injector pulse-width (`~35–44%` larger flow capacity) and cold-start cranking calibration updates in `fuel_tables.asm`.

### 3.4 `ISA-101` Envelope Warnings
1. **Surge-on-Throttle-Lift (`CAUTION` / `CRITICAL` Amber-Crimson)**:
   Triggered when $\frac{d(\text{TPS})}{dt} < -\text{threshold}$ (rapid throttle lift under boost) drives the instantaneous trajectory $(\dot{m}_{\text{corr}}, PR)$ left of the digitized `surge_line` before the bypass/blow-off valve relieves manifold pressure.
2. **Choke-at-Altitude (`CAUTION` / `CRITICAL` Amber-Crimson)**:
   Triggered when reduced ambient pressure $P_{\text{amb}}$ (high elevation) raises required $PR = \frac{P_{\text{manifold}} + \Delta P_{\text{IC}}}{P_{\text{amb}} - \Delta P_{\text{intake}}}$ for a fixed boost target while pushing corrected mass flow $\dot{m}_{\text{corr}}$ right of `choke_boundary` ($\dot{m}_{\text{choke}}(PR)$), risking overspeed and runaway $T_2$ discharge heat.
