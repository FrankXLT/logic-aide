# AGENTS.md — Multi-Agent & IDE-Neutral Operating Contract

LogicAIDE is intentionally **agent- and IDE-neutral**. Whether operated from **Claude Code**, **Antigravity**, **Cursor**, **Codex CLI**, or **VS Code Copilot**, all coding agents share the same CLI-first tooling (`uv run logicaide ...`), FastMCP servers, `.quiet-ui.md` visual contract, and Hardware Deadbolt safety boundaries.

## 1. Universal Safety & Ground-Truth Rules
- **Hardware Deadbolt**: All agents are strictly read-only with respect to live vehicle hardware flashing (`ostrich2`, `flash_ecu`, `/dev/ttyUSB*`, `COM*`) and stock ROM baselines (`rom/inputs/`, `rom/stock_baselines/`). All candidate builds land in `staging/candidates/<build_id>/` with checksums, `$FFC0`–`$FFFF` vector checks, `<= 11 ms` bus-cycle proofs, and Sobol Monte Carlo logs.
- **Never Guess Hex Offsets or Pinouts**: Cite verified `rom_manifest.json` symbols, `docs/HARDWARE_AND_SCI_PROTOCOL.md`, or `Tier 1`/`Tier 2` heritage sources in `knowledge/`. Return `No Verified Source Match` rather than inventing RAM addresses or pin assignments.
- **Physical Drivetrain Compatibility Guard**: Never combine a vintage `2.2L`/`2.5L` K-car block with a Neon `ECC` 2.0L DOHC cylinder head (`2.2`/`2.5` blocks use 8V SOHC or 2.2L Lotus 16V Turbo III heads; `ECC`/`EDZ` DOHC swaps pair with the `2.4L` iron block).

## 2. CLI-First + FastMCP Dual Interface
Every core subsystem exposes both a deterministic CLI command (`uv run logicaide <subcommand>`) and an optional FastMCP server (`mcp_servers/`) so non-MCP agents and scripts can run identical checks:
- `uv run logicaide telemetry ...` <-> `ecu-telemetry` FastMCP
- `uv run logicaide sim ...` <-> `hc11-emulator` FastMCP
- `uv run logicaide heritage ...` <-> `chrysler-heritage` FastMCP

## 3. Specialized Agent Roles (`.claude/agents/`)
- **`rom-disassembly-analyst`**: Maps splayed 68HC11 `.asm` modules, `rom_manifest.json`, and Python Behavioral Twins (`rom/models/*_twin.py`).
- **`telemetry-duckdb-analyst`**: Queries 30s Zstd Parquet chunks, `load_cell_rollups` (`250 RPM x 10 kPa MAP x TPS Regime x Charge-Temp`), and linked `tuning_issues`.
- **`hc11-emulator-tester`**: Executes deterministic `$8000`–`$FFFF` `m68hc11` replays, Python-Twin bit-exact parity assertions, and `scipy.stats.qmc` Sobol / `Optuna` Monte Carlo sweeps.
- **`firmware-safety-reviewer`**: Fresh-context final safety gate verifying SHA-256 checksums, `$FFC0`–`$FFFF` vectors, `< 11 ms` loop timing, 8-bit carry overflow safety, and mechanical boost/spark clamps before staging sign-off.
- **`ui-visual-reviewer`**: Enforces `.quiet-ui.md` (`v9.5.0`), `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` across `390px`, `900px`, `1280x800`, and `1440px` in both **Garage Dark** and **Sunlight High-Contrast** themes.

## 4. Module & Navigation Discipline
- Keep modular code, config, and technical doc files `<= 500` lines (`650` hard ceiling) and functions `<= 100` lines (self-contained bundled HTML artifacts with inline `quiet-evidence` SVGs under `docs/showcase/` are compiled outputs exempt from the source cap).
- Generate `ctags` from `rom_manifest.json` so any IDE or agent can jump straight to 68HC11 labels and Python twin counterparts without scanning monolithic binaries.
- Keep `docs/WORKFLOWS.md` synchronized whenever workflows `WF-01`..`WF-06` evolve.
