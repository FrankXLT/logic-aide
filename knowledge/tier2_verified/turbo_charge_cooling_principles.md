> **Scope**: Cleanroom Tier 2 Thermodynamic Synthesis of Forced-Induction Compressor Efficiency, Intercooling Effectiveness, Charge Density & Chrysler `DEFCHG` Tuning
> **Boundary**: First-principles compressible-flow/ideal-gas equations and engineering design trade-offs alongside `Corky_Bell_Maximum_boost.pdf` topic index only; zero verbatim textbook prose.

---
doc_id: "turbo-charge-cooling-principles"
title: "Forced-Induction Compressor Thermodynamics, Intercooling Effectiveness & Charge-Temp Compensation"
trust_tier: "Tier 2"
source_type: "TURBO_THERMODYNAMIC_SYNTHESIS"
source_citation: "Cleanroom Engineering Thermodynamic Synthesis cross-referenced to Corky_Bell_Maximum_boost.pdf (Ch.3-5, 12) and Chrysler SMEC/SBEC DEFCHG/Fueling Physics"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "Maximum_boost Ch.3-5 (p.25-71) & Ch.12 (p.127-139) topic index"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
hex_symbols: []
symbols_referenced: ["DEFCHG", "PEFTBL", "WOTTBL", "WOTMAP", "MAP", "K13"]
pins_referenced: ["LM 2-25"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Turbocharger Compressor & Charge-Cooling Engineering Synthesis

## 1. Ideal-Gas Compression & Adiabatic Temperature Rise

Compressing ambient air of absolute pressure $P_1$ (`14.7 psia` sea-level nominal) and absolute temperature $T_1 = T_{\text{amb}}(^\circ\text{F}) + 459.67$ across compressor pressure ratio $\Pi_c = \frac{P_2}{P_1}$ increases charge temperature inversely with adiabatic compressor efficiency $\eta_c$ (`~0.60–0.78` inside compressor island vs. `< 0.55` approaching choke):

$$T_{\text{comp, out}} = T_1 + \frac{T_1 \left(\Pi_c^{\frac{\gamma - 1}{\gamma}} - 1\right)}{\eta_c}, \qquad \frac{\gamma - 1}{\gamma} \approx 0.2857 \text{ (for diatomic air } \gamma = 1.4\text{)}$$

- **Design Consequence (`Ch. 3` Compressor Sizing)**: Running an undersized compressor wheel past its peak efficiency island drives $\eta_c \downarrow$, spiking $T_{\text{comp, out}}$ for the exact same manifold gauge boost and consuming excess exhaust backpressure (`P_exhaust > P_intake`), which elevates residual cylinder exhaust gas fraction and knock propensity.

## 2. Intercooler Thermal Effectiveness ($\varepsilon$) vs. Pressure Drop ($\Delta P$)

An air-to-air or air-to-water intercooler transfers enthalpy from the hot compressor discharge $T_{\text{comp, out}}$ toward ambient coolant/airsink temperature $T_{\text{sink}}$ with effectiveness $\varepsilon$:

$$\varepsilon = \frac{T_{\text{comp, out}} - T_{\text{ic, out}}}{T_{\text{comp, out}} - T_{\text{sink}}} \implies T_{\text{ic, out}} = T_{\text{comp, out}} - \varepsilon (T_{\text{comp, out}} - T_{\text{sink}})$$

- **Net Manifold Charge Density Ratio ($\rho_2 / \rho_1$)**: Using ideal-gas law $\rho = \frac{P}{R_{\text{specific}} T}$ and core pressure loss $\Delta P_{\text{ic}} = P_{\text{comp, out}} - P_{\text{manifold}}$:
  $$\frac{\rho_{\text{manifold}}}{\rho_1} = \left(\frac{P_{\text{comp, out}} - \Delta P_{\text{ic}}}{P_1}\right) \cdot \left(\frac{T_1}{T_{\text{ic, out}}}\right)$$
- **Core Sizing Trade-Off (`Ch. 5`)**: High charge-density recovery requires balancing high thermal effectiveness ($\varepsilon \approx 0.70–0.85$ via multi-row offset fins and sufficient ambient frontal airflow) against internal flow-area restriction (`<= 1.0–1.5 psi` target $\Delta P_{\text{ic}}$ at peak mass flow so turbo pressure ratio $\Pi_c$ does not need to climb unnecessarily to hold target manifold boost).

## 3. Interaction with Chrysler Speed-Density `ECU`s (`DEFCHG` & Spark Knock Margin)

- **Why Speed-Density Cares (`K13` Charge Temp Sensor vs. `DEFCHG`)**: Unlike `MAF` systems that directly weigh cooled air mass, Chrysler `6803` `LM` (`2-25` `K13`) and `68HC11` `SMEC`/`SBEC` calculate air mass from `MAP`, `RPM`, `PEFTBL`, and a **charge-temperature compensation term** (`T_manifold` vs. limpin constant `DEFCHG`).
- **Intercooled vs. Non-Intercooled Upgrades**:
  - Adding an efficient intercooler to an originally non-intercooled `Turbo I` (`2.2L`/`2.5L`) engine lowers $T_{\text{manifold}}$, increasing true cylinder air mass $\rho_{\text{manifold}}$ at identical boost pressure `MAP` while simultaneously expanding octane-knock headroom.
  - Because stock non-intercooled `WOTTBL` / `PEFTBL` / `DEFCHG` tables assume warmer manifold temperatures, always log wideband `AFR` (`TechEdge` / `WinLog`), charge temperature, and knock counts (`0xF2`/`0xF6`) after changing intercooler hardware rather than assuming `MAP` alone tracks mass airflow identically.
- **Wastegate & Boost-Creep Stability (`Ch. 12`)**: Turbine/wastegate port area and exhaust backpressure govern high-RPM boost creep; keep mechanical wastegate base spring conservative so ECU `WGDC` duty control (`K1`) modulates cleanly below fuel-cut (`LMPSHF`) and `Code 45` overboost thresholds.
