"""Wave E heritage discovery: bounded archival matrix across 4 cleanroom sources."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Sequence

from logicaide.heritage import discovery, discovery_wave_c, discovery_wave_d

MAX_WAVE_E_SCAN_FILES = 1500
MAX_WAVE_E_SCAN_BYTES = 1024 * 1024

HIJACKED_DOMAINS: tuple[str, ...] = ("turbo-mopar.net", "homemadeturbo.com")
ALLPAR_CUTOFF_TO = "20170531"
ALLPAR_EXCLUDE_FORUMS_FILTER = "!original:.*/forums/.*"
ALLPAR_PREFIXES: tuple[str, ...] = (
    "allpar.com/mopar/*",
    "allpar.com/omni/*",
    "allpar.com/model/*",
    "allpar.com/cars/*",
    "allpar.com/fix/*",
    "allpar.com/history/*",
    "allpar.com/corporate/*",
)
TURBO_MOPAR_CUTOFF_TO = "20161231"

WAVE_E_CORE_TOKENS: tuple[str, ...] = (
    "SMEC", "SBEC", "68HC11", "6803", "PEFTBL", "WOTTBL", "BatOff", "Pin 11",
    "28C256", "DRB-II", "62500", "0xF2", "0xF4", "MPScan", "mpscilink",
    "ChemAsm", "zChEM", "Blueberry60", "Ladybug60", "D-Cal", "TurboCalc",
    "VNT25", "TE04H", "TD04LR", "#782", "#445", "DOHC", "A555", "A568",
)

# (source_site, category, fingerprint, query_pattern, to_timestamp, filter_exprs, collapse, target_destination, topic_tokens, rationale)
WAVE_E_SPECS: tuple[tuple[str, str, str, str, str | None, tuple[str, ...], str, str, tuple[str, ...], str], ...] = (
    ("allpar.com", "allpar_archival_mopar", "allpar/mopar SMEC SBEC", "allpar.com/mopar/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "knowledge/ecu/",
     ("SMEC", "SBEC", "68HC11", "6803", "PEFTBL", "WOTTBL", "BatOff", "Pin 11", "DRB-II"),
     "Pre-sale Allpar Mopar tech archive (cutoff 2017-05-31, forums excluded)."),
    ("allpar.com", "allpar_archival_omni", "allpar/omni 6803 GLHS", "allpar.com/omni/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "knowledge/ecu/",
     ("6803", "TE04H", "A555", "A568"),
     "Pre-sale Allpar Omni/Charger/GLHS 6803 Logic Module and transaxle archive."),
    ("allpar.com", "allpar_archival_model", "allpar/model Shelby VNT25", "allpar.com/model/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "profiles/turbo/",
     ("VNT25", "TD04LR", "DOHC", "A555", "A568"),
     "Pre-sale Allpar Chrysler/Dodge turbo model archive (VNT25, TD04LR, DOHC)."),
    ("allpar.com", "allpar_archival_cars", "allpar/cars 2.2L 2.5L heads", "allpar.com/cars/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "knowledge/engine/",
     ("#782", "#445", "DOHC", "TE04H"),
     "Pre-sale Allpar 2.2L/2.5L platform archive (#782/#445 heads, DOHC swaps)."),
    ("allpar.com", "allpar_archival_fix", "allpar/fix DRB-II diagnostics", "allpar.com/fix/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "knowledge/wiring/",
     ("DRB-II", "62500", "Pin 11", "SMEC", "SBEC"),
     "Pre-sale Allpar repair & DRB-II/SCI 62500 diagnostic notes."),
    ("allpar.com", "allpar_archival_history", "allpar/history Chrysler turbo ECU", "allpar.com/history/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "knowledge/ecu/",
     ("SMEC", "SBEC", "6803", "68HC11"),
     "Pre-sale Allpar Chrysler turbo electronics history across 6803, SMEC, and SBEC."),
    ("allpar.com", "allpar_archival_corporate", "allpar/corporate powertrain specs", "allpar.com/corporate/*",
     ALLPAR_CUTOFF_TO, (ALLPAR_EXCLUDE_FORUMS_FILTER,), "digest", "knowledge/drivetrain/",
     ("A555", "A568", "DOHC", "TurboCalc"),
     "Pre-sale Allpar corporate powertrain engineering specs archive."),
    ("turbo-mopar.com", "turbo_mopar_forums_index", "turbo-mopar forums archive", "turbo-mopar.com/forums/*",
     TURBO_MOPAR_CUTOFF_TO, (), "urlkey", "knowledge/ecu/",
     ("SMEC", "SBEC", "68HC11", "6803", "PEFTBL", "WOTTBL", "BatOff"),
     "Pre-hijack Wayback CDX urlkey index of turbo-mopar.com/forums (cutoff 2016-12-31)."),
    ("turbo-mopar.com", "turbo_mopar_smec_sbec", "turbo-mopar SMEC SBEC Pin11", "turbo-mopar.com/forums/*smec*",
     TURBO_MOPAR_CUTOFF_TO, (), "urlkey", "knowledge/ecu/",
     ("SMEC", "SBEC", "Pin 11", "28C256", "Blueberry60", "Ladybug60"),
     "Pre-hijack Wayback archive of SMEC/SBEC Pin 11, 28C256, and Blueberry60/Ladybug60."),
    ("turbo-mopar.com", "turbo_mopar_chem_cal", "turbo-mopar ChemAsm zChEM D-Cal", "turbo-mopar.com/forums/*chem*",
     TURBO_MOPAR_CUTOFF_TO, (), "urlkey", "knowledge/ecu/",
     ("ChemAsm", "zChEM", "D-Cal", "PEFTBL", "WOTTBL", "BatOff"),
     "Pre-hijack Wayback archive of ChemAsm, zChEM, D-Cal, PEFTBL, and WOTTBL threads."),
    ("turbo-mopar.com", "turbo_mopar_sci_diag", "turbo-mopar MPScan SCI 0xF2", "turbo-mopar.com/forums/*sci*",
     TURBO_MOPAR_CUTOFF_TO, (), "urlkey", "knowledge/ecu/",
     ("MPScan", "mpscilink", "62500", "0xF2", "0xF4", "DRB-II"),
     "Pre-hijack Wayback archive of MPScan, mpscilink, 62500 baud, and 0xF2/0xF4 threads."),
    ("sites.google.com", "mpscilink_archive", "sites.google.com/site/mpscilink", "sites.google.com/site/mpscilink/*",
     None, (), "digest", "knowledge/ecu/",
     ("mpscilink", "MPScan", "62500", "0xF2", "0xF4"),
     "Archived mpscilink site hosting Chrysler SCI 62500/0xF2/0xF4 logger docs."),
    ("minimopar.net", "minimopar_perf_turbo", "minimopar/perf turbo & heads", "minimopar.net/perf/*",
     None, (), "digest", "profiles/turbo/",
     ("#782", "#445", "TE04H", "VNT25", "TD04LR", "TurboCalc"),
     "MiniMopar performance archive (#782/#445 heads, TE04H, VNT25, TD04LR, TurboCalc)."),
    ("minimopar.net", "minimopar_smec", "minimopar SMEC 68HC11 28C256", "minimopar.net/*smec*",
     None, (), "digest", "knowledge/ecu/",
     ("SMEC", "68HC11", "Pin 11", "28C256", "BatOff"),
     "MiniMopar SMEC 68HC11 archive (Pin 11, 28C256 EEPROM, BatOff notes)."),
    ("minimopar.net", "minimopar_sbec", "minimopar SBEC Blueberry60 Ladybug60", "minimopar.net/*sbec*",
     None, (), "digest", "knowledge/ecu/",
     ("SBEC", "68HC11", "Blueberry60", "Ladybug60", "D-Cal"),
     "MiniMopar SBEC archive (68HC11 ROMs, Blueberry60, Ladybug60, D-Cal)."),
    ("minimopar.net", "minimopar_6803", "minimopar 6803 Logic Module", "minimopar.net/*6803*",
     None, (), "digest", "knowledge/ecu/",
     ("6803", "PEFTBL", "WOTTBL"),
     "MiniMopar 6803 Logic/Power Module archive covering PEFTBL/WOTTBL tables."),
    ("minimopar.net", "minimopar_drb_faults", "minimopar DRB-II fault codes", "minimopar.net/*drb*",
     None, (), "digest", "knowledge/wiring/",
     ("DRB-II", "SMEC", "SBEC"),
     "MiniMopar DRB-II diagnostic code reference and fault tree archive."),
    ("minimopar.net", "minimopar_sci_protocol", "minimopar SCI 62500 0xF2 0xF4", "minimopar.net/*sci*",
     None, (), "digest", "knowledge/ecu/",
     ("62500", "0xF2", "0xF4", "MPScan", "mpscilink"),
     "MiniMopar SCI bus archive (62500 baud, 0xF2/0xF4 tables, MPScan, mpscilink)."),
    ("minimopar.net", "minimopar_chem_cal", "minimopar ChemAsm zChEM PEFTBL", "minimopar.net/*chem*",
     None, (), "digest", "knowledge/ecu/",
     ("ChemAsm", "zChEM", "PEFTBL", "WOTTBL", "D-Cal"),
     "MiniMopar calibration disassembly archive (ChemAsm, zChEM, D-Cal, PEFTBL)."),
    ("minimopar.net", "minimopar_drivetrain_swaps", "minimopar A555 A568 DOHC", "minimopar.net/*trans*",
     None, (), "digest", "knowledge/drivetrain/",
     ("A555", "A568", "DOHC"),
     "MiniMopar transaxle and swap archive (A555, A568, DOHC hybrid builds)."),
    ("thedodgegarage.com", "dodgegarage_smec", "thedodgegarage SMEC 68HC11 Pin11", "thedodgegarage.com/*smec*",
     None, (), "digest", "knowledge/ecu/",
     ("SMEC", "68HC11", "Pin 11", "28C256", "BatOff"),
     "TheDodgeGarage SMEC 68HC11 archive (Pin 11, 28C256 socket mods, BatOff)."),
    ("thedodgegarage.com", "dodgegarage_sbec", "thedodgegarage SBEC Blueberry60", "thedodgegarage.com/*sbec*",
     None, (), "digest", "knowledge/ecu/",
     ("SBEC", "Blueberry60", "Ladybug60", "68HC11"),
     "TheDodgeGarage SBEC tech archive (Blueberry60, Ladybug60, 68HC11 calibration)."),
    ("thedodgegarage.com", "dodgegarage_6803", "thedodgegarage 6803 LM PM", "thedodgegarage.com/*6803*",
     None, (), "digest", "knowledge/ecu/",
     ("6803", "PEFTBL", "WOTTBL"),
     "TheDodgeGarage 6803 Logic/Power Module archive and calibration reference."),
    ("thedodgegarage.com", "dodgegarage_chem_tables", "thedodgegarage PEFTBL ChemAsm D-Cal", "thedodgegarage.com/*peftbl*",
     None, (), "digest", "knowledge/ecu/",
     ("PEFTBL", "WOTTBL", "ChemAsm", "zChEM", "D-Cal"),
     "TheDodgeGarage calibration archive (PEFTBL, WOTTBL, ChemAsm, zChEM, D-Cal)."),
    ("thedodgegarage.com", "dodgegarage_sci_drb", "thedodgegarage DRB-II MPScan 0xF2", "thedodgegarage.com/*drb*",
     None, (), "digest", "knowledge/ecu/",
     ("DRB-II", "62500", "0xF2", "0xF4", "MPScan", "mpscilink"),
     "TheDodgeGarage diagnostic & SCI archive (DRB-II, 62500, 0xF2/0xF4, MPScan)."),
    ("thedodgegarage.com", "dodgegarage_turbo_maps", "thedodgegarage VNT25 TE04H TD04LR", "thedodgegarage.com/*turbo*",
     None, (), "digest", "profiles/turbo/",
     ("TurboCalc", "VNT25", "TE04H", "TD04LR"),
     "TheDodgeGarage turbocharger archive (TurboCalc, VNT25, TE04H, TD04LR)."),
    ("thedodgegarage.com", "dodgegarage_head_flow", "thedodgegarage #782 #445 DOHC", "thedodgegarage.com/*head*",
     None, (), "digest", "knowledge/engine/",
     ("#782", "#445", "DOHC"),
     "TheDodgeGarage cylinder head flowbench archive (#782, #445, DOHC)."),
    ("thedodgegarage.com", "dodgegarage_transaxles", "thedodgegarage A555 A568 swaps", "thedodgegarage.com/*a555*",
     None, (), "digest", "knowledge/drivetrain/",
     ("A555", "A568", "DOHC"),
     "TheDodgeGarage transaxle archive (A555, A568 Getrag, DOHC swap fitment)."),
)


def validate_wave_e_query_pattern(pattern: str, *, source_kind: str = "wayback_cdx") -> str:
    """Validate Wave E query pattern against hijack blocklist and Allpar prefix bounds."""
    cleaned = str(pattern).strip()
    if not cleaned:
        raise ValueError("Wave E query pattern must not be empty.")
    low = cleaned.lower()
    no_scheme = re.sub(r"^https?://", "", low)
    no_www = no_scheme[4:] if no_scheme.startswith("www.") else no_scheme
    host = re.split(r"[/?#:*]", no_www, maxsplit=1)[0]

    for hijacked in HIJACKED_DOMAINS:
        if host == hijacked or host.endswith(f".{hijacked}") or re.search(
            rf"(?<![a-z0-9-]){re.escape(hijacked)}(?![a-z0-9-])", low
        ):
            raise ValueError(f"Rejected hijacked domain {hijacked!r} in Wave E pattern: {pattern!r}")

    if host == "allpar.com" or host.endswith(".allpar.com") or "allpar.com" in no_www:
        if not any(no_www.startswith(prefix[:-1]) for prefix in ALLPAR_PREFIXES):
            raise ValueError(
                f"Rejected unbounded or non-allowlisted allpar.com pattern (root allpar.com/* forbidden): {pattern!r}"
            )

    if (host == "turbo-mopar.com" or host.endswith(".turbo-mopar.com")) and source_kind != "wayback_cdx":
        raise ValueError(f"Direct live queries to turbo-mopar.com are forbidden; use wayback_cdx: {pattern!r}")
    return cleaned


def _resolve_repo_root(repo_root: Path | str | None) -> Path:
    """Resolve repository root for dynamic wiki/rom/sci seeding."""
    if repo_root is not None:
        return Path(repo_root)
    candidate = Path(__file__).resolve().parents[3]
    return candidate if (candidate / "wiki").is_dir() or (candidate / "rom").is_dir() else Path(".")


def _scan_text_file(fpath: Path, root: Path, tokens: Sequence[str]) -> set[str]:
    """Read a non-symlink text file safely under root and match Wave E core tokens."""
    if fpath.is_symlink():
        return set()
    try:
        resolved_root = root.resolve()
        if not fpath.resolve().is_relative_to(resolved_root):
            return set()
        if not fpath.is_file() or fpath.stat().st_size > MAX_WAVE_E_SCAN_BYTES:
            return set()
        text_low = fpath.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return set()
    return set(discovery_wave_d._match_wave_d_tokens(text_low, tokens))


def _scan_repo_seed_sources(repo_root: Path, tokens: Sequence[str]) -> dict[str, set[str]]:
    """Seed core Wave E tokens from wiki/**/*.md, rom/manifests/, and rom/sci/mpr_sci_crossref.json."""
    seeded: dict[str, set[str]] = {"wiki": set(), "rom_manifests": set(), "sci_crossref": set()}
    for key, sub_path, ext in (
        ("wiki", repo_root / "wiki", ".md"),
        ("rom_manifests", repo_root / "rom" / "manifests", ".json"),
    ):
        if sub_path.is_symlink() or not sub_path.is_dir():
            continue
        scanned = 0
        for root, dirs, files in os.walk(sub_path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if scanned >= MAX_WAVE_E_SCAN_FILES:
                    break
                fpath = Path(root) / name
                if fpath.suffix.lower() != ext:
                    continue
                scanned += 1
                seeded[key].update(_scan_text_file(fpath, sub_path, tokens))
    sci_path = repo_root / "rom" / "sci" / "mpr_sci_crossref.json"
    seeded["sci_crossref"].update(_scan_text_file(sci_path, repo_root, tokens))
    return seeded


def _scan_private_seed_source(
    private_root: Path | None, manifest_path: Path | str | None, tokens: Sequence[str]
) -> set[str]:
    """Seed core tokens from manifest and clean read-only private_references (no quarantine/files dirs)."""
    observed: set[str] = set()
    if manifest_path is not None:
        mpath = Path(manifest_path)
        if mpath.is_symlink():
            raise ValueError(f"manifest_path must not be a symlink: {mpath}")
        if mpath.is_file():
            observed.update(_scan_text_file(mpath, mpath.parent, tokens))

    if private_root is not None and private_root.is_symlink():
        raise ValueError(f"private_root must not be a symlink: {private_root}")
    if private_root is None or not private_root.is_dir():
        return observed

    scanned = 0
    allowed_exts = discovery.TEXT_SCAN_EXTENSIONS | {".json"}
    for root, dirs, files in os.walk(private_root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs
            if not d.endswith("_files") and not d.startswith(".")
            and d.lower() != "quarantine_pii_do_not_ingest"
            and d.lower() not in discovery_wave_d._EXCLUDED_DIR_TOKENS
        )
        for name in sorted(files):
            if scanned >= MAX_WAVE_E_SCAN_FILES:
                continue
            fpath = Path(root) / name
            if fpath.is_symlink():
                continue
            if discovery_wave_c._is_quarantined_path(fpath, private_root):
                continue
            if discovery_wave_d._is_excluded_wave_d_path(fpath, private_root):
                continue
            observed.update(discovery_wave_d._match_wave_d_tokens(name.lower(), tokens))
            if fpath.suffix.lower() not in allowed_exts:
                continue
            scanned += 1
            observed.update(_scan_text_file(fpath, private_root, tokens))
    return observed


def extract_wave_e_fingerprints(
    private_root: Path | str | None = None,
    manifest_path: Path | str | None = None,
    extra_fingerprints: Sequence[dict[str, Any] | str] | None = None,
    *,
    repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Extract Wave E bounded archival leads seeded from wiki, rom manifests, SCI crossref, and private refs."""
    eff_repo = _resolve_repo_root(repo_root)
    proot = Path(private_root) if private_root is not None else None
    repo_seeds = _scan_repo_seed_sources(eff_repo, WAVE_E_CORE_TOKENS)
    priv_seeds = _scan_private_seed_source(proot, manifest_path, WAVE_E_CORE_TOKENS)
    source_map: dict[str, set[str]] = {**repo_seeds, "private_references": priv_seeds}

    records: list[dict[str, Any]] = []
    for source_site, cat, fp, qpat, to_ts, filter_exprs, collapse, dest, topic_tokens, rat in WAVE_E_SPECS:
        validated_pat = validate_wave_e_query_pattern(qpat, source_kind="wayback_cdx")
        matched_sources = [src for src, toks in source_map.items() if any(t in toks for t in topic_tokens)]
        matched_tokens = [t for t in topic_tokens if any(t in toks for toks in source_map.values())]
        seed_note = (
            f" Seeded ({', '.join(matched_tokens[:4])}) via {', '.join(matched_sources)}."
            if matched_sources else ""
        )
        rec: dict[str, Any] = {
            "source_kind": "wayback_cdx",
            "source_site": source_site,
            "wayback_only": True,
            "category": cat,
            "fingerprint": fp,
            "query_pattern": validated_pat,
            "collapse": collapse,
            "target_destination": dest,
            "rationale": f"{rat}{seed_note}",
            "local_sha256": "",
        }
        if to_ts is not None:
            rec["to_timestamp"] = to_ts
        if filter_exprs:
            rec["filter_exprs"] = filter_exprs
        records.append(rec)

    for extra in discovery._normalize_extra_fingerprints(extra_fingerprints):
        sk = str(extra.get("source_kind") or "wayback_cdx")
        extra["query_pattern"] = validate_wave_e_query_pattern(extra["query_pattern"], source_kind=sk)
        records.append(extra)
    return records
