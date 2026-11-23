# `rom/` — Splayed 68HC11 Assembly, Manifests & Python Behavioral Twins

This directory holds the splayed Motorola `68HC11` assembly representation of `1988–1993` Chrysler SMEC/SBEC `32 KB` (`$8000`–`$FFFF`) ROMs and their bit-exact Python counterparts.

## Layout & Workflow
- **Splayed `.asm` Modules**: Monolithic disassemblies are split by subsystem (reset/vectors, SCI loop, fuel `PEFTBL`, spark `SPKTBL`, wastegate/boost control, diagnostics) so human engineers and agents can edit small, focused files (`<= 500` lines, LF line endings).
- **`rom_manifest.json`**: Authoritative symbol table mapping routine names, table dimensions, RAM variables, and ROM offsets (`$8000`–`$FFFF`). Never guess hex addresses—always look up verified entries here.
- **`ctags` Navigation**: Generate `tags` from `rom_manifest.json` and splayed `.asm` sources so any IDE or agent jumps directly between 68HC11 labels and Python twin functions.
- **`rom/models/*_twin.py` (Python Behavioral Twins)**: Readable Python implementations of ECU fuel, spark, boost, and limiter logic. All algorithm changes are prototyped here first and must pass **bit-exact parity** (unsigned 8-bit wrapping, carry flags, table lookup interpolation) against `m68hc11` assembly simulation.
- **`rom/stock_baselines/` & `rom/inputs/`**: **Strictly immutable** reference zones. Baseline hashes are verified via SHA-256 round-trip reassembly before any candidate patch is staged.

## Safety Rules
- Never commit copyrighted OEM `.bin`, `.s19`, `.rom`, `.hex`, or `.srec` files (enforced by `.gitignore`).
- Candidate builds are never written here; they go to [`staging/candidates/`](../staging/candidates/README.md).
