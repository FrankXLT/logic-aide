# LogicAIDE Workflows & Operator Contract (`docs/WORKFLOWS.md`)

## Who & Core Goal

**LogicAIDE** (*Logic Module + Agentic IDE*) serves **multi-car Turbo Chrysler SMEC and SBEC tuners** operating across any coding agent (**Claude Code**, **Antigravity**, **Cursor**, **Codex CLI**, or **VS Code Copilot**) via shared `uv run logicaide ...` CLI entrypoints and FastMCP servers. Its core goal is to turn 62,500-baud vehicle telemetry, splayed 68HC11 ROM disassembly, Python Behavioral Twins, Sobol Monte Carlo simulation, and curated Chrysler heritage knowledge into safe, auditable calibration candidates—while keeping live ECU flashing strictly behind a **Human-Only Hardware Deadbolt**.

---

## Common Terms Glossary

- **Logic Module**: Chrysler's `6803` (`LM`) and `68HC11` (`SMEC` / `SBEC`) engine controller family (`52-way`/`60-way` connectors, partitioned manifests under `rom/manifests/<rom_id>/rom_manifest.json`).
- **High-Speed SCI**: Chrysler's `62,500`-baud (`62.5k`) Serial Communications Interface diagnostic stream polled during live vehicle drives (`VEHICLE_SCI`), backed by the `402`-variable / `10`-protocol `rom/sci/mpr_sci_crossref.json` cross-reference.
- **Load-Cell Data Lake**: DuckDB-queried 30-second Zstd Parquet lake unifying `VEHICLE_SCI`, `SINGLE_REPLAY`, and `MONTE_CARLO_SWEEP` runs with `load_cell_rollups` binned by `250 RPM x 10 kPa MAP x TPS Regime x ACT`.
- **Monte Carlo Sweep**: Deterministic `m68hc11` parameter sweeps generated with `scipy.stats.qmc` Sobol sequences (and optional `Optuna` surface search) across temperature, pressure, voltage, and load corners.
- **Python Behavioral Twin**: Readable Python model (`rom/models/*_twin.py`) implementing exact 8-bit/16-bit Q-format math and saturation clamps, verified bit-for-bit against 68HC11 `.asm` before staging.
- **Drivetrain & Turbo Profile**: Vehicle hardware manifest (`profiles/schema/vehicle_build.schema.json` Draft 2020-12, `profiles/examples/*.json`, and `turbo_spec.json`) compiled via `uv run logicaide profile compile` into SQLite `vehicles` / `vehicle_profile_revisions`.
- **Staging Pen**: `staging/candidates/<build_id>/`, the sole destination for candidate `.bin` builds and `REPORT.md` safety audits awaiting manual human flashing.
- **Living Heritage Wiki**: Repo-resident Markdown encyclopedia under `wiki/` with validated YAML frontmatter, shared `data/heritage_index.sqlite` RAG indexing, and outbound `.wiki.git` / static-site dual publishing via `uv run logicaide heritage sync-wiki`.
- **Wave Discovery Harvester**: CLI workflow (`uv run logicaide heritage discover --wave A|B|C`) extracting read-only high-entropy heritage fingerprints, SHA-256 deduplicating against the vault, and emitting cleanroom `knowledge/discovery_leads.yaml` (Wave A), `knowledge/discovery_leads_wave_b.yaml` (Wave B), or `knowledge/discovery_leads_wave_c.yaml` (Wave C).
- **TurboCalc v5 Compressor Catalog**: Digitized catalog (`profiles/turbo/catalog.json`, `145` calibrated turbos + `13` pending stubs across `157` SHA-256 deduplicated maps) normalized via 4-point affine matrices and `NIST SP 811` dry-air units (`lb/min` & `kg/s`).
- **Wayback CDX Sweep**: Polite, digest-collapsed Internet Archive CDX metadata query (`fl=timestamp,original,mimetype,statuscode,digest&filter=statuscode:200&collapse=digest`) generating raw `id_` memento queues under gitignored `data/heritage_raw/discovery_queue/`.

---

## Visual & Ergonomic Baseline (All Workflows)

