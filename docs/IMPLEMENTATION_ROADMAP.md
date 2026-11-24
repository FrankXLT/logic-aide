# LogicAIDE Implementation Roadmap (`docs/IMPLEMENTATION_ROADMAP.md`)

LogicAIDE boots from a **completed `Phase 0` architecture & contract baseline** into a strictly ordered **5-milestone Python, simulation, RAG, and Workbench implementation (`M1`–`M5`)**. Any new coding session (Claude Code, Antigravity, Cursor, Codex CLI, or VS Code Copilot) should verify `Phase 0` artifacts first, then build milestones sequentially with **`uv run pytest -n 0`** (single-process test execution preventing SQLite, `pyserial` `loop://`, or `m68hc11` concurrency races).

---

## `Phase 0` — Completed Baseline (Already Committed)
- **Safety & Harness Contracts**: `.claude/settings.json` Hardware Deadbolt (`pyserial` `loop://` allowed; physical `/dev/ttyUSB*`/`COM*` and programmers denied), `.claude/agents/*.md`, `.claude/rules/*.md`, `.claude/skills/*/SKILL.md`, `SECURITY.md`, `AGENTS.md`, `CLAUDE.md`.
- **Engineering Specifications**: `ARCHITECTURE.md`, `docs/WORKFLOWS.md`, `docs/HARDWARE_AND_SCI_PROTOCOL.md`, `docs/EMULATOR_AND_ROM_SPLAYING.md`, `docs/DATA_MODEL_AND_MCP.md`, `docs/DRIVETRAIN_AND_TURBO_THERMODYNAMICS.md`, `docs/HERITAGE_KNOWLEDGE_AND_RAG.md`, `docs/VEHICLE_PROFILE_SCHEMA.md`.
- **Schemas & Showcase**: Draft 2020-12 `profiles/schema/vehicle_build.schema.json`, example vehicle profiles (`profiles/examples/`), `.quiet-ui.md` (`v9.5.0`), and zero-dependency offline showcase `docs/showcase/index.html`.
- **Not Yet Implemented**: `pyproject.toml`, `src/logicaide/`, runnable `uv run logicaide ...` subcommands, `mcp_servers/*.py`, `ui/` workbench application, and `tests/*.py`.

---

## Milestone 1 — Core Package, Vehicle Schema Compiler & SQLite Catalog (`M1`)
- **Primary Deliverables**:
  - `pyproject.toml` (Python `>= 3.11`, `jsonschema`, `typer` or `click`, `pydantic`, `pyserial`, `duckdb`, `pyarrow`, `scipy`, `optuna`, `pytest`, console script `logicaide = "logicaide.cli:main"`).
  - `src/logicaide/` cohesive domain modules (`config/`, `profiles/`, `catalog/`, `rom/manifest.py`, `cli.py`, each `<= 500` lines, functions `<= 100` lines).
  - `uv run logicaide profile compile <profile.json>`: validates against `profiles/schema/vehicle_build.schema.json` (`Draft202012Validator`), evaluates the Mopar 3-tier compatibility matrix (`BOLT_ON_NATIVE`, `HYBRID_MACHINING_REQUIRED` checklist, `INCOMPATIBLE` block), computes effective fuel-rail flow scaling (`sqrt(P_rail / P_rated)` + specific gravity), and writes immutable revision snapshots to SQLite `vehicles` and `vehicle_profile_revisions`.
  - `rom_manifest.json` loader + `ctags` generator (`uv run logicaide sim manifest-sync`) enforcing **manifest-only `0xF2`/`0xF4` RAM offset resolution** (never hardcoding offsets across SMEC vs. SBEC masks).
- **Acceptance Gate**: `uv run pytest -n 0 tests/test_profiles.py tests/test_catalog.py tests/test_manifest.py` passes covering all 3 Mopar compatibility tiers, `<= 8160 RPM` ceiling enforcement, and SQLite revision lineage.

---

## Milestone 2 — 62.5k SCI Poller (`loop://`), Dead-Cell Quarantine & DuckDB Lake (`M2`)
- **Primary Deliverables**:
  - `src/logicaide/telemetry/` (`sci_poller.py`, `dead_cell.py`, `parquet_chunker.py`, `duckdb_lake.py`, `issues.py`).
  - `uv run logicaide telemetry watch`: read-only `7812.5` baud `0x12` handshake -> `62,500` baud zero-echo poller driven strictly by `rom_manifest.json` `ram_watchlist` (`0xF2`/`0xF4`). Tested end-to-end against `pyserial` `serial.serial_for_url("loop://")` mock ECU responders (zero physical `/dev/tty*` access in tests).
  - Startup Dead-Cell Probe (`12 ms` timeout, `2` startup misses or `3` runtime timeouts -> `QUARANTINED`), preserving explicit `Not in Watchlist (null)` vs. `dead_cell_quarantine = true` in 30-second Zstd Parquet chunks (`data/drives/run_kind=.../`).
  - DuckDB `load_cell_rollups` view (`250 RPM x 10 kPa MAP x TPS Regime x Charge-Temp`), `Spacebar` / touch `tuning_issues` bookmarks, and `uv run logicaide telemetry export-fixture` strictly stripping `lat`, `lon`, `altitude`, and `gps_track`.
