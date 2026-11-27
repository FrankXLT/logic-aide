> **Scope**: Cleanroom Tier 2 Digest of Acoustic Knock Listening Conditioning, `MSD DIS-2` (`PN 6212`), and `TechEdge` + `WinLog` Telemetry Logging
> **Boundary**: Capacitor isolation values, DIS-2 harness colors/fault LEDs, TechEdge/WinLog serial/sampling architecture only; omits PII and legacy binaries.

---
doc_id: "knock-listen-and-msd-dis2-notes"
title: "Knock Audio Isolation, MSD DIS-2 (PN 6212) Harness & TechEdge/WinLog Telemetry Notes"
trust_tier: "Tier 2"
source_type: "DIAGNOSTIC_HARDWARE_AND_TELEMETRY_DIGEST"
source_citation: "Knock Listen Device.pdf, MSD Programmable DIS-2 (PN 6212) Manual, WinLog - TechEdge Addendum (3/12/08), WinLog v1.0 & WinLogView Guides"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "Knock Listen p.1-3, pn6212 p.1-28, techedge p.1-7, winlog p.1-43"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
hex_symbols: []
symbols_referenced: ["PN 6212", "PN 8912", "PN 7550", "PN 2346", "PN 23121", "PN 23131", "Spectrogram 5.0", "2J0", "3H1", "N12"]
pins_referenced: ["12-Pin Deutsch", "8-Pin Deutsch", "3-Pin Weatherpack", "60-way Pin 2", "60-way Pin 3"]
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Knock Audio Isolation, `MSD DIS-2` (`PN 6212`) & `TechEdge`/`WinLog` Telemetry

## 1. Passive Capacitive Knock-Sensor Listening Interface (`Knock Listen Device.pdf`)

Piezoelectric factory knock sensors (`N12` on Chrysler `2.2L`/`2.5L` heads/blocks, tapped at ECU harness `1-2` / `2-24` / `Pin 2`–`Pin 3` per model-year FSM) require high-impedance AC isolation so monitoring hardware does not load the ECU's internal knock bias/diagnostic circuit:

- **Mandatory Isolation Component**: Series **non-polarized `0.01 uF`** capacitors in both signal paths prior to the headphone amp or PC microphone input. Polarized electrolytics or direct DC coupling distort ECU knock fault detection and voltage dividers.
- **Audio / Spectral Analysis**: Clean piezoelectric detonation events produce sharp metallic transients concentrated in the **`10–12 kHz`** band (`Spectrogram 5.0` dual-channel audio card capture via `1/8 in` stereo jack without external amplifier, or portable headphone amp such as `FiiO E5` with noise-canceling headphones during `WOT` pulls). Jump single-sensor outputs across L/R stereo inputs on single-knock Chrysler 4-cylinder engines.

---

## 2. `MSD Programmable DIS-2` (`PN 6212`) Electrical & Harness Reference

Capacitive-discharge (`490–505 V` primary capacitor) twin-coil waste-spark ignition control (`+/- 0.1 deg` & `+/- 10 RPM` accuracy; `20 deg` multispark duration `< 3,300 RPM`, transitioning to single full-energy spark above `3,300 RPM`; automatic `> 27 V` over-voltage & reverse-polarity shutdown):

| Connector / Group | Wire Color(s) | Signal Assignment & Chrysler Interface Notes |
| :--- | :--- | :--- |
| **Heavy Power Leads (`12 AWG`)** | `Heavy Red` / `Heavy Black` | Direct battery `+` (never alternator stud) and solid battery/engine `-` (`~0.7 A` per `1,000 RPM`; full output `11–18 V`, cranks down to `7 V`). |
| **`12-Pin` Deutsch (Ign / Coils)** | `Red` | Switched `+12 V` ignition enable (`DIS-2` On/Off) |
| **`12-Pin` Deutsch (Coils `1 & 2`)** | `Brown/Orange` (`Coil 1+ & 2+`), `Brown/White` (`Coil 1-`), `Brown/Green` (`Coil 2-`) | **Exclusive** coil primary conductors—stock harness coil wires must NOT remain tied to coil terminals |
| **`12-Pin` Deutsch (Triggers/Tach)** | `White` (`Coil 1`), `Green` (`Coil 2`), `Gray` (`Tach Out`) | ECU coil trigger inputs (`Gray` outputs `12 V` square wave, `30 deg` duty cycle). Note Chrysler `4-cyl` DIS installations (`Dodge`/`Plymouth`/`Chrysler` listed on Fig. 5) require **`PN 8912` Tach/Ignition Adapter** where driver loading is monitored; solid-core plug wires are prohibited. |
| **`2-Pin` `AMP` (Cam Sync)** | `Lt Blue` / `Lt Green` | Optional cam sync sensor (`PN 2346`) for individual-cylinder timing |
| **`2-Pin` Deutsch (`3-Step Rev`)** | `Light Blue` (`Burnout +12V`), `Dark Blue` (`Launch +12V`) | `+12 V` activation selects burnout or launch limit/retard/shift-light reset (`PN 8736` optional selector `3,000–12,500 RPM`) |
| **`8-Pin` Deutsch (`3-Step Retard` / `RPM Sw`)** | `Pink` (`Step 1`), `Violet` (`Step 2`), `Tan` (`Step 3 / 0.1–9.9s ramp`), `Purple/Blue` (`3A Gnd RPM Sw`) | Plus `Red/Green` (`3 A` fused) & `Yellow/Orange` shift-light pair; `Black` unused. |
| **`3-Pin` Weatherpack (`MAP`)** | `Brown/Violet`, `Brown/Yellow`, `Dark Brown` | External optional MAP boost retard sensor (`2-Bar` `2–29 psi` `PN 23121` or `3-Bar` `2–44 psi` `PN 23131`). |
| **Status LED & Troubleshooting** | `Code 2` / `Code 3` | Flashes **`Code 2`** = No Cam Sync, **`Code 3`** = Low Battery supply voltage. Handheld programmer (`PN 7550`) attaches via `9-pin` harness; settings persist in non-volatile memory when unpowered. |

---

## 3. `TechEdge` (`v1.5`/`v2.x`) & `WinLog` Multi-Device Telemetry Conventions

LogicAIDE Parquet telemetry ingestion aligns legacy `WinLog` / `TechEdge` field naming and scaling:

- **WinLog Platform (`100 ms` / `10 Hz` Default Poll)**: Supports up to `64` concurrent serial drivers (Chrysler SCI + wideband O2) logging `ASCII/CSV` fields for `WinLogView` XY/time-series replay (`0–1%` CPU polling in WinLog vs. busy-wait loops in older `TEWBLOG`).
- **TechEdge `v2.x` (`2J0` / `3H1` / `L16 >= xx61` firmware) Driver Config**: Configure `Sample Rate` (`100 ms` = `10 Hz`), `4EGT`/`6User` modes, `Lambda2` (`3H1`), `VSS Volts` (`2J0`), `K-Type Thermocouple` cold-junction compensation (referencing onboard `thermistor` field or raw voltage mode for non-K sensors), and `RPM Input Sparks/Rev` (`2` sparks/rev on standard `4-cylinder` waste-spark or distributor engines).