- **Contract**: `.quiet-ui.md` (`v9.5.0`, `Strict Drift: true`) with `.va-stage[data-width="workbench"]` (`max-inline-size: min(96vw, 112rem)`).
- **Themes**: **Sunlight High-Contrast** (`#FFFFFF` Studio canvas, `>= 4.5:1` contrast) and **Garage Dark** (`#090D16` / `#0F1624` midnight telemetry canvas), toggled with Sun/Moon iconography and synced `<meta name="color-scheme">`.
- **Chrome & Units**: `<= 48px` N2 topbar with left-of-brand section control; N1 collapsible rail at `>= 1100px`; `ISA-101` calm alarm palette; zero emojis in chrome; `NIST SP 811` labels (`Boost / kPa`, `Pulse Width / ms`, `Corrected Airflow / lb·min⁻¹`, `Charge Temp / °C`); deterministic `quiet-evidence` / `window.QuietSvgLayout` SVGs.

---

## `WF-01` — Project Showcase (`docs/showcase/index.html`)

- **What User Sees & Taps**:
  - Offline static showcase page using WAI-ARIA tabs across the six LogicAIDE capabilities—including the upgraded `RAG & Deadbolt` pipeline (`warcio` + `Trafilatura`/`Crawl4AI`, `Docling` + `Marker`/`Surya`, `mmdlint` + OCR Mermaid Gate, Parent-Child `FTS5` + `sqlite-vec`)—theme toggle (**Sunlight High-Contrast** / **Garage Dark**) with Sun/Moon icon swap, `M1`–`M5` implementation roadmap (`docs/IMPLEMENTATION_ROADMAP.md`), and copyable CLI quick-start commands (`uv run logicaide ...`).
  - Zero network dependencies; includes `@media (scripting: none)` fallback rendering all tab panels sequentially when JS is disabled, plus keyboard-accessible `role="region"` table scroll wrappers.
- **Viewport Behavior**:
  - `390x844`: Single-column cards, horizontally scrollable tab list with subtle right-edge fade scroll cue and `>= 44px` hit targets, `0px` horizontal overflow. **Never-Regress Scroll Guardrail**: Every horizontally overflowing `.quiet-evidence` schematic container (`.va-figure-scroll`) or `<pre>` code block (`scrollWidth > clientWidth`) MUST display a crisp right-edge gradient fade and non-overlapping `Swipe schematic ->` or `Scroll ->` hint badge (plus `role="region"`, `aria-label`, and `tabindex="0"`), automatically hiding both cue and fade when content fits (`1280x800` / `1440x900`) or once scrolled to the far-right end.
  - `900x1000`: Two-column capability grid, compact `<= 48px` header; overflowing two-column `<pre>` blocks retain right-edge scroll cues cleanly.
  - `1280x800`: Passenger-seat HMI proportions with full above-the-fold architecture summary and tab strip; non-overflowing diagrams render without scroll badges.
  - `1440x900`: Full `.va-stage[data-width="workbench"]` engineering showcase with N1 collapsible rail (`M1-M5` badge) and zero scroll hint clutter.
- **Empty & Error States**:
  - If scripting is blocked or offline fonts/assets are absent, `@media (scripting: none)` reveals all sections cleanly with system monospace/sans fallbacks, no broken icons, and a CSS-only mobile (`< 768px`) baseline scroll cue on wide schematics and `<pre>` blocks.

## `WF-02` — Live SCI Monitor

