# `staging/candidates/` — Human-Only Hardware Deadbolt Staging Pen

`staging/candidates/` is the **final quarantine and sign-off pen** where validated candidate ROMs await human inspection. AI agents may write candidate builds here, **never** directly to an EPROM emulator or vehicle ECU.

## Required Staging Artifact Bundle (`staging/candidates/<build_id>/`)
Every candidate directory must contain:
1. **Candidate `.bin` (`32 KB`, gitignored)**: Assembled `68HC11` binary ready for manual EEPROM burn or Ostrich2 load.
2. **`REPORT.md`**: Human-readable summary referencing the linked `tuning_issues` / `optimization_campaigns` IDs, baseline SHA-256, and modified `rom_manifest.json` symbols.
3. **Checksum & Vector Proof**:
   - Baseline SHA-256 vs candidate SHA-256 and 8-bit/16-bit ROM checksum verification.
   - `$FFC0`–`$FFFF` reset/interrupt vector integrity check.
   - Worst-case `m68hc11` loop cycle proof (`< 11 ms`) and 8-bit carry/overflow audit.
   - Mechanical boost/wastegate & spark advance clamp verification.
4. **Monte Carlo Trace Diff**: Before/after `m68hc11` + Python Twin simulation deltas across the target GPS-stripped road stimulus slices.
5. **Copyable Manual Flash CLI**: Exact command for the **human operator** to copy, inspect, and execute manually (for example, after verifying 60-way **Pin 11 SMEC-vs-SBEC continuity** and **12V/21V FTDI isolation** per [`SECURITY.md`](../../SECURITY.md)).

> **Gate Policy**: If any check fails, the UI and `REPORT.md` display **`SAFETY GATE BLOCKED`** and omit the manual flash command.
