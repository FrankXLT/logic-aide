# `tests/` — Verification & Safety Gate Suite

`tests/` enforces LogicAIDE's deterministic engineering guarantees across ROM assembly, Python Behavioral Twins, telemetry processing, and Quiet UI rendering.

## Test Categories
- **Unit & Thermodynamic Tests**: Validate `load_cell_rollups` binning, dead-cell quarantine probes, `profiles/` drivetrain compatibility guards (rejecting `2.2L`/`2.5L` + `Neon ECC` combinations), and `turbo_spec.json` 4-point compressor map math.
- **SHA-256 Round-Trip ROM Proof**: Verifies that unmodified splayed `.asm` modules under `rom/` reassemble byte-for-byte to the exact SHA-256 of the reference stock `.bin` (when local inputs are present in `rom/inputs/`).
- **Python Behavioral Twin Bit-Exact Parity**: Runs GPS-stripped stimulus slices from [`tests/fixtures/`](fixtures/README.md) through both `rom/models/*_twin.py` and the `m68hc11` `$8000`–`$FFFF` simulator, asserting zero bit divergence, `< 11 ms` loop timing, and valid `$FFC0`–`$FFFF` vectors.
- **Visual Contract Tests (`.quiet-ui.md`)**: Runs `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` across `390x844`, `900x1000`, `1280x800`, and `1440x900` in **Garage Dark** and **Sunlight High-Contrast** themes.