- **What User Sees & Taps**:
  - Calm Cockpit top status strip showing `car_id`, active `62.5k` SCI polling state (`0xF2`/`0xF4`/`0xF6` and `LM_DIRECT_RAM`/`LM_IO` offsets resolved strictly from partitioned `rom_manifest.json` watchlists), frame rate (`Hz`), and a single prominent **`Bookmark Issue (Spacebar)`** action writing to `tuning_issues`.
  - **62.5k SCI Loopback Watch CLI (`M2 Live`)**: `uv run logicaide telemetry watch [--rom-id ID] [--profile PATH] [--car-id ID] [--build-id ID] [--session-id ID] [--port loop://] [--sci-table TABLE] [--extra SPEC] [--strict-table] [--max-frames N] [--duration-s S] [--probe-timeout-ms 12.0] [--poll-interval-ms 0.0] [--map-sensor-bar 1|2|3] [--wideband/--narrowband] [--drop-offset SPEC] [--reprobe SPEC] [--reprobe-at-frame N] [--fixture PATH] [--catalog PATH] [--lake-dir DIR] [--db-path DB] [--persist/--no-persist] [--bookmark-frame N] [--bookmark-note TEXT] [--bookmark-severity INFO|CAUTION|CRITICAL] [--spacebar/--no-spacebar] [--json]` executes the Hardware-Deadbolt-compliant `7812.5`-baud echoed `0x12` handshake -> `62,500`-baud zero-echo `(FX, AA) -> [BB]` poller (closing with `0xFE`) over `loop://` virtual loopback (`pyserial 3.5` natively handles `loop://`; `validate_serial_target` retains test transport rules)—hard-blocking physical `/dev/tty*`, `COM*`, and `socket://` ports plus forbidden opcodes (`0x13`, `0x17`, `0x23`, `0x41`, `0x51`) per `SECURITY.md`.
  - **Dead-Cell Timeout Quarantine & `tuning_issues` Bookmarks**: Each `(FX, AA)` cell enforces a `12 ms` RX timeout (`--probe-timeout-ms`): `2` startup misses trigger `STARTUP_PROBE_TIMEOUT`, and `3` consecutive runtime timeouts trigger `RUNTIME_TIMEOUT` plus an RX flush preventing 1-byte frame slip. Quarantined cells require manual `--reprobe` (no automatic retry) and persist explicit `null != 0` in 30s Zstd Parquet chunks. CLI flags (`--bookmark-frame` / `--bookmark-note` / `--bookmark-severity`) and non-blocking TTY `--spacebar` record `bookmarks` into SQLite `tuning_issues` and link `issue_id` onto the matching Parquet frame row.
  - **Strict Manifest Lookup CLI (Wave B Live)**: `uv run logicaide sim lookup-sci --rom-id <rom_id> [--sci-table 0xF2|0xF6|LM_DIRECT_RAM|LM_IO] [--offset 0x63 | --var MapValue] [--catalog PATH] [--strict/--no-strict] [--json]` resolves corroborated offsets against `rom/sci/mpr_sci_crossref.json` (`402` variables across `10` protocols: `LM`, `SMEC T1`, `SMEC T2`, `SBEC T1`, `SBEC TIII`, `SBECII T1`, `SBECII TIII`, `FCC`, `CUSTOM1`, `CUSTOM2`) and prints `var_name | offset | table | units | range | source_verified`. (Wave C `telemetry ingest` / `telemetry rollups` / `telemetry export-fixture` lake tooling is also live.)
  - Live numeric tiles (`RPM`, `MAP / kPa`, `AFR`, `Spark / °BTDC`, `Knock / °`, `ACT / °C`) plus a **Dead-Cell Quarantine** annunciator when packet timeouts occur.
  - Strictly read-only: no ECU write, boost-override, or flash controls exist in the monitor (`pyserial` `loop://` virtual loopback used exclusively for offline tests).
- **Viewport Behavior**:
  - `390x844`: Large `>= 44px` touch `Bookmark Issue` button pinned for thumb access, 2-column high-contrast numeric tiles, `0px` overflow.
  - `900x1000`: 3-column numeric readout + rolling 30s sparkline strip.
  - `1280x800`: Passenger-seat HMI layout—high-legibility numeric grid, rolling strip chart, and `Spacebar` shortcut badge visible without scrolling.
  - `1440x900`: Full cockpit strip + collapsible watchlist drawer showing active vs `Not in Watchlist (null)` RAM offsets.
- **Empty & Error States**:
  - **No Adapter / Idle**: Calm nominal badge `SCI STANDBY — NO ADAPTER CONNECTED` with `uv run logicaide telemetry watch` guidance (no flashing red alarm).
  - **Uncorroborated Offset (`--strict`)**: Abstains cleanly with **`No Verified Source Match`** (`NO_VERIFIED_SOURCE_MATCH` in `--json`) when an offset or variable lacks `.asm`/`.sym` corroboration in the target ECU manifest.
  - **Packet Drop / Bus Stall**: Triggers `ISA-101` amber **`DEAD-CELL QUARANTINE`** banner, tags interval `dead_cell_quarantine = true`, and refuses to interpolate stale values.
  - **Unpolled Offset**: Displays `Not in Watchlist (null)` rather than `0.0`.

