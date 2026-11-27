"""3-pass Practitioner Authority Graph for LogicAIDE Wave E cleanroom forum/archive triage."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import yaml

from logicaide.heritage.web_normalizer import enforce_heritage_safety, scrub_all_pii

CORE_ECU_PRACTITIONER = "CORE_ECU_PRACTITIONER"
TRUSTED_DOMAIN_PEER = "TRUSTED_DOMAIN_PEER"
UNVERIFIED_COMMUNITY = "UNVERIFIED_COMMUNITY"
MUTED_HANDLE = "MUTED"
DEFAULT_ROSTER_PATH = Path("data/heritage_raw/practitioner_roster.yaml")

SCHEMA_VERSION = "1.0"
_ROSTER_BANNER = "# LogicAIDE Wave E Practitioner Authority Roster (human-editable YAML; override: pin | mute)"
_DOMAIN_PEER_SUBSYSTEMS = frozenset({"transaxle", "cylinder_head", "turbo_airflow", "hardware_bench"})
_TIER_RANK = {CORE_ECU_PRACTITIONER: 0, TRUSTED_DOMAIN_PEER: 1, UNVERIFIED_COMMUNITY: 2}
_CATEGORIES = ("opcodes_6803_68hc11", "hex_ranges", "calibration_tables", "rom_sci_tooling", "socket_bench_hardware")

_OP_CORE = "LDAA|LDAB|LDD|STAA|STAB|LDX|LDY|BCLR|BSET|BRSET|BRCLR|JMP|JSR|RTI|SWI|IDIV|FDIV|CMPA|CMPB|ADDA|ADDD|SUBD|TSTA|CLC|SEI"
_UNAMBIGUOUS_OP_RE = re.compile(rf"(?<![A-Za-z0-9_])({_OP_CORE})(?![A-Za-z0-9_])")
_LOWER_ASM_OP_RE = re.compile(rf"(?m)^[ \t]*(?:[A-Za-z_]\w*:[ \t]*)?({_OP_CORE.lower()})[ \t]+(?:#?\$[0-9A-Fa-f]+|0x[0-9A-Fa-f]+)")
_AMBIGUOUS_OP_RE = re.compile(r"(?<![A-Za-z0-9_])(STD|SEC|CLI|CBA|MUL)(?![A-Za-z0-9_])")
_ASM_LINE_OP_RE = re.compile(r"(?m)^[ \t]*(?:[A-Za-z_]\w*:[ \t]*)?(STD|SEC|CLI|CBA|MUL)(?:[ \t]+(?:#?\$[0-9A-Fa-f]+|0[xX][0-9A-Fa-f]+|\w+,[XY])|[ \t]*(?:;.*)?$)")
_STD_OPERAND_RE = re.compile(r"\bSTD[ \t]+(?:#?\$[0-9A-Fa-f]+|0[xX][0-9A-Fa-f]+|\w+,[XY])")
_AMBIGUOUS_PROSE_FILTER = {
    "STD": re.compile(r"\bSTD[ \t]+(?:bore|rod|main|size|cab|bed|trans|deck|pistons?)\b|\b(?:0?\.\d+|standard)\s+STD\b", re.I),
    "SEC": re.compile(r"\b\d+(?:\.\d+)?[ \t]*SEC\b|\bSEC[ \t]+(?:et|run|pass|quarter|mile|delay|timeout)\b", re.I),
    "CLI": re.compile(r"\bCLI[ \t]+(?:tool|command|mode|flag|arg|option|script|wrapper)\b", re.I),
}
_NAMED_HEX_SET = frozenset(("$8000", "$C000", "$E000", "$B600", "$B7FF", "$FFC0", "$FFFF", "$001E", "$001F", "$1030", "$2000"))
_DOLLAR_HEX_RE = re.compile(r"(?<![A-Za-z0-9_])(\$8000|\$C000|\$E000|\$B600|\$B7FF|\$FFC0|\$FFFF|\$001E|\$001F|\$1030|\$2000|\$00[0-9A-Fa-f]{2})(?![0-9A-Za-z_.])", re.I)
_OX_HEX_RE = re.compile(r"(?<![A-Za-z0-9_])0[xX]([0-9A-Fa-f]{2,4})(?![0-9A-Za-z_])")
_CAL_TABLES = ("FastDataOutputAddressTable", "XmitRequestedRamLocation", "SetupHiSpeedDataTransfer", "DRBFunctionTable", "DRBMemoryTable", "ErrorBitRegister", "PEFTBL", "WOTTBL", "BATOFF", "CLDMAP", "BARDFT", "MapValue")
_CAL_LOOKUP = {name.lower(): name for name in _CAL_TABLES}
_CAL_TABLE_RE = re.compile(rf"(?<![A-Za-z0-9])({'|'.join(_CAL_TABLES)})(?![A-Za-z0-9])", re.I)
_TOOLING_CANON = {"chemasm": "ChemAsm", "chemasmsmec": "ChemAsmSMEC", "chemasmlm": "ChemAsmLM", "zchem": "zChEM", "mpscan": "MPScan", "mpscilink": "mpscilink", "mptune": "MPTune", "d-cal": "D-Cal", "dualogger": "DuaLogger", "blueberry60": "Blueberry60", "ladybug60": "Ladybug60", "as6811": "AS6811", "as11": "AS11", "drb-ii": "DRB-II", "drbii": "DRB-II"}
_HI_TOOLING = frozenset(_TOOLING_CANON.values()) - {"MPTune", "DRB-II"} | {"62500", "62.5k", "7812.5"}
_TOOLING_RE = re.compile(r"(?<![A-Za-z0-9])(ChemAsmSMEC|ChemAsmLM|ChemAsm|zChEM|MPScan|mpscilink|MPTune|D-Cal|DuaLogger|Blueberry60|Ladybug60|AS6811|AS11|DRB[- ]II)(?![A-Za-z0-9])", re.I)
_BAUD_RE = re.compile(r"(?<![0-9.])(62,?500|62\.5\s*[kK](?:bps|baud)?|7812\.5)(?![0-9.])")
_HI_HARDWARE = frozenset({"SST27SF020", "28C256", "27C256", "27128", "2764", "74LS193", "74HC00", "4N25", "1N4733A", "LM2907", "LM2917", "NJM4151", "Z12", "DIS_Adaptor", "Knock_Board", "VB921"})
_HARDWARE_RE = re.compile(r"(?<![A-Za-z0-9_])(SST27SF020|28C256|27C256|27128|2764|74LS193|74HC00|4N25|1N4733A|LM2907N?|LM2917N?|NJM4151|Z12|DIS[_ -]?Adaptor|Knock[_ -]?Board|VB921|Ostrich(?:\s*2(?:\.0)?)?|Pin[\s_-]?11|(?:60|50|40)[- ]way)(?![A-Za-z0-9_])", re.I)
_SUBSYSTEM_PATTERNS = {
    "ecu_rom": re.compile(r"\b(?:SMEC|SBEC|68HC11|6803|EPROM|EEPROM|disassembly|checksum|vector|fuel\s+table|spark\s+table|cal(?:ibration)?\s+mask)\b", re.I),
    "sci_diag": re.compile(r"\b(?:SCI|CCD|DRB[- ]?II|baud|fault\s+code|Code\s+(?:11|12|23|24|55)|D20|D21|DK20|DK21|diagnostic|datalog(?:ger|ging)?)\b", re.I),
    "transaxle": re.compile(r"\b(?:A404|A413|A415|A470|A520|A555|A568|A604|41TE|42LE|Getrag|transaxle|transmission|torque\s+converter|stall\s+speed|final\s+drive|gear\s+ratio|differential|halfshaft|shift\s+linkage|flywheel|flexplate|ATSG)\b", re.I),
    "cylinder_head": re.compile(r"(?:#782|#445|\b(?:782|445)\s+(?:head|casting)\b|\b(?:G-?head|swirl[- ]port|common[- ]port|DOHC|SOHC|Lotus\s+16V|Maserati\s+16V|ECC|ECB|EDZ|K-?block|camshaft|cam\s+profile|valve\s+lift|flowbench|cfm|port(?:ed|ing)|deck\s+height|head\s+gasket|compression\s+ratio|valve\s+spring)\b)", re.I),
    "turbo_airflow": re.compile(r"\b(?:T03|TB03|TE04H|VNT25|TD04LR?|TD05H|HX40|HX50|HX60|GT3071R|T3/T4|Garrett|Mitsubishi\s+turbo|wastegate|blow[- ]?off|BOV|intercooler|compressor\s+map|turbine|boost|3[- ]bar|2[- ]bar|MAP\s+sensor|TurboCalc|squirrelpf|airflow|spool|A/R|trim|surge\s+line|pressure\s+ratio|manifold\s+pressure|baro)\b", re.I),
    "hardware_bench": re.compile(r"\b(?:bench|breadboard|oscilloscope|logic\s+analyzer|multimeter|continuity|pinout|bulkhead|harness|solder|socket|ZIF|pullup|resistor|opto|ASD\s+relay|Cavity\s+\d+|K22|A14|C20)\b", re.I),
}
_POSITIVE_ENDORSE_RE = re.compile(r"\b(?:thanks|thank\s+you|thx|kudos|props|credits?|confirmed|confirm(?:s|ed)?|verified|verify|measured|flowed|flowbench|bench(?:ed|-tested)?|ratios?|collaborat(?:ed|ing|ion)|corroborat(?:ed|es|ing)|matched|spot\s+on|accurate)\b", re.I)
_NEGATIVE_DISPUTE_RE = re.compile(r"\b(?:wrong|incorrect|disagree(?:s|d|ing)?|myths?|blown|bad\s+advice|false|bogus|debunk(?:ed)?|misleading|nonsense|unverified|unconfirmed|not\s+(?:verified|confirmed|measured|correct)|don'?t\s+trust|do\s+not\s+trust)\b", re.I)
_RESOLUTION_CUE_RE = re.compile(r"\b(?:verified|confirmed|solved|fixed|resolved|measured|bench(?:ed|-tested)?|corroborated|working)\b", re.I)
_TECH_CORROBORATION_RE = re.compile(r"(?:#782|#445|\b(?:A404|A413|A415|A470|A520|A555|A568|A604|41TE|42LE|T03|TB03|TE04H|VNT25|TD04LR?|G-?head|Pin\s+\d+|Cavity\s+\d+|K22|K4|K6|Z12|D20|D21|DK20|DK21|A14|C20)\b|\b\d+(?:\.\d+)?\s*(?:V|VDC|mV|ohm|ohms|kHz|Hz|baud|psi|kPa|cfm|cc/min|lb/hr|ms|us|deg)\b)", re.I)
_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?(?:\.[A-Za-z0-9_-]+)*)")
_QUOTE_BLOCK_RE = re.compile(r"(?:\[quote[^\]]*\].*?\[/quote\]|<blockquote\b[^>]*>.*?</blockquote>)", re.I | re.S)


def _norm_handle(handle: str) -> str:
    return handle.strip().lstrip("@").strip().lower()


def _clean_handle(raw: Any) -> str:
    """Normalize display handle string, strip leading @, scrub PII, and check Deadbolt."""
    if raw is None or not str(raw).strip():
        return ""
    cleaned = enforce_heritage_safety(str(raw).strip(), label="handle")
    return re.sub(r"\s+", " ", cleaned.strip().lstrip("@").strip())


def _extract_post_text(item: Mapping[str, Any], *, include_title: bool = True) -> str:
    parts = [str(item["title"])] if include_title and item.get("title") else []
    parts.extend(str(item[k]) for k in ("body", "text", "content", "message", "snippet", "markdown") if item.get(k))
    return "\n".join(parts).strip()


def _flatten_posts(threads_or_posts: Any) -> list[dict[str, Any]]:
    """Normalize threads, wrapper mappings, or flat post sequences into post dicts."""
    if threads_or_posts is None:
        return []
    if isinstance(threads_or_posts, Mapping):
        items: Iterable[Any] = next((threads_or_posts[k] for k in ("threads", "posts") if isinstance(threads_or_posts.get(k), Sequence) and not isinstance(threads_or_posts[k], (str, bytes)) and not (k == "posts" and threads_or_posts.get("author"))), [threads_or_posts])
    else:
        items = threads_or_posts

    flattened: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        nested = next((item[k] for k in ("posts", "replies", "messages") if isinstance(item.get(k), Sequence) and not isinstance(item.get(k), (str, bytes))), None)
        if nested is None:
            flattened.append({**item, "post_id": item.get("post_id", f"post-{idx}")})
            continue
        tid = str(item.get("thread_id") or item.get("id") or f"thread-{idx}")
        op_author, op_text = item.get("author") or item.get("op_author") or item.get("handle"), _extract_post_text(item, include_title=True)
        if op_author and op_text and not item.get("posts"):
            flattened.append({"post_id": f"{tid}-op", "thread_id": tid, "author": op_author, "body": op_text, "quoted_authors": item.get("quoted_authors") or ()})
        for p_idx, sub in enumerate(nested):
            if isinstance(sub, Mapping):
                sc = {**sub, "thread_id": sub.get("thread_id", tid), "post_id": sub.get("post_id", f"{tid}-p{p_idx}")}
                if p_idx == 0 and item.get("title") and not sc.get("title"):
                    sc["title"] = item["title"]
                flattened.append(sc)
    return flattened


def _canon_hw(raw: str) -> str:
    low = raw.lower()
    for prefix, val in (("ostrich", "Ostrich"), ("lm2907", "LM2907"), ("lm2917", "LM2917")):
        if low.startswith(prefix):
            return val
    if "pin" in low and "11" in low:
        return "Pin 11"
    if "dis" in low and "adaptor" in low:
        return "DIS_Adaptor"
    if "knock" in low and "board" in low:
        return "Knock_Board"
    return re.sub(r"\s+", "-", low) if low.endswith("way") else ("Z12" if low == "z12" else raw.upper())


def _extract_proof_signals(text: str) -> tuple[dict[str, set[str]], set[str]]:
    """Extract Pass 1 proof signals across the 5 technical categories and high-entropy set."""
    cats: dict[str, set[str]] = {c: set() for c in _CATEGORIES}
    hi: set[str] = set()
    op_hits = {m.group(1) for m in _UNAMBIGUOUS_OP_RE.finditer(text)} | {m.group(1).upper() for m in _LOWER_ASM_OP_RE.finditer(text)}
    asm_lines, has_std_op = {m.group(1) for m in _ASM_LINE_OP_RE.finditer(text)}, bool(_STD_OPERAND_RE.search(text))
    for m in _AMBIGUOUS_OP_RE.finditer(text):
        tok = m.group(1)
        if not (tok in _AMBIGUOUS_PROSE_FILTER and _AMBIGUOUS_PROSE_FILTER[tok].search(text)) and ((tok == "STD" and has_std_op) or tok in asm_lines or op_hits):
            op_hits.add(tok)
    cats["opcodes_6803_68hc11"].update(op_hits)
    hi.update(f"opcodes_6803_68hc11:{t}" for t in op_hits)

    hex_hits = {m.group(1).upper() for m in _DOLLAR_HEX_RE.finditer(text)} | {f"0x{m.group(1).upper()}" for m in _OX_HEX_RE.finditer(text)}
    cats["hex_ranges"].update(hex_hits)
    hi.update(f"hex_ranges:{t}" for t in hex_hits)
    for m in _CAL_TABLE_RE.finditer(text):
        raw = m.group(1)
        if not (raw.lower() == "mapvalue" and raw == raw.lower()):
            tok = _CAL_LOOKUP.get(raw.lower(), raw)
            cats["calibration_tables"].add(tok)
            hi.add(f"calibration_tables:{tok}")
    for m in (*_TOOLING_RE.finditer(text), *_BAUD_RE.finditer(text)):
        low = re.sub(r"[\s,]+", "", m.group(1)).lower()
        tok = _TOOLING_CANON.get(low, "62.5k" if "62.5" in low else ("62500" if "62500" in low else ("7812.5" if "7812.5" in low else m.group(1))))
        cats["rom_sci_tooling"].add(tok)
        if tok in _HI_TOOLING:
            hi.add(f"rom_sci_tooling:{tok}")
    for tok in (_canon_hw(m.group(1)) for m in _HARDWARE_RE.finditer(text)):
        cats["socket_bench_hardware"].add(tok)
        if tok in _HI_HARDWARE:
            hi.add(f"socket_bench_hardware:{tok}")
    return cats, hi


def _score_pass1_handles(posts: Sequence[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Execute Pass 1 technical proof scoring across all posts per authored handle."""
    acc: dict[str, dict[str, Any]] = {}
    norm_to_display: dict[str, str] = {}
    cleaned_posts: list[dict[str, Any]] = []

    for idx, post in enumerate(posts):
        raw_author = next((post.get(k) for k in ("author", "handle", "username", "user", "posted_by") if post.get(k)), None)
        if not (author := _clean_handle(raw_author)):
            continue
        display = norm_to_display.setdefault(_norm_handle(author), author)
        post_id = str(post.get("post_id") or f"post-{idx}")
        text = enforce_heritage_safety(_extract_post_text(post, include_title=True), label=f"post:{post_id}")
        raw_q = post.get("quoted_authors") or post.get("quotes") or ()
        quoted = [q for q in (_clean_handle(x) for x in ([raw_q] if isinstance(raw_q, str) else raw_q)) if q]

        entry = acc.setdefault(display, {"cats": {c: set() for c in _CATEGORIES}, "hi": set(), "subs": set(), "count": 0, "snippets": []})
        cats, hi = _extract_proof_signals(text)
        for c, vals in cats.items():
            entry["cats"][c].update(vals)
        entry["hi"].update(hi)
        subs = {tag for tag, pat in _SUBSYSTEM_PATTERNS.items() if pat.search(text)}
        if cats["opcodes_6803_68hc11"] or cats["calibration_tables"] or (cats["rom_sci_tooling"] & {"ChemAsm", "ChemAsmSMEC", "ChemAsmLM", "zChEM", "AS6811", "AS11", "D-Cal", "MPTune"}) or (cats["hex_ranges"] & _NAMED_HEX_SET):
            subs.add("ecu_rom")
        if (cats["rom_sci_tooling"] & {"MPScan", "mpscilink", "DuaLogger", "Blueberry60", "Ladybug60", "DRB-II", "62500", "62.5k", "7812.5"}) or (cats["hex_ranges"] & {"0xF2", "0xF4", "0xF6"}) or (cats["calibration_tables"] & {"DRBFunctionTable", "DRBMemoryTable", "FastDataOutputAddressTable", "XmitRequestedRamLocation", "SetupHiSpeedDataTransfer", "ErrorBitRegister"}):
            subs.add("sci_diag")
        if cats["socket_bench_hardware"]:
            subs.add("hardware_bench")
        if "BARDFT" in cats["calibration_tables"]:
            subs.add("turbo_airflow")
        entry["subs"].update(subs)
        entry["count"] += 1
        snip = scrub_all_pii(re.sub(r"\s+", " ", text).strip()[:160])
        if snip and snip not in entry["snippets"] and len(entry["snippets"]) < 3:
            entry["snippets"].append(snip)
        cleaned_posts.append({"post_id": post_id, "author": display, "text": text, "quoted_authors": quoted})

    scored: dict[str, dict[str, Any]] = {}
    for display, d in acc.items():
        c, hi = d["cats"], d["hi"]
        active = sorted(k for k in _CATEGORIES if c[k])
        score = round(1.5 * len(c["opcodes_6803_68hc11"]) + 2.0 * len(c["calibration_tables"]) + sum(1.5 if t in _NAMED_HEX_SET or t in {"0xF2", "0xF4", "0xF6"} else 1.25 for t in c["hex_ranges"]) + sum(1.75 if f"rom_sci_tooling:{t}" in hi else 0.7 for t in c["rom_sci_tooling"]) + sum(1.5 if f"socket_bench_hardware:{t}" in hi else 0.7 for t in c["socket_bench_hardware"]), 3)
        tier = CORE_ECU_PRACTITIONER if (len(active) >= 2 and score >= 5.0) or len(hi) >= 3 else UNVERIFIED_COMMUNITY
        scored[display] = {"handle": display, "handle_slug": re.sub(r"[^a-z0-9]+", "-", display.lower()).strip("-") or "handle", "trust_tier": tier, "pass1_trust_tier": tier, "proof_score": score, "high_entropy_signal_count": len(hi), "signal_categories": active, "proof_signals": sorted(f"{k}:{t}" for k in active for t in c[k]), "subsystems": sorted(d["subs"]), "post_count": d["count"], "endorsed_by": [], "pinned": False, "muted": False, "override": None, "sample_snippets": d["snippets"]}
    return scored, norm_to_display, cleaned_posts


