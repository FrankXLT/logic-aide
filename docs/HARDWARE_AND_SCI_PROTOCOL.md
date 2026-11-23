# Hardware & Chrysler SCI Protocol Specification

> **Safety Gate Notice**: All LogicAIDE software agents operate under the **Hardware Deadbolt** (`CLAUDE.md` / `AGENTS.md`). Agents MUST NOT invoke `ostrich2`, `flash_ecu`, `mptun`, serial bootloaders, or write directly to `/dev/ttyUSB*` or `COM*`. Live harness pinning, continuity tests, and ROM flashing are strictly human-executed bench and vehicle procedures.

---

## 1. Chrysler 4-Cylinder ECU Hardware Evolution (1984–1993)

| Era | Controller Family | Architecture & Mounting | MCU & Memory | Key Diagnostic / Connector Traits |
| :--- | :--- | :--- | :--- | :--- |
| **1984–1987** | **LM / PM** (*Logic Module / Power Module*) | Two-box split: Logic Module in passenger kick panel; Power Module in engine-bay airbox/fender for power transistor cooling. | Motorola 6803-series (`LM`); discrete injector/coil/ASD drivers (`PM`). | Dual cabin-to-engine harness; early low-speed diagnostic connector near firewall/power module. |
| **1988–1989** | **SMEC** (*Single Module Engine Controller*) | Single engine-bay housing containing **two separate potted PCBs** (logic board + power board). | Motorola **68HC11** + external 28-pin JEDEC EPROM (`27C256`, 32 KB). | **60-way** logic connector + **14-way** power connector; Pin 11 unpopulated from factory on many Turbo harnesses. |
| **1990–1993** | **SBEC** (*Single Board Engine Controller*) | True **single-board** integration mounted in the engine bay. | Motorola **68HC11** (`E`-series/custom Chrysler mask) + consolidated memory/EPROM architecture. | Consolidated **60-way** main connector; several TBI/SBEC harnesses repurpose Pin 11 as Chassis/Power Ground. |

---

## 2. 60-Way Connector Reference & Harness Verification

> **YEAR / CALIBRATION VERIFICATION REQUIRED**: Chrysler reassigned several 60-way cavities across 1988–89 Turbo SMEC, 1988–89 TBI SMEC, and 1990–93 MPFI/TBI SBEC harnesses. Never assume pin parity from model name alone—verify against the Factory Service Manual (FSM) wiring diagram and physical continuity tests before probing or powering any circuit.

