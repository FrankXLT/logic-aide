# LogicAIDE Workflows & Operator Contract (`docs/WORKFLOWS.md`)

## Who & Core Goal

**LogicAIDE** (*Logic Module + Agentic IDE*) serves **multi-car Turbo Chrysler SMEC and SBEC tuners** operating across any coding agent (**Claude Code**, **Antigravity**, **Cursor**, **Codex CLI**, or **VS Code Copilot**) via shared `uv run logicaide ...` CLI entrypoints and FastMCP servers. Its core goal is to turn 62,500-baud vehicle telemetry, splayed 68HC11 ROM disassembly, Python Behavioral Twins, Sobol Monte Carlo simulation, and curated Chrysler heritage knowledge into safe, auditable calibration candidates—while keeping live ECU flashing strictly behind a **Human-Only Hardware Deadbolt**.

---

## Common Terms Glossary

- **Logic Module**: Chrysler's 68HC11-based engine controller family (`SMEC` / `SBEC`, 60-way harness connector, `$8000`–`$FFFF` EPROM calibration/program space).
- **High-Speed SCI**: Chrysler's 62,500-baud (`62.5k`) Serial Communications Interface diagnostic stream polled during live vehicle drives (`VEHICLE_SCI`).
- **Load-Cell Data Lake**: DuckDB-queried 30-second Zstd Parquet lake unifying `VEHICLE_SCI`, `SINGLE_REPLAY`, and `MONTE_CARLO_SWEEP` runs with `load_cell_rollups` binned by `250 RPM x 10 kPa MAP x TPS Regime x ACT`.
- **Monte Carlo Sweep**: Deterministic `m68hc11` parameter sweeps generated with `scipy.stats.qmc` Sobol sequences (and optional `Optuna` surface search) across temperature, pressure, voltage, and load corners.
- **Python Behavioral Twin**: Readable Python model (`rom/models/*_twin.py`) implementing exact 8-bit/16-bit Q-format math and saturation clamps, verified bit-for-bit against 68HC11 `.asm` before staging.
- **Drivetrain & Turbo Profile**: Vehicle hardware manifest (`profiles/schema/vehicle_build.schema.json` Draft 2020-12, `profiles/examples/*.json`, and `turbo_spec.json`) defining block, head, 3-tier compatibility status, transaxle, fuel (`93 E10` vs `E85`), injector pressure/SG/deadtime scaling, and compressor map.
- **Staging Pen**: `staging/candidates/<build_id>/`, the sole destination for candidate `.bin` builds and `REPORT.md` safety audits awaiting manual human flashing.
- **Living Heritage Wiki**: Repo-resident Markdown encyclopedia under `wiki/` with validated YAML frontmatter, shared `data/heritage_index.sqlite` RAG indexing, and outbound `.wiki.git` / static-site dual publishing via `uv run logicaide heritage sync-wiki`.
- **Wave Discovery Harvester**: CLI workflow (`uv run logicaide heritage discover --wave A`) extracting read-only high-entropy heritage fingerprints, SHA-256 deduplicating against the vault, and emitting cleanroom `knowledge/discovery_leads.yaml`.
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
  - Calm Cockpit top status strip showing `car_id`, active `62.5k` SCI polling state (`0xF2`/`0xF4` offsets resolved strictly from `rom_manifest.json`), frame rate (`Hz`), and a single prominent **`Bookmark Issue (Spacebar)`** action writing to `tuning_issues`.
  - Live numeric tiles (`RPM`, `MAP / kPa`, `AFR`, `Spark / °BTDC`, `Knock / °`, `ACT / °C`) plus a **Dead-Cell Quarantine** annunciator when packet timeouts occur.
  - Strictly read-only: no ECU write, boost-override, or flash controls exist in the monitor (`pyserial` `loop://` used exclusively for offline tests).
- **Viewport Behavior**:
  - `390x844`: Large `>= 44px` touch `Bookmark Issue` button pinned for thumb access, 2-column high-contrast numeric tiles, `0px` overflow.
  - `900x1000`: 3-column numeric readout + rolling 30s sparkline strip.
  - `1280x800`: Passenger-seat HMI layout—high-legibility numeric grid, rolling strip chart, and `Spacebar` shortcut badge visible without scrolling.
  - `1440x900`: Full cockpit strip + collapsible watchlist drawer showing active vs `Not in Watchlist (null)` RAM offsets.
