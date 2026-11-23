# LogicAIDE `.claude/` Harness Guide

The `.claude/` directory configures specialized subagents, path-scoped safety rules, and repeatable workflows for **LogicAIDE** (*Logic Module + Agentic IDE*). Every agent role and skill here mirrors deterministic `uv run logicaide ...` CLI entrypoints so Claude Code, Antigravity, Cursor, Codex CLI, and VS Code Copilot users operate under identical safety and verification guarantees.

## Directory Layout

- `settings.json` — Active day-1 **Hardware Deadbolt** permission deny rules (do not weaken or bypass).
- `agents/` — Focused subagent definitions (`rom-disassembly-analyst`, `telemetry-duckdb-analyst`, `hc11-emulator-tester`, `firmware-safety-reviewer`, `ui-visual-reviewer`).
- `rules/` — Path-scoped guardrails automatically loaded when touching `rom/`/`emulator/`/`staging/`, `telemetry/`/`mcp_servers/`/`issues/`/`data/`, or `ui/`/`docs/showcase/`.
- `skills/` — Repeatable workflow playbooks (`kickoff-interview`, `diagnose-drive`, `stage-rom-candidate`, `verify-ui`).

## Active Day-1 Hardware Deadbolt (`settings.json`)

`.claude/settings.json` enforces a non-negotiable read-only boundary around live vehicle flashing, secrets, and stock ROM baselines:

- **Denied Flashing / Serial Bash patterns**: `*ostrich2*`, `*flash_ecu*`, `*mptun*`, `*dd */dev/tty*`, serial redirection/tools (`*> /dev/tty*`, `*tee */dev/tty*`, `*cp */dev/tty*`, `*cat */dev/tty*`, `*pyserial*`, `*minicom*`, `*picocom*`, `*socat*`, `*stty */dev/tty*`, `*COM[0-9]*`), plus `Bash` mutation patterns against `rom/inputs` and `rom/stock_baselines`
- **Denied Secret Access (`Read` / `Grep` / `Bash`)**: `./.env`, `./.env.*`, `./secrets.env`, nested `**/.env*`, `**/secrets.env`, `**/*.pem`, `**/*.key`
- **Denied Baseline Edits/Writes (`Edit` / `Write`)**: `./rom/inputs/**`, `./rom/stock_baselines/**`

All candidate firmware outputs MUST land in `staging/candidates/<build_id>/` with an audited `REPORT.md`. A human operator reviews `REPORT.md` and executes any physical EPROM/Ostrich2/flash command outside AI agent control.

### Optional Per-Machine Shell Guard Hooks (`Stop` / `PreToolUse`)

`.claude/settings.json` ships the active cross-platform `permissions.deny` firewall above. Teams wanting additional per-machine shell hook enforcement can add `PreToolUse` and `Stop` script hooks in a local `.claude/settings.local.json` (gitignored):

1. **`PreToolUse` (`Bash`)**: Reject any indirect subshell script writing outside `staging/candidates/` with `.bin`/`.s19` extensions or opening raw serial descriptors.
2. **`PreToolUse` (`Edit|Write`)**: Reject writes containing raw `lat`/`lon` GPS columns in `tests/fixtures/**` or `staging/candidates/**`, and reject `.asm` edits without an accompanying `rom/models/*_twin.py` parity test run.
3. **`Stop`**: Run `uv run pytest -q` or `git diff --name-only` verification to ensure no staged `.bin` or unverified `.asm` edits were left without a `REPORT.md` check.

## Ready-to-Enable `.mcp.json` Configuration

Copy the block below into `.mcp.json` at the repository root (or enable per-IDE) once the Python package and `mcp_servers/` modules are present:

```json
{
  "mcpServers": {
    "ecu-telemetry": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_servers.ecu_telemetry"],
      "env": {
        "LOGICAIDE_DATA_DIR": "./data"
      }
    },
    "hc11-emulator": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_servers.hc11_emulator"],
      "env": {
        "LOGICAIDE_ROM_DIR": "./rom",
        "LOGICAIDE_STAGING_DIR": "./staging"
      }
    },
    "chrysler-heritage": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_servers.chrysler_heritage"],
      "env": {
        "LOGICAIDE_KNOWLEDGE_DIR": "./knowledge"
      }
    }
  }
}
```

## CLI-First Parity for Non-Claude Agents

Every FastMCP server maps 1:1 to deterministic `uv run logicaide` commands so non-Claude agents and CI runners get identical behavior without MCP overhead:

| FastMCP Server | CLI Equivalent | Primary Responsibilities |
| :--- | :--- | :--- |
| `ecu-telemetry` | `uv run logicaide telemetry ...` | Query 30s Zstd Parquet chunks, `load_cell_rollups`, `tuning_issues`, GPS-stripped fixture export |
| `hc11-emulator` | `uv run logicaide sim ...` | Run `$8000`–`$FFFF` `m68hc11` stimulus replays, Python Twin parity, Sobol / Optuna sweeps, stage checks |
| `chrysler-heritage` | `uv run logicaide heritage ...` | Hybrid BM25 + dense RAG across Tier 1/2/3 `knowledge/` digests with provenance tags |
| *(Visual Gate)* | `uv run python scripts/verify_visual_contract.py` | Run `audit_quiet_html.py`, `audit_workbench_probes.py`, and Selenium + Pillow PNG matrix |
