> **Scope**: Cleanroom Tier 2 Chrysler Automatic & Manual Transaxle Service Digest (`ATSG A404/A413/A415/A470`, `PartNumbers A604/41TE/A606/42LE/42RLE`, and Wave D ATSG Reference Index)
> **Boundary**: Hydraulic test port pressures, fluid specs, thrust-washer/bearing clearances, fastener torques, clutch stack dimensions, and Wave D ATSG model coverage only; excludes verbatim copyrighted repair narrative.

---
doc_id: "atsg-a413-and-transaxle-reference"
title: "Chrysler TorqueFlite A404/A413/A415/A470 Service Specs & A604/41TE/A606 Part-Number Atlas"
trust_tier: "Tier 2"
source_type: "DRIVETRAIN_SERVICE_DIGEST"
source_citation: "ATSG A404/A413/A415/A470 Service Manual (p.8-10, 70-72) & Chrysler FWD A604/40TE/41TE/A606/42RLE Part Catalog (p.296-297) + Wave D ATSG Index"
warc_capture_date: "2026-11-26T00:00:00Z"
page_ref: "ATSG A404/A413/A415/A470 p.8-10 & p.70-72; PartNumbers_A604 p.296-297"
ecu_families: ["LM_PM", "SMEC_DUAL_BOARD", "SBEC_SINGLE_BOARD"]
ecu_scope: ["LM_6803", "SMEC_88_89", "SBEC_90_93", "SBEC_II_94_95"]
engine_scope: ["2.2L_K", "2.5L_K", "2.0L_ECC", "2.4L_EDZ"]
hex_symbols: []
symbols_referenced: ["A404", "A413", "A415", "A470", "A412", "A460", "A520", "A555", "A568", "A604", "41TE", "A670", "C-3293"]
pins_referenced: []
mermaid_verification: "NONE"
cleanroom_status: "DISTILLED_ATTRIBUTED"
verified_by: "human_maintainer"
---

# Chrysler TorqueFlite `A404` / `A413` / `A415` / `A470` & `A604`/`41TE` Transaxle Digest

## 1. `A404` / `A413` / `A415` / `A470` Clutch/Band Application & Hydraulic Tests (`ATSG p.8–10`)

- **Fluid Temperature & Test Setup**: Warm ATF (`Dexron II` OEM spec; **`1.8 L` / `2.0 U.S. qt`** dry/service fill check) to **`150–200 °F`**, raise front drive wheels safely on hoist, disconnect throttle and shift cables for external lever control, and use `150 psi` gauges on line/cooler/kickdown ports plus a **`300 psi` gauge (`C-3293`)** on the **`Low-Reverse Pressure`** rear-servo port (`Fig. 3`).
- **Selector Element Matrix**:
  - `R (2.10)`: Front Clutch + Low-Reverse Band
  - `D-1 / 2-1 (2.69)`: Rear Clutch + Overrunning Clutch (`1-Low`: Rear Clutch + Low-Reverse Band)
  - `D-2 / 2-2 (1.55)`: Rear Clutch + Front (Kickdown) Band
  - `D-3 Direct (1.00)`: Front Clutch + Rear Clutch

| Hydraulic Test | Selector / RPM | Port(s) Monitored (`Fig. 3`) | Specified Pressure Range (`Min -> WOT Lever`) | Diagnostic Fault Isolation |
| :--- | :--- | :--- | :--- | :--- |
| **Test 1** | **`"1"` (Low) @ `1000 RPM`** | `Line Pressure` + `Low-Reverse` (Rear Servo) | Line **`52–58 psi`** (forward) -> **`80–88 psi`** (rearward); Rear servo within **`3 psi`** of line | Pump output, regulator, rear clutch & rear servo seal integrity |
| **Test 2** | **`"2"` (Second) @ `1000 RPM`** | `Line Pressure` + lower cooler line tee | Line **`52–58`** -> **`80–88 psi`**; **Lube `10–25 psi`** (forward) -> **`10–35 psi`** (rearward) | Pump flow, cooler restriction & rear clutch circuit |
| **Test 3** | **`"D"` (Drive) @ `1600 RPM`** | `Line Pressure` + `Kickdown Release` (`Front Servo`) | Line **`52–58`** -> **`80–88 psi`**; Front servo release within **`3 psi`** of line (up to kickdown) | Rear clutch, front clutch & direct hydraulic circuit |
| **Test 4** | **`"R"` (Reverse) @ `1600 RPM`** | **`Low-Reverse`** (`300 psi` gauge) | **`180–220 psi`** (lever rearward) -> **`260–300 psi`** (lever forward); drops to `0 psi` in `"D"` | Direct reverse servo leakage / case porosity check (`D-R` low = front clutch leak; `D,1,2` low = rear clutch leak) |