| 60-Way Pin | Signal Name | Typical Circuit / Wire Color | SMEC vs. SBEC Variant & Verification Requirement |
| :---: | :--- | :--- | :--- |
| **1** | **MAP Signal** | `K4` / `K01` (`DG/RD*`) | Manifold Absolute Pressure analog input (`0–5 V`). **Verify Year/Calibration**. |
| **2** | **CTS** *or* **Knock / Detonation** | `N12` (`BK/LG*`) *or* `K02` (`TN/BK*`) | **YEAR / CALIBRATION VERIFICATION REQUIRED**: Carries **Knock / Detonation** on early 1988–89 Turbo SMECs (where **Pin 3** carries `CTS`), **versus Coolant Temp (`CTS`)** on later SMEC/SBEC (e.g., 1990–93 TBI SBEC). |
| **3** | **CTS (Early Turbo SMEC)** *or* **Model-Year Specific** | `K10` (`TN/WT`) *or* unpopulated/reassigned | **YEAR / CALIBRATION VERIFICATION REQUIRED**: Supplies **Coolant Temp (`CTS`)** on early 1988–89 Turbo SMECs that place Knock on Pin 2; verify exact FSM cavity assignment on later SMEC/SBEC harnesses before probing. |
| **4** | **Sensor Signal Return** | `K4` return (`BK/LB*`) | Low-current analog ground return for MAP, TPS, and temperature sensors. **Verify Year/Calibration**. |
| **5** | **Sensor / Logic Ground Return** | `BK/WT*` | Secondary sensor/logic ground return (often bonded to cylinder head/fuel rail); check `< 0.5 Ω` to battery negative. **Verify Year/Calibration**. |
| **6** | **+5V Sensor Reference** | `VT/BK` / `VT/OR` | Regulated `+5.0 V` ECU supply feeding MAP and TPS potentiometers. **Verify Year/Calibration**. |
| **11** | **CRITICAL: Bootstrap `Vpp` *vs.* Chassis Ground** | Blank cavity (SMEC) *vs.* `BK/TN` (SBEC TBI) | **MANDATORY OHMMETER CHECK BEFORE ENERGIZING**: Factory blank cavity on 1988–89 Turbo SMEC harnesses—wired to `+12V` (BoostButton flash mod) or `~21V` (`12V + 9V` series bootstrap for stock SMEC ROM read). **HOWEVER**, several 1990–93 SBEC/TBI harnesses wire Pin 11 directly to **Power / Chassis Ground** (alongside Pin 12). Energizing `+12V`/`~21V` into a grounded Pin 11 creates a **dead short**. |
| **12** | **Power / Chassis Ground** | `Z1` (`BK/TN` / `BK*`) | Main controller power/chassis ground return; always verify with an ohmmeter whether Pin 11 is bonded to Pin 12 ground on SBEC/TBI harnesses before energizing bootstrap power. |
| **22** | **TPS Signal** | `K22` (`OR/DB`) | Throttle Position Sensor wiper voltage (`~0.5–4.7 V`). **Verify Year/Calibration**. |
| **25** | **SCI TX** *(ECU $\to$ Host / Scan Tool)* | `D21` (`PK`) | **YEAR / CALIBRATION VERIFICATION REQUIRED**: Present on TBI/SBEC and supported SMEC harnesses; some early 1988–89 Turbo I/II SMEC harnesses leave Pin 25 blank and route SCI to the under-hood diagnostic connector. |
| **41** | **O2 Sensor Signal** | `K41` (`BK/DB`) | Exhaust narrowband zirconia lambda input (`0–1 V`). **Verify Year/Calibration**. |
| **45** | **SCI RX** *(Host / Scan Tool $\to$ ECU)* | `D20` (`LG`) | **YEAR / CALIBRATION VERIFICATION REQUIRED**: Present on TBI/SBEC and supported SMEC harnesses; certain early Turbo I SMECs assign Pin 45 to `C20` (A/C switch/clutch) and route SCI via the diagnostic connector. |

### Pre-Connect Pin 11, Variant & SCI Safety Checklist (Human Only)
1. Confirm controller PCB generation (**dual-board SMEC** vs. **single-board SBEC**) and exact calibration part number.
2. With ignition **OFF** and the 60-way plug disconnected from the ECU, measure resistance from harness **Pin 11 to Pin 12 / Battery Negative / Chassis Ground** with a DMM. Any continuity (`< 100 kΩ` / continuity beep) confirms a grounded Pin 11 harness—**LOCK OUT bootstrap voltage injection**.
3. Verify exact model-year FSM assignments for **Pin 2 / Pin 3** (`CTS` vs. `Knock`) and confirm **Pin 25 (`SCI TX`) / Pin 45 (`SCI RX`)** continuity to the diagnostic pigtail (checking that Pin 45 is not `C20` A/C clutch on early Turbo I SMEC harnesses) before connecting an isolated FTDI interface.

---

## 3. Chrysler SCI Serial Bus Protocol Framing

The 68HC11 internal SCI UART (`$102B`–`$102F`) runs asynchronous 8N1 (`1 start, 8 data, no parity, 1 stop`) derived from the `2.0 MHz` E-clock (`8.0 MHz` crystal $\div 4$).

