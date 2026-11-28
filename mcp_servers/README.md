# `mcp_servers/` — FastMCP Servers & CLI-First Core Parity

`mcp_servers/` exposes LogicAIDE's three core domains to MCP-compatible coding agents (Claude Code, Antigravity, Cursor, Codex CLI, VS Code Copilot) while sharing 100% of their business logic with the `uv run logicaide` CLI.

## Server Inventory

| FastMCP Server | Tools | CLI Counterpart | Capabilities |
| :--- | :---: | :--- | :--- |
| **`ecu-telemetry`** | `6` | `uv run logicaide telemetry {watch,ingest,rollups,issues,export-fixture}` | `list_vehicles`, `query_load_cell_rollups`, `get_drive_timeseries`, `list_bookmarks`, `create_or_update_tuning_issue`, & `export_gps_stripped_fixture` (strictly read-only toward hardware) |
| **`hc11-emulator`** | `6` | `uv run logicaide sim {splay,manifest-sync,verify-sha256,parity,replay,sweep,stage}` | `inspect_rom_manifest`, `verify_roundtrip_sha256`, `run_twin_parity`, `run_single_replay`, `run_monte_carlo_sweep`, & `evaluate_staging_safety_gate` (`$8000`–`$FFFF` emulation & Sobol/Optuna sweeps) |
| **`chrysler-heritage`** | `7` | `uv run logicaide heritage {crawl,parse-web,parse-docs,build-index,query,diagram,discover,sync-wiki}` | `search_heritage_docs`, `get_verified_schematic`, `lookup_60way_pin`, `lookup_sci_offset`, `get_provenance_card`, `discover_heritage_leads`, & `sync_heritage_wiki` (3-tier RRF RAG & Gate 4a/4b schematics) |

## Root `.mcp.json` & Offline `--stdio` Mode
- **Zero-Config Discovery**: Root `.mcp.json` registers all three servers (`ecu-telemetry`, `hc11-emulator`, `chrysler-heritage`) via `uv run --offline python mcp_servers/<server>.py --stdio`.
- **Built-In `--stdio` JSON-RPC**: Each server supports optional `FastMCP` registration when `mcp`/`fastmcp` is installed and includes a built-in zero-dependency `--stdio` JSON-RPC transport for offline agent sessions.

## Workbench CLI Bridges (`brief` & `readiness`)
- **`uv run logicaide readiness [--auto-rebuild] [--strict] [--json]`**: Audits `catalog.sqlite`, DuckDB Parquet `load_cell_rollups`, partitioned ROM manifests/`tags`, and `data/heritage_index.sqlite` freshness (auto-rebuilding stale heritage indexes when `wiki/` or `knowledge/` Markdown is newer).
- **`uv run logicaide brief {session,cell,issue,build}`**: Emits deterministic, GPS/PII-free context packets with suggested `mcp_follow_up` tool calls (`build` adds the 60-way Pin 11 reminder on `PASS` and suppresses programmer commands on `BLOCKED`).
- **Issue & Replay Parity**: `uv run logicaide telemetry issues list|create|update` mirrors `list_bookmarks` / `create_or_update_tuning_issue`, while `uv run logicaide sim replay` and `sim splay` mirror `run_single_replay` and ROM splaying.

## Design Rules
- **Thin Protocol Wrappers**: FastMCP tools must contain zero duplicated business logic; they delegate directly to the same Python library functions invoked by `uv run logicaide`.
- **Hardware Deadbolt Enforced**: None of the MCP servers import serial flashers (`ostrich2`, `flash_ecu`) or open `/dev/ttyUSB*` / `COM*` in write/programming mode.
- **Agent-Neutral**: Every workflow (`WF-01`..`WF-07`) works identically from the CLI even when MCP is disabled.
