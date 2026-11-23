# `emulator/` — `m68hc11` Cycle-Accurate Harness & Sobol/Optuna Sweeps

`emulator/` executes 68HC11 SMEC/SBEC firmware deterministically against road-recorded stimulus slices and explores calibration adjustments via Monte Carlo campaigns.

## Core Components
- **`m68hc11` `$8000`–`$FFFF` Execution Harness**: Loads assembled `32 KB` ROM images into emulated Motorola `68HC11` memory space with cycle-accurate timing telemetry.
- **`$1000`–`$103F` Hardware Register Hooks**: Models on-chip 68HC11 I/O registers (timers, ADC `ADR1`–`ADR4`, SCI serial registers, ports) so road-logged sensor states drive the real ECU binary cleanly.
- **11 ms Real-Time Cycle Budget Gate**: Asserts that worst-case interrupt service routines plus main-loop execution remain strictly below **11 ms** so real-world ECU scheduling never overruns.
- **Sobol / LHS & Optuna Monte Carlo Campaigns**:
  - Uses `scipy.stats.qmc` (Sobol sequences and Latin Hypercube Sampling) to sample multi-dimensional table perturbations across GPS-stripped `1–3 s` stimulus slices.
  - Uses `Optuna` multi-objective optimization to minimize AFR error, knock-retard events, and transient stumble while honoring mechanical boost/spark safety clamps.
- **Python Twin Bit-Exact Parity Runner**: Verifies every simulated run against `rom/models/*_twin.py` with zero bit divergence.

## Interfaces
- CLI: `uv run logicaide sim ...`
- FastMCP: `hc11-emulator` (`mcp_servers/`)