def _apply_pass2_endorsements(scored_handles: dict[str, dict[str, Any]], cleaned_posts: Sequence[dict[str, Any]], norm_to_display: dict[str, str], muted_norms: set[str]) -> list[dict[str, Any]]:
    """Run 1-hop positive endorsement scan strictly from non-muted Pass 1 CORE practitioners."""
    core_norms = {_norm_handle(h) for h, rec in scored_handles.items() if rec["pass1_trust_tier"] == CORE_ECU_PRACTITIONER and _norm_handle(h) not in muted_norms}
    edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for post in cleaned_posts:
        author, author_norm = post["author"], _norm_handle(post["author"])
        if author_norm not in core_norms:
            continue
        unquoted = _QUOTE_BLOCK_RE.sub(" ", post["text"])
        endorsed: dict[str, list[str]] = {}
        for clause in (s.strip() for s in re.split(r"(?:\n+|[.!?;]+\s+)", unquoted) if s.strip()):
            if _NEGATIVE_DISPUTE_RE.search(clause) or not (cues := [m.group(0).lower() for m in _POSITIVE_ENDORSE_RE.finditer(clause)]):
                continue
            targets = {norm_to_display[n] for m in _MENTION_RE.finditer(clause) if (n := _norm_handle(m.group(1).rstrip(".-"))) in norm_to_display}
            for norm, disp in norm_to_display.items():
                if len(norm) >= 2 and disp not in targets and re.search(rf"(?<![A-Za-z0-9_]){re.escape(disp)}(?![A-Za-z0-9_])", clause, re.I):
                    targets.add(disp)
            for target in targets:
                if _norm_handle(target) != author_norm:
                    endorsed.setdefault(target, []).extend(cues)

        if not _NEGATIVE_DISPUTE_RE.search(unquoted) and (full_cues := [m.group(0).lower() for m in _POSITIVE_ENDORSE_RE.finditer(unquoted)]):
            for q_raw in post["quoted_authors"]:
                if (target := norm_to_display.get(_norm_handle(q_raw))) and _norm_handle(target) != author_norm:
                    endorsed.setdefault(target, []).extend(full_cues)

        for target, cues in sorted(endorsed.items()):
            if _norm_handle(target) in muted_norms or target not in scored_handles or (author, target, post["post_id"]) in seen_pairs:
                continue
            seen_pairs.add((author, target, post["post_id"]))
            trec = scored_handles[target]
            if author not in trec["endorsed_by"]:
                trec["endorsed_by"] = sorted([*trec["endorsed_by"], author])
            if trec["pass1_trust_tier"] != CORE_ECU_PRACTITIONER and (set(trec["subsystems"]) & _DOMAIN_PEER_SUBSYSTEMS):
                trec["trust_tier"] = TRUSTED_DOMAIN_PEER
            edges.append({"from_handle": author, "to_handle": target, "post_id": post["post_id"], "signals": sorted(set(cues)), "snippet": re.sub(r"\s+", " ", post["text"]).strip()[:160]})
    return edges


