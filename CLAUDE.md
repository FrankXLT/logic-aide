# LogicAIDE (`CLAUDE.md`)

**LogicAIDE** (*Logic Module + Agentic IDE*) is an offline telemetry, splayed 68HC11 SMEC/SBEC ROM simulation, modular Mopar drivetrain & turbo thermodynamics, and Chrysler heritage RAG workspace.

## Non-Negotiable Safety Invariant — Human-Only Hardware Deadbolt
1. **Zero Hardware Flashing by AI**: Never invoke `ostrich2`, `flash_ecu`, serial bootloaders, or write directly to `/dev/ttyUSB*` / `COM*`. Candidate ROMs go strictly to `staging/candidates/<build_id>/` for manual human verification and flashing.
2. **Immutable Baselines**: Never modify `rom/inputs/` or `rom/stock_baselines/`.
3. **Pin 11 (+12V/+21V vs. Ground) & Harness Variants**: Never instruct applying `+12V`/`+21V` to 60-way Pin 11 without SMEC-vs-SBEC board ID and ohmmeter clearance; verify Pin 2/Pin 3 (`CTS` vs. early `Knock`) and Pin 25/Pin 45 (`SCI`) against FSM diagrams.
4. **GPS Privacy**: Strip raw GPS coordinates before exporting `tests/fixtures/` or `staging/candidates/` stimulus slices.

## Modular Engineering Standards
- **Workflows Source of Truth**: Keep `docs/WORKFLOWS.md` (`WF-01`..`WF-06`) current whenever user flows or edge-case states change.
- **Python Behavioral Twin First**: Prototype ROM algorithm changes in readable Python (`rom/models/*_twin.py`) and verify bit-exact parity against 68HC11 `.asm` in `m68hc11` before staging.
- **File & Function Budget**: Keep source files `<= 500` lines (`650` hard ceiling) and functions `<= 100` lines. Use CLI-first entrypoints (`uv run logicaide ...`) alongside FastMCP tools.
- **UI Verification**: UI changes under `ui/` or `docs/showcase/` must comply with `.quiet-ui.md` and pass `audit_quiet_html.py` + `verify_visual_contract.py` at `390px`, `900px`, `1280x800`, and `1440px` across both themes.

## Repository Map
- `docs/`: Authoritative specs (`WORKFLOWS.md`, `HARDWARE_AND_SCI_PROTOCOL.md`, `EMULATOR_AND_ROM_SPLAYING.md`, `DATA_MODEL_AND_MCP.md`, `DRIVETRAIN_AND_TURBO_THERMODYNAMICS.md`, `HERITAGE_KNOWLEDGE_AND_RAG.md`, `showcase/index.html`)
- `rom/` & `emulator/`: Splayed `.asm`, `rom_manifest.json`, Python twins, and `m68hc11` harness
- `telemetry/`, `issues/`, `profiles/`: 62.5k SCI Parquet lake, `tuning_issues`, `turbo_spec.json` & drivetrain builders
- `knowledge/` & `staging/candidates/`: Curated heritage RAG docs & safety-gated `.bin` staging pen