- **Acceptance Gate**: `uv run pytest -n 0 tests/test_sci_loop.py tests/test_lake_rollups.py` proves `loop://` handshake/quarantine recovery, `null != 0` preservation, and zero-GPS fixture export.

---

## Milestone 3 — Python Behavioral Twins, `m68hc11` Harness, Sobol Sweeps & Staging (`M3`)
- **Primary Deliverables**:
  - `rom/models/` fixed-point Python Behavioral Twins (`fuel_twin.py`, `spark_twin.py`, `boost_twin.py`) implementing exact 8-bit/16-bit Q-format arithmetic and carry saturation clamps.
  - `src/logicaide/sim/` (`splay.py`, `assembler.py`, `hc11_runner.py`, `monte_carlo.py`, `safety_gate.py`).
  - `AS11` / `Blackfoot` round-trip SHA-256 verification against `rom/stock_baselines/` (never mutating `rom/inputs/` or `rom/stock_baselines/`).
  - `m68hc11` `$8000`–`$FFFF` execution harness with `$1000`–`$103F` peripheral hooks proving bit-exact Python-Twin parity, `$FFC0`–`$FFFF` vector integrity, and worst-case loop duration **`<= 11.0 ms`** (`<= 22,000` E-cycles @ `2.0 MHz`).
  - `scipy.stats.qmc.Sobol` + `Optuna` perturbation sweeps logging `MONTE_CARLO_SWEEP` Parquet chunks and emitting `staging/candidates/<build_id>/REPORT.md` (with `PASS` manual CLI block or `SAFETY GATE BLOCKED`).
- **Acceptance Gate**: `uv run pytest -n 0 tests/test_twin_parity.py tests/test_hc11_gate.py` passes with 0-LSB parity divergence, `< 22,000` E-cycles, and active Hardware Deadbolt blocks.

---

## Milestone 4 — Heritage Scraping, Verified Schematics & Hybrid RAG (`M4`)
- **Primary Deliverables**:
  - `src/logicaide/heritage/`:
    - `crawl_warc.py`: `1 req/s` polite `warcio` `.warc.gz` archival capture into gitignored `data/heritage_raw/`.
    - `parse_web.py`: `lxml` `<pre>`/`<table>` -> GFM pipe-table normalizer + `Trafilatura` prose extraction + `Crawl4AI` multi-page thread stitcher.
    - `parse_docs.py`: `Docling` `TableFormer` + `300 DPI` figure crops + cross-page table stitcher + `Marker`+`Surya` OCR fallback for engineer margin annotations.
    - `mermaid_gate.py`: `mmdlint` AST Gate 4a (`<= 2` repair retries) + Page-OCR Token Grounding Gate 4b emitting `VERIFIED` vs. `NEEDS_HUMAN_REVIEW` with 3–5 bullet walkthroughs.
    - `chunker.py`: 2-line `[DOC|TIER|ECU|ENGINE|SECTION]` scope prefix injection + `> 35`-row table splitting into `15–20` row slices with repeated header/separator rows + parent-child splitting.
    - `hybrid_index.py`: Rebuildable `data/heritage_index.sqlite` storing `heritage_parents`, `heritage_children`, `heritage_fts` (`tokenize = "unicode61 remove_diacritics 0 tokenchars '$_-#.'"`), and `heritage_vec` (`BAAI/bge-m3` `1024-d` cosine embeddings).
    - `search.py`: Trust-Weighted RRF (`k = 60`; `Tier 1 = 1.25x`, `Tier 2 = 1.10x`, `Tier 3 = 0.90x`), optional `BAAI/bge-reranker-v2-m3`, `parent_id` hydration, and **`No Verified Source Match`** abstention.
  - CLI subcommands: `heritage crawl`, `heritage parse-web`, `heritage parse-docs`, `heritage build-index` (alias `heritage index`), `heritage query`, `heritage diagram`.
- **Acceptance Gate**: `uv run pytest -n 0 tests/test_heritage_rag.py` verifies `<pre>` / HTML table normalization, cross-page table stitching & `> 35`-row slice header repetition, Gate 4a `mmdlint` + Gate 4b Page-OCR grounding quarantining unbacked pins/hex as `NEEDS_HUMAN_REVIEW` and excluding them from `get_verified_schematic`, exact `FTS5` token preservation (`$8000`, `0xF2`, `Pin_11`, `#782`, `#445`, `P4529353`), SMEC-vs-SBEC scope isolation, parent hydration, and `No Verified Source Match` abstention.

---

## Milestone 5 — FastMCP Servers, `.mcp.json` Activation & Quiet UI Workbench (`M5`)
- **Primary Deliverables**:
  - `mcp_servers/` thin FastMCP wrappers (`ecu_telemetry.py`, `hc11_emulator.py`, `chrysler_heritage.py`) sharing 100% business logic with `uv run logicaide` and enabling root `.mcp.json`.
  - `ui/` Quiet UI `v9.5.0` workbench implementing `WF-02`..`WF-06` (`1280x800` passenger-seat HMI, `390x844` mobile, `900x1000`, `1440x900`, **Garage Dark** & **Sunlight High-Contrast**, `<optgroup>` 3-tier Mopar builder, `quiet-evidence` compressor & `16x16` diff figures).
- **Acceptance Gate**: `uv run pytest -n 0`, `audit_quiet_html.py --strict-drift`, `audit_workbench_probes.py`, and `verify_visual_contract.py` pass across all 4 viewports and both themes.