## `WF-03` — Multi-Car Lake & 16x16 Replay

- **What User Sees & Taps**:
  - **Telemetry Lake & Rollup CLI (Wave C Live)**: `uv run logicaide telemetry ingest`, `uv run logicaide telemetry rollups`, `uv run logicaide telemetry export-fixture`, and `uv run python scripts/ingest_private_logs.py` ingest multi-format logs into the 33-column Zstd-3 30s Hive-partitioned Parquet lake, quarantine `2275 °F` EGT sensor faults (**Dead-Cell Quarantine**), and emit GPS-stripped slices under `tests/fixtures/telemetry/`.
  - Filter bar (`car_id`, `run_kind` pill selector for `VEHICLE_SCI | SINGLE_REPLAY | MONTE_CARLO_SWEEP`, `session_id`, and `tuning_issues` bookmark jump).
  - Synchronized multi-channel strip plots (`quiet-evidence` SVG) linked with a `16x16` `PEFTBL` / Spark diff heatmap table (`250 RPM x 10 kPa MAP` load cells) and an **`Export Stimulus Fixture`** button (GPS-stripped).
- **Viewport Behavior**:
  - `390x844`: Stacked filter accordion, single-chart focus selector, horizontally scrollable `16x16` table inside card container (`0px` page overflow).
  - `900x1000`: Stacked strip plot above `16x16` load-cell matrix.
  - `1280x800`: Synchronized strip plot (top) + compact `16x16` diff table (bottom) with hover/tap cell inspection.
  - `1440x900`: Side-by-side N1 issue/session tree (`>= 1100px`), synchronized strip plots, and `16x16` delta matrix.
- **Empty & Error States**:
  - **Empty Parquet Lake**: Shows empty-state card with exact `uv run logicaide telemetry ingest` CLI command and sample `tests/fixtures/telemetry/` slice loader.
  - **Missing Channel**: Renders cell/series gap labeled `Not in Watchlist (null)` (never plotted as zero); `2275 °F` EGT rails trigger `dead_cell_quarantine = true`.
  - **GPS Guard Violation**: Blocks fixture export if `lat`/`lon` columns are detected, displaying `EXPORT BLOCKED: STRIP GPS COLUMNS FIRST`.

## `WF-04` — Python Twin & Safety Staging

