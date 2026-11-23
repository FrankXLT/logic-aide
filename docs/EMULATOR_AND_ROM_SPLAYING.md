# Emulator, ROM Splaying & Python Behavioral Twin Specification

> **Safety Gate Notice**: `rom/inputs/` and `rom/stock_baselines/` are strictly **immutable**. Candidate binaries emitted by the assembler pipeline MUST remain in `staging/candidates/<build_id>/` until SHA-256, `$FFC0`–`$FFFF` vector checks, Python-Twin bit-exact parity, `<= 22,000` E-cycle timing, and Sobol/Optuna sweep gates all pass (`AGENTS.md`).

---

## 1. 32 KB (`$8000`–`$FFFF`) ROM Splaying Architecture

Monolithic `32,768`-byte SMEC and SBEC binaries (`27C256` EPROM image mapped at `$8000`–`$FFFF`) are decomposed ("splayed") into cohesive, modular assembly files (`<= 500` lines per file) under `rom/src/` with a companion `rom_manifest.json`.

### 1.1 Dual Splaying Ingestion Paths
1. **Turbonator Relocatable Source Path**:
   - Ingests community **Turbonator** `.asm` source modules and `.tpl` calibration templates directly into modular `rom/src/` units, preserving labels, equate tables, and conditional assembly flags.
2. **Ghidra 68HC11 Disassembly Path (Stock OEM SMEC/SBEC)**:
   - Disassembles immutable factory binaries from `rom/stock_baselines/` using Ghidra's Motorola `68HC11` processor specification (`$8000` base, big-endian).
   - Separates verified code subroutines, 1D/2D calibration lookup tables, and `$FFC0`–`$FFFF` vector pointers into deterministic `ORG`-pinned or section-linked `.asm` modules.

### 1.2 Modular Source Layout (`rom/src/`)

| Module File | Address / Responsibility | Primary Tunable & Functional Scope |
| :--- | :--- | :--- |
| **`vectors.asm`** | `$FFC0`–`$FFFF` | Reset (`$FFFE`), COP/Clock Monitor, SCI (`$FFD6`), Timer IC/OC, and IRQ vector table. |
| **`fuel_tables.asm`** | Fuel & Injection Subsystem | `PEFTBL` (`16x16` RPM $\times$ MAP fuel tables), AE/DE transients, cranking pulse width, battery deadtime table, overboost fuel-cut. |
| **`spark_tables.asm`** | Ignition Timing Subsystem | Base spark advance maps, boost retard, `CTS`/`ACT` temperature trim, knock retard/recovery ramps. |
| **`boost_vnt.asm`** | Boost Control Subsystem | Wastegate solenoid / VNT duty-cycle lookup and closed-loop PID trim routines. |
| **`sci_diag.asm`** | Serial & Diagnostics | `7812.5` baud echoed commands (`0x10`–`0xFE`), `0x12` baud switch, `62,500` baud `0xF2`/`0xF4` RAM table dispatcher. |

---

## 2. Cross-Assembly & Mandatory SHA-256 Round-Trip Gate

LogicAIDE supports deterministic 68HC11 cross-assembly via any of three validated assemblers:
- **`AS11`**: Classic Motorola freeware 68HC11 cross-assembler (reference syntax for Turbonator builds).
- **`Blackfoot`**: Open-source modern replacement for `AS11` supporting identical 68HC11 mnemonics and S19/binary output.
- **`A09`**: Macro cross-assembler configured in 68HC11 target mode for structured macro builds.

### SHA-256 Byte-Identical Round-Trip Gate
Before **any** tuning modification or behavioral twin divergence is permitted on a newly splayed ROM:
1. Assemble the unmodified splayed tree (`rom/src/main.asm` including the 5 subsystem modules) into a flat `32,768`-byte `.bin` image.
2. Compute `sha256(assembled_bin)` and assert exact equality with `sha256(rom/stock_baselines/<baseline>.bin)`.
3. **Hard Failure Rule**: Even a 1-byte mismatch or shifted padding byte fails the gate (`ROUNDTRIP_SHA256_MISMATCH`), blocking downstream simulation or staging until labels/directives match the original binary byte-for-byte.

---

## 3. `rom_manifest.json` Schema & Automatic `ctags`

