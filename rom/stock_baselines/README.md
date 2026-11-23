# `rom/stock_baselines/` — Verified Immutable Stock ROM Baselines (Gitignored)

Verified, SHA-256 checksummed `32 KB` (`$8000`–`$FFFF`) factory Chrysler SMEC and SBEC binaries promoted manually by a human operator from `rom/inputs/` live here as immutable golden baselines.

## Rules & Handoff from `rom/inputs/`
1. **Manual Human Intake Only**: After verifying a user-supplied stock binary in `rom/inputs/`, the human operator copies or links the checksummed `.bin` into `rom/stock_baselines/<rom_id>.bin` and records its `baseline_sha256` in `rom/rom_manifest.json`.
2. **Gitignored & Write-Denied**: `.bin`, `.s19`, `.rom`, `.hex`, and `.srec` files are excluded via `.gitignore`, and `.claude/settings.json` denies agent `Edit`/`Write`/`Bash` mutations under both `rom/inputs/` and `rom/stock_baselines/`.
3. **Clean Round-Trip Gate**: Any modification to splayed `.asm` modules under `rom/src/` must prove that assembling the unmodified baseline snapshot reproduces the exact `rom/stock_baselines/<rom_id>.bin` SHA-256 before staging candidate builds in [`staging/candidates/`](../../staging/candidates/README.md).
