# `mcp_servers/` — FastMCP Servers & CLI-First Core Parity

`mcp_servers/` exposes LogicAIDE's three core domains to MCP-compatible coding agents (Claude Code, Antigravity, Cursor, Codex CLI, VS Code Copilot) while sharing 100% of their business logic with the `uv run logicaide` CLI.

## Server Inventory

| FastMCP Server | CLI Counterpart | Capabilities |
| :--- | :--- | :--- |
| **`ecu-telemetry`** | `uv run logicaide telemetry ...` | Query 30s Zstd Parquet chunks, `load_cell_rollups`, dead-cell quarantine reports, & `tuning_issues` bookmarks (strictly read-only toward hardware) |
| **`hc11-emulator`** | `uv run logicaide sim ...` | Run `m68hc11` `$8000`–`$FFFF` stimulus replays, Python Twin bit-exact parity assertions, `< 11 ms` timing checks, & Sobol/Optuna sweeps |
| **`chrysler-heritage`** | `uv run logicaide heritage ...` | Hybrid `FTS5` + `sqlite-vec` search over `knowledge/` 3-tier provenance digests & `rom_manifest.json` symbol lookups |

## Design Rules
- **Thin Protocol Wrappers**: FastMCP tools must contain zero duplicated business logic; they delegate directly to the same Python library functions invoked by `uv run logicaide`.
- **Hardware Deadbolt Enforced**: None of the MCP servers import serial flashers (`ostrich2`, `flash_ecu`) or open `/dev/ttyUSB*` / `COM*` in write/programming mode.
- **Agent-Neutral**: Every workflow (`WF-01`..`WF-06`) works identically from the CLI even when MCP is disabled.