def _parse_override_value(entry: Any) -> str | None:
    """Parse an override directive ('pin' or 'mute') from a YAML entry or string."""
    if isinstance(entry, str):
        val = entry.strip().lower()
        return "pin" if val in {"pin", "pinned"} else ("mute" if val in {"mute", "muted", MUTED_HANDLE.lower()} else None)
    if isinstance(entry, Mapping):
        if entry.get("muted") is True or str(entry.get("trust_tier", "")).strip().upper() == MUTED_HANDLE:
            return "mute"
        if isinstance(entry.get("override"), str) and (parsed := _parse_override_value(entry["override"])):
            return parsed
        if entry.get("pinned") is True:
            return "pin"
    return None


def load_roster_overrides(roster_path: Path | str = DEFAULT_ROSTER_PATH) -> dict[str, str]:
    """Read existing YAML operator overrides ('pin' | 'mute'), rejecting symlinks."""
    path = Path(roster_path)
    if path.is_symlink():
        raise ValueError(f"Roster path must not be a symlink: {path}")
    if not path.exists() or not (loaded := yaml.safe_load(path.read_text(encoding="utf-8"))):
        return {}
    container: Any = next((loaded[k] for k in ("handles", "practitioners", "roster") if isinstance(loaded, Mapping) and k in loaded), loaded)
    overrides: dict[str, str] = {}
    if isinstance(container, Mapping):
        for raw_h, entry in container.items():
            if (h := _clean_handle(raw_h)) and (ov := _parse_override_value(entry)):
                overrides[h] = ov
    elif isinstance(container, Sequence) and not isinstance(container, (str, bytes)):
        for entry in container:
            if isinstance(entry, Mapping) and (h := _clean_handle(entry.get("handle") or entry.get("name"))) and (ov := _parse_override_value(entry)):
                overrides[h] = ov
    return overrides