- **What User Sees & Taps**:
  - **Cleanroom ROM Manifest Seeding & CTAGS Sync (Wave B Live)**: `uv run python scripts/seed_rom_manifest.py [--private-root PATH] [--rom-dir PATH] [--baselines-dir PATH] [--sync-tags/--no-sync-tags] [--tags-path PATH] [--json]` parses read-only reference `.asm`/`.lst`/`.tpl` trees to emit `rom/sci/mpr_sci_crossref.json`, `rom/manifests/catalog.json`, and 4 strictly partitioned manifests (`smec_88_89_v19`, `sbec_90_93_v3_1`, `lm_86_87_v13`, `lm_86_glhs_v1`) without copying binaries into git-tracked paths. `uv run logicaide sim manifest-sync [--rom-id ID] [--catalog PATH] [--tags-path tags] [--rom-dir rom] [--json]` regenerates sorted Exuberant CTAGS (`tags`) across `rom/src/*.asm`, `rom/models/*_twin.py` ASTs, verified manifests, `DRBMemoryTable`, `FastDataOutputAddressTable`, and `ram_watchlist`.
  - **Splay & SHA-256 Roundtrip Gate (`M3` Live)**: `uv run logicaide sim verify-sha256 [--rom PATH] [--asm PATH] [--splay-dir DIR] [--json]` splays a 32 KB ROM into cleanroom `.asm` modules (`main.asm`, `fuel_tables.asm`, `spark_tables.asm`, `boost_vnt.asm`, `sci_diag.asm`, `vectors.asm`) and reassembles them with the 2-pass `m68hc11` assembler, enforcing byte-exact SHA-256 parity (`ROUNDTRIP_SHA256_MISMATCH` on any diff).
  - **Bit-Exact 68HC11 Parity & Timing Proof (`M3` Live)**: `uv run logicaide sim parity [--profile PATH] [--use-adc] [--json]` executes `rom/models/*_twin.py` alongside the `m68hc11` `$8000`–`$FFFF` emulator (zero-page RAM or `$1030`–`$1034` ADC + `$102B`–`$102F` SCI peripheral hooks), asserting `0`-LSB parity (`max_lsb_diff == 0`), `$FFC0`–`$FFFF` vector integrity, and `<= 22,000` E-cycles (`<= 11.0 ms` at `2.0 MHz`).
  - **Deterministic Monte Carlo Sweeps (`M3` Live)**: `uv run logicaide sim sweep [--profile PATH] [--sampler SOBOL|LHS|OPTUNA] [--n-trials N] [--json]` runs deterministic QMC/Optuna calibration sweeps across RPM, MAP, baro, battery, and knock corners, writing `MONTE_CARLO_SWEEP` 30s Zstd Parquet chunks plus `optimization_campaigns` and `tuning_issues` rows in SQLite.
  - **6-Check `ISA-101` Staging Safety Gate (`M3` Live)**: `uv run logicaide sim stage [--profile PATH] [--build-id ID] [--json]` validates checksum/roundtrip, `$FFC0`–`$FFFF` vectors, `< 11.0 ms` loop cycle budget, `0`-LSB twin parity, Monte Carlo overflow/wrap/limiter bounds (`<= 8160 RPM` rev limit; `105`/`205`/`315 kPa` overboost ceilings), and drivetrain/deadbolt checks—emitting a `32,768`-byte `.bin`, `gate_summary.json`, and `REPORT.md` to `staging/candidates/<build_id>/`.
  - When all 6 checks pass, `REPORT.md` provides a **human-only Pin 11 continuity & bench programmer checklist** (`minipro -p AT28C256 -w ...`); AI never flashes hardware.
- **Viewport Behavior**:
  - `390x844`: Vertical gate checklist cards with `>= 44px` copy button (`0px` overflow).
  - `900x1000`: Two-column gate checklist + cycle-budget bar chart.
  - `1280x800`: Twin-vs-HC11 parity summary alongside the 6-check `ISA-101` gate card.
  - `1440x900`: Full 3-pane staging workbench (N1 candidate tree, cycle/parity charts, `REPORT.md` gate card).
- **Empty & Error States**:
  - **Missing Manifest Catalog**: Prompts operator to run `uv run python scripts/seed_rom_manifest.py` before `uv run logicaide sim manifest-sync` or `sim lookup-sci`.
  - **No Staged Candidate**: Displays `NO STAGED CANDIDATES` with `uv run logicaide sim stage` instructions.
  - **Safety Failure**: Displays high-contrast crimson **`SAFETY GATE BLOCKED — DO NOT FLASH`** header, suppresses any copyable flash CLI block, and highlights the exact failing SHA-256, vector, cycle, parity, or mechanical-clamp check.

## `WF-05` — Heritage Hybrid RAG

- **What User Sees & Taps**:
  - **PDF Extraction (`uv run logicaide heritage parse-docs` / `scripts/curate_heritage_pdfs.py`)**: Track D1 (`pdftotext -layout` + cross-page pipe-table stitching) and Track D2 (`ocr_fallback_rendered` / `ocr_corrupted_rendered` via `pdftoppm`) extract read-only `private_references/` PDFs into gitignored `data/heritage_raw/pdfs_md/` and `data/heritage_raw/figures/`, strictly excluding all `14` non-Chrysler `EXCLUDE` PDFs and `QUARANTINE_PII_DO_NOT_INGEST` files.
  - **Mermaid Gate 4a/4b & Cleanroom Index (`uv run logicaide heritage build-index --verify-frontmatter` / `index`)**: `chunker.py` strips 2-line Scope/Boundary headers, injects concise `[DOC / SOURCE]` scope prefixes, slices long tables with repeated headers, decomposes Mermaid trees/sequences, and verifies all diagrams via `mermaid_gate.py` (Gate 4a syntax + max-2 quote repair; Gate 4b page-OCR pin/table/circuit/hex/fault-code grounding) before building offline `data/heritage_index.sqlite` (`FTS5` `unicode61 tokenchars '$_-#.'` + `float32` cosine embeddings via `sqlite-vec` / `heritage_vec_fallback`).
  - **Hybrid Retrieval, CLI & `chrysler-heritage` MCP (`heritage query`, `heritage diagram`, `mcp_servers/chrysler_heritage.py`)**: Trust-Weighted RRF (`k=60`; `Tier 1 = 1.25x`, `Tier 2 = 1.10x`, `Tier 3 = 0.90x`) + full parent-section hydration across `6/6` `wiki/` portals and `18` indexed cleanroom digests (`16` curated PDF digests: `4` `knowledge/tier1_oem/` + `12` `knowledge/tier2_verified/`, plus `2` `knowledge/tier3_community/` digests). Exposes `search_heritage_docs`, `get_verified_schematic`, `lookup_60way_pin`, `lookup_sci_offset` (bridging `rom_manifest.json` via `lookup_sci`), `get_provenance_card`, `discover_heritage_leads`, and `sync_heritage_wiki` across CLI and stdio MCP.
