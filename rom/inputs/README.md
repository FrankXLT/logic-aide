# `rom/inputs/` — User-Supplied Stock ROM Inputs (Gitignored)

Place your own legally obtained `32 KB` (`$8000`–`$FFFF`) Chrysler SMEC or SBEC stock `.bin` binaries in this directory for local disassembly, SHA-256 round-trip verification, and `m68hc11` simulation.

## Rules & Immutability
1. **Gitignored**: `.bin`, `.s19`, `.rom`, `.hex`, and `.srec` files are excluded in `.gitignore`. Never force-add or commit OEM binaries to Git.
2. **Read-Only to Agents**: AI agents and build pipelines treat `rom/inputs/` as strictly immutable. Never patch or overwrite files in this folder.
3. **SHA-256 Baseline Verification & Promotion**:
   - Compute and record the stock binary's SHA-256 hash upon intake.
   - Human operators promote verified `.bin` files into [`../stock_baselines/`](../stock_baselines/README.md) alongside `rom/rom_manifest.json`.
   - Verify that reassembling the unmodified splayed `.asm` snapshot reproduces the exact baseline SHA-256 digest before any calibration work begins.
