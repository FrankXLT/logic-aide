---
name: stage-rom-candidate
description: End-to-end Python-Twin -> .asm -> AS11/Blackfoot -> m68hc11 Sobol Monte Carlo sweep -> firmware-safety-reviewer -> staging/candidates/<build_id>/REPORT.md workflow stopping at the Human Deadbolt.
---

# Stage ROM Candidate Skill

End-to-end workflow to take a verified calibration or logic change from Python Behavioral Twin through 68HC11 simulation and independent safety sign-off, stopping strictly at the **Human Deadbolt**.

## Step 1 — Baseline & Symbol Verification (`rom-disassembly-analyst`)

1. Confirm target stock ROM exists (read-only) in `rom/stock_baselines/` and matches `rom/rom_manifest.json`.
2. Record the clean unmodified baseline git ref and run `uv run logicaide sim verify-sha256` via `AS11` / Blackfoot to prove the baseline `.asm` snapshot reproduces the exact stock SHA-256 hash before edits begin.
3. Never touch `rom/inputs/` or `rom/stock_baselines/`.

## Step 2 — Python Behavioral Twin First (`rom/models/*_twin.py`)

1. Implement and unit-test the proposed fuel, spark, or boost change in `rom/models/*_twin.py` (`uv run pytest`).
2. Verify mechanical bounds against `profiles/<car_id>.json` (`93 E10` vs `E85`, boost ceiling, drivetrain compatibility guard).

## Step 3 — `.asm` Implementation & Bit-Exact Parity (`hc11-emulator-tester`)

1. Apply the corresponding minimal edit to splayed 68HC11 `.asm` modules under `rom/src/` and regenerate `tags` (`ctags` via `uv run logicaide sim manifest-sync`).
2. Assemble with `AS11` / Blackfoot into `staging/candidates/<build_id>/` and run `uv run logicaide sim parity --build <build_id>` to prove bit-exact parity with `rom/models/*_twin.py`.

## Step 4 — Deterministic Replay & Sobol Monte Carlo Sweep

1. Run `SINGLE_REPLAY` against relevant GPS-stripped `tests/fixtures/*.parquet` slices.
2. Run `uv run logicaide sim sweep --build <build_id> --method sobol` (`scipy.stats.qmc` Sobol, plus `Optuna` if optimizing bounded surfaces) logging outputs to Parquet (`run_kind = 'MONTE_CARLO_SWEEP'`).

## Step 5 — Fresh-Context Safety Gate (`firmware-safety-reviewer`)

Invoke read-only `firmware-safety-reviewer` in a fresh context against the clean baseline proof and `staging/candidates/<build_id>/` artifacts to produce the structured gate verdict stamped into `staging/candidates/<build_id>/REPORT.md`:
- SHA-256 clean baseline proof, candidate `32768`-byte integrity & OEM checksum validity
- `$FFC0`–`$FFFF` vector table diff
- `< 11 ms` (`< 22,000` E-clock cycles) worst-case loop ceiling
- 8-bit carry overflow / stack safety and boost/spark clamps
- Zero GPS leaks in candidate fixtures

## Step 6 — Human Deadbolt Stop

- If any check fails, stamp `staging/candidates/<build_id>/REPORT.md` with **`SAFETY GATE BLOCKED`** and halt.
- If all checks pass, stamp **`PASS`** in `staging/candidates/<build_id>/REPORT.md` with a **copyable manual CLI block** for human review and physical flashing. **Never execute `ostrich2`, `flash_ecu`, or serial writes.**