- **Viewport Behavior**:
  - `390x844`: Single-column reader (`>= 44px` filter/tier chips, readable line length, `0px` overflow).
  - `900x1000`: Reader column with collapsible schematic drawer.
  - `1280x800` & `1440x900`: `.va-split-reader-gallery` (`>= 60%` prose/table left, verified Mermaid schematic / connector viewer right).
- **Empty, Quarantine & Error States**:
  - **Unverified Address/Pinout**: Strictly renders **`No Verified Source Match`** when fused scores fall below threshold or only `Tier 3` forum speculation exists for a 60-way pin or `0xF2`/`0xF4`/`0xF6` offset — never synthesizes or guesses hex RAM addresses or 60-way pin numbers.
  - **Quarantined Diagram (`NEEDS_HUMAN_REVIEW`)**: Diagrams with unbacked pin, circuit, fault-code, or hex tokens (or invalid syntax after 2 Gate 4a repairs) are tagged `NEEDS_HUMAN_REVIEW`, redacted from default `get_verified_schematic` / `heritage diagram` output (requiring `--pending-review` to inspect), and flagged in amber until maintainer verification.
  - **Missing Index**: Prompts operator to run `uv run logicaide heritage build-index` (alias `heritage index`).

## `WF-06` — Drivetrain & Compressor Map

- **What User Sees & Taps**:
  - **Profile Compiler CLI (Wave B / `M1` Live)**: `uv run logicaide profile compile <profile.json> [--repo-root PATH] [--db-path PATH] [--persist/--no-persist] [--require-rom-manifest/--no-require-rom-manifest] [--json]` runs the full validation & persistence pipeline: Draft 2020-12 `profiles/schema/vehicle_build.schema.json` validation, 3-tier Mopar block/head compatibility + 7-item `AMBER` swap checklist, fuel pressure & specific-gravity (`SG_REF = 0.742`) effective-flow verification, `BATOFF` curve monotonicity (`<= 8.0V` to `>= 15.0V`, `min_linear_pw_ms >= short_pulse_knee_ms`) + cross-check against `profiles/presets/injectors_batoff.json`, turbo `turbo_spec_ref` / `figure_sha256` & alias resolution against `profiles/turbo/catalog.json`, partitioned ROM manifest bridge (`rom_manifest_ref` direct or via `rom/manifests/catalog.json` enforcing `ecu_family` & `rom_size_bytes` parity), and idempotent SQLite persistence into `data/drives/catalog.sqlite` (`vehicles` & `vehicle_profile_revisions`).
  - Semantic `<optgroup>` hardware selectors (`Engine Block`, `Cylinder Head`, `Transaxle`, `Fuel`, `Injector Preset`, `Turbocharger`) backed by `145` calibrated + `13` `axis_calibration_pending` `turbo_spec.json` entries imported via `uv run logicaide turbo import-turbocalc` (`profiles/turbo/catalog.json` across `157` SHA-256 deduplicated maps), `95` validated BATOFF injector deadtime presets (`profiles/presets/injectors_batoff.json`), anonymized flowbench curves (`profiles/presets/cylinder_head_flowbench.json`), and Wave C cleanroom adapter/hardware BOM presets (`profiles/presets/hardware_boms.json`).
  - Interactive `NIST SP 811` digitized compressor map (`PR / 1` vs `Corrected Airflow / lb·min⁻¹` & `kg/s`) seeded by 4-point affine matrices with `68%`/`74%` efficiency islands, surge line, choke boundary, and overlaid operating points from `load_cell_rollups`.
