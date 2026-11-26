"""Wave A heritage discovery, SHA-256 vault deduplication, and polite CDX queue builder."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Sequence
import urllib.error
import urllib.parse
import urllib.request

import yaml

SCHEMA_VERSION = "1.0"
CDX_BASE_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_WEB_BASE = "https://web.archive.org/web"
DEFAULT_USER_AGENT = "LogicAIDE-Heritage-Discovery/1.0 (cleanroom-archival)"
POLITE_REQUEST_DELAY_S = 1.5
SHA256_HEX_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
IMAGE_EXTENSIONS = frozenset({".gif", ".jpg", ".jpeg", ".png", ".bmp"})
TEXT_SCAN_EXTENSIONS = frozenset({".txt", ".md", ".htm", ".html", ".php", ".xml", ".sql", ".csv"})
MAX_TEXT_SCAN_BYTES = 512 * 1024
MAX_TEXT_SCAN_FILES = 800

_PII_SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/home/[^\s/\"']+(?:/[^\s\"']*)*"), "[REDACTED_PATH]"),
    (re.compile(r"/Users/[^\s/\"']+(?:/[^\s\"']*)*"), "[REDACTED_PATH]"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\s\\\"']+(?:\\[^\s\"']*)*", re.IGNORECASE), "[REDACTED_PATH]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\\\\[A-Za-z0-9_.-]+(?:\\[^\s\"']*)*"), "[REDACTED_HOST]"),
)

# Uncalibrated raster maps in TurboCalc v5 beta 4 absent from tools.sql.
KNOWN_UNCALIBRATED_MAPS: frozenset[str] = frozenset({
    "703457-2comp_e.jpg", "740759_2_comp_e.jpg", "740759_4_comp_e.jpg",
    "gt3071r_700382_3_com_e.gif", "t3_60_28411147.jpg.gif", "hx40.jpg",
    "hx50_map.gif", "hx60.jpg", "ihi_family.jpg", "k16-2467.gif",
    "k26-2470r.gif", "td04h-16t-cfm.gif", "td04h-19t-cfm.gif", "td05h-18g-cfm.gif",
})

# (category, fingerprint, query_pattern, target_destination, rationale)
WAVE_A_SEED_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("legacy_tool_domain", "squirrelpf.com", "squirrelpf.com/*", "data/heritage_raw/warc/",
     "Legacy TurboCalc host domain holding archival compressor maps and PHP airflow tools."),
    ("legacy_tool_domain", "sqrl_tools", "squirrelpf.com/turbocalc/*", "profiles/turbo/",
     "Legacy TurboCalc database identifier associated with calibrated map coordinate records."),
    ("legacy_tool_domain", "turbocalc", "squirrelpf.com/*turbocalc*", "profiles/turbo/",
     "Legacy TurboCalc v3-v5 application tree for compressor map axis metadata recovery."),
    ("oem_chrysler_turbo", "T03 Garrett", "thedodgegarage.com/turbo_*", "profiles/turbo/",
     "OEM 1984-1987 Chrysler Turbo I/II T03 Garrett wheel and compressor map reference."),
    ("oem_chrysler_turbo", "TB03 Garrett", "minimopar.net/perf/*", "profiles/turbo/",
     "OEM Chrysler TB03 Garrett T-III wheel dimension and housing reference."),
    ("oem_chrysler_turbo", "TE04H", "thedodgegarage.com/*te04*", "profiles/turbo/",
     "OEM Mitsubishi TE04H Chrysler Turbo I/IV compressor flow and trim reference."),
    ("oem_chrysler_turbo", "VNT25", "shelbydodge.com/turbonator-wiki/*", "profiles/turbo/",
     "OEM Garrett VNT25 variable nozzle turbine geometry and boost control documentation."),
    ("oem_chrysler_turbo", "TD04LR", "turbododge.com/*td04lr*", "profiles/turbo/",
     "OEM Mitsubishi TD04LR reverse-rotation compressor map and trim reference."),
    ("head_casting_and_ecu_token", "#782", "thedodgegarage.com/*782*", "knowledge/engine/",
     "Chrysler 2.2L/2.5L #782 cylinder head casting port and flowbench archival data."),
    ("head_casting_and_ecu_token", "#445", "thedodgegarage.com/*445*", "knowledge/engine/",
     "Chrysler 2.2L/2.5L #445 swirl-port head casting specs and flow comparison records."),
    ("head_casting_and_ecu_token", "G-Head", "minimopar.net/*head*", "knowledge/engine/",
     "Chrysler common-port G-Head airflow CFM curves and valve lift benchmarks."),
    ("head_casting_and_ecu_token", "88-90 SMEC 52-way", "minimopar.net/*smec*", "knowledge/ecu/",
     "1988-1990 SMEC connector pinout lineage (cross-check against FSM before Pin 11 use)."),
    ("head_casting_and_ecu_token", "SBEC", "chryslerccdsci.wordpress.com/*", "knowledge/ecu/",
     "1990-1993 SBEC 60-way wiring, SCI telemetry, and ROM layout archival documentation."),
)


def scrub_pii(text: str) -> str:
    """Remove local home paths, email addresses, and UNC hostnames from text."""
    cleaned = text
    for pattern, replacement in _PII_SCRUB_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def _slugify(value: str) -> str:
    """Convert an arbitrary fingerprint string into a deterministic lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "lead"


