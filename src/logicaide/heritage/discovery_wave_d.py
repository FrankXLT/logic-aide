"""Wave D heritage discovery: OEM FSM/wiring/ATSG/IC PDFs, DRB-II, bulkhead circuits, and forges."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Sequence

from logicaide.heritage import discovery, discovery_wave_b, discovery_wave_c

MAX_WAVE_D_SCAN_FILES = 1500
MAX_WAVE_D_PDF_BYTES = 64 * 1024 * 1024
MAX_WAVE_D_TEXT_BYTES = 512 * 1024

EXCLUDE_PDF_TOKENS: frozenset[str] = frozenset({
    "9101.pdf", "9102.pdf", "9103.pdf", "9104.pdf",
    "9105.pdf", "9106.pdf", "9107.pdf", "9108.pdf",
    "imprezawiringdiagram.pdf", "gufb.pdf", "eectuner_faq.pdf",
    "mstweak3000.pdf", "vex format v1.pdf", "scr-drift-02-2003.pdf",
})

_NON_PDF_NOISE_FILENAMES: frozenset[str] = frozenset({
    "atsg_a604_readme.txt",
    "squirrel-pf-editing.doc",
})

_EXCLUDED_DIR_TOKENS: frozenset[str] = frozenset({
    "portmon",
    "sho",
    "subaru turbo research",
    "a9l",
    "msns-e",
})

WAVE_D_REQUIRED_FINGERPRINTS: tuple[str, ...] = (
    "88 T2.pdf", "87List.pdf", "87 K & E complete.pdf",
    "87 P Turbo engine wiring.pdf", "88 P turbo engine wiring.pdf",
    "89 mini wiring complete.pdf", "84 L engine side.pdf",
    "87 G 50 way engine side.pdf", "Aries Reliant 50 way bulkhead", "89 G 50 way",
    "DRB-II Code 11-55", "SMEC DRB fault tree",
    "ATSG A404 A413 A415 A470", "ATSG A520", "ATSG A555",
    "ATSG A568", "ATSG A604", "PartNumbers_A604",
    "74LS193", "SST27SF", "LM2907N", "NJM4151", "MSD DIS-2 pn6212",
    "ChEM2.pdf", "flashable smec Add a pin.pdf",
    "89T1vs89T2sparkadvance", "Knock Listen Device",
    "K22", "Z12", "D20", "D21", "DK20", "DK21", "A14", "C20",
)

_WAVE_D_FILENAME_ALIASES: dict[str, tuple[str, ...]] = {
    "87 G 50 way engine side.pdf": ("87g50wayengineside", "87 g 50 way engine side"),
    "Aries Reliant 50 way bulkhead": ("aries reliant 50 way bulkhead",),
    "89 G 50 way": ("89 g 50 way", "89g50way"),
    "ATSG A404 A413 A415 A470": ("a404_a413_a415_a470", "a404 a413 a415 a470"),
    "ATSG A604": ("atsg_a604", "atsg a604"),
    "SST27SF": ("sst27sf", "s71152"),
    "MSD DIS-2 pn6212": ("msd dis-2", "pn6212"),
    "89T1vs89T2sparkadvance": ("89t1vs89t2sparkadvance",),
    "Knock Listen Device": ("knock listen device",),
}

# (category, fingerprint, query_pattern, target_destination, rationale)
WAVE_D_CDX_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("oem_fsm_wiring_pdf", "88 T2.pdf", "turbododge.com/*88*t2*",
     "knowledge/wiring/", "1988 Turbo II 52p SMEC FSM PDF."),
    ("oem_fsm_wiring_pdf", "87List.pdf", "turbododge.com/*87list*",
     "knowledge/wiring/", "1987 49p OEM fault list FSM PDF."),
    ("oem_fsm_wiring_pdf", "87 K & E complete.pdf", "minimopar.net/*87*wiring*",
     "knowledge/wiring/", "1987 K/E-body 126p wiring PDF."),
    ("oem_fsm_wiring_pdf", "87 P Turbo engine wiring.pdf", "thedodgegarage.com/*87*turbo*wiring*",
     "knowledge/wiring/", "1987 P-body turbo wiring PDF."),
    ("oem_fsm_wiring_pdf", "88 P turbo engine wiring.pdf", "thedodgegarage.com/*88*turbo*wiring*",
     "knowledge/wiring/", "1988 P-body turbo wiring PDF."),
    ("oem_fsm_wiring_pdf", "89 mini wiring complete.pdf", "minimopar.net/*89*wiring*",
     "knowledge/wiring/", "1989 minivan 120p wiring PDF."),
    ("oem_fsm_wiring_pdf", "84 L engine side.pdf", "turbododge.com/*84*l*engine*",
     "knowledge/wiring/", "1984 L-body 40-way (8-612) bulkhead PDF."),
    ("oem_fsm_wiring_pdf", "87 G 50 way engine side.pdf", "thedodgegarage.com/*87*50*way*",
     "knowledge/wiring/", "1987 G-body 50-way bulkhead PDF."),
    ("oem_fsm_wiring_pdf", "Aries Reliant 50 way bulkhead", "minimopar.net/*50*way*",
     "knowledge/wiring/", "Aries/Reliant 50-way pinout PDF."),
    ("oem_fsm_wiring_pdf", "89 G 50 way", "thedodgegarage.com/*89*50*way*",
     "knowledge/wiring/", "1989 G-body 50-way bulkhead PDFs."),
    ("atsg_transaxle_manual", "ATSG A404 A413 A415 A470", "minimopar.net/*a413*",
     "knowledge/drivetrain/", "ATSG A404/A413/A415/A470 95p PDF."),
    ("atsg_transaxle_manual", "ATSG A520", "turbododge.com/*a520*",
     "knowledge/drivetrain/", "ATSG A520 5-speed service PDF."),
    ("atsg_transaxle_manual", "ATSG A555", "turbododge.com/*atsg*a555*",
     "knowledge/drivetrain/", "ATSG A555 5-speed service PDF."),
    ("atsg_transaxle_manual", "ATSG A568", "thedodgegarage.com/*atsg*a568*",
     "knowledge/drivetrain/", "ATSG A568 Getrag service PDF."),
    ("atsg_transaxle_manual", "ATSG A604", "minimopar.net/*a604*",
     "knowledge/drivetrain/", "ATSG A604 Ultradrive manual PDF."),
    ("atsg_transaxle_manual", "PartNumbers_A604", "minimopar.net/*41te*",
     "knowledge/drivetrain/", "A604/41TE/42LE part numbers PDF."),
    ("ic_datasheet_pdf", "74LS193", "moparchem.com/*74ls193*",
     "knowledge/hardware/", "74LS193 counter IC datasheet PDF."),
    ("ic_datasheet_pdf", "SST27SF", "boostbutton.com/*sst27sf*",
     "knowledge/hardware/", "SST27SF MTP Flash datasheet PDF."),
    ("ic_datasheet_pdf", "LM2907N", "turbo-mopar.com/*lm2907*",
     "knowledge/hardware/", "LM2907N F/V IC datasheet PDF."),
    ("ic_datasheet_pdf", "NJM4151", "turbo-mopar.com/*njm4151*",
     "knowledge/hardware/", "NJM4151 V/F IC datasheet PDF."),
    ("ecu_ignition_logger_pdf", "MSD DIS-2 pn6212", "turbododge.com/*pn6212*",
     "knowledge/hardware/", "MSD DIS-2 pn6212 28p manual PDF."),
    ("ecu_ignition_logger_pdf", "ChEM2.pdf", "boostbutton.com/*chem2*",
     "knowledge/ecu/", "ChEM2 22p ECU guide PDF."),
    ("ecu_ignition_logger_pdf", "flashable smec Add a pin.pdf", "minimopar.net/*smec*pin*",
     "knowledge/ecu/", "SMEC Pin 11 add-a-pin guide PDF."),
    ("ecu_ignition_logger_pdf", "89T1vs89T2sparkadvance", "thedodgegarage.com/*89t1*89t2*",
     "knowledge/ecu/", "1989 T1 vs T2 spark advance PDF."),
    ("ecu_ignition_logger_pdf", "Knock Listen Device", "turbo-mopar.com/*knock*listen*",
     "knowledge/hardware/", "Knock Listen Device schematic PDF."),
    ("bulkhead_circuit_code", "K22", "minimopar.net/*k22*",
     "knowledge/wiring/", "Bulkhead circuit K22 pinout ref."),
    ("bulkhead_circuit_code", "Z12", "minimopar.net/*z12*",
     "knowledge/wiring/", "Bulkhead ground circuit Z12 ref."),
    ("bulkhead_circuit_code", "D20", "chryslerccdsci.wordpress.com/*d20*",
     "knowledge/wiring/", "SCI TX bulkhead circuit D20 ref."),
    ("bulkhead_circuit_code", "D21", "chryslerccdsci.wordpress.com/*d21*",
     "knowledge/wiring/", "SCI RX bulkhead circuit D21 ref."),
    ("bulkhead_circuit_code", "DK20", "thedodgegarage.com/*dk20*",
     "knowledge/wiring/", "50/60-way SCI circuit DK20 ref."),
    ("bulkhead_circuit_code", "DK21", "thedodgegarage.com/*dk21*",
     "knowledge/wiring/", "50/60-way SCI circuit DK21 ref."),
    ("bulkhead_circuit_code", "A14", "minimopar.net/*a14*",
     "knowledge/wiring/", "50/60-way B+ circuit A14 ref."),
    ("bulkhead_circuit_code", "C20", "minimopar.net/*c20*",
     "knowledge/wiring/", "50/60-way bulkhead C20 ref."),
)

# (category, fingerprint, forge, query_pattern, target_destination, rationale)
WAVE_D_FORGE_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("drb_diagnostic_tree", "DRB-II Code 11-55", "github",
     "chrysler drb-ii code 11-55 smec sbec", "knowledge/ecu/", "DRB-II Code 11-55 tree archive."),
    ("drb_diagnostic_tree", "SMEC DRB fault tree", "gitlab",
     "chrysler smec drb-ii fault tree 68hc11", "knowledge/ecu/", "SMEC DRB-II 68HC11 fault tree."),
    ("fsm_ocr_mermaid_tooling", "Chrysler FSM OCR", "github",
     "docling marker surya chrysler fsm pdf", "scripts/", "Docling/Marker FSM PDF OCR tools."),
    ("fsm_ocr_mermaid_tooling", "ATSG 68HC11 Mermaid", "sourceforge",
     "chrysler atsg 68hc11 mermaid schematic", "scripts/", "ATSG/68HC11 Mermaid diagram tools."),
)


def build_code_forge_query_url(forge: str, query: str) -> str:
    """Delegate code-forge query URL construction to the shared Wave B helper."""
    return discovery_wave_b.build_code_forge_query_url(forge, query)


def _matches_wave_d_exclusions(candidate: Path, root: Path) -> bool:
    """Check a candidate path and relative path under root against Wave C/D exclusions."""
    name_low = candidate.name.lower()
    if name_low in EXCLUDE_PDF_TOKENS:
        return True
    if name_low in _NON_PDF_NOISE_FILENAMES or name_low.startswith("chrysler copyright text"):
        return True
    if not candidate.is_relative_to(root):
        return False
    if discovery_wave_c._is_quarantined_path(candidate, root):
        return True
    rel_parts_low = [part.lower() for part in candidate.relative_to(root).parts]
    rel_posix_low = "/".join(rel_parts_low)
    if any(part in _EXCLUDED_DIR_TOKENS for part in rel_parts_low[:-1]):
        return True
    if any(tok in rel_posix_low for tok in EXCLUDE_PDF_TOKENS):
        return True
    return False


def _is_excluded_wave_d_path(fpath: Path, private_root: Path) -> bool:
    """Return True if unresolved or resolved path escapes root or matches Wave C/D exclusions."""
    if _matches_wave_d_exclusions(fpath, private_root):
        return True
    try:
        resolved_path = fpath.resolve()
        resolved_root = private_root.resolve()
    except OSError:
        return True
    if not resolved_path.is_relative_to(resolved_root):
        return True
    return _matches_wave_d_exclusions(resolved_path, resolved_root)


def _match_wave_d_tokens(text_low: str, tokens: Sequence[str], *, compact: bool = False) -> list[str]:
    """Match Wave D fingerprint tokens and filename aliases against text."""
    space_norm = re.sub(r"[_\-\s]+", " ", text_low)
    compact_norm = re.sub(r"[^a-z0-9]+", "", text_low) if compact else ""
    matched: list[str] = []
    for tok in tokens:
        tok_low = tok.lower()
        hit = False
        if len(tok_low) <= 4:
            hit = bool(re.search(rf"(?<![a-z0-9]){re.escape(tok_low)}(?![a-z0-9])", text_low))
        else:
            tok_space = re.sub(r"[_\-\s]+", " ", tok_low)
            hit = tok_low in text_low or tok_space in space_norm
        if not hit and compact:
            for alias in _WAVE_D_FILENAME_ALIASES.get(tok, ()):
                if alias in text_low or alias in space_norm or alias in compact_norm:
                    hit = True
                    break
        if hit:
            matched.append(tok)
    return matched


def _scan_wave_d_corpus(
    private_root: Path | None,
    manifest_path: Path | str | None,
    tokens: Sequence[str],
) -> tuple[set[str], dict[str, str]]:
    """Scan manifest and clean Wave D files read-only for tokens and in-scope PDF SHA-256."""
    observed: set[str] = set()
    token_sha256: dict[str, str] = {}

    if manifest_path is not None:
        mpath = Path(manifest_path)
        if mpath.is_symlink():
            raise ValueError(f"manifest_path must not be a symlink: {mpath}")
        if mpath.is_file():
            mtext = mpath.read_text(encoding="utf-8", errors="ignore").lower()
            observed.update(_match_wave_d_tokens(mtext, tokens))

    if private_root is not None and private_root.is_symlink():
        raise ValueError(f"private_root must not be a symlink: {private_root}")
    if private_root is None or not private_root.is_dir():
        return observed, token_sha256

    resolved_root = private_root.resolve()
    scanned = 0
    for root, dirs, files in os.walk(private_root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if not d.endswith("_files")
            and not d.startswith(".")
            and d.lower() != "quarantine_pii_do_not_ingest"
            and d.lower() not in _EXCLUDED_DIR_TOKENS
        )
        for name in sorted(files):
            fpath = Path(root) / name
            if fpath.is_symlink():
                continue
            try:
                if not fpath.resolve().is_relative_to(resolved_root):
                    continue
            except OSError:
                continue
            if _is_excluded_wave_d_path(fpath, private_root):
                continue
            if scanned >= MAX_WAVE_D_SCAN_FILES:
                continue
            suffix_low = fpath.suffix.lower()
            if suffix_low == ".pdf":
                matched_pdf = _match_wave_d_tokens(name.lower(), tokens, compact=True)
                if not matched_pdf:
                    continue
                try:
                    if fpath.stat().st_size > MAX_WAVE_D_PDF_BYTES:
                        continue
                except OSError:
                    continue
                scanned += 1
                observed.update(matched_pdf)
                digest = discovery._sha256_file(fpath)
                for tok in matched_pdf:
                    token_sha256.setdefault(tok, digest)
            elif suffix_low in discovery.TEXT_SCAN_EXTENSIONS:
                observed.update(_match_wave_d_tokens(name.lower(), tokens, compact=True))
                try:
                    if fpath.stat().st_size > MAX_WAVE_D_TEXT_BYTES:
                        continue
                    text_low = fpath.read_text(encoding="utf-8", errors="ignore").lower()
                except OSError:
                    continue
                scanned += 1
                observed.update(_match_wave_d_tokens(text_low, tokens))
    return observed, token_sha256


def extract_wave_d_fingerprints(
    private_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    extra_fingerprints: Sequence[dict[str, Any] | str] | None = None,
) -> list[dict[str, Any]]:
    """Extract Wave D CDX and code-forge leads with read-only clean PDF SHA-256 dedup."""
    proot = Path(private_root) if private_root is not None else None
    all_tokens = [spec[1] for spec in WAVE_D_CDX_SPECS] + [spec[1] for spec in WAVE_D_FORGE_SPECS]
    observed, token_sha256 = _scan_wave_d_corpus(proot, manifest_path, all_tokens)
    records: list[dict[str, Any]] = []

    for cat, fp, qpat, dest, rat in WAVE_D_CDX_SPECS:
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

    for cat, fp, forge, qpat, dest, rat in WAVE_D_FORGE_SPECS:
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
