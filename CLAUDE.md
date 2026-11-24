# LogicAIDE (`CLAUDE.md`)

**LogicAIDE** (*Logic Module + Agentic IDE*) is an offline telemetry, splayed 68HC11 SMEC/SBEC ROM simulation, modular Mopar drivetrain & turbo thermodynamics, and Chrysler heritage RAG workspace.

## Repository Phase & Milestone 1–5 Build Order

- **Current Phase — `Phase 0` Complete**: Authoritative architecture & protocol specs (`docs/`), Draft 2020-12 vehicle build schema + examples (`profiles/`), `.claude/` Deadbolt harness, and static offline showcase (`docs/showcase/index.html`) are complete. **No Python package (`src/logicaide/`) exists yet — build Milestones 1–5 strictly in order:**
  1. **M1 — CLI, Vehicle Schema & Catalog**: `pyproject.toml`, `src/logicaide/` CLI, `logicaide profile compile` (Draft 2020-12 validator + Mopar 3-tier guard), SQLite `vehicles` & `vehicle_profile_revisions`, and `rom_manifest.json` loader.
  2. **M2 — 62.5k SCI Poller & Parquet Lake**: `telemetry/` `62.5k` SCI poller (tested via `pyserial` `loop://`), startup dead-cell quarantine (`null != 0`), 30s Zstd Parquet chunks, DuckDB `load_cell_rollups`, and `tuning_issues` bookmarks.
  3. **M3 — Behavioral Twins, `m68hc11` & Staging**: `rom/models/*_twin.py` fixed-point twins, `m68hc11` `$8000`–`$FFFF` harness (`<= 11 ms` / `<= 22,000` E-cycles & `$FFC0`–`$FFFF` vectors), `AS11`/`Blackfoot` SHA-256 proof, `scipy.stats.qmc` Sobol sweeps, and `staging/candidates/<build_id>/REPORT.md`.
  4. **M4 — Heritage Hybrid RAG**: `warcio`+`Trafilatura`/`Crawl4AI` web & `Docling`+`Marker`/`Surya` OCR ingestion into `data/heritage_raw/`, `mmdlint`/OCR Mermaid gate (`VERIFIED` vs `NEEDS_HUMAN_REVIEW`), 2-line scope-prefixed parent-child `FTS5`+`sqlite-vec` Trust-Weighted RRF, and `No Verified Source Match`.
  5. **M5 — FastMCP & Quiet UI Workbench**: `mcp_servers/` thin wrappers (`ecu-telemetry`, `hc11-emulator`, `chrysler-heritage`), root `.mcp.json` activation, and Quiet UI `v9.5.0` workbench (`ui/`).
- Full acceptance criteria: [`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md).

## Non-Negotiable Safety Invariants
1. **Human-Only Hardware Deadbolt**: Never invoke `ostrich2`, `flash_ecu`, `boostbutton`, `mptun`, `minipro`, `xgpro`, `stm32flash`, or access physical `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*`, `/dev/cu.*`, or `COM*`. (`pyserial` `loop://` offline unit tests are allowed.) Candidate builds go solely to `staging/candidates/<build_id>/`.
2. **Immutable Baselines & GPS Privacy**: Never modify `rom/inputs/` or `rom/stock_baselines/`. Strip raw GPS (`lat`/`lon`/`altitude`/`gps_track`) before exporting `tests/fixtures/` or `staging/candidates/`.
3. **Manifest-Only SCI RAM Polling**: Never hardcode `0xF2`/`0xF4` RAM offsets across SMEC vs. SBEC calibration IDs — resolve every polled offset exclusively from `rom_manifest.json` (`No Verified Source Match` if absent).
4. **Pin 11 & Harness Variant Gate**: Never instruct applying `+12V`/`+21V` to 60-way Pin 11 without SMEC-vs-SBEC board ID and ohmmeter ground-isolation clearance; verify Pin 2/Pin 3 (`CTS` vs. early `Knock`) and Pin 25/Pin 45 (`SCI`).
5. **Mopar 3-Tier Compatibility Guard**: All Chrysler `2.2L`, `2.5L`, `2.0L ECC`, and `2.4L EDZ` blocks share `4.00 in` (`101.6 mm`) bore spacing (`87.5 mm` bore). Classify pairings as `BOLT_ON_NATIVE`, `HYBRID_MACHINING_REQUIRED` (amber swap checklist), or `INCOMPATIBLE` (hard block).

## Modular Engineering & Test Standards
- **Sequential Single-Process Tests**: Run `uv run pytest -n 0` to prevent `xdist` workers from racing SQLite locks, `m68hc11` state, or `pyserial` `loop://` streams.
- **Workflows & Twins**: Keep `docs/WORKFLOWS.md` current (`WF-01`..`WF-06`). Prototype ROM changes in `rom/models/*_twin.py` and prove bit-exact parity before staging.
- **Budget & UI Gates**: Keep source files `<= 500` lines (`650` hard ceiling), functions `<= 100` lines, and LF line endings. UI changes must pass `audit_quiet_html.py`, `audit_workbench_probes.py`, and `verify_visual_contract.py` at `390x844`, `900x1000`, `1280x800`, and `1440x900` across both themes.

## Repository Map
- `docs/`: Authoritative specs (`IMPLEMENTATION_ROADMAP.md`, `WORKFLOWS.md`, `VEHICLE_PROFILE_SCHEMA.md`, `HARDWARE_AND_SCI_PROTOCOL.md`, `EMULATOR_AND_ROM_SPLAYING.md`, `DATA_MODEL_AND_MCP.md`, `DRIVETRAIN_AND_TURBO_THERMODYNAMICS.md`, `HERITAGE_KNOWLEDGE_AND_RAG.md`, `showcase/index.html`)
- `profiles/`: Draft 2020-12 `schema/vehicle_build.schema.json`, `examples/`, and `turbo_spec.json`
- `rom/` & `emulator/`: Splayed `.asm`, `rom_manifest.json`, Python twins, and `m68hc11` harness
- `telemetry/`, `issues/`, `knowledge/`, `mcp_servers/`, `ui/`, `staging/candidates/`