- **Viewport Behavior**:
  - `390x844`: Stacked `<optgroup>` selectors (`>= 44px` height) and dynamic 3-tier compatibility advisory above responsive `quiet-evidence` compressor SVG (`0px` overflow).
  - `900x1000`: Compact selector strip above full-width compressor island chart.
  - `1280x800` & `1440x900`: Left hardware/fuel builder rail + right digitized compressor island overlay and injector duty summary.
- **3-Tier Compatibility & Envelope States**:
  - **`BOLT_ON_NATIVE` (`ISA-101` Nominal)**: `2.2L`/`2.5L` K-block paired with `8V #445` Open, `8V #782` Swirl, `1991–1993` Lotus `16V` DOHC (`Turbo III`), or `1988–1991` Maserati `16V` DOHC (`Chrysler TC`), displaying `10 mm` vs. `11 mm` head-bolt and cross-drilled gasket notes.
  - **`HYBRID_MACHINING_REQUIRED` (`ISA-101` Amber Advisory)**: `2.2L`/`2.5L` K-block paired with `Neon ECC 2.0L DOHC` or `2.4L EDZ`/SRT-4 `16V` DOHC head (or full `2.4L EDZ` swaps into SMEC/SBEC cars, sharing `4.00 in` / `101.6 mm` bore spacing and `87.5 mm` standard bore). Displays the 7-item **Swap Advisory Checklist**: oil/coolant deck passage plugging, external head oil feed & oil-pan drainback routing, head-bolt/stud sleeve alignment, custom timing-belt tensioner geometry, piston valve reliefs (`piston_valve_reliefs=true`), SMEC/SBEC crank/cam Hall-effect trigger sync, and counter-clockwise `PEFTBL` cam-peak rotation.
  - **`INCOMPATIBLE` (`ISA-101` Crimson Block)**: Non-viable pairings (e.g., `8V #445`/`#782` SOHC or K-car `Lotus`/`Maserati 16V` heads onto a `2.4L EDZ` block) block `profile compile` and simulation export with a mechanical explanation (`ProfileCompilationError`).
  - **Compressor Surge / Choke / Pending Stub**: Operating points crossing the surge line or choke limit highlight in `ISA-101` amber/crimson with mass-flow margin readout; selecting one of the `13` `axis_calibration_pending: true` stubs surfaces a `CALIBRATION PENDING` banner and locks simulation export until axis landmarks are verified.

## `WF-07` — Living Heritage Wiki & Wave Discovery Harvester

- **What User Sees & Taps**:
  - **Living Heritage Wiki Publisher (`6/6` Verified Portals via `uv run logicaide heritage sync-wiki --validate-only` / `--dry-run`)**: All six `wiki/` portals (`ecu-families`, `rom-and-sci`, `turbo-and-airflow`, `drivetrain-and-swaps`, `wiring-and-diagnostics`, `hardware-bench`) are `verified` with zero validation warnings (`Wiring & Diagnostics` promoted with [`60-way-connector-atlas.md`](../wiki/wiring-and-diagnostics/60-way-connector-atlas.md), [`50-way-bulkhead-atlas.md`](../wiki/wiring-and-diagnostics/50-way-bulkhead-atlas.md), [`drb-ii-fault-trees.md`](../wiki/wiring-and-diagnostics/drb-ii-fault-trees.md), and [`wiring-and-diagnostics-overview.md`](../wiki/wiring-and-diagnostics/wiring-and-diagnostics-overview.md)), verifying YAML frontmatter, relative link integrity, Gate 4a/4b `VERIFIED` Mermaid diagrams, and dual-publish mirror staging (`data/heritage_raw/wiki_stage/` vs. flattened `strip_frontmatter=True` `.wiki.git` output).
  - **GitHub Wiki Mirror Push (`uv run logicaide heritage sync-wiki --push-github-wiki [--wiki-remote URL] [--dry-run] [--json]`)**: Validates all `wiki/` pages, flattens outbound copies with YAML frontmatter stripped into the gitignored local mirror `data/heritage_raw/wiki_github_mirror`, prunes stale `.md` pages removed from `wiki/`, creates a mirror commit when changes exist, and pushes directly to the repository's `.wiki.git` remote (defaulting to `origin`'s `.wiki.git` unless `--wiki-remote` overrides it, reporting `pushed` or `up_to_date`; `--json` emits the structured sync/push summary). Cannot be combined with `--validate-only`.
  - **Wave A–D Discovery Harvester (`uv run logicaide heritage discover --wave A|B|C|D [--fetch-cdx/--no-fetch-cdx]`)**: Read-only `private_references/` fingerprint census, SHA-256 vault deduplication, cleanroom lead manifests (`discovery_leads.yaml`, `discovery_leads_wave_b.yaml`, `discovery_leads_wave_c.yaml`, `knowledge/discovery_leads_wave_d.yaml`), `code_forge` (`github`/`gitlab`/`sourceforge`) queries, and polite `1.5s`-paced Wayback CDX queues (`wave_a_queue.json`..`wave_d_queue.json`) while strictly excluding all `14` non-Chrysler `EXCLUDE` PDFs and `QUARANTINE_PII_DO_NOT_INGEST` files.