def apply_overrides(scored_handles: Mapping[str, Mapping[str, Any]], existing_overrides: Mapping[str, Any] | None, pin_handles: Iterable[str] = (), mute_handles: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    """Apply persistent pin/mute overrides, blocking muted handles and muted endorsements."""
    result: dict[str, dict[str, Any]] = {}
    norm_to_key: dict[str, str] = {}
    for raw_key, rec in scored_handles.items():
        if clean_key := _clean_handle(raw_key):
            copied = deepcopy(dict(rec))
            copied.setdefault("handle", clean_key)
            copied.setdefault("handle_slug", re.sub(r"[^a-z0-9]+", "-", clean_key.lower()).strip("-") or "handle")
            result[clean_key] = copied
            norm_to_key[_norm_handle(clean_key)] = clean_key

    resolved: dict[str, tuple[str, str]] = {}
    for raw_h, raw_val in (existing_overrides or {}).items():
        if (clean_h := _clean_handle(raw_h)) and (ov := _parse_override_value(raw_val)):
            resolved[_norm_handle(clean_h)] = (norm_to_key.get(_norm_handle(clean_h), clean_h), ov)
    for ov, handles in (("pin", pin_handles), ("mute", mute_handles)):
        for raw_h in handles:
            if clean_h := _clean_handle(raw_h):
                resolved[_norm_handle(clean_h)] = (norm_to_key.get(_norm_handle(clean_h), clean_h), ov)

    muted_norms = {norm for norm, (_, ov) in resolved.items() if ov == "mute"}
    for norm, (disp, _) in resolved.items():
        if norm not in norm_to_key:
            norm_to_key[norm] = disp
            result[disp] = {
                "handle": disp, "handle_slug": re.sub(r"[^a-z0-9]+", "-", disp.lower()).strip("-") or "handle",
                "trust_tier": UNVERIFIED_COMMUNITY, "pass1_trust_tier": UNVERIFIED_COMMUNITY, "proof_score": 0.0,
                "high_entropy_signal_count": 0, "signal_categories": [], "proof_signals": [], "subsystems": [],
                "post_count": 0, "endorsed_by": [], "pinned": False, "muted": False, "override": None, "sample_snippets": [],
            }

    for key, rec in result.items():
        if isinstance(rec.get("endorsed_by"), list):
            orig = rec["endorsed_by"]
            active = [e for e in orig if _norm_handle(str(e)) not in muted_norms]
            rec["endorsed_by"] = active
            if orig and not active and rec.get("trust_tier") == TRUSTED_DOMAIN_PEER and rec.get("pass1_trust_tier") != CORE_ECU_PRACTITIONER:
                rec["trust_tier"] = UNVERIFIED_COMMUNITY
        ov = resolved.get(_norm_handle(key), (None, None))[1]
        rec["override"], rec["muted"], rec["pinned"] = ov, (ov == "mute"), (ov == "pin")
        if ov == "mute":
            rec["trust_tier"] = UNVERIFIED_COMMUNITY
        elif ov == "pin":
            rec["trust_tier"] = CORE_ECU_PRACTITIONER
    return result


def _write_roster_yaml(roster_path: Path, payload: dict[str, Any]) -> None:
    """Write human-editable YAML roster while preserving manual comments/notes and rejecting symlinks."""
    if roster_path.is_symlink():
        raise ValueError(f"Roster path must not be a symlink: {roster_path}")

    def _key_norm(raw_key: str) -> str:
        try:
            val = yaml.safe_load(raw_key.strip())
        except yaml.YAMLError:
            val = raw_key.strip()
        return _norm_handle(_clean_handle(val))

    notes, leading, h_comments, inline, pending = {}, [], {}, {}, []
    if roster_path.exists():
        raw_text = roster_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw_text)
        if isinstance(parsed, Mapping) and isinstance(parsed.get("handles"), Mapping):
            for raw_h, rec in parsed["handles"].items():
                if isinstance(rec, Mapping) and rec.get("notes") and (h := _clean_handle(raw_h)):
                    notes[_norm_handle(h)] = enforce_heritage_safety(str(rec["notes"]), label=f"notes:{h}")
        seen_root, in_handles, cur_norm = False, False, None
        for line in raw_text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                cl = enforce_heritage_safety(line, label="roster_comment")
                if not seen_root and _ROSTER_BANNER not in cl:
                    leading.append(cl)
                elif seen_root:
                    pending.append(cl)
            elif not s:
                continue
            elif not line.startswith(" "):
                seen_root, in_handles, cur_norm = True, s.startswith("handles:"), None
                pending.clear()
            elif in_handles and re.match(r"^  [^\s#]", line):
                kp, _, cp = line[2:].partition(":")
                cur_norm = _key_norm(kp)
                if cur_norm and pending:
                    h_comments[cur_norm] = list(pending)
                pending.clear()
                if cur_norm and "#" in cp:
                    inline[cur_norm] = enforce_heritage_safety("#" + cp.split("#", 1)[1], label="inline_comment")
            elif in_handles and cur_norm and s.startswith("override:") and "#" in s:
                inline[f"{cur_norm}::override"] = enforce_heritage_safety("#" + s.split("#", 1)[1], label="override_comment")
            else:
                pending.clear()

    for h, rec in payload["handles"].items():
        if _norm_handle(h) in notes and "notes" not in rec:
            rec["notes"] = notes[_norm_handle(h)]
    dumped = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False, default_flow_style=False)
    out_lines, in_handles, cur_norm = [_ROSTER_BANNER, *leading], False, None
    for line in dumped.splitlines():
        if not line.startswith(" "):
            in_handles, cur_norm = line.startswith("handles:"), None
        elif in_handles and re.match(r"^  [^\s]", line):
            cur_norm = _key_norm(line[2:].split(":", 1)[0])
            out_lines.extend(h_comments.get(cur_norm, ()))
            if cur_norm in inline:
                line = f"{line}  {inline[cur_norm]}"
        elif in_handles and cur_norm and line.lstrip().startswith("override:") and f"{cur_norm}::override" in inline:
            line = f"{line}  {inline[f'{cur_norm}::override']}"
        out_lines.append(line)
    rendered = "\n".join([*out_lines, *pending]) + "\n"
    try:
        yaml.safe_load(rendered)
    except yaml.YAMLError:
        rendered = "\n".join([_ROSTER_BANNER, *leading, dumped, *pending]).rstrip() + "\n"
    rendered = enforce_heritage_safety(rendered, label=str(roster_path))
    roster_path.parent.mkdir(parents=True, exist_ok=True)
    roster_path.write_text(rendered, encoding="utf-8")