```text
[Ignition On / Reset]
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│  LOW-SPEED DIAGNOSTIC MODE (7812.5 baud, 8N1, ECHOED)     │
│  Commands: 0x10, 0x11, 0x12, 0x13, 0x17, 0xFE             │
└───────┬───────────────────────────────────────────────────┘
        │ Host sends 0x12
        │ ECU echoes 0x12 at 7812.5 baud (handshake ACK)
        ▼
┌───────────────────────────────────────────────────────────┐
│  HIGH-SPEED RAM STREAM MODE (62,500 baud, 8N1, ZERO-ECHO) │
│  Frame: Host [0xF2 | 0xF4] [AA] ──► ECU [BB]              │
│  Quarantine Gate: Unmapped AA timeout ──► mark NULL       │
└───────────────────────────────────────────────────────────┘
```

### 3.1 Low-Speed Mode (`7812.5` baud, Echoed)
In low-speed mode, the ECU echoes each accepted command byte before emitting payload bytes:

| Opcode | Name | Request / Framing | ECU Response Behavior |
| :---: | :--- | :--- | :--- |
| **`0x10`** | **Read Stored DTCs** | `[0x10]` | Echoes `0x10`, streams stored two-digit/hex fault codes, terminates with `0xFE` (and checksum byte on applicable ROMs). |
| **`0x11`** | **Error Bits / Pending Flags** | `[0x11]` | Echoes `0x11`, returns internal error-flag/pending-fault bitmask bytes. |
| **`0x12`** | **High-Speed Switch** | `[0x12]` | Echoes `0x12` at `7812.5` baud, then immediately switches `BAUD` prescaler to **`62,500` baud** zero-echo mode. |
| **`0x13`** | **Actuator Test Mode (ATM)** | `[0x13] [actuator_id]` | Echoes `0x13` and activates targeted output driver tests (injectors, ignition coil, AIS motor, ASD/fan relays) under safety interlocks. |
| **`0x17`** | **Clear Stored DTCs** | `[0x17]` | Echoes `0x17` and clears non-volatile fault-code RAM counters. |
| **`0xFE`** | **Diagnostics / Exit / Marker** | `[0xFE]` | Serves as diagnostic/low-speed session reset or end-of-frame delimiter depending on calibration state. |

### 3.2 High-Speed Mode (`62,500` baud, Zero-Echo)
After the `0x12` echoed handshake completes, the host switches immediately to `62,500` baud (zero command echo) and polls internal 68HC11 RAM via Chrysler virtual table selectors (`0xF0`–`0xFD`), predominantly **`0xF2`** and **`0xF4`** on SMEC/SBEC calibrations:

```text
Host TX (2 bytes):  [0xF2 | 0xF4] [AA]      (Table selector FX + 1-byte RAM offset AA)
ECU  RX (1 byte):   [BB]                    (Raw uint8 RAM value; NO echo of FX or AA)
```

- **Byte-to-Address Resolution**: Each `(FX, AA)` pair maps through a calibration-specific ROM lookup table inside `sci_diag.asm` to a 68HC11 RAM address.
- **No Unverified Offsets**: LogicAIDE **never guesses** `0xF2`/`0xF4` offsets across ROM masks. Every polled `AA` offset MUST originate from the vehicle's verified `rom_manifest.json` `ram_watchlist` or a `Tier 1`/`Tier 2` heritage entry (`AGENTS.md`).

---

## 4. Dead-Cell Timeout Quarantine

Polling an unmapped or disabled `0xF2`/`0xF4` offset `AA` on certain SMEC/SBEC ROM masks causes the ECU's SCI dispatcher to drop the request without transmitting `BB`. Because standard polling loops block until a serial read timeout (`15–50 ms` per missing byte), even **two dead offsets** inside a 20-cell watchlist collapse loop throughput from `> 150 Hz` to `< 15 Hz` and introduce variable timestamp skew into `load_cell_rollups`.