- **Viewport Behavior**:
  - `390x844` (Mobile): Single-column wiki reader and stacked discovery lead cards with `>= 44px` status filter chips and horizontal scroll wrappers (right-edge fade + `Scroll ->` badge on wide lead tables, `0px` page overflow).
  - `900x1000` (Tablet Split): Collapsible `_Sidebar.md` category drawer alongside wiki prose/frontmatter card and compact lead status summary.
  - `1280x800` (Laptop): Side-by-side `.va-split-reader-gallery` pairing rendered `wiki/` page + provenance header on the left with dual-publish diff/queue telemetry on the right.
  - `1440x900` (Desktop Wide): Full `.va-stage[data-width="workbench"]` 3-pane layout: N1 `wiki/` topic tree (`>= 1100px`), central Markdown/Mermaid preview, and right-hand Wave Discovery Harvester & `.wiki.git` mirror status rail.
- **Empty, Quarantine & Hardware Deadbolt States**:
  - **Extensionless GitHub Wiki Slug & Backtick Link Guardrail**: Flattened GitHub Wiki mirrors (`--flatten` / `--push-github-wiki`) MUST rewrite internal `.md` links to extensionless Gollum page slugs (`resolved.stem` + optional `#anchor`, zero `.md)` wiki targets) and handle inline backticks inside link labels (e.g., ``[`60-Way` `SMEC` & `SBEC` Connector Atlas](60-way-connector-atlas)``) via a single code-aware token pass while leaving standalone inline code spans untouched; `--no-flatten` preserves relative `.md` directory links for static sites.
  - **`remote_uninitialized` GitHub Wiki State**: When GitHub's Wiki feature / `.wiki.git` repository has not yet been initialized remotely, `--push-github-wiki` still stages and commits the validated mirror in `data/heritage_raw/wiki_github_mirror`, sets `github_wiki_status="remote_uninitialized"`, and surfaces clean actionable guidance (`action_required`) to enable Wiki in repository settings / create the initial Wiki page before re-running.
  - **`axis_calibration_pending: true` Leads**: Uncalibrated compressor map stubs surface as high-priority `queued_cdx` / `needs_manual_capture` `ISA-101` amber recovery leads until digitized into `profiles/turbo/`.
  - **`QUARANTINE_PII_DO_NOT_INGEST` & PII Scrubbing**: Any personal names, emails, local home paths, UNC hostnames, or vendor order receipts are blocked pre-publish (`WikiValidationError`) and stripped via `scrub_pii()`; quarantined raw files never enter `wiki/`, `knowledge/discovery_leads*.yaml`, or `data/heritage_index.sqlite`.
  - **Hardware Deadbolt Boundary**: Live ECU write/flash routines remain strictly forbidden across `wiki/` content gates and discovery outputs; `sync-wiki` never mutates `wiki/` in place, and missing `private_references/` falls back cleanly to `profiles/turbo/` stubs and cleanroom seeds without raising errors.
