## Summary
<!-- Describe the change, linked issue (`tuning_issues` ID or GitHub issue), and affected workflows (`WF-01`..`WF-06`). -->

## Verification Checklist

### Safety & Hardware Deadbolt
- [ ] **Hardware Deadbolt**: Zero code paths invoke `ostrich2`, `flash_ecu`, or open `/dev/ttyUSB*` / `COM*` for flashing. Candidate artifacts write only to `staging/candidates/<build_id>/`.
- [ ] **Immutable Baselines**: `rom/inputs/` and `rom/stock_baselines/` are untouched, and no copyrighted OEM `.bin`/FSM scans were committed.
- [ ] **GPS Privacy**: Any exported fixture or stimulus slice under `tests/fixtures/` or `staging/candidates/` has raw GPS coordinates stripped.

### ROM & Simulation Gates *(check if ROM, `.asm`, `rom_manifest.json`, or `rom/models/` touched)*
- [ ] **SHA-256 Round-Trip Proof**: Unmodified splayed `.asm` reassembles to exact baseline `.bin` SHA-256.
- [ ] **Python Twin Bit-Exact Parity**: `rom/models/*_twin.py` matches `m68hc11` assembly output bit-for-bit on stimulus fixtures.
- [ ] **Cycle Budget & Vectors**: Worst-case loop execution `< 11 ms`, `$FFC0`–`$FFFF` vectors verified, and 8-bit carry/overflow safety checked.

### Documentation & Code Budget
- [ ] **Workflow Sync**: `docs/WORKFLOWS.md` updated if user flows, CLI flags, or edge cases changed (`N/A` if unchanged).
- [ ] **File/Function Budget & LF Endings**: Modified files `<= 500` lines (`650` hard ceiling), functions `<= 100` lines, and LF (`\n`) line endings only.

### UI Visual Contract *(check if `ui/` or `docs/showcase/` touched)*
- [ ] **Quiet UI Gates Passed**: `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` pass across `390x844`, `900x1000`, `1280x800`, and `1440x900` in both **Garage Dark** and **Sunlight High-Contrast** themes.
