# LogicAIDE System Architecture

LogicAIDE is structured as a **six-layer deterministic engineering pipeline** with hard trust boundaries between live vehicle hardware, simulated ROM execution, cleanroom knowledge, and human sign-off.

---

## Six-Layer Pipeline

### Layer 1 — Live 62.5k SCI Telemetry & Dead-Cell Probe (`telemetry/`)
- Polls the 1988–1993 Chrysler `62.5 kbaud` bidirectional SCI bus (read-only capture mode during road logging).
- Runs a **startup Dead-Cell Probe** that monitors raw ADC channels (`MAP`, `TPS`, `ECT`, `ACT`, `O2`, `Battery`) for flatline, rail-stuck, or out-of-range faults and quarantines suspect channels before analysis.
- Merges synchronized smartphone IMU telemetry (longitudinal/lateral acceleration) and writes **30-second Zstd-compressed Parquet chunks** under `data/drives/` (gitignored).
- Supports zero-look-away `Spacebar` / touch bookmarks that emit `VEHICLE_SCI` anomaly markers directly into the session log.

### Layer 2 — Multi-Car SQLite / Parquet Load-Cell Data Lake (`telemetry/`, `issues/`)
- Indexes Parquet chunks across multiple Turbo Chrysler chassis (`Daytona Shelby`, `Shelby CSX`, `Turbo Shadow`, `Spirit R/T`, `Omni GLH/GLHS`, turbo minivans) via DuckDB and SQLite.
- Aggregates telemetry into standardized `load_cell_rollups`:
  `250 RPM x 10 kPa MAP x TPS Regime x Charge-Temp`.
- Links anomalous cells and road bookmarks to `tuning_issues` and `optimization_campaigns` records in `issues/` so every calibration change traces back to empirical road data.

### Layer 3 — Splayed 68HC11 + Python Behavioral Twin + Sobol/Optuna Simulator (`rom/`, `emulator/`)
- **Splayed Disassembly**: Splits monolithic `32 KB` (`$8000`–`$FFFF`) SMEC/SBEC binaries into modular `.asm` files mapped by `rom_manifest.json` and indexed with `ctags`.
- **Python Behavioral Twin**: Mirrors critical fuel, spark, wastegate, and limiter routines in `rom/models/*_twin.py`. Every edit must prove **bit-exact parity** (8-bit unsigned arithmetic, carry/overflow, table interpolation) against the assembled 68HC11 code.
- **`m68hc11` Execution Harness**: Simulates `$8000`–`$FFFF` ROM execution with `$1000`–`$103F` hardware register hooks, verifying worst-case loop execution stays strictly below the **11 ms** real-time ECU cycle budget.
- **Monte Carlo Exploration**: Uses `scipy.stats.qmc` (Sobol / Latin Hypercube Sampling) and `Optuna` campaigns driven by GPS-stripped `1–3 s` road stimulus slices to explore table sensitivities without risking real hardware.

### Layer 4 — Mopar Drivetrain & Compressor Thermodynamics (`profiles/`)
- Models physical vehicle combinations across blocks (`2.2L`, `2.5L`, `2.4L`), cylinder heads (`8V SOHC`, `2.2L Lotus 16V Turbo III`, `Neon ECC DOHC` restricted strictly to `2.4L` blocks), and transaxle ratios (`A525`, `A555`, `A568`, `NV-T350`, `A413`).
- Integrates `turbo_spec.json` 4-point digitized compressor maps to compute pressure ratio, corrected mass flow (`lb·min⁻¹`), surge margin, choke boundary, and adiabatic charge-temp rise.

### Layer 5 — Heritage Hybrid FTS5 / `sqlite-vec` RAG (`knowledge/`)
- Ingests cleanroom documentation using `Trafilatura` (web archives) and `Docling` (technical documents) into gitignored `data/heritage_raw/`, then distills verified **3-tier provenance Markdown digests** into `knowledge/`.
- Combines SQLite `FTS5` lexical search (for exact hex addresses, part numbers, and SCI PIDs) with `sqlite-vec` semantic search for contextual troubleshooting.

### Layer 6 — Quiet UI v9.5.0 Workbench & Human Deadbolt Staging Pen (`ui/`, `staging/candidates/`)
- Presents telemetry, 16x16 fuel/spark diffs, compressor overlays, and heritage citations in a Quiet UI `v9.5.0` workbench (`.quiet-ui.md`) supporting **Garage Dark** and **Sunlight High-Contrast** themes at `390x844`, `900x1000`, `1280x800`, and `1440x900`.
- Outputs verified builds exclusively to `staging/candidates/<build_id>/` containing the candidate `.bin`, `REPORT.md`, checksum/vector proof, Monte Carlo trace diff, and a copyable manual flash CLI for the human owner.

---

## Trust Enclaves

| Enclave | Scope | Write Authority |
| :--- | :--- | :--- |
| **1. Hardware & Baseline Enclave** | `/dev/ttyUSB*`, `COM*`, `ostrich2`, `flash_ecu`, `rom/inputs/`, `rom/stock_baselines/` | **Human-Only** (AI strictly prohibited from writing) |
| **2. Telemetry & Data Lake Enclave** | `data/drives/`, `load_cell_rollups`, `issues/` | Automated append/rollup; GPS stripped before fixture export |
| **3. Simulation & Twin Enclave** | `rom/models/`, `rom/` splayed `.asm`, `emulator/` | AI & Human; gated by SHA-256 round-trip, twin parity, & 11 ms ceiling |
| **4. Cleanroom Knowledge Enclave** | `knowledge/` (curated Markdown), `data/heritage_raw/` (gitignored) | Curated 3-tier provenance only; zero raw copyrighted FSM commits |
| **5. Staging Pen Enclave** | `staging/candidates/<build_id>/` | AI writes candidate artifacts; Human executes final hardware flash |

---

## Architectural Invariant Checklist

- [ ] **Hardware Deadbolt intact**: No automated code path opens serial ports for flashing or invokes `ostrich2` / `flash_ecu`.
- [ ] **Baselines untouched**: `rom/inputs/` and `rom/stock_baselines/` remain unmodified and SHA-256 verified.
- [ ] **Round-trip ROM proof**: Unmodified stock `.asm` reassembles to the exact byte-for-byte SHA-256 of the input `.bin`.
- [ ] **Python Twin parity**: `rom/models/*_twin.py` matches `m68hc11` register/RAM outputs bit-for-bit on stimulus fixtures.
- [ ] **11 ms loop ceiling**: Simulated worst-case interrupt + main loop execution remains `< 11 ms`.
- [ ] **`$FFC0`–`$FFFF` vector integrity**: Reset and interrupt vectors resolve to valid ROM entrypoints.
- [ ] **Drivetrain physics guard**: `Neon ECC` DOHC head is never paired with a `2.2L` or `2.5L` block.
- [ ] **GPS stripped**: `tests/fixtures/` and `staging/candidates/` contain zero raw GPS coordinates.
- [ ] **UI contract verified**: `ui/` and `docs/showcase/` pass `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py`.