## 2. Clearances, `A-460` Ratios & Fastener Tightening Specs (`ATSG p.70–72`)

- **Axial Settings**: `Output Shaft` tapered roller **`0.00–0.07 mm` (`0.000–0.0028 in`) preload**; `Transfer Shaft` **`0.05–0.25 mm` (`0.002–0.010 in`) end play**; `Differential` **`0.15–0.29 mm` (`0.006–0.012 in`) preload**. Thrust washers: `No.1/2/11` phenolic (`1.55–1.60 mm`), `No.3` selective output bronze (`1.98–2.41 mm`), `No.7/8` sun gear (`0.85–0.91 mm`), `No.5/6/9/10` carrier bronze (`1.22–1.28 mm`).
- **`A-460` 4-Speed Manual Reference (`p.70`)**: Ratios `1st 3.29`, `2nd 1.89`, `3rd 1.21`, `4th 0.88`, `Rev 3.14`, `Diff 3.05`, `Overall 2.69`; `1.8 L` (`2.0 U.S. qt`).
- **Critical `A-404`/`A-413`/`A-470` Torques (`p.71`)**: Bellhousing-to-block `95 N·m (70 ft-lbs)`; Differential ring gear (`Tool C-4706`) `95 N·m (70 ft-lbs)`; Output/Transfer shaft nuts `271 N·m (200 ft-lbs)`; Flexplate-to-crank `68 N·m (50 ft-lbs)` (`A-413` `88 N·m / 65 ft-lbs`, `A-470` `136 N·m / 100 ft-lbs` per footnote); Flexplate-to-converter `54 N·m (40 ft-lbs)`; Valve body `Torx T25` `5 N·m (40 in-lbs)`; Transfer plate-to-case `12 N·m (105 in-lbs)`; Oil pan / rear / diff cover `19 N·m (165 in-lbs)`; Pump-to-case `31 N·m (275 in-lbs)`; Kickdown band locknut `47 N·m (35 ft-lbs)`; Reverse band locknut `27 N·m (20 ft-lbs)`.

## 3. `A604` (`40TE`/`41TE`) vs. `A606` (`42LE`) & `42RLE` Part Reference (`PartNumbers_A604`)

- **Case ID**: `A604`/`40TE`/`41TE`/`41AE` (transverse FWD/AWD) uses a **`14-bolt` pan** (silicone RTV sealed — avoid pan/diff/end gaskets); `A606`/`42LE` (longitudinal FWD) uses a **`13-bolt` pan** (`42RLE` = RWD derivative).
- **Kits & Hydraulic Updates (`88-Up`)**: Rebuild families `K7200*` / `K72900*` (`-4` solenoid kit `88-99` vs `K72900A-4` `00-Up`; OEM pump housing gasket `4412844`, solenoid gaskets `4799844AA`, orange silicone connector seal `4431965`), `TransGo` `SK 604` (`K72908J`) + boost bushing `604BSH`, `Sonnax` `SC-41TE` (`72S01`). Frictions: Underdrive/Reverse `.073 in` (`72740`), Overdrive `88-89 .085 in` (3-disc `4377137`) -> `90-Up .073 in` (4-disc `4531678`), `2nd/4th .085 in` (`4567212`), `Low/Rev .073 in` (`4377134`).
- **Wave D Indexed ATSG Library Scope**: Covers `A404/A413/A415/A470` (`95p`), `A670` (`86p`), `A604 Part 1–4` (`347p` total), `42LE/A606` (`103p`), `A500` (`88p`), `42RE/A500SE` (`104p`), `A727/A904` (`78p`), and `A999/A904T` (`88p`); manual `A520/A555/A568` geometry/gearset ratios remain verified in `a555-a568-a413-transaxles.md`.