- **Empty & Error States**:
  - **No Adapter / Idle**: Calm nominal badge `SCI STANDBY — NO ADAPTER CONNECTED` with `uv run logicaide telemetry watch` guidance (no flashing red alarm).
  - **Packet Drop / Bus Stall**: Triggers `ISA-101` amber **`DEAD-CELL QUARANTINE`** banner, tags interval `dead_cell_quarantine = true`, and refuses to interpolate stale values.
  - **Unpolled Offset**: Displays `Not in Watchlist (null)` rather than `0.0`.

## `WF-03` — Multi-Car Lake & 16x16 Replay

- **What User Sees & Taps**:
  - Filter bar (`car_id`, `run_kind` pill selector for `VEHICLE_SCI | SINGLE_REPLAY | MONTE_CARLO_SWEEP`, `session_id`, and `tuning_issues` bookmark jump).
  - Synchronized multi-channel strip plots (`quiet-evidence` SVG) linked with a `16x16` `PEFTBL` / Spark diff heatmap table (`250 RPM x 10 kPa MAP` load cells) and an **`Export Stimulus Fixture`** button (GPS-stripped).
- **Viewport Behavior**:
  - `390x844`: Stacked filter accordion, single-chart focus selector, horizontally scrollable `16x16` table inside card container (`0px` page overflow).
  - `900x1000`: Stacked strip plot above `16x16` load-cell matrix.
  - `1280x800`: Synchronized strip plot (top) + compact `16x16` diff table (bottom) with hover/tap cell inspection.
  - `1440x900`: Side-by-side N1 issue/session tree (`>= 1100px`), synchronized strip plots, and `16x16` delta matrix.
- **Empty & Error States**:
  - **Empty Parquet Lake**: Shows empty-state card with exact `uv run logicaide telemetry ingest` CLI command and sample fixture loader.
  - **Missing Channel**: Renders cell/series gap labeled `Not in Watchlist (null)` (never plotted as zero).
  - **GPS Guard Violation**: Blocks fixture export if `lat`/`lon` columns are detected, displaying `EXPORT BLOCKED: STRIP GPS COLUMNS FIRST`.

## `WF-04` — Python Twin & Safety Staging

- **What User Sees & Taps**:
  - Side-by-side diff between `rom/models/*_twin.py` output and `m68hc11` `$8000`–`$FFFF` execution trace, paired with Sobol Monte Carlo pass/fail distribution and sequential `uv run pytest -n 0` parity test logs.
  - Prominent **`ISA-101` Safety Gate Card** (`staging/candidates/<build_id>/REPORT.md`) showing 6 discrete gate checks (SHA-256 baseline round-trip, `$FFC0`–`$FFFF` vectors, `< 11 ms` bus cycles, 8-bit carry overflow, mechanical clamps including `<= 8160 RPM` rev limit, GPS-free fixtures).
  - Cold-start orientation: `CLAUDE.md` and `docs/IMPLEMENTATION_ROADMAP.md` clearly distinguish completed `Phase 0` specs/schemas/showcase from Milestones `M1`–`M5` Python implementation.
  - When all checks pass, displays a **copyable manual flash CLI block**; AI never flashes hardware.
- **Viewport Behavior**:
  - `390x844`: Vertical gate checklist cards with `>= 44px` copy button (`0px` overflow).
  - `900x1000`: Two-column gate checklist + cycle-budget bar chart.
  - `1280x800`: Twin-vs-HC11 parity summary alongside the 6-check `ISA-101` gate card.
  - `1440x900`: Full 3-pane staging workbench (N1 candidate tree, cycle/parity charts, `REPORT.md` gate card).
- **Empty & Error States**:
  - **No Staged Candidate**: Displays `NO STAGED CANDIDATES` with `uv run logicaide sim stage` instructions.
  - **Safety Failure**: Displays high-contrast crimson **`SAFETY GATE BLOCKED`** header, suppresses any copyable flash CLI block, and highlights the exact failing cycle, vector, or carry check.

## `WF-05` — Heritage Hybrid RAG