def build_practitioner_authority_graph(
    threads_or_posts: Any, *, roster_path: Path | str = DEFAULT_ROSTER_PATH,
    pin_handles: Iterable[str] = (), mute_handles: Iterable[str] = (), save_roster: bool = True,
) -> dict[str, Any]:
    """Run the 3-pass Practitioner Authority Graph and optionally persist YAML roster."""
    path = Path(roster_path)
    existing_overrides = load_roster_overrides(path)
    scored, norm_to_display, cleaned_posts = _score_pass1_handles(_flatten_posts(threads_or_posts))
    pre_muted = {_norm_handle(h) for h, ov in existing_overrides.items() if ov == "mute"}
    pre_muted.difference_update(_norm_handle(_clean_handle(h)) for h in pin_handles)
    pre_muted.update(_norm_handle(_clean_handle(h)) for h in mute_handles)

    edges = _apply_pass2_endorsements(scored, cleaned_posts, norm_to_display, pre_muted)
    final_handles = apply_overrides(scored, existing_overrides, pin_handles=pin_handles, mute_handles=mute_handles)
    ordered = dict(sorted(final_handles.items(), key=lambda kv: (_TIER_RANK.get(kv[1]["trust_tier"], 9), kv[0].lower())))
    tiers = [r["trust_tier"] for r in ordered.values()]
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "roster_path": str(path),
        "summary": {
            "total_handles": len(ordered), "core_ecu_practitioners": tiers.count(CORE_ECU_PRACTITIONER),
            "trusted_domain_peers": tiers.count(TRUSTED_DOMAIN_PEER), "unverified_community": tiers.count(UNVERIFIED_COMMUNITY),
            "muted_handles": sum(1 for r in ordered.values() if r.get("muted")),
            "pinned_handles": sum(1 for r in ordered.values() if r.get("pinned")), "endorsement_edge_count": len(edges),
        },
        "handles": ordered, "endorsements": edges, "overrides": {h: r["override"] for h, r in ordered.items() if r.get("override")},
    }
    if save_roster:
        _write_roster_yaml(path, payload)
    return payload


