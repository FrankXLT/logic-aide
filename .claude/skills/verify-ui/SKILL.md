---
name: verify-ui
description: Run audit_quiet_html.py, audit_workbench_probes.py, and verify_visual_contract.py across 390x844, 900x1000, 1280x800, and 1440x900 in Sunlight High-Contrast and Garage Dark themes.
---

# Verify UI Skill

Executes the full `.quiet-ui.md` (`v9.5.0`) static, DOM-probe, and visual screenshot verification suite for `ui/` and `docs/showcase/`.

## Step 1 — Static Contract & Drift Audit (`audit_quiet_html.py`)

Run the static HTML/CSS/JS audit to verify `.quiet-ui.md` conformance, deprecation blocklist absence, `@media (scripting: none)` offline fallback, `<optgroup>` hardware selectors, `NIST SP 811` units, `ISA-101` calm alarm classes, zero emojis in chrome, and `quiet-evidence` SVG usage:

```bash
uv run python scripts/audit_quiet_html.py
```

## Step 2 — Headless DOM & Ergonomics Probes (`audit_workbench_probes.py`)

Run CDP / Selenium DOM probes checking `.va-stage[data-width="workbench"]` geometry, `<= 48px` topbar height, left-of-brand nav control, `>= 44px` coarse touch targets on mobile, `0px` horizontal overflow, and N1 rail behavior at `>= 1100px`:

```bash
uv run python scripts/audit_workbench_probes.py
```

## Step 3 — Visual Matrix Screenshot Verification (`verify_visual_contract.py`)

Capture and inspect 1:1 PNGs across all required viewports (`390x844`, `900x1000`, `1280x800`, `1440x900`, `paired_1904x971`) in both **Sunlight High-Contrast** (`white`) and **Garage Dark** (`dark`) theme polarities:

```bash
uv run python scripts/verify_visual_contract.py
```

## Step 4 — Human / Reviewer Sign-Off (`ui-visual-reviewer`)

- Inspect generated PNG artifacts directly (do not rely solely on exit codes) to confirm crisp contrast (`>= 4.5:1`), legible monospace telemetry numerals, uncluttered `1280x800` HMI framing, and unclipped axis labels.
- Report exact passing/failing viewports and themes; any failure blocks merge until resolved and re-verified.