- **What User Sees & Taps**:
  - Querying `uv run logicaide heritage query ...` or `uv run logicaide heritage diagram ...` renders `.va-split-reader-gallery` (`>= 60%` clean attributed prose and header-repeated/stitched pipe tables on the left; `<= 40%` verified Mermaid schematic / connector viewer on the right), unifying `knowledge/**/*.md` and `wiki/**/*.md` inside the shared `data/heritage_index.sqlite` (`FTS5` + `sqlite-vec`) corpus.
  - Every retrieved claim displays `Tier 1`/`Tier 2`/`Tier 3` provenance pills (`Tier 1: Factory/OEM`, `Tier 2: Verified Disassembly`, `Tier 3: Community Notes`), `ECU` scope badges (`SMEC_88_89` vs. `SBEC_90_93`), exact source `page_ref` / `warc_capture_date`, Wave A Tier-2 digests (e.g., `knowledge/tier2_verified/g_head_flowbench.md`), `knowledge/discovery_leads.yaml` status surfacing (`queued_cdx` / `already_covered` / `needs_manual_capture`), and Pin 11 continuity / Pin 2, 3, 25, 45 variant callouts when 60-way wiring is queried.
- **Viewport Behavior**:
  - `390x844`: Single-column reader (`>= 44px` filter/tier chips, readable line length, `0px` overflow).
  - `900x1000`: Reader column with collapsible schematic drawer.
  - `1280x800` & `1440x900`: `.va-split-reader-gallery` (`>= 60%` prose/table left, verified Mermaid schematic / connector viewer right).
- **Empty, Quarantine & Error States**:
  - **Unverified Address/Pinout**: Strictly renders **`No Verified Source Match`** when lexical/vector scores fail thresholds or only `Tier 3` forum speculation exists for a 60-way pin or `0xF2`/`0xF4` offset — never synthesizes or guesses hex RAM addresses or 60-way pin numbers.
  - **Quarantined Diagram (`NEEDS_HUMAN_REVIEW`)**: If a scanned FSM/wiring diagram transpiled into Mermaid contains any pin number, circuit code, or hex address not backed by page-level OCR tokens (or fails headless `mmdlint` syntax checks after 2 retries), tags it `mermaid_verification: NEEDS_HUMAN_REVIEW`, excludes it from automated agent wiring guidance (`get_verified_schematic`), and displays it in `ISA-101` amber in the operator review queue until human sign-off.
  - **Missing Index**: Prompts operator to run `uv run logicaide heritage build-index` (alias `heritage index`).

## `WF-06` — Drivetrain & Compressor Map

- **What User Sees & Taps**:
  - Semantic `<optgroup>` hardware selectors (`Engine Block`, `Cylinder Head`, `Transaxle`, `Fuel`, `Injector Preset`, `Turbocharger`) backed by Draft 2020-12 `profiles/schema/vehicle_build.schema.json` and Wave A catalog artifacts: `145` calibrated + `13` `axis_calibration_pending` `turbo_spec.json` entries imported via `uv run logicaide turbo import-turbocalc` (`profiles/turbo/catalog.json` across `157` SHA-256 deduplicated maps), `95` validated BATOFF injector deadtime presets (`profiles/presets/injectors_batoff.json`), and anonymized flowbench curves (`profiles/presets/cylinder_head_flowbench.json`).
  - Interactive `NIST SP 811` digitized compressor map (`PR / 1` vs `Corrected Airflow / lb·min⁻¹` & `kg/s`) seeded by 4-point affine matrices with `68%`/`74%` efficiency islands, surge line, choke boundary, and overlaid operating points from `load_cell_rollups`.
- **Viewport Behavior**:
  - `390x844`: Stacked `<optgroup>` selectors (`>= 44px` height) and dynamic 3-tier compatibility advisory above responsive `quiet-evidence` compressor SVG (`0px` overflow).
  - `900x1000`: Compact selector strip above full-width compressor island chart.
  - `1280x800` & `1440x900`: Left hardware/fuel builder rail + right digitized compressor island overlay and injector duty summary.
