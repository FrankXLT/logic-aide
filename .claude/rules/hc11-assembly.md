---
paths:
  - "rom/**"
  - "emulator/**"
  - "staging/**"
---

# 68HC11 Assembly, Emulator & Staging Rule

Applies whenever reading, editing, assembling, or simulating files in `rom/`, `emulator/`, or `staging/`.

## Mandatory Invariants

1. **Immutable Baselines**:
   - Never edit, overwrite, or delete anything under `rom/inputs/` or `rom/stock_baselines/`.
   - Never invoke `ostrich2`, `flash_ecu`, `mptun`, or write to `/dev/ttyUSB*` / `COM*`.
2. **Python Behavioral Twin First**:
   - Before editing any `.asm` module, prototype and test the algorithm change in `rom/models/*_twin.py`.
   - Maintain bit-exact parity (8-bit/16-bit Q-format, rounding, carry saturation) between `rom/models/*_twin.py` and the 68HC11 assembly tested in `m68hc11`.
3. **SHA-256 Baseline Round-Trip Proof**:
   - Disassembled stock `.asm` must reassemble via `AS11` / Blackfoot to the exact byte-for-byte SHA-256 digest of the corresponding `rom/stock_baselines/` binary before applying functional edits.
4. **Vector Table & Real-Time Loop Budget**:
   - Verify `$FFC0`–`$FFFF` interrupt and reset vectors match baseline unless explicitly approved and documented in `REPORT.md`.
   - Enforce worst-case fuel/spark main loop execution strictly **`< 11 ms`** (`< 22,000` E-clock bus cycles at 2.0 MHz) across `m68hc11` stimulus replays and Sobol sweeps.
5. **8-Bit Carry & Calibration Safety**:
   - Explicitly guard `ADDA`, `SUBA`, `MUL`, and `IDIV` paths against unsigned/signed wraparound using `BCS`/`BCC` saturation clamps.
   - Keep `rom/rom_manifest.json` and `ctags` (`tags`) synchronized when symbols or table addresses change; never invent unverified hex offsets (use `No Verified Source Match` if absent).
   - Output candidate builds solely to `staging/candidates/<build_id>/` with an audited `REPORT.md`.
