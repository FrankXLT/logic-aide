# `tests/` — Verification & Safety Gate Suite

`tests/` enforces LogicAIDE's deterministic engineering guarantees across ROM assembly, Python Behavioral Twins, telemetry processing, and Quiet UI rendering.

## Test Categories
- **Unit & Thermodynamic Tests (`uv run pytest -n 0`)**: Validate `load_cell_rollups` binning, `pyserial` `loop://` SCI polling & dead-cell quarantine probes (using `rom_manifest.json` offsets only), Draft 2020-12 `profiles/` 3-tier Mopar compatibility guards (`BOLT_ON_NATIVE`, `HYBRID_MACHINING_REQUIRED`, blocking `INCOMPATIBLE` pairs such as `8V` SOHC on `2.4L EDZ`), and `turbo_spec.json` 4-point compressor map math.
- **SHA-256 Round-Trip ROM Proof**: Verifies that unmodified splayed `.asm` modules under `rom/` reassemble byte-for-byte to the exact SHA-256 of the reference stock `.bin` (when local inputs are present in `rom/inputs/`).
- **Python Behavioral Twin Bit-Exact Parity**: Runs GPS-stripped stimulus slices from [`tests/fixtures/`](fixtures/README.md) through both `rom/models/*_twin.py` and the `m68hc11` `$8000`–`$FFFF` simulator, asserting zero bit divergence, `< 11 ms` loop timing, and valid `$FFC0`–`$FFFF` vectors.
- **Visual Contract Tests (`.quiet-ui.md`)**: Runs `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` across `390x844`, `900x1000`, `1280x800`, and `1440x900` in **Garage Dark** and **Sunlight High-Contrast** themes.