- **3-Tier Compatibility & Envelope States**:
  - **`BOLT_ON_NATIVE` (`ISA-101` Nominal)**: `2.2L`/`2.5L` K-block paired with `8V #445` Open, `8V #782` Swirl, `1991–1993` Lotus `16V` DOHC (`Turbo III`), or `1988–1991` Maserati `16V` DOHC (`Chrysler TC`), displaying `10 mm` vs. `11 mm` head-bolt and cross-drilled gasket notes.
  - **`HYBRID_MACHINING_REQUIRED` (`ISA-101` Amber Advisory)**: `2.2L`/`2.5L` K-block paired with `Neon ECC 2.0L DOHC` or `2.4L EDZ`/SRT-4 `16V` DOHC head (or full `2.4L EDZ` swaps into SMEC/SBEC cars, sharing `4.00 in` / `101.6 mm` bore spacing and `87.5 mm` standard bore). Displays the 7-item **Swap Advisory Checklist**: oil/coolant deck passage plugging, external head oil feed & oil-pan drainback routing, head-bolt/stud sleeve alignment, custom timing-belt tensioner geometry, piston valve reliefs, SMEC/SBEC crank/cam Hall-effect trigger sync, and counter-clockwise `PEFTBL` cam-peak rotation.
  - **`INCOMPATIBLE` (`ISA-101` Crimson Block)**: Non-viable pairings (e.g., `8V #445`/`#782` SOHC or K-car `Lotus`/`Maserati 16V` heads onto a `2.4L EDZ` block) block profile compilation and simulation export with a mechanical explanation.
  - **Compressor Surge / Choke / Pending Stub**: Operating points crossing the surge line or choke limit highlight in `ISA-101` amber/crimson with mass-flow margin readout; selecting one of the `13` `axis_calibration_pending: true` stubs surfaces a `CALIBRATION PENDING` banner and locks simulation export until axis landmarks are verified.

## `WF-07` — Living Heritage Wiki & Wave Discovery Harvester

- **What User Sees & Taps**:
  - **Living Heritage Wiki Publisher (`uv run logicaide heritage sync-wiki --dry-run`)**: Category browser over repo-resident `wiki/` (`Home.md`, `_Sidebar.md`, `_Footer.md`, and 6 flat-namespace topic trees), showing YAML frontmatter validation badges, relative link integrity, Mermaid AST status, and dual-publish preview targets (flattened `strip_frontmatter=True` mirror for outbound `.wiki.git` vs. hierarchical static-site stage under `data/heritage_raw/wiki_stage/`).
  - **Wave Discovery Harvester (`uv run logicaide heritage discover --wave A`)**: Read-only `private_references/` fingerprint census (`squirrelpf.com`, `sqrl_tools`, `turbocalc`, `T03`/`TB03`/`TE04H`/`VNT25`/`TD04LR`, `#782`/`#445`/`G-Head`, `88-90 SMEC 52-way`/`SBEC`), SHA-256 vault deduplication badge (`vault_sha256_count`), cleanroom `knowledge/discovery_leads.yaml` status table (`queued_cdx` / `already_covered` / `needs_manual_capture`), and offline/polite `1.5s`-paced Wayback CDX `id_` memento queue (`data/heritage_raw/discovery_queue/wave_a_queue.json`).
- **Viewport Behavior**:
  - `390x844` (Mobile): Single-column wiki reader and stacked discovery lead cards with `>= 44px` status filter chips and horizontal scroll wrappers (right-edge fade + `Scroll ->` badge on wide lead tables, `0px` page overflow).
  - `900x1000` (Tablet Split): Collapsible `_Sidebar.md` category drawer alongside wiki prose/frontmatter card and compact lead status summary.
  - `1280x800` (Laptop): Side-by-side `.va-split-reader-gallery` pairing rendered `wiki/` page + provenance header on the left with dual-publish diff/queue telemetry on the right.
  - `1440x900` (Desktop Wide): Full `.va-stage[data-width="workbench"]` 3-pane layout: N1 `wiki/` topic tree (`>= 1100px`), central Markdown/Mermaid preview, and right-hand Wave Discovery Harvester & `.wiki.git` mirror status rail.
- **Empty, Quarantine & Hardware Deadbolt States**:
  - **`axis_calibration_pending: true` Leads**: Uncalibrated compressor map stubs surface as high-priority `queued_cdx` / `needs_manual_capture` `ISA-101` amber recovery leads until digitized into `profiles/turbo/`.
  - **`QUARANTINE_PII_DO_NOT_INGEST` & PII Scrubbing**: Any personal names, emails, local home paths, UNC hostnames, or vendor order receipts are blocked pre-publish (`WikiValidationError`) and stripped via `scrub_pii()`; quarantined raw files never enter `wiki/`, `knowledge/discovery_leads.yaml`, or `data/heritage_index.sqlite`.
  - **Hardware Deadbolt Boundary**: Live ECU write/flash routines remain strictly forbidden across `wiki/` content gates and discovery outputs; `sync-wiki` never mutates `wiki/` in place, and missing `private_references/` falls back cleanly to `profiles/turbo/` stubs and cleanroom seeds without raising errors.