Every splayed ROM workspace defines a canonical `rom/rom_manifest.json` linking hex addresses, table geometry, `0xF2`/`0xF4` SCI watchlist cells, and Python Behavioral Twin targets:

```json
{
  "manifest_version": "1.0.0",
  "rom_id": "1989_smec_t2_stock",
  "ecu_family": "SMEC_DUAL_BOARD",
  "rom_size_bytes": 32768,
  "base_address": "0x8000",
  "baseline_sha256": "<64-hex-sha256>",
  "assembler": "blackfoot",
  "memory_regions": {
    "internal_ram": { "start": "0x0000", "end": "0x01FF" },
    "io_registers": { "start": "0x1000", "end": "0x103F" },
    "rom_code":     { "start": "0x8000", "end": "0xFFBF" },
    "vectors":      { "start": "0xFFC0", "end": "0xFFFF" }
  },
  "tables": [
    {
      "symbol": "PEFTBL",
      "address": "<hex_rom_addr_0x8000_0xFFBF>",
      "rows": 16,
      "cols": 16,
      "element_type": "uint8",
      "row_axis": { "quantity": "Engine Speed", "unit": "min^-1" },
      "col_axis": { "quantity": "Manifold Absolute Pressure", "unit": "kPa" },
      "source_file": "rom/src/fuel_tables.asm",
      "twin_target": "rom/models/fuel_twin.py:eval_peftbl"
    }
  ],
  "ram_watchlist": [
    {
      "sci_table": "0xF4",
      "offset_hex": "<verified_offset_hex>",
      "ram_address": "<verified_ram_addr>",
      "column_name": "map_kpa",
      "raw_type": "uint8",
      "quantity": "Manifold Absolute Pressure",
      "unit": "kPa",
      "scale_expr": "<verified_scale_expr>",
      "source_verified": true
    }
  ],
  "symbols": [
    {
      "name": "CALC_FUEL_PW",
      "address": "<hex_rom_addr_0x8000_0xFFBF>",
      "source_file": "rom/src/fuel_tables.asm",
      "line": "<line_number>",
      "twin_target": "rom/models/fuel_twin.py:calc_fuel_pw"
    }
  ]
}
```

### Automatic `ctags` Symbol Index
- Whenever `uv run logicaide sim manifest-sync` runs, LogicAIDE regenerates the gitignored `tags` file merging:
  1. Assembly labels and table symbols from `rom/src/*.asm` + `rom_manifest.json` addresses.
  2. Typed Python Behavioral Twin functions in `rom/models/*_twin.py`.
- Both human IDEs and coding agents use `tags` to jump directly between a 68HC11 label (e.g., `CALC_FUEL_PW`) and its Python twin (`calc_fuel_pw`) without scanning 32 KB hex dumps.

---

## 4. Python Behavioral Twin (`rom/models/*_twin.py`)

Every tunable 68HC11 subroutine under `rom/src/*.asm` maintains a **1-to-1 typed Python Behavioral Twin** (`rom/models/fuel_twin.py`, `spark_twin.py`, `boost_twin.py`):

- **Exact Fixed-Point Semantics**: Python twins operate on explicit `uint8` (`0..255`) and `uint16` (`0..65535`) integer arithmetic, matching 68HC11 `MUL` (`A * B -> D`), `IDIV` (`D / X -> X`, rem `D`), `FDIV`, carry/borrow flags, and saturating table clamps rather than floating-point approximations.
- **Python-First Prototyping Workflow**:
  1. Implement or refine the calibration algorithm (e.g., E85 deadtime scaling, boost retard curve, VNT PID clamp) in `rom/models/*_twin.py`.
  2. Validate behavior with fast `pytest` unit tests and sweep visualizations before touching `.asm`.
  3. Port the verified math into `rom/src/*.asm` and assemble the candidate binary.
  4. **Bit-Exact Parity Assertion**: The `m68hc11` harness executes the compiled `.asm` subroutine across an exhaustive stimulus vector matrix and asserts `twin_result == hc11_result` (`0` LSB tolerance) for every output byte and condition flag.

---

## 5. `m68hc11` Execution Harness & 11 ms Bus-Cycle Budget

LogicAIDE uses the Python **`m68hc11`** (`HC11`) cycle-accurate CPU emulator (`anarkiwi/m68hc11`) for deterministic closed-loop verification:

