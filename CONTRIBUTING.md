# Contributing to LogicAIDE

LogicAIDE is built for deterministic, verifiable calibration work on vintage 1988–1993 Turbo Chrysler SMEC/SBEC controllers. Every pull request—whether authored by a human engineer or an AI coding agent—must satisfy the cleanroom, parity, size, and visual contracts below.

---

## 1. Branching & PR Workflow

1. Create a focused feature or fix branch from `main` (e.g., `feat/peftbl-twin-parity`, `fix/sci-dead-cell-probe`, `docs/workflow-sync`).
2. Keep changes scoped to one subsystem or workflow (`WF-01`..`WF-06` in `docs/WORKFLOWS.md`).
3. Ensure `docs/WORKFLOWS.md` is updated whenever user flows, edge cases, or CLI/FastMCP flags change.
4. Complete every applicable item in [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) before requesting review.

---

## 2. Cleanroom Heritage Rules

- **Zero Copyrighted OEM Assets in Git**: Never commit Chrysler Factory Service Manual (FSM) PDF scans, copyrighted wiring diagrams, or OEM `.bin` / `.s19` / `.hex` ROM binaries.
- User-supplied stock ROMs belong strictly in gitignored [`rom/inputs/`](rom/inputs/README.md); raw scraped/archived documents belong in gitignored `data/heritage_raw/`.
- Only cleanroom technical notes, original spreadsheets/tables, and curated **3-tier provenance digests** may be committed under `knowledge/`.
- Never guess RAM addresses, hex offsets, or 60-way pinouts—cite verified symbols in `rom_manifest.json` or Tier 1/2 heritage docs, or explicitly return `No Verified Source Match`.

---

## 3. ROM & Simulation Verification Gates

### SHA-256 Round-Trip ROM Proof
Any change touching splayed 68HC11 `.asm` sources, `rom_manifest.json`, or assembler build scripts must pass the **round-trip stock baseline check**:
- Assemble the unmodified splayed baseline and assert its SHA-256 digest matches the user's verified stock `.bin` byte-for-byte before applying feature patches.

### Python Behavioral Twin Bit-Exact Parity Gate
- Prototype all fuel, spark, wastegate, and limiter logic changes in Python (`rom/models/*_twin.py`) first.
- Run the `m68hc11` parity suite to prove **bit-exact** output parity (including 8-bit wrapping/carry behavior and lookup interpolation) between Python and 68HC11 assembly across stimulus fixtures.
- Confirm worst-case main-loop + interrupt cycle count stays strictly below **11 ms** and `$FFC0`–`$FFFF` vectors remain valid.

---

## 4. Code Budget & Formatting Standards

- **File Length Budget**: Every modular source, config, and technical documentation file must stay **`<= 500` lines** (`650` lines absolute ceiling), excluding compiled single-file HTML showcase bundles (`docs/showcase/index.html`) that inline SVG figures and tokens for offline use. Prefer small, single-responsibility modules.
- **Function Length Budget**: Keep functions **`<= 100` lines** with shallow nesting and explicit types.
- **Line Endings**: **LF (`\n`) only**, enforced repository-wide by `.gitattributes` for 68HC11 cross-assembler (`AS11`/`Blackfoot`/`A09`) and Python parity. Never commit CRLF line endings.
- **CLI-First Parity**: Any new capability exposed through `mcp_servers/` (FastMCP) must also expose an equivalent `uv run logicaide ...` CLI command.

---

## 5. Visual Verification Requirements (`.quiet-ui.md`)

Changes touching `ui/`, `docs/showcase/`, or Quiet UI tokens must comply with [`.quiet-ui.md`](.quiet-ui.md) (`v9.5.0`) and pass all three verification gates before merge:

1. `audit_quiet_html.py` — Static HTML/CSS/JS drift check, deprecation blocklist, and `NIST SP 811` quantity calculus check (`Quantity / Unit`).
2. `audit_workbench_probes.py` — CDP checks for `.va-stage[data-width="workbench"]`, compact `<= 48px` topbar, `<optgroup>` drivetrain pickers, and chrome budget.
3. `verify_visual_contract.py` — 1:1 PNG visual captures verified across **`390x844`**, **`900x1000`**, **`1280x800`**, and **`1440x900`** viewports in both **Garage Dark** (`dark`) and **Sunlight High-Contrast** (`white`) themes with zero horizontal overflow.
