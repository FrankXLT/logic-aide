# Changelog

All notable changes to **LogicAIDE** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-11-24

### Fixed
- **Mobile Showcase Horizontal Scroll Cues (`docs/showcase/index.html`, `docs/WORKFLOWS.md`)**: Added registered `@property` CSS edge fade masks (`animation-timeline: scroll(self inline)` + fallback) and non-overlapping `Swipe schematic ->` / `Scroll ->` hint pills across overflowing `.va-figure-scroll` schematics and `<pre>` code blocks (`390x844` / `900x1000` + `@media (scripting: none)` fallback), automatically hiding on `1280x800` / `1440x900` desktop or once scrolled to the right end, and recorded the `WF-01` never-regress guardrail.

## [0.3.0] - 2026-11-23

### Changed
- **Heritage Preservation, Verified Schematics & Parent-Child Hybrid RAG (`M4`)**: Upgraded [`docs/HERITAGE_KNOWLEDGE_AND_RAG.md`](docs/HERITAGE_KNOWLEDGE_AND_RAG.md), `docs/IMPLEMENTATION_ROADMAP.md`, `docs/WORKFLOWS.md` (`WF-01`, `WF-05`), `knowledge/README.md`, `docs/DATA_MODEL_AND_MCP.md`, `mcp_servers/README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, and `docs/showcase/index.html` (`LA-RAG-004` + Milestone 4 card) to specify `warcio` (`1 req/s`) + `lxml`/`Trafilatura`/`Crawl4AI`, `Docling` (`TableFormer`, `300 DPI` crops) + `Marker`/`Surya` OCR fallback, Gate 4a `mmdlint` + Gate 4b Page-OCR Mermaid verification (`VERIFIED` vs. `NEEDS_HUMAN_REVIEW`), 2-line `[DOC|TIER|ECU|ENGINE|SECTION]` scope-prefixed parent-child chunking with `> 35`-row repeated-header table slices, SQLite `FTS5` (`tokenchars '$_-#.'`) + `BAAI/bge-m3` `sqlite-vec` Trust-Weighted RRF (`1.25x`/`1.10x`/`0.90x`), and updated CLI/FastMCP contracts (`heritage crawl`, `parse-web`, `parse-docs`, `build-index`, `query`, `diagram`; `search_heritage_docs`, `get_verified_schematic`).

## [0.2.0] - 2026-11-23

### Added
- **Draft 2020-12 Vehicle Profile Schema & Examples**: Added `profiles/schema/vehicle_build.schema.json`, `docs/VEHICLE_PROFILE_SCHEMA.md`, `profiles/examples/csx_89_neon_dohc.json`, and `profiles/examples/shadow_90_sbec_t2.json` covering all 7 vehicle domains, fuel pressure/SG scaling, `batoff_deadtime_ms` (`8..15 V` + pressure slope + short-pulse knee), voltage-derated pump curves, and the `<= 8160 RPM` ceiling.
- **Milestone 1–5 Cold-Start Roadmap**: Added `docs/IMPLEMENTATION_ROADMAP.md` and updated `CLAUDE.md` (`< 95` lines) distinguishing completed `Phase 0` specs/schemas/showcase from Milestones `M1`–`M5`, `uv run pytest -n 0`, and `rom_manifest.json`-only `0xF2`/`0xF4` SCI RAM polling.

### Changed
- **Mopar 3-Tier Drivetrain Matrix**: Synchronized `AGENTS.md`, `README.md`, `ARCHITECTURE.md`, `docs/WORKFLOWS.md`, `docs/DRIVETRAIN_AND_TURBO_THERMODYNAMICS.md`, `profiles/README.md`, and `.claude/` to reflect shared `4.00 in` (`101.6 mm`) bore spacing, the `1988–1991` Maserati `16V` DOHC (`Chrysler TC`) head, `BOLT_ON_NATIVE`, `HYBRID_MACHINING_REQUIRED` (with 7-item amber Swap Advisory Checklist), and `INCOMPATIBLE` hard blocks.
- **Hardware Deadbolt (`pyserial` `loop://` Fix)**: Removed `Bash(*pyserial*)` from `.claude/settings.json` while adding explicit denies for physical `/dev/ttyUSB*`, `/dev/ttyACM*`, `/dev/ttyS*`, `/dev/cu.*`, `COM*`, `boostbutton`, `minipro`, `xgpro`, and `stm32flash`.
- **Showcase Visual Polish (`docs/showcase/index.html`)**: Fixed Sun/Moon theme toggle icon swap + `color-scheme` sync, updated compressor x-axis to `Corrected Airflow / lb·min⁻¹` with softened excursion fills and `68%`/`74%` efficiency contours, added mobile tab strip scroll fade cue, and synced `M1-M5` roadmap & 3-tier Mopar calculator.

## [0.1.0] - 2026-11-23

### Added
- **Greenfield Project Bootstrap**: Initialized repository configuration, `.gitattributes` (LF line endings pinned for 68HC11 assemblers and Python parity), and `.gitignore` (excluding OEM `.bin`/`.s19` ROMs, Parquet telemetry lakes, and `data/heritage_raw/`).
- **Agent & IDE-Neutral Contract**: Added `AGENTS.md`, `CLAUDE.md`, and `.claude/agents/` role specifications (`rom-disassembly-analyst`, `telemetry-duckdb-analyst`, `hc11-emulator-tester`, `firmware-safety-reviewer`, `ui-visual-reviewer`).
- **Quiet UI v9.5.0 Contract**: Added `.quiet-ui.md` specifying Garage Dark & Sunlight High-Contrast polarity, `390x844` / `900x1000` / `1280x800` / `1440x900` viewports, `NIST SP 811` metrology discipline, and visual verification gates.
- **Core Documentation & GitHub Templates**: Added `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, PR template, drive-anomaly / ROM-feature issue templates, and 12 subsystem `README.md` scaffolds.
