# Changelog

All notable changes to **LogicAIDE** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-23

### Added
- **Greenfield Project Bootstrap**: Initialized repository configuration, `.gitattributes` (LF line endings pinned for 68HC11 assemblers and Python parity), and `.gitignore` (excluding OEM `.bin`/`.s19` ROMs, Parquet telemetry lakes, and `data/heritage_raw/`).
- **Agent & IDE-Neutral Contract**: Added `AGENTS.md`, `CLAUDE.md`, and `.claude/agents/` role specifications (`rom-disassembly-analyst`, `telemetry-duckdb-analyst`, `hc11-emulator-tester`, `firmware-safety-reviewer`, `ui-visual-reviewer`).
- **Quiet UI v9.5.0 Contract**: Added `.quiet-ui.md` specifying Garage Dark & Sunlight High-Contrast polarity, `390x844` / `900x1000` / `1280x800` / `1440x900` viewports, `NIST SP 811` metrology discipline, and visual verification gates.
- **Core Documentation & GitHub Templates**: Added `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, PR template, drive-anomaly / ROM-feature issue templates, and 12 subsystem `README.md` scaffolds.