def build_cdx_query_url(url_pattern: str, limit: int = 50) -> str:
    """Build a canonical Wayback Machine CDX JSON query URL with digest collapsing."""
    encoded_url = urllib.parse.quote(url_pattern, safe="")
    return (
        f"{CDX_BASE_URL}?url={encoded_url}&output=json"
        "&fl=timestamp,original,mimetype,statuscode,digest"
        f"&filter=statuscode:200&collapse=digest&limit={int(limit)}"
    )


def build_memento_url(timestamp: str, original_url: str, raw: bool = True) -> str:
    """Build a Wayback Machine memento URL using the raw id_ modifier by default."""
    ts = str(timestamp).strip()
    orig = str(original_url).strip()
    if not re.fullmatch(r"\d{4,14}", ts):
        raise ValueError(f"Invalid Wayback memento timestamp: {timestamp!r}")
    if not orig.startswith(("http://", "https://")):
        raise ValueError(f"Invalid memento target URL scheme: {original_url!r}")
    flag = "id_" if raw else ""
    return f"{WAYBACK_WEB_BASE}/{ts}{flag}/{orig}"


def _sha256_file(path: Path) -> str:
    """Compute lowercase hexadecimal SHA-256 digest of a file via chunked reads."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest().lower()


def collect_vault_sha256(manifest_path: Path | str | None, raw_root: Path | str | None) -> set[str]:
    """Collect existing SHA-256 digests from the routing manifest and raw heritage vault."""
    vault_hashes: set[str] = set()
    if manifest_path is not None and Path(manifest_path).is_file():
        content = Path(manifest_path).read_text(encoding="utf-8", errors="ignore")
        vault_hashes.update(m.lower() for m in SHA256_HEX_RE.findall(content))

    if raw_root is not None and Path(raw_root).is_dir():
        for root, dirs, files in os.walk(Path(raw_root), followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                fpath = Path(root) / name
                if fpath.is_file():
                    vault_hashes.add(_sha256_file(fpath))
    return vault_hashes


def _load_turbo_profile_state(profiles_turbo_dir: Path | str | None) -> tuple[set[str], list[dict[str, str]]]:
    """Read calibrated basenames and axis_calibration_pending stubs from profiles/turbo."""
    calibrated_basenames: set[str] = set()
    pending_stubs: list[dict[str, str]] = []
    if profiles_turbo_dir is None or not Path(profiles_turbo_dir).is_dir():
        return calibrated_basenames, pending_stubs

    for spec_path in sorted(Path(profiles_turbo_dir).rglob("*")):
        if not spec_path.is_file() or spec_path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            continue
        try:
            raw_text = spec_path.read_text(encoding="utf-8")
            data = json.loads(raw_text) if spec_path.suffix.lower() == ".json" else yaml.safe_load(raw_text)
        except (OSError, ValueError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        turbo_id = str(data.get("turbo_id") or spec_path.parent.name or spec_path.stem)
        rel_img = str(data.get("source_image_relpath") or data.get("legacy_map_path") or data.get("figure_path") or "")
        basename = Path(rel_img).name if rel_img else ""
        sha_val = str(data.get("source_image_sha256") or data.get("figure_sha256") or "").strip().lower()
        parent_name = Path(rel_img).parent.name.lower() if rel_img and Path(rel_img).parent.name else ""
        brand = parent_name if parent_name and parent_name != "maps" else str(data.get("company_name") or "").strip().lower()
        if data.get("axis_calibration_pending") is True:
            pending_stubs.append({
                "turbo_id": turbo_id, "basename": basename or f"{turbo_id}.gif", "brand": brand,
                "sha256": sha_val if SHA256_HEX_RE.fullmatch(sha_val) else "",
            })
        elif basename:
            calibrated_basenames.add(basename.lower())
    return calibrated_basenames, pending_stubs


def _find_beta4_maps_dir(private_root: Path) -> Path | None:
    """Locate turbocalc v5 beta 4 - canvas/maps read-only under private_root."""
    for root, dirs, _ in os.walk(private_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.endswith("_files") and not d.startswith("."))
        if len(Path(root).relative_to(private_root).parts) > 5:
            dirs[:] = []
            continue
        if Path(root).name.lower() == "turbocalc v5 beta 4 - canvas":
            candidate = Path(root) / "maps"
            if candidate.is_dir():
                return candidate
    return None


def _scan_private_tokens(private_root: Path, tokens: Sequence[str]) -> set[str]:
    """Scan bounded non-binary files in private_root read-only to confirm token presence."""
    found: set[str] = set()
    lowered_tokens = {tok: tok.lower() for tok in tokens}
    scanned = 0
    for root, dirs, files in os.walk(private_root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.endswith("_files") and not d.startswith("."))
        for name in sorted(files):
            lower_name = name.lower()
            for tok, tok_low in lowered_tokens.items():
                if tok_low in lower_name:
                    found.add(tok)
            if scanned >= MAX_TEXT_SCAN_FILES:
                continue
            fpath = Path(root) / name
            if fpath.suffix.lower() not in TEXT_SCAN_EXTENSIONS:
                continue
            try:
                if fpath.stat().st_size > MAX_TEXT_SCAN_BYTES:
                    continue
                text_low = fpath.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            scanned += 1
            for tok, tok_low in lowered_tokens.items():
                if tok_low in text_low:
                    found.add(tok)
    return found


def _make_map_record(
    brand: str, basename: str, target_dest: str, rationale: str, local_sha256: str
) -> dict[str, Any]:
    """Build a normalized uncalibrated compressor map fingerprint record."""
    fingerprint = f"{brand}/{basename}" if brand else basename
    pattern = f"squirrelpf.com/turbocalc/maps/{fingerprint}" if brand else f"squirrelpf.com/turbocalc/maps/*{basename}"
    return {
        "category": "uncalibrated_compressor_map",
        "fingerprint": fingerprint,
        "query_pattern": pattern,
        "target_destination": target_dest,
        "rationale": rationale,
        "local_sha256": local_sha256,
    }


def _collect_uncalibrated_maps(
    private_root: Path | None,
    calibrated_basenames: set[str],
    pending_stubs: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Gather uncalibrated compressor map leads from profiles/turbo stubs and beta 4 maps."""
    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for stub in pending_stubs:
        stub_key = stub["basename"].lower()
        if stub_key in seen_keys:
            continue
        seen_keys.add(stub_key)
        records.append(_make_map_record(
            stub["brand"], stub["basename"], "profiles/turbo/",
            f"High-priority Wave A recovery for uncalibrated stub ({stub['turbo_id']}).", stub["sha256"],
        ))

    if private_root is None or not private_root.is_dir():
        return records
    maps_dir = _find_beta4_maps_dir(private_root)
    if maps_dir is None:
        return records

    raster_paths = [p for p in sorted(maps_dir.rglob("*")) if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    use_known_filter = len(raster_paths) > 50 and len(calibrated_basenames) < 100
    for rpath in raster_paths:
        b_low = rpath.name.lower()
        if b_low in calibrated_basenames or b_low in seen_keys:
            continue
        if use_known_filter and b_low not in KNOWN_UNCALIBRATED_MAPS:
            continue
        seen_keys.add(b_low)
        brand = rpath.parent.name.lower() if rpath.parent != maps_dir else ""
        records.append(_make_map_record(
            brand, rpath.name, "data/heritage_raw/figures/turbo_maps/",
            "High-priority Wave A recovery for uncalibrated compressor map raster.", _sha256_file(rpath),
        ))
    return records


def _normalize_extra_fingerprints(
    extra_fingerprints: Sequence[dict[str, Any] | str] | None,
) -> list[dict[str, Any]]:
    """Normalize caller-supplied fallback or supplemental fingerprints into lead records."""
    normalized: list[dict[str, Any]] = []
    for raw_entry in extra_fingerprints or ():
        entry = {"fingerprint": raw_entry} if isinstance(raw_entry, str) else raw_entry
        if not isinstance(entry, dict):
            continue
        fp = scrub_pii(str(entry.get("fingerprint") or entry.get("token") or "").strip())
        if not fp:
            continue
        raw_pat = str(entry.get("query_pattern") or "").strip()
        default_pat = fp if "*" in fp else (f"{fp}*" if ("." in fp or "/" in fp) else f"*{_slugify(fp)}*")
        sha_val = str(entry.get("sha256") or entry.get("local_sha256") or "").strip().lower()
        normalized.append({
            "category": scrub_pii(str(entry.get("category") or "extra_fingerprint")),
            "fingerprint": fp,
            "query_pattern": scrub_pii(raw_pat) if raw_pat else default_pat,
            "target_destination": scrub_pii(str(entry.get("target_destination") or "data/heritage_raw/discovery_queue/")),
            "rationale": scrub_pii(str(entry.get("rationale") or "Supplemental Wave A recovery fingerprint.")),
            "local_sha256": sha_val if SHA256_HEX_RE.fullmatch(sha_val) else "",
            "explicit_status": str(entry.get("status") or ""),
        })
    return normalized


def extract_wave_a_fingerprints(
    private_root: Path | str | None,
    profiles_turbo_dir: Path | str | None = None,
    extra_fingerprints: Sequence[dict[str, Any] | str] | None = None,
) -> list[dict[str, Any]]:
    """Extract Wave A fingerprints read-only from private_references, profiles, and extras."""
    proot = Path(private_root) if private_root is not None else None
    calibrated_basenames, pending_stubs = _load_turbo_profile_state(profiles_turbo_dir)
    map_records = _collect_uncalibrated_maps(proot, calibrated_basenames, pending_stubs)

    seed_tokens = [spec[1] for spec in WAVE_A_SEED_SPECS]
    observed_tokens = _scan_private_tokens(proot, seed_tokens) if (proot and proot.is_dir()) else set()
    seed_records = [
        {
            "category": cat,
            "fingerprint": fp,
            "query_pattern": qpat,
            "target_destination": dest,
            "rationale": f"{rat}{' Confirmed in read-only corpus scan.' if fp in observed_tokens else ''}",
            "local_sha256": "",
        }
        for cat, fp, qpat, dest, rat in WAVE_A_SEED_SPECS
    ]
    return map_records + seed_records + _normalize_extra_fingerprints(extra_fingerprints)


def _build_lead_entry(
    wave: str,
    raw_record: dict[str, Any],
    vault_sha256_set: set[str],
    seen_ids: set[str],
) -> dict[str, Any]:
    """Build and PII-sanitize a single discovery lead dictionary."""
    fingerprint = scrub_pii(str(raw_record["fingerprint"]))
    category = scrub_pii(str(raw_record["category"]))
    query_pattern = scrub_pii(str(raw_record["query_pattern"]))
    local_sha = str(raw_record.get("local_sha256") or "").lower()
    matches = [local_sha] if (local_sha and local_sha in vault_sha256_set) else []
    already_in_vault = bool(matches)

    explicit_status = str(raw_record.get("explicit_status") or "")
    if already_in_vault:
        status = "already_covered"
    elif explicit_status in {"queued_cdx", "already_covered", "needs_manual_capture"}:
        status = explicit_status
    elif not query_pattern:
        status = "needs_manual_capture"
    else:
        status = "queued_cdx"

    base_id = f"wave-{wave.lower()}-{_slugify(category)}-{_slugify(fingerprint)}"
    lead_id, suffix = base_id, 2
    while lead_id in seen_ids:
        lead_id = f"{base_id}-{suffix}"
        suffix += 1
    seen_ids.add(lead_id)

    return {
        "lead_id": lead_id,
        "wave": wave.upper(),
        "category": category,
        "fingerprint": fingerprint,
        "query_pattern": query_pattern,
        "cdx_query_url": build_cdx_query_url(query_pattern),
        "sample_memento_url_template": f"{WAYBACK_WEB_BASE}/{{timestamp}}id_/{{original}}",
        "vault_dedup_sha256_matches": matches,
        "already_in_vault": already_in_vault,
        "status": status,
        "target_destination": scrub_pii(str(raw_record["target_destination"])),
        "rationale": scrub_pii(str(raw_record["rationale"])),
    }


def fetch_cdx_snapshots(
    cdx_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 20.0,
) -> list[dict[str, str]]:
    """Query Wayback Machine CDX JSON endpoint and deduplicate rows by CDX digest."""
    req = urllib.request.Request(cdx_url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    if not isinstance(payload, list) or len(payload) < 2:
        return []

    headers = [str(col) for col in payload[0]]
    snapshots: list[dict[str, str]] = []
    seen_digests: set[str] = set()
    for row in payload[1:]:
        if not isinstance(row, list) or len(row) != len(headers):
            continue
        item = {headers[i]: str(row[i]) for i in range(len(headers))}
        try:
            item["memento_url"] = build_memento_url(item.get("timestamp", ""), item.get("original", ""), raw=True)
        except ValueError:
            continue
        digest = item.get("digest", "")
        if digest and digest in seen_digests:
            continue
        if digest:
            seen_digests.add(digest)
        snapshots.append(item)
    return snapshots


_QUEUE_KEYS = (
    "lead_id", "wave", "category", "fingerprint", "query_pattern",
    "cdx_query_url", "sample_memento_url_template", "already_in_vault", "target_destination",
)


def _populate_queue_items(leads: list[dict[str, Any]], fetch_cdx: bool) -> list[dict[str, Any]]:
    """Build offline or live-fetched CDX queue items excluding vault-covered leads."""
    queue_items: list[dict[str, Any]] = []
    global_digests: set[str] = set()
    cdx_cache: dict[str, list[dict[str, str]]] = {}
    request_count = 0

    for lead in leads:
        if lead["already_in_vault"] or lead["status"] != "queued_cdx":
            continue
        item: dict[str, Any] = {k: lead[k] for k in _QUEUE_KEYS}
        if fetch_cdx:
            cdx_url = lead["cdx_query_url"]
            if cdx_url not in cdx_cache:
                if request_count > 0:
                    time.sleep(POLITE_REQUEST_DELAY_S)
                request_count += 1
                try:
                    cdx_cache[cdx_url] = fetch_cdx_snapshots(cdx_url)
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    item["cdx_error"] = scrub_pii(str(exc))
                    cdx_cache[cdx_url] = []
            deduped = []
            for snap in cdx_cache[cdx_url]:
                dig = snap.get("digest", "")
                if dig and dig in global_digests:
                    continue
                if dig:
                    global_digests.add(dig)
                deduped.append(snap)
            item["cdx_snapshots"] = deduped
        queue_items.append(item)
    return queue_items


def run_discover(
    wave: str = "A",
    private_root: Path | str | None = Path("private_references"),
    manifest_path: Path | str | None = Path("private_references/_INDEX_AND_ROUTING_MANIFEST.md"),
    queue_dir: Path | str = Path("data/heritage_raw/discovery_queue"),
    output_yaml: Path | str = Path("knowledge/discovery_leads.yaml"),
    fetch_cdx: bool = False,
    raw_root: Path | str | None = None,
    profiles_turbo_dir: Path | str | None = Path("profiles/turbo"),
    extra_fingerprints: Sequence[dict[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Run Wave discovery, SHA-256 vault deduplication, and emit cleanroom YAML + queue JSON."""
    wave_norm = wave.strip().upper() or "A"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", wave_norm):
        raise ValueError(f"Invalid wave identifier: {wave!r}")
    qdir = Path(queue_dir).resolve()
    out_yaml_path = Path(output_yaml)
    effective_raw_root = (
        Path(raw_root) if raw_root is not None else (qdir.parent if qdir.name == "discovery_queue" else qdir)
    )

    vault_sha256_set = collect_vault_sha256(manifest_path=manifest_path, raw_root=effective_raw_root)
    raw_records = extract_wave_a_fingerprints(
        private_root=private_root,
        profiles_turbo_dir=profiles_turbo_dir,
        extra_fingerprints=extra_fingerprints,
    )

    seen_ids: set[str] = set()
    leads = [_build_lead_entry(wave_norm, rec, vault_sha256_set, seen_ids) for rec in raw_records]
    generated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    leads_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "wave": wave_norm, "generated_utc": generated_utc,
        "vault_sha256_count": len(vault_sha256_set), "leads": leads,
    }
    out_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    out_yaml_path.write_text(yaml.safe_dump(leads_payload, sort_keys=False, allow_unicode=False), encoding="utf-8")

    queue_items = _populate_queue_items(leads, fetch_cdx=fetch_cdx)
    qdir.mkdir(parents=True, exist_ok=True)
    queue_file = (qdir / f"wave_{wave_norm.lower()}_queue.json").resolve()
    if not queue_file.is_relative_to(qdir):
        raise ValueError(f"Queue path escapes queue directory: {queue_file}")
    queue_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "wave": wave_norm, "generated_utc": generated_utc,
        "fetch_cdx": bool(fetch_cdx), "vault_sha256_count": len(vault_sha256_set),
        "queued_count": len(queue_items),
        "skipped_already_in_vault_count": sum(1 for lead in leads if lead["already_in_vault"]),
        "queue": queue_items,
    }
    queue_file.write_text(json.dumps(queue_payload, indent=2) + "\n", encoding="utf-8")
    return leads_payload
