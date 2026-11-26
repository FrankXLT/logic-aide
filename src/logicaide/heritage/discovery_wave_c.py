"""Wave C heritage discovery: hardware adapters, swap/transaxle specs, telemetry logs, and forges."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from logicaide.heritage import discovery, discovery_wave_b

MAX_WAVE_C_SCAN_FILES = 1500
MAX_WAVE_C_SCAN_BYTES = 2 * 1024 * 1024
WAVE_C_HASH_EXTENSIONS = frozenset({".htm", ".html", ".doc", ".rtf", ".pde", ".asm", ".csv", ".dlg"})

QUARANTINE_EXACT_FILENAMES: frozenset[str] = frozenset({
    "contact.txt",
    "reg code.txt",
    "serial.txt",
    "password.txt",
    "dd.txt",
    "config.cfg",
    "flash" + ".frm",
    "eeprom.htm",
    "mouser1.htm",
    "mouser2.htm",
})

WAVE_C_REQUIRED_FINGERPRINTS: tuple[str, ...] = (
    "SST27SF020",
    "LM2917",
    "DIS_Adaptor",
    "Knock_Board",
    "ECC DOHC -12AN 0.098",
    "A555",
    "A568",
    "26-pin digital dash",
    "74LS193",
    "4N25",
    "1N4733A",
    "VB921",
    "PIC16F690",
    "PIC12F629",
    "28C256",
    "A413",
    "OBX Quaife",
    "2275 EGT",
    "run6.dlg",
)

# (category, fingerprint, query_pattern, target_destination, rationale)
WAVE_C_CDX_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "hardware_adapter_bom",
        "4N25",
        "turbo-mopar.com/*",
        "knowledge/hardware/",
        "4N25 optocoupler SCI interface and archival Chrysler turbo hardware notes.",
    ),
    (
        "engine_swap_oil_feed",
        "ECC DOHC -12AN 0.098",
        "relentlessracing.com/*",
        "knowledge/engine/",
        "ECC DOHC swap -12AN return and 0.098 oil restrictor machining notes.",
    ),
    (
        "transaxle_and_drivetrain",
        "A555",
        "turbododge.com/*",
        "knowledge/drivetrain/",
        "Chrysler A555 5-speed manual transaxle ratios, cables, and swap reference.",
    ),
    (
        "hardware_adapter_bom",
        "74LS193",
        "moparchem.com/*",
        "knowledge/hardware/",
        "74LS193 synchronous counter bank-switching EPROM adapter archive.",
    ),
    (
        "digital_dash_harness",
        "26-pin digital dash",
        "thedodgegarage.com/*",
        "knowledge/wiring/",
        "Chrysler 26-pin digital dash connector pinout and cluster swap reference.",
    ),
    (
        "ecu_memory_mod",
        "28C256",
        "minimopar.net/*",
        "knowledge/ecu/",
        "28C256 EEPROM SMEC/SBEC socket modification and Pin 11 bootstrap archive.",
    ),
    (
        "hardware_adapter_bom",
        "LM2917",
        "turbododge.com/*lm2917*",
        "knowledge/hardware/",
        "LM2917 frequency-to-voltage knock threshold conditioning circuit archive.",
    ),
    (
        "hardware_adapter_bom",
        "1N4733A",
        "turbododge.com/*1n4733a*",
        "knowledge/hardware/",
        "1N4733A 5.1V Zener SCI RX/TX voltage clamp protection circuit reference.",
    ),
    (
        "ignition_dis_adapter",
        "VB921",
        "turbododge.com/*vb921*",
        "knowledge/hardware/",
        "VB921 smart coil driver stage for waste-spark DIS ignition conversion.",
    ),
    (
        "ignition_dis_adapter",
        "PIC16F690",
        "turbododge.com/*pic16f690*",
        "knowledge/hardware/",
        "PIC16F690 hybrid waste-spark DIS timing microcontroller firmware archive.",
    ),
    (
        "ignition_dis_adapter",
        "PIC12F629",
        "turbododge.com/*pic12f629*",
        "knowledge/hardware/",
        "PIC12F629 compact waste-spark DIS pulse translator firmware archive.",
    ),
    (
        "transaxle_and_drivetrain",
        "A568",
        "thedodgegarage.com/*a568*",
        "knowledge/drivetrain/",
        "Chrysler A568 Getrag 5-speed transaxle gearing and bellhousing reference.",
    ),
    (
        "transaxle_and_drivetrain",
        "A413",
        "minimopar.net/*a413*",
        "knowledge/drivetrain/",
        "Chrysler A413 3-speed TorqueFlite hydraulic and converter stall reference.",
    ),
    (
        "transaxle_and_drivetrain",
        "OBX Quaife",
        "turbododge.com/*quaife*",
        "knowledge/drivetrain/",
        "OBX and Quaife helical LSD differential fitment notes for A555/A568.",
    ),
    (
        "telemetry_road_log",
        "2275 EGT",
        "turbo-mopar.com/*egt*",
        "knowledge/telemetry/",
        "Open-thermocouple 2275 F EGT rail fault signature in archival road logs.",
    ),
    (
        "telemetry_road_log",
        "run6.dlg",
        "turbododge.com/*run6*",
        "knowledge/telemetry/",
        "Archival 80 ms Chrysler SCI datalogger run6.dlg column layout reference.",
    ),
)

# (category, fingerprint, forge, query_pattern, target_destination, rationale)
WAVE_C_FORGE_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "ignition_dis_adapter",
        "DIS_Adaptor",
        "github",
        "DIS_Adaptor PIC16F690 PIC12F629 chrysler",
        "knowledge/hardware/",
        "Waste-spark DIS_Adaptor PCB schematics, Gerber notes, and PIC firmware.",
    ),
    (
        "knock_detector_hardware",
        "Knock_Board",
        "github",
        "Knock_Board LM2917 arduino knock",
        "knowledge/hardware/",
        "Arduino + LM2917 8-cell Knock_Board conditioning sketch and schematics.",
    ),
    (
        "sci_telemetry_client",
        "mpscandroid",
        "github",
        "mpscandroid mprxref chrysler sci",
        "knowledge/ecu/",
        "Read-only Chrysler SCI logger cross-reference tables (mprxref/mprsettings).",
    ),
    (
        "hardware_adapter_bom",
        "SST27SF020",
        "gitlab",
        "SST27SF020 74LS193 eprom adapter",
        "knowledge/hardware/",
        "SST27SF020 MTP Flash 27C256/27C128 bank-switch EPROM adapter hardware.",
    ),
    (
        "sci_hardware_interface",
        "Chrysler SCI hardware",
        "sourceforge",
        "chrysler sci datalogger 4N25 1N4733A",
        "knowledge/ecu/",
        "Read-only Chrysler SCI opto-isolated TTL/USB interface schematics.",
    ),
)


def build_code_forge_query_url(forge: str, query: str) -> str:
    """Delegate code-forge query URL construction to the shared Wave B helper."""
    return discovery_wave_b.build_code_forge_query_url(forge, query)


def _is_quarantined_path(fpath: Path, private_root: Path) -> bool:
    """Return True if a path falls under *_files/ or QUARANTINE_PII_DO_NOT_INGEST rules."""
    rel_parts_low = [part.lower() for part in fpath.relative_to(private_root).parts]
    if any(
        part.endswith("_files") or part.startswith(".") or part == "quarantine_pii_do_not_ingest"
        for part in rel_parts_low[:-1]
    ):
        return True
    name_low = fpath.name.lower()
    if name_low in QUARANTINE_EXACT_FILENAMES or name_low.endswith("contact.txt"):
        return True
    if "portmon" in rel_parts_low[:-1] and name_low.endswith(".log"):
        return True
    return False


def _scan_wave_c_corpus(
    private_root: Path | None,
    manifest_path: Path | str | None,
    tokens: Sequence[str],
) -> tuple[set[str], dict[str, str]]:
    """Scan manifest and non-quarantined Wave C files read-only for tokens and SHA-256."""
    observed: set[str] = set()
    token_sha256: dict[str, str] = {}
    name_matched: set[str] = set()
    lowered = {tok: tok.lower() for tok in tokens}

    if manifest_path is not None and Path(manifest_path).is_file():
        mtext = Path(manifest_path).read_text(encoding="utf-8", errors="ignore").lower()
        for tok, tok_low in lowered.items():
            if tok_low in mtext:
                observed.add(tok)

    if private_root is None or not private_root.is_dir():
        return observed, token_sha256

    scanned = 0
    for root, dirs, files in os.walk(private_root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if not d.endswith("_files") and not d.startswith(".") and d.lower() != "quarantine_pii_do_not_ingest"
        )
        for name in sorted(files):
            fpath = Path(root) / name
            if _is_quarantined_path(fpath, private_root):
                continue
            name_low = name.lower()
            is_hash_target = fpath.suffix.lower() in WAVE_C_HASH_EXTENSIONS
            matched_in_name = [tok for tok, tok_low in lowered.items() if tok_low in name_low]
            observed.update(matched_in_name)

            if not is_hash_target or scanned >= MAX_WAVE_C_SCAN_FILES:
                continue
            try:
                if fpath.stat().st_size > MAX_WAVE_C_SCAN_BYTES:
                    continue
                text_low = fpath.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            scanned += 1
            matched_in_text = [tok for tok, tok_low in lowered.items() if tok_low in text_low]
            if not matched_in_name and not matched_in_text:
                continue
            observed.update(matched_in_text)
            digest = discovery._sha256_file(fpath)
            for tok in matched_in_name:
                if tok not in name_matched:
                    token_sha256[tok] = digest
                    name_matched.add(tok)
            for tok in matched_in_text:
                token_sha256.setdefault(tok, digest)
    return observed, token_sha256


def extract_wave_c_fingerprints(
    private_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    extra_fingerprints: Sequence[dict[str, Any] | str] | None = None,
) -> list[dict[str, Any]]:
    """Extract Wave C CDX and code-forge leads with read-only corpus SHA-256 dedup."""
    proot = Path(private_root) if private_root is not None else None
    all_tokens = [spec[1] for spec in WAVE_C_CDX_SPECS] + [spec[1] for spec in WAVE_C_FORGE_SPECS]
    observed, token_sha256 = _scan_wave_c_corpus(proot, manifest_path, all_tokens)
    records: list[dict[str, Any]] = []

    for cat, fp, qpat, dest, rat in WAVE_C_CDX_SPECS:
        suffix = " Confirmed in read-only corpus scan." if fp in observed else ""
        records.append({
            "source_kind": "wayback_cdx",
            "category": cat,
            "fingerprint": fp,
            "query_pattern": qpat,
            "target_destination": dest,
            "rationale": f"{rat}{suffix}",
            "local_sha256": token_sha256.get(fp, ""),
        })

    for cat, fp, forge, qpat, dest, rat in WAVE_C_FORGE_SPECS:
        suffix = " Confirmed in read-only corpus scan." if fp in observed else ""
        records.append({
            "source_kind": "code_forge",
            "forge": forge,
            "category": cat,
            "fingerprint": fp,
            "query_pattern": qpat,
            "code_forge_query_url": build_code_forge_query_url(forge, qpat),
            "target_destination": dest,
            "rationale": f"{rat}{suffix}",
            "local_sha256": token_sha256.get(fp, ""),
        })

    for extra in discovery._normalize_extra_fingerprints(extra_fingerprints):
        if extra.get("source_kind") == "code_forge" and not extra.get("code_forge_query_url"):
            forge = str(extra.get("forge") or "github")
            extra["forge"] = forge
            extra["code_forge_query_url"] = build_code_forge_query_url(forge, extra["query_pattern"])
        records.append(extra)
    return records