### Startup Probe & Quarantine State Machine
1. **Startup Watchlist Probe**: Immediately after the `0x12` transition to `62,500` baud, the logger sends each configured `(FX, AA)` pair once in isolation with a strict `12 ms` probe timeout (`probe_timeout_ms = 12`).
2. **Quarantine Promotion Rule**:
   - At startup: if an offset fails `2` consecutive isolated probes (`NO_REPLY`), mark status = `QUARANTINED` (`reason = STARTUP_PROBE_TIMEOUT`).
   - During live driving: if an active offset incurs `3` consecutive read timeouts, demote it from `ACTIVE` to `QUARANTINED` (`reason = RUNTIME_TIMEOUT`) and flush the UART RX buffer to prevent 1-byte frame slip.
3. **Data & UI Contract**:
   - Quarantined cells are **dropped from the active hot poll ring** so healthy cells sustain full `62.5 kbaud` cadence.
   - Quarantined columns are written to 30s Parquet chunks as **explicit `null`** (never `0` or last-value hold, avoiding false lean/cold/zero-RPM artifacts).
   - The telemetry strip surfaces a `DEAD-CELL QUARANTINED` annunciator listing the quarantined `(FX, AA)` hex offsets; re-enabling requires explicit user re-probe.

---

## 5. BoostButton / FTDI Isolation vs. Moates Ostrich 2.0

### 5.1 BoostButton / FTDI (`FT232RL`) Serial Isolation
The `FT232RL` USB-UART bridge supports integer/sub-integer clock division (`3 MHz / N`) yielding exact `7812.5` (`0%` error) and `62,500` (`0%` error) baud rates where standard 16550 UARTs fail. However, automotive benches expose `FT232RL` `3.3V`/`5V` TTL I/O and host laptops to `+12V` / `~21V` Pin 11 bootstrap supplies, inductive alternator transients, and chassis ground offsets.

Required hardware protection layer between vehicle harness and USB host:
- **Galvanic Isolation**: USB-side digital isolator (`ADuM3160` or opto-isolated `TX`/`RX` barrier) breaking ground loops between laptop chassis and vehicle battery negative.
- **Line Clamping & Series Impedance**: `1 kΩ`–`4.7 kΩ` series protection resistors + bidirectional TVS / `5.1 V` Zener clamps on `Pin 25` (`SCI TX`) and `Pin 45` (`SCI RX`) so accidental `12V`/`21V` contact cannot back-feed the FTDI IC.
- **Bootstrap Path Separation**: Physical momentary switch / interlock on `Pin 11` completely isolated from `Pin 25`/`Pin 45` logic rails, activated **only** after verifying SMEC board identity and Pin 11 ground isolation.

### 5.2 Moates Ostrich 2.0 Orientation (28-Pin vs. 32-Pin Sockets)
When running live SRAM emulation on a socketed SMEC/SBEC instead of flashing EEPROM/BoostButton hardware:
- **28-Pin JEDEC Top-Justification & Pin 1 Alignment**:
  - The red stripe on the Ostrich 2.0 28-pin ribbon header marks **Pin 1** and MUST align strictly with the **notch / Pin 1 dot** on the ECU DIP-28 socket.
  - On ECUs or socket adapters carrying a 32-pin JEDEC footprint receiving a 28-pin emulator cable (or when uploading a `32 KB` `27C256` binary into a larger Ostrich SRAM bank), follow **top-justified / end-justified** alignment:
    - **Hardware pins**: Pin 1 / Vcc/GND alignment per the adapter's JEDEC pinout—leave unused upper pin positions only where the JEDEC adapter specification dictates (never blindly center a 28-pin header in a 32-pin socket, which shifts `A0..A14` and shorts `Vcc`).
    - **Ostrich SRAM buffer (`32 KB` ROM)**: Load `32,768` bytes **top/end-justified** into the upper `$8000`–`$FFFF` window of the target bank with side switches set to **`28-pin`** mode (internal `100 kΩ` pull-ups hold upper unused address lines high).
- **Zero-Agent-Write Boundary**: LogicAIDE generates verified `32,768`-byte `.bin` artifacts only inside `staging/candidates/<build_id>/`; human operators manually load the image to Ostrich 2.0 or bench flashers outside the agent sandbox.
