"""Wave B heritage discovery: ROM/SCI/DRB-II fingerprints, CDX queries, and code-forge leads."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence
import urllib.parse

from logicaide.heritage import discovery

MAX_WAVE_B_SCAN_FILES = 1500
MAX_WAVE_B_SCAN_BYTES = 2 * 1024 * 1024
WAVE_B_HASH_EXTENSIONS = frozenset({".asm", ".csv"})

CODE_FORGE_TEMPLATES: dict[str, str] = {
    "github": "https://github.com/search?q={query}&type=repositories",
    "gitlab": "https://gitlab.com/search?search={query}",
    "sourceforge": "https://sourceforge.net/directory/?q={query}",
}

WAVE_B_REQUIRED_FINGERPRINTS: tuple[str, ...] = (
    "DRBFunctionTable",
    "DRBMemoryTable",
    "FastDataOutputAddressTable",
    "XmitRequestedRamLocation",
    "SetupHiSpeedDataTransfer",
    "PEFTBL_PumpingEfficiency",
    "ChemAsmSMEC",
    "ChemAsmLM",
    "zChEM",
    "Blueberry60",
    "Ladybug60",
    "D-Cal_120",
    "tbl2calx",
    "DuaLogger.prc",
)

# (category, fingerprint, query_pattern, target_destination, rationale)
WAVE_B_CDX_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "rom_toolchain",
        "ChemAsmLM",
        "mptune.com/*",
        "rom/src/",
        "MPTune 16 KB 6803 LM assembler, templates, and calibration archival tree.",
    ),
    (
        "rom_toolchain",
        "ChemAsmSMEC",
        "boostbutton.com/*",
        "rom/src/",
        "32 KB 68HC11 SMEC assembler, linker, and checksum toolchain archival tree.",
    ),
    (
        "rom_toolchain",
        "D-Cal_120",
        "thedodgegarage.com/*ecu*",
        "knowledge/ecu/",
        "D-Cal_120 calibration documentation and legacy Chrysler ECU reference archive.",
    ),
    (
        "sci_protocol_symbol",
        "DRBMemoryTable",
        "minimopar.net/*sbec*",
        "rom/src/",
        "SBEC/SMEC DRBMemoryTable RAM watchlist pointers and ECU pinout archive.",
    ),
    (
        "sci_logger_tool",
        "DuaLogger.prc",
        "chryslerccdsci.wordpress.com/*",
        "knowledge/ecu/",
        "Chrysler SCI/CCD protocol notes and DuaLogger.prc PalmOS read-only logger.",
    ),
    (
        "rom_disassembly_symbol",
        "PEFTBL_PumpingEfficiency",
        "turbododge.com/*disassembly*",
        "rom/src/",
        "TurboDodge ROM disassembly threads covering PEFTBL_PumpingEfficiency tables.",
    ),
    (
        "rom_toolchain",
        "zChEM",
        "boostbutton.com/*zChEM*",
        "rom/src/",
        "zChEM Chrysler ROM assembly packaging and calibration build archive.",
    ),
    (
        "rom_baseline_source",
        "Blueberry60",
        "turbododge.com/*Blueberry60*",
        "rom/src/",
        "Blueberry60 16 KB 6803 LM community disassembly and baseline source.",
    ),
    (
        "rom_baseline_source",
        "Ladybug60",
        "turbododge.com/*Ladybug60*",
        "rom/src/",
        "Ladybug60 32 KB 68HC11 SMEC community disassembly and baseline source.",
    ),
    (
        "rom_toolchain",
        "tbl2calx",
        "mptune.com/*tbl2calx*",
        "rom/src/",
        "Legacy .tbl to .calx calibration table converter source and format notes.",
    ),
    (
        "sci_protocol_symbol",
        "DRBFunctionTable",
        "chryslerccdsci.wordpress.com/*DRBFunctionTable*",
        "rom/src/",
        "SMEC/SBEC DRBFunctionTable SCI diagnostic dispatch table disassembly.",
    ),
    (
        "sci_protocol_symbol",
        "XmitRequestedRamLocation",
        "chryslerccdsci.wordpress.com/*XmitRequestedRamLocation*",
        "rom/src/",
        "SMEC/SBEC 0xf2/0xfe SCI RAM location transmit routine disassembly.",
    ),
)

# (category, fingerprint, forge, query_pattern, target_destination, rationale)
WAVE_B_FORGE_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "sci_protocol_symbol",
        "FastDataOutputAddressTable",
        "github",
        "FastDataOutputAddressTable",
        "rom/src/",
        "SBEC 0xf6 high-speed SCI FastDataOutputAddressTable references on GitHub.",
    ),
    (
        "sci_protocol_symbol",
        "SetupHiSpeedDataTransfer",
        "github",
        "SetupHiSpeedDataTransfer",
        "rom/src/",
        "SBEC SetupHiSpeedDataTransfer 62.5 kbaud SCI setup references on GitHub.",
    ),
    (
        "cross_assembler_emulator",
        "as11",
        "github",
        "as11 68hc11 assembler",
        "scripts/",
        "Motorola 68HC11 cross-assembler (as11) for deterministic ROM round-trip.",
    ),
    (
        "cross_assembler_emulator",
        "blackfoot",
        "github",
        "blackfoot 68hc11 6803",
        "scripts/",
        "Blackfoot 68HC11/6803 cross-assembler and cleanroom disassembly tooling.",
    ),
    (
        "cross_assembler_emulator",
        "m68hc11",
        "sourceforge",
        "m68hc11 6803 simulator",
        "scripts/",
        "Cycle-accurate 68HC11/6803 simulator and GNU binutils toolchain on SourceForge.",
    ),
    (
        "disassembler_sleigh",
        "Ghidra SLEIGH 68HC11 / 6803",
        "github",
        "ghidra sleigh 68hc11 6803",
        "scripts/",
        "Ghidra SLEIGH processor definitions for Motorola 68HC11 and 6803 instruction sets.",
    ),
    (
        "sci_ccd_drb_repo",
        "Chrysler SCI logger",
        "github",
        "chrysler sci datalogger 7812.5",
        "knowledge/ecu/",
        "Read-only Chrysler SCI 7812.5/62500 baud telemetry and RAM logger repositories.",
    ),
    (
        "sci_ccd_drb_repo",
        "Chrysler CCD DRB-II",
        "gitlab",
        "chrysler ccd drb-ii",
        "knowledge/ecu/",
        "Chrysler CCD bus and DRB-II cartridge prompt protocol repositories on GitLab.",
    ),
    (
        "cross_assembler_emulator",
        "6803 cross-assembler",
        "gitlab",
        "6803 68hc11 cross assembler",
        "scripts/",
        "6803/68HC11 cross-assembler and cycle-accurate emulator mirrors on GitLab.",
    ),
)


def build_code_forge_query_url(forge: str, query: str) -> str:
    """Build a canonical code-forge repository search URL for GitHub, GitLab, or SourceForge."""
    forge_norm = str(forge).strip().lower()
    if forge_norm not in CODE_FORGE_TEMPLATES:
        raise ValueError(f"Unsupported code forge: {forge!r}")
    query_clean = discovery.scrub_pii(str(query).strip())
    if not query_clean:
        raise ValueError("Code forge query must be non-empty")
    encoded = urllib.parse.quote_plus(query_clean)
    return CODE_FORGE_TEMPLATES[forge_norm].format(query=encoded)


def _scan_wave_b_corpus(
    private_root: Path | None,
    manifest_path: Path | str | None,
    tokens: Sequence[str],
) -> tuple[set[str], dict[str, str]]:
    """Scan manifest and private .asm/.csv files read-only for Wave B tokens and SHA-256."""
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
        dirs[:] = sorted(d for d in dirs if not d.endswith("_files") and not d.startswith("."))
        for name in sorted(files):
            name_low = name.lower()
            fpath = Path(root) / name
            is_hash_target = fpath.suffix.lower() in WAVE_B_HASH_EXTENSIONS
            matched_in_name = [tok for tok, tok_low in lowered.items() if tok_low in name_low]
            observed.update(matched_in_name)

            if not is_hash_target or scanned >= MAX_WAVE_B_SCAN_FILES:
                continue
            try:
                if fpath.stat().st_size > MAX_WAVE_B_SCAN_BYTES:
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


def extract_wave_b_fingerprints(
    private_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    extra_fingerprints: Sequence[dict[str, Any] | str] | None = None,
) -> list[dict[str, Any]]:
    """Extract Wave B CDX and code-forge leads with read-only .asm/.csv SHA-256 dedup."""
    proot = Path(private_root) if private_root is not None else None
    all_tokens = [spec[1] for spec in WAVE_B_CDX_SPECS] + [spec[1] for spec in WAVE_B_FORGE_SPECS]
    observed, token_sha256 = _scan_wave_b_corpus(proot, manifest_path, all_tokens)
    records: list[dict[str, Any]] = []

    for cat, fp, qpat, dest, rat in WAVE_B_CDX_SPECS:
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

    for cat, fp, forge, qpat, dest, rat in WAVE_B_FORGE_SPECS:
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
