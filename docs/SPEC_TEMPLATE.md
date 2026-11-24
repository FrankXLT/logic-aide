# Milestone Spec Template (`docs/SPEC_TEMPLATE.md`)

Copy this template into `docs/specs/<milestone>-<slug>.md` (or a PR description) before implementing any feature, ROM calibration change, or UI module.

---

## 1. Metadata & Workflow Binding

- **Spec ID / Milestone**: `M<N>-<slug>`
- **Author / Agent**: `<human or agent role>`
- **Bound Workflow IDs (`docs/WORKFLOWS.md`)**: `[WF-01 | WF-02 | WF-03 | WF-04 | WF-05 | WF-06]`
- **Target `car_id` / Profile(s)**: `<e.g., 1989-daytona-shelby / SMEC / 93 E10>` (or `All`)
- **CLI & FastMCP Parity**:
  - CLI command: `uv run logicaide <subcommand> ...`
  - FastMCP tool (if applicable): `<ecu-telemetry | hc11-emulator | chrysler-heritage>`

## 2. Problem Statement & Scope

Describe what operator problem this milestone solves, which files/directories it touches, and what is explicitly **out of scope**.

- **Physical Drivetrain Compatibility Check** *(if hardware/profile related)*:
  - Engine Block + Cylinder Head pairing verified under Mopar 3-tier matrix (`BOLT_ON_NATIVE`, `HYBRID_MACHINING_REQUIRED` 7-item checklist, never `INCOMPATIBLE`).
  - SMEC vs SBEC 60-way Pin 11 continuity and Pin 2/Pin 3/Pin 25/Pin 45 FSM variant assignments verified before any wiring note.

## 3. Data, Telemetry & Privacy Contracts

- **Parquet `run_kind` Impact**: `[VEHICLE_SCI | SINGLE_REPLAY | MONTE_CARLO_SWEEP | N/A]`
- **`load_cell_rollups` Dimensions**: `250 RPM x 10 kPa MAP x TPS Regime x ACT`
- **Missing & Fault Semantics**:
  - Unpolled SCI parameters remain `Not in Watchlist (null)` (never coerced to `0`), with `0xF2`/`0xF4` offsets resolved strictly from `rom_manifest.json`.
  - Packet timeouts set `dead_cell_quarantine = true`.
- **GPS Privacy**: Confirm `lat`, `lon`, `altitude`, and `gps_track` are stripped from all `tests/fixtures/` and `staging/candidates/` exports.

## 4. Python Behavioral Twin & 68HC11 Parity *(Required for ROM/Sim Changes)*

- **Baseline ROM**: `<filename in rom/stock_baselines/>` (SHA-256: `<digest>`)
- **Modified `rom_manifest.json` Symbols / Addresses**: `<exact verified symbols or "None">`
- **Python Behavioral Twin Module**: `rom/models/<module>_twin.py`
  - Fixed-point scaling / Q-format: `<e.g., uint8 * 0.5 kPa>`
  - Overflow / Carry guard: `<BCS/BCC clamp behavior>`
- **68HC11 `.asm` Parity & Timing Targets**:
  - Bit-exact parity test: `uv run pytest -n 0 tests/test_<module>_parity.py`
  - Loop bus-cycle budget: `< 11 ms (< 22,000 E-clock cycles at 2.0 MHz)`
  - `$FFC0`–`$FFFF` vectors unchanged: `[Yes | Explicitly Audited]`

## 5. Operator UI & `.quiet-ui.md` Contract *(Required for `ui/` or `docs/showcase/` Changes)*

- **Theme Polarity**: **Sunlight High-Contrast** (`#FFFFFF`) & **Garage Dark** (`#090D16`)
- **Header & Layout**: `<= 48px` N2 topbar, `.va-stage[data-width="workbench"]`, N1 rail at `>= 1100px`
- **Controls & Metrology**: `<optgroup>` pickers, `NIST SP 811` (`Quantity / Unit`), `ISA-101` calm alarm palette, zero emojis, deterministic `quiet-evidence` SVGs.
- **Empty & Error States Specified**: `<list states matching docs/WORKFLOWS.md>`

## 6. Acceptance Criteria & Verification Gates

Every checked gate below must be executed after the final edit and recorded with observed results:

- [ ] **Module Budget**: All touched files `<= 500` lines (`650` hard ceiling); functions `<= 100` lines; LF line endings.
- [ ] **Unit & Parity Tests**: `uv run pytest -n 0` passes with zero skipped parity assertions.
- [ ] **Safety Gate** *(if firmware)*: Fresh-context `firmware-safety-reviewer` stamps `staging/candidates/<build_id>/REPORT.md` (Human Deadbolt intact; zero hardware flash calls).
- [ ] **UI Gates** *(if UI)*: `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` pass across `390x844`, `900x1000`, `1280x800`, and `1440x900` in both themes.
- [ ] **Docs Sync**: `docs/WORKFLOWS.md` updated if any `WF-01`..`WF-06` behavior or error state changed.