def _is_trusted_author(handle: Any, roster: Mapping[str, Any]) -> bool:
    clean = _clean_handle(handle)
    handles_map = roster.get("handles", roster) if isinstance(roster, Mapping) else {}
    if not clean or not isinstance(handles_map, Mapping):
        return False
    rec = handles_map.get(clean) or next((v for k, v in handles_map.items() if _norm_handle(str(k)) == _norm_handle(clean) and isinstance(v, Mapping)), None)
    return bool(isinstance(rec, Mapping) and not rec.get("muted") and rec.get("override") != "mute" and rec.get("trust_tier") in {CORE_ECU_PRACTITIONER, TRUSTED_DOMAIN_PEER})


def classify_thread_trust_tier(thread: Mapping[str, Any], roster: Mapping[str, Any]) -> str:
    """Return 'Tier 2' if OP or verified resolution is from a trusted practitioner with technical corroboration, else 'Tier 3'."""
    if not isinstance(thread, Mapping) or (flag := thread.get("technical_corroboration", thread.get("corroborated"))) is False:
        return "Tier 3"
    has_tech = lambda txt: (flag is True or (isinstance(flag, str) and flag.strip().lower() not in {"", "false", "no", "0"}) or bool(txt and (any(_extract_proof_signals(txt)[0].values()) or _TECH_CORROBORATION_RE.search(txt))))
    posts = [p for p in (thread.get("posts") or thread.get("replies") or thread.get("messages") or ()) if isinstance(p, Mapping)]
    op_author = thread.get("op_author") or thread.get("author") or (posts[0].get("author") if posts else None)
    op_text = _extract_post_text(posts[0], include_title=True) if thread.get("posts") and posts else _extract_post_text(thread, include_title=True)
    if thread.get("title") and thread.get("posts") and posts and not posts[0].get("title"):
        op_text = f"{thread['title']}\n{op_text}".strip()
    if _is_trusted_author(op_author, roster) and any(has_tech(txt) for txt in (op_text, *(_extract_post_text(p, include_title=False) for p in posts if _is_trusted_author(p.get("author"), roster)))):
        return "Tier 2"

    res_obj = thread.get("verified_resolution") or thread.get("resolution") or thread.get("accepted_answer")
    res_author = (res_obj.get("author") or res_obj.get("handle") if isinstance(res_obj, Mapping) else None) or thread.get("resolution_author") or thread.get("verified_by") or thread.get("accepted_author")
    if res_author or isinstance(res_obj, (Mapping, str)):
        res_text = (_extract_post_text(res_obj, include_title=False) or str(res_obj.get("fix") or "")) if isinstance(res_obj, Mapping) else (res_obj if isinstance(res_obj, str) else _extract_post_text(thread))
        if _is_trusted_author(res_author, roster) and has_tech(res_text):
            return "Tier 2"

    marked = [p for p in posts if any(p.get(k) for k in ("is_resolution", "verified_resolution", "is_verified_resolution", "accepted", "is_accepted", "is_fix")) or p.get("role") in {"resolution", "verified_resolution", "fix"}]
    if any(_is_trusted_author(p.get("author"), roster) and has_tech(_extract_post_text(p, include_title=False)) for p in marked):
        return "Tier 2"
    if not marked and not isinstance(res_obj, (Mapping, str)) and not res_author and any(
        _is_trusted_author(p.get("author"), roster) and _RESOLUTION_CUE_RE.search(txt := _extract_post_text(p, include_title=False)) and has_tech(txt) for p in posts
    ):
        return "Tier 2"
    return "Tier 3"
