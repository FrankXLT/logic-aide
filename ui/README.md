# `ui/` — Quiet UI v9.5.0 Telemetry & Calibration Workbench

`ui/` implements the browser-based LogicAIDE engineering workbench governed strictly by [`.quiet-ui.md`](../.quiet-ui.md) (`v9.5.0`, Quiet Cockpit / ISA-101 calm monochrome with amber/crimson reserved for excursions and safety gate blocks).

## Visual & Ergonomic Contract
- **Theme Polarity Pair**:
  - **Garage Dark (`dark`)**: `#090D16` / `#0F1624` midnight canvas for dimly lit garages and night pulls.
  - **Sunlight High-Contrast (`white`)**: `#FFFFFF` Studio canvas (`>= 4.5:1` contrast) for outdoor/driveway glare readability.
- **Responsive Viewport Targets**:
  - `1280x800`: Passenger-seat Tablet HMI / laptop frame (N2 compact `<= 48px` topbar, `Spacebar` / touch bookmark trigger).
  - `390x844`: Handheld garage phone (`>= 44px` coarse touch hit targets, `0px` horizontal overflow).
  - `900x1000`: Unfolded foldable / compact tablet layout.
  - `1440x900`: Desktop engineering bench (`.va-stage[data-width="workbench"]`, collapsible N1 side rail).
- **Metrology & Controls**:
  - Drivetrain selectors group variants via semantic `<optgroup>` pickers (`Engine Block`, `Cylinder Head`, `Transaxle`, `Fuel`).
  - All plots and diff tables follow `NIST SP 811` quantity calculus (`Quantity / Unit`, e.g., `Boost / kPa`, `Pulse Width / ms`, `Mass Flow / lb·min⁻¹`).
  - SVG charts and compressor maps compile deterministically via `quiet-evidence` or `window.QuietSvgLayout`—never hand-guessed coordinates.

## Verification Gates
All changes under `ui/` or `docs/showcase/` must pass:
1. `audit_quiet_html.py`
2. `audit_workbench_probes.py`
3. `verify_visual_contract.py` (1:1 PNG captures across all 4 viewports & both themes)