### 5.1 Memory Map & Peripheral Hooks
- **ROM Image (`$8000`–`$FFFF`)**: Loads the `32,768`-byte candidate `.bin` directly into `0x8000..0xFFFF`.
- **Peripheral & ADC Register Block (`$1000`–`$103F`)**:
  - Hooks memory reads/writes across the 64-byte register window (`PORTA`–`PORTE`, `ADCTL`/`ADR1`–`ADR4` at `$1030`–`$1034`, `BAUD`/`SCCR1`/`SCCR2`/`SCSR`/`SCDR` at `$102B`–`$102F`, `TCNT`/`TOC1`–`TOC5`).
  - Injects deterministic sensor stimulus (`MAP`, `TPS`, `CTS`, `ACT`, `O2`, `BATT_V`, crank/cam RPM ticks) directly into ADC result registers and timer capture latches.

### 5.2 `$FFC0`–`$FFFF` Interrupt & Reset Vector Gate
Before running any loop simulation, the harness inspects all 32 vector words in `$FFC0`–`$FFFF`:
- Reset vector (`$FFFE`–`$FFFF`) and all active ISR vectors (SCI, Timer Overflow, Input Capture, Output Compare, RTI, COP) MUST point inside valid ROM code boundaries (`$8000`–`$FFBF`) and resolve to valid opcode entry points (never `$0000`, `$FFFF`, or mid-table data).

### 5.3 Strict 11 ms Main-Loop Bus-Cycle Budget
- Motorola 68HC11 clock divider: `8.0 MHz` external crystal $\div 4 = \mathbf{2.0\text{ MHz}}$ **E-clock** ($0.5\text{ }\mu\text{s}$ per bus E-cycle).
- Maximum main-loop wall-clock ceiling: **`11.0 ms`**:
  $$\text{Max E-Cycles} = 0.011\text{ s} \times 2{,}000{,}000\text{ cycles/s} = \mathbf{22{,}000\text{ E-cycles}}$$
- The harness records `cpu.cycles` across worst-case branch conditions (high RPM, knock event + AE enrichment + boost PID + SCI poll active simultaneously). Any build exceeding `22,000` E-cycles fails the safety gate (`LOOP_BUDGET_EXCEEDED`).

---

## 6. Monte Carlo Sweep Engine (`scipy.stats.qmc` + `Optuna`)

Single road logs never cover every corner of intake heat, voltage sag, and transient sensor noise. LogicAIDE executes multi-thousand-point stress campaigns prior to candidate sign-off:

- **Samplers**:
  - **`scipy.stats.qmc.Sobol`** (power-of-two low-discrepancy space-filling sequences) and **`LatinHypercube`** for uniform coverage of multi-dimensional calibration corners.
  - **`Optuna`** adaptive samplers (`TPESampler` / `QMCSampler`) to hunt worst-case 8-bit boundary margins, minimum cycle headroom, or target AFR/knock objectives.
- **Perturbation Dimensions**:
  1. **Intake Heat-Soak (`ACT`)**: `0 °C` to `+95 °C` rapid soak transitions testing charge-temp compensation clamps.
  2. **Battery Voltage Sag (`9.5 V`–`15.0 V`)**: Alternator/cranking sag stressing injector deadtime (`batt_deadtime_ms`) and dwell tables.
  3. **MAP Sensor Jitter & Steps**: High-frequency `±10 kPa` pulsation and rapid tip-in steps (`30 -> 240 kPa`) stressing AE/DE math and boost-cut hysteresis.
  4. **RPM & CTS Extremes**: Cold start (`-20 °C`) to redline (`7,200 min⁻¹`) table edge indices.
- **Overflow & Defect Detection**:
  - Flags any **8-bit carry/wrap overflow** (`255 -> 0` or `0 -> 255` wrap in pulse width, spark advance, or duty cycle), unexpected fuel-cut flicker, or `> 22,000` E-cycle spike.
  - Streams synthetic telemetry as `run_kind = MONTE_CARLO_SWEEP` 30s Zstandard Parquet chunks, writes campaign metadata to `optimization_campaigns`, and auto-files linked `tuning_issues` rows on any safety violation.
- **Staging Output**: Passing builds land solely in `staging/candidates/<build_id>/` alongside their `SHA-256`, vector report, parity log, cycle-budget proof, and Monte Carlo summary.
