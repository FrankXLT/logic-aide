# Security & Hardware Safety Policy (`SECURITY.md`)

LogicAIDE operates on real 1988–1993 Turbo Chrysler engine controllers (`SMEC` and `SBEC`) capable of damaging engines, wiring harnesses, and laptops if misused. **Safety boundaries in this document are non-negotiable.**

---

## 1. Strict Engine/ECU Hardware Deadbolt

- **Zero Automated Flashing**: AI agents and automated scripts are **strictly prohibited** from writing to vehicle serial ports (`/dev/ttyUSB*`, `/dev/ttyACM*`, `COM*`), invoking `ostrich2`, `flash_ecu`, or running SCI bootstrap loaders.
- **Read-Only Telemetry Only**: Automated telemetry tools may only *read* the `62.5 kbaud` SCI stream.
- **Staging Pen Workflow**: All modified ROMs must be written exclusively to `staging/candidates/<build_id>/` alongside a `REPORT.md`, SHA-256 checksum, `$FFC0`–`$FFFF` vector proof, `< 11 ms` cycle proof, and Monte Carlo diff. Only a human operator may inspect those artifacts and copy/execute the manual flash command.

---

## 2. Critical Electrical & Harness Warnings

> **WARNING — 12V / 21V SCI FTDI Bootstrap Isolation**
> Never connect a `5V` or `3.3V` USB-to-TTL FTDI adapter directly to Chrysler SCI bootstrap or programming pins without verified optical/diode isolation and current limiting. Applying `+12V` or `+21V` bootstrap programming voltage to an unprotected FTDI interface can destroy the adapter, damage the host laptop USB controller, or latch up the ECU's `68HC11` port pins.

> **WARNING — 60-Way Pin 11 SMEC-vs-SBEC Continuity Hazard & Variant Pinouts (Pins 2, 3, 25, 45)**
> Before applying `+12V` / `+21V` programming/bootstrap power or connecting a bench harness to the Chrysler 60-way connector:
> 1. **Explicitly identify the ECU hardware generation (`SMEC` vs `SBEC`)** visually and by part number.
> 2. **Verify Pin 11 harness continuity with a multimeter**: On **SMEC** harnesses, 60-way **Pin 11 is unpopulated/open**; on **SBEC** harnesses, **Pin 11 can be tied to sensor/logic/power ground** (alongside Pin 12). Applying `+12V`/`+21V` to Pin 11 on an SBEC harness without lifting/isolating Pin 11 causes an immediate **direct ground short**, risking melted harness wiring, blown fuses, or destroyed ECU traces.
> 3. **Verify year/calibration assignments for Pins 2, 3, 25, and 45** (`Pin 2`/`Pin 3` swap `CTS` vs. `Knock`, and early 1988–89 Turbo SMECs omit Pin 25 or assign Pin 45 to `C20` A/C clutch) against `docs/HARDWARE_AND_SCI_PROTOCOL.md` and the exact model-year FSM before probing. Never let an AI agent instruct applying `+12V`/`+21V` without first prompting for board ID and Pin 11 continuity verification.

---

## 3. Mechanical & Calibration Safety Clamps

Every candidate ROM staged for human review must enforce hard calibration safety boundaries:
- **Wastegate & Overboost Fuel Cut**: Verify mechanical wastegate base pressure and ROM overboost fuel-cut thresholds (`MAP` ceiling) are intact and never silently disabled.
- **Spark Advance Clamps**: High-load/high-boost timing cells must remain bounded against knock-prone advance values; Python Twin & Sobol Monte Carlo sweeps check for signed/unsigned 8-bit overflow wrap (`0xFF -> 0x00`) that could flip retard into dangerous advance.
- **8-Bit Carry & `$FFC0`–`$FFFF` Vectors**: The `firmware-safety-reviewer` gate must verify interrupt vectors and stack safety prior to staging sign-off.

---

## 4. Immutable Baselines & GPS Privacy

- **Immutable Stock Baselines**: `rom/inputs/` and `rom/stock_baselines/` are strictly read-only to agents and build pipelines. Never overwrite, patch in place, or commit OEM `.bin` files.
- **Mandatory GPS Privacy Stripping**: Raw latitude, longitude, and home/route metadata must be stripped from all telemetry slices before exporting into `tests/fixtures/` or `staging/candidates/`.

---

## 5. Reporting Safety or Vulnerability Issues

If you discover a bug in the 68HC11 parity check, checksum/vector validator, SCI dead-cell quarantine, or hardware safety guardrails, open an issue immediately and mark any affected candidate builds in `staging/candidates/` as **DO NOT FLASH**.
