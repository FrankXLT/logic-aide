---
paths:
  - "ui/**"
  - "docs/showcase/**"
---

# Operator UI & Showcase Rule

Applies whenever creating or modifying workbench templates, components, stylesheets, scripts, or showcase pages under `ui/` or `docs/showcase/`.

## Mandatory Invariants

1. **`.quiet-ui.md` (`v9.5.0`) Conformance**:
   - Conform strictly to `.quiet-ui.md` (`Strict Drift: true`). Never introduce any item from `.quiet-ui.md`'s Deprecation Blocklist.
   - Maintain `.va-stage[data-width="workbench"]` (`max-inline-size: min(96vw, 112rem)`) and `@media (scripting: none)` fallback visibility for offline showcase tabs.
2. **Dual Theme Polarity**:
   - Preserve crisp parity between **Sunlight High-Contrast** (`#FFFFFF` Studio canvas, `>= 4.5:1` contrast) and **Garage Dark** (`#090D16` / `#0F1624` midnight telemetry canvas).
3. **Topbar & Responsive Viewport Discipline**:
   - Enforce a `<= 48px` N2 topbar height with left-of-brand section/navigation control and N1 collapsible rail at `>= 1100px`.
   - Verify layout integrity at `390x844` (`0px` horizontal overflow, `>= 44px` touch hit targets), `900x1000`, `1280x800` (passenger-seat HMI laptop), and `1440x900` (desktop bench).
4. **Controls, Metrology & Calm Alarm Palette**:
   - Hardware/drivetrain `<select>` pickers MUST group variants with semantic `<optgroup>` (`Engine Block`, `Cylinder Head`, `Transaxle`, `Fuel`).
   - Quantitative charts, tables, and axis labels MUST use `NIST SP 811` quantity calculus (`Quantity / Unit`, e.g., `Boost / kPa`, `Pulse Width / ms`, `Mass Flow / lb·min⁻¹`, `Charge Temp / °C`).
   - Status indicators MUST follow the `ISA-101` calm alarm palette (neutral nominal monochrome; amber/crimson strictly for excursions and `SAFETY GATE BLOCKED`).
   - **Zero emojis** in UI chrome, buttons, tabs, or telemetry labels.
   - Render SVG plots/diagrams deterministically via `quiet-evidence` (`urn:quiet-ui:schema:v9:*`) or `window.QuietSvgLayout` — never hand-author ad-hoc SVG coordinates.
5. **Required Verification Gates**:
   - Every UI change MUST pass `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` across both themes before sign-off.
