"""Deterministic offline Parent-Child Hybrid RAG query engine and safety gates."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from logicaide.heritage.rag_index import (
    DEFAULT_INDEX_DB, EMBEDDING_DIM, _try_load_sqlite_vec, compute_offline_embedding,
    cosine_similarity, pack_float32_embedding, unpack_float32_embedding,
)
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH, NO_VERIFIED_SOURCE_MATCH, lookup_sci

RRF_K = 60
TIER_WEIGHTS: dict[str, float] = {"Tier 1": 1.25, "Tier 2": 1.10, "Tier 3": 0.90}
_TIER_RANK = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
_VECTOR_ADMIT_COSINE, _ABSTAIN_MAX_COSINE = 0.12, 0.20
_DEFAULT_ROM_IDS = ("smec_88_89_v19", "sbec_90_93_v3_1", "lm_86_87_v13", "lm_86_glhs_v1")
_REQUIRED_TABLES = frozenset({"heritage_parents", "heritage_children", "heritage_fts"})

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9$_#.-]+")
_PIN_QUERY_RE = re.compile(r"(?:\b(?:pins?|cavit(?:y|ies)|cav)[\s_#-]*\d{1,2}\b|\b(?:60|50|40|14|26)[\s_-]*(?:way|pin)\b|\bpinout\b|\bconnector\s+pins?\b|\b(?:red|blue|db15)\s+(?:pin\s+)?\d{1,2}\b|\b[12]-(?:[1-9]|[1-5]\d|60)\b(?![A-Za-z]))", re.I)
_SCI_QUERY_RE = re.compile(r"(?:(?<!\w)\$[0-9A-Fa-f]{2,4}\b|\b0[xXhH][0-9A-Fa-f]{2,4}\b|\b(?:sci|ram)\s+(?:offset|address|location|table|byte|param)\b|\bsci\s+(?:tx|rx|commands?|baud|stream)\b)", re.I)
_HEX_OR_DEC_RE = re.compile(r"^(?:0[xh][0-9a-f]+|\$[0-9a-f]+|\d+)$", re.I)
_NON60_CONN_RE = re.compile(r"\b(?:ic-datasheets|74ls193|sst27sf|lm2907|lm2917|njm4151|14-way|14way|50-way|50way|40-way|40way|25-way|25way|10-way|12-way|37-cavity|db15|arduino)\b", re.I)
_NON60_SUB_RE = re.compile(r"\b(?:28c256|74hc00|jedec|28-pin|32-pin|hardware bom|relay pin wiring|lm\+pm|lm / pm)\b", re.I)
_NON60_HDR_RE = re.compile(r"\b(?:14-way|14way|50-way|50way|40-way|40way|25-way|25way|db15|arduino|lm|pm|njm\d*|eeprom|source signal|ic|28-pin|32-pin)\b", re.I)
_NON60_PROSE_RE = re.compile(r"\b(?:eeprom|ic|socket|db15|arduino|red|blue|u1|pia|50-way|40-way|14-way|25-way|28-pin|32-pin|16-pin|8-pin)\s+(?:(?:conn(?:\.\s*|\s+)|(?:connector|bulkhead)\s+))?(?:pin|cavity|cav)s?[\s_#-]*\d{1,2}(?:[\s,/&-]+\d{1,2})*\b", re.I)
_TBL_SEP_RE = re.compile(r"^:?-{2,}:?$")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does", "for", "from", "had", "has", "have", "how",
    "i", "if", "in", "into", "is", "it", "its", "may", "my", "no", "not", "of", "on", "or", "our", "should", "that", "the",
    "their", "them", "then", "there", "these", "they", "this", "to", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your",
})
_PIN_WARNINGS: dict[tuple[int, ...], str] = {
    (11,): "Pin 11 Z12 Warning (SMEC vs SBEC): 1988-1989 SMEC leaves Pin 11 blank (or +12V/>22V write bias if modified), whereas 1990-1993 SBEC/SBEC-II bonds Pin 11 and Pin 12 to Z12 Power Ground. DMM-verify Pin 11 is open (OL / >10 MOhm) to Pin 12 and chassis ground before applying voltage or plugging in a modified SMEC.",
    (2, 3): "Pin 2 / Pin 3 Cross-Check (SMEC vs SBEC): 1988-1989 SMEC puts Knock (N12) on Pin 2 and CTS (K10) on Pin 3; 1990-1993 SBEC puts CTS (K02) on Pin 2 and A14 Fuel Pump/ASD +12V on Pin 3 (Knock moves to Pin 42 K42).",
    (25, 45): "Pin 25 / Pin 45 SCI Cross-Check (SMEC vs SBEC): 1990-1993 SBEC uses Pin 25 (D21 SCI TX) and Pin 45 (D20 SCI RX); 1988 P-Body SMEC puts SCI on Pin 51/Pin 31, leaves Pin 25 blank, and puts C20 +12V A/C on Pin 45. Verify zero +12V on Pin 45 before connecting isolated FTDI SCI.",
    (31, 51): "Pin 31 / Pin 51 Delta (1988 SMEC vs SBEC): 1988 P-Body SMEC uses Pin 31 (DK20 SCI RX) and Pin 51 (DK21 SCI TX); SBEC/SBEC-II moves SCI to Pin 45/Pin 25 and assigns Pin 51 to K51 ASD.",
    (12,): "Pin 12 Delta: Bonded to Pin 11 (Z12 Power Ground) on 1990-1993 SBEC/SBEC-II vs K14 ASD pull-in on 1988 P-Body SMEC (Z1 ground on later SMEC).",
    (8, 57): "Pin 8 / Pin 57 Delta: 1991 SBEC assigns Pin 8 (A61 Fuel Pump) while 1992 SBEC-II moves ASD sense to Pin 57 (A142); 1988 SMEC uses Pin 8 for X32 speed control.",
}
_DEFAULT_PIN_WARNING = "SMEC vs SBEC Pre-Connect Gate: Verify Pin 11 (Z12 ground vs blank), Pin 2/3 (Knock/CTS vs CTS/ASD), and Pin 25/45 (SCI vs C20 +12V) against the vehicle's exact model-year FSM."


def _validate_db(db_path: Path | str) -> Path:
    target = Path(db_path)
    if not target.is_file():
        raise FileNotFoundError(f"Heritage RAG index not found: {target}. Rebuild via logicaide.heritage.rag_index.build_heritage_index().")
    return target


def _open_conn(db_path: Path | str) -> sqlite3.Connection:
    target = _validate_db(db_path)
    if target.stat().st_size == 0:
        raise ValueError(f"Invalid or corrupt heritage SQLite DB {db_path}: database file is empty (0 bytes)")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        if missing := sorted(_REQUIRED_TABLES - names):
            raise ValueError(f"Invalid or corrupt heritage SQLite DB {db_path}: missing tables {', '.join(missing)}")
        qc = conn.execute("PRAGMA quick_check(1)").fetchone()
        if qc is None or qc[0] != "ok":
            raise ValueError(f"Invalid or corrupt heritage SQLite DB {db_path}: {qc[0] if qc else 'integrity check failed'}")
        return conn
    except Exception as exc:
        if conn is not None:
            conn.close()
        if isinstance(exc, sqlite3.Error):
            raise ValueError(f"Invalid or corrupt heritage SQLite DB {db_path}: {exc}") from exc
        raise


def _allowed_tiers(min_tier: int) -> tuple[str, ...]:
    tier_map = {1: ("Tier 1",), 2: ("Tier 1", "Tier 2"), 3: ("Tier 1", "Tier 2", "Tier 3")}
    if isinstance(min_tier, bool) or not isinstance(min_tier, int) or min_tier not in tier_map:
        raise ValueError(f"min_tier must be 1, 2, or 3 (got {min_tier!r})")
    return tier_map[min_tier]


def _parse_db_num(val: Any, field: str, kind: type[int] | type[float], default: int | None = None) -> Any:
    if val is None and default is not None:
        return default
    if val is None or isinstance(val, bool):
        raise ValueError(f"Invalid or corrupt heritage SQLite DB: invalid {field} ({val!r})")
    try:
        num = kind(val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid or corrupt heritage SQLite DB: invalid {field} ({val!r})") from exc
    if kind is float and not math.isfinite(num):
        raise ValueError(f"Invalid or corrupt heritage SQLite DB: non-finite {field} ({val!r})")
    return num


def _parse_json_list(raw: Any) -> list[str]:
    if isinstance(raw, list) or not raw:
        return [str(x) for x in raw] if raw else []
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else [str(parsed)]
    except (TypeError, json.JSONDecodeError):
        return [str(raw)]


def _matches_ecu(ecu_scope: list[str], ecu: str | None) -> bool:
    target = (ecu or "").strip().lower()
    return not target or any(s.lower() == target or s.lower().startswith(f"{target}_") for s in ecu_scope)


def _no_match(**extra: Any) -> dict[str, Any]:
    return {"matched": False, "status": "NO_VERIFIED_SOURCE_MATCH", "message": NO_VERIFIED_SOURCE_MATCH, **extra}


def _strip_stopwords(query: str) -> str:
    filtered = [tok for tok in _FTS_TOKEN_RE.findall(query or "") if tok.strip(".-").lower() not in _STOPWORDS]
    return " ".join(filtered) if filtered else query


def escape_fts5_query(query: str) -> str:
    """Safely quote search tokens for FTS5 and expand hyphen/underscore compounds as OR terms."""
    terms, seen = [], set()
    for raw in _FTS_TOKEN_RE.findall(query or ""):
        if not (cleaned := raw.strip(".-")):
            continue
        for cand in (cleaned, *(p for p in re.split(r"[-_]+", cleaned) if p and p != cleaned)):
            if (key := cand.lower()) not in seen:
                seen.add(key)
                terms.append('"' + cand.replace('"', '""') + '"')
    return " OR ".join(terms)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')", (table_name,)).fetchone() is not None


def _detect_vector_backend(conn: sqlite3.Connection) -> tuple[str, Any | None]:
    meta_backend = "heritage_vec_fallback"
    if _table_exists(conn, "heritage_index_meta"):
        row = conn.execute("SELECT value FROM heritage_index_meta WHERE key = 'vector_backend'").fetchone()
        if row is not None and row["value"]:
            meta_backend = str(row["value"])
    if meta_backend in {"sqlite_vec", "sqlite-vec"} and _table_exists(conn, "heritage_vec") and (vec_mod := _try_load_sqlite_vec(conn)) is not None:
        return meta_backend, vec_mod
    return "heritage_vec_fallback", None


def _load_eligible_children(
    conn: sqlite3.Connection, allowed_tiers: tuple[str, ...], ecu: str | None, parent_ids: set[str] | None = None
) -> tuple[dict[str, tuple[int, str, str]], dict[int, str]]:
    ph = ",".join("?" for _ in allowed_tiers)
    rows = conn.execute(f"SELECT c.rowid AS child_rowid, c.child_id, c.parent_id, p.trust_tier, p.ecu_scope FROM heritage_children c JOIN heritage_parents p ON p.parent_id = c.parent_id WHERE p.trust_tier IN ({ph})", allowed_tiers).fetchall()
    by_cid: dict[str, tuple[int, str, str]] = {}
    rowid_to_cid: dict[int, str] = {}
    for row in rows:
        pid = row["parent_id"]
        if (parent_ids is None or pid in parent_ids) and _matches_ecu(_parse_json_list(row["ecu_scope"]), ecu):
            cid, rid = row["child_id"], _parse_db_num(row["child_rowid"], "child_rowid", int)
            by_cid[cid], rowid_to_cid[rid] = (rid, pid, row["trust_tier"]), cid
    return by_cid, rowid_to_cid


def _rank_fts(conn: sqlite3.Connection, fts_query: str, eligible_by_cid: dict[str, tuple[int, str, str]]) -> dict[str, int]:
    if not fts_query or not eligible_by_cid:
        return {}
    rows = conn.execute("SELECT child_id FROM heritage_fts WHERE heritage_fts MATCH ? ORDER BY bm25(heritage_fts), child_id ASC", (fts_query,)).fetchall()
    return {cid: idx for idx, cid in enumerate(dict.fromkeys(r["child_id"] for r in rows if r["child_id"] in eligible_by_cid), start=1)}


def _rank_vectors(conn: sqlite3.Connection, query: str, rowid_to_cid: dict[int, str], backend: str, vec_mod: Any | None) -> tuple[dict[str, tuple[int, float]], float]:
    if not rowid_to_cid:
        return {}, 0.0
    q_vec = compute_offline_embedding(query, dim=EMBEDDING_DIM)
    if math.sqrt(sum(v * v for v in q_vec)) <= 0.0:
        return {}, 0.0
    scored, max_cos = [], 0.0
    if vec_mod is not None and backend in {"sqlite_vec", "sqlite-vec"}:
        if (total_vec := int(conn.execute("SELECT COUNT(*) FROM heritage_vec").fetchone()[0])) > 0:
            q_blob = vec_mod.serialize_float32(q_vec) if hasattr(vec_mod, "serialize_float32") else pack_float32_embedding(q_vec)
            for row in conn.execute("SELECT child_rowid, distance FROM heritage_vec WHERE embedding MATCH ? AND k = ?", (q_blob, min(total_vec, 4096))).fetchall():
                if (cid := rowid_to_cid.get(_parse_db_num(row["child_rowid"], "child_rowid", int))) is not None:
                    cos = 1.0 - _parse_db_num(row["distance"], "heritage_vec.distance", float)
                    max_cos = max(max_cos, cos)
                    if cos >= _VECTOR_ADMIT_COSINE:
                        scored.append((cos, cid))
    elif _table_exists(conn, "heritage_vec_fallback"):
        for row in conn.execute("SELECT child_rowid, embedding_blob, norm FROM heritage_vec_fallback").fetchall():
            cid = rowid_to_cid.get(_parse_db_num(row["child_rowid"], "child_rowid", int))
            if cid is not None and _parse_db_num(row["norm"], "heritage_vec_fallback.norm", float) > 0.0:
                if row["embedding_blob"] is None:
                    raise ValueError("Invalid or corrupt heritage SQLite DB: NULL embedding_blob in heritage_vec_fallback")
                cos = cosine_similarity(q_vec, unpack_float32_embedding(row["embedding_blob"]))
                max_cos = max(max_cos, cos)
                if cos >= _VECTOR_ADMIT_COSINE:
                    scored.append((cos, cid))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return {cid: (idx, cos) for idx, (cos, cid) in enumerate(scored, start=1)}, max_cos


def _strip_mermaid_fences(markdown: str, ver: str) -> str:
    if "mermaid" not in markdown.lower():
        return markdown
    lines, out, i, marker = markdown.splitlines(), [], 0, f"> _[Mermaid schematic withheld — verification status: {ver or 'UNKNOWN'}]_"
    while i < len(lines):
        if m := re.match(r"^\s*([`~]{3,})(.*)$", lines[i]):
            ch, n, lang = m.group(1)[0], len(m.group(1)), (m.group(2).strip().lower().split() or [""])[0]
            end_re, j = re.compile(rf"^\s*{re.escape(ch)}{{{n},}}\s*$"), i + 1
            while j < len(lines) and not end_re.match(lines[j]):
                j += 1
            end = min(j + 1, len(lines))
            out.extend([marker] if lang == "mermaid" else lines[i:end])
            i = end
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _hydrate_parent_row(row: sqlite3.Row, *, hide_quarantined_mermaid: bool) -> dict[str, Any]:
    ver = row["mermaid_verification"]
    hide = hide_quarantined_mermaid and ver != "VERIFIED"
    body_md = _strip_mermaid_fences(str(row["body_markdown"] or ""), str(ver or "UNKNOWN")) if hide else row["body_markdown"]
    return {
        "parent_id": row["parent_id"], "doc_id": row["doc_id"], "trust_tier": row["trust_tier"],
        "ecu_scope": _parse_json_list(row["ecu_scope"]), "engine_scope": _parse_json_list(row["engine_scope"]),
        "section_path": row["section_path"], "source_citation": row["source_citation"], "page_ref": row["page_ref"],
        "warc_capture_date": row["warc_capture_date"], "body_markdown": body_md,
        "mermaid_source": None if hide else row["mermaid_source"], "mermaid_verification": ver,
        "token_count": _parse_db_num(row["token_count"], "token_count", int, default=0), "sha256": row["sha256"],
    }


def _fuse_and_hydrate(
    conn: sqlite3.Connection, eligible_by_cid: dict[str, tuple[int, str, str]], fts_ranks: dict[str, int],
    vec_ranks: dict[str, tuple[int, float]], *, lex_weight: float, vec_weight: float, score_threshold: float,
) -> list[dict[str, Any]]:
    best: dict[str, tuple[float, str, int | None, int | None, float | None]] = {}
    for cid in set(fts_ranks) | set(vec_ranks):
        _, pid, tier = eligible_by_cid[cid]
        f_rank, v_info = fts_ranks.get(cid), vec_ranks.get(cid)
        v_rank, cos_val = v_info if v_info is not None else (None, None)
        score = TIER_WEIGHTS.get(tier, 0.90) * ((lex_weight / (RRF_K + f_rank) if f_rank is not None else 0.0) + (vec_weight / (RRF_K + v_rank) if v_rank is not None else 0.0))
        if score >= score_threshold and ((prev := best.get(pid)) is None or (score, cid) > (prev[0], prev[1])):
            best[pid] = (score, cid, f_rank, v_rank, cos_val)
    if not best:
        return []
    pids, cids = sorted(best), [best[p][1] for p in sorted(best)]
    p_rows = {r["parent_id"]: r for r in conn.execute(f"SELECT * FROM heritage_parents WHERE parent_id IN ({','.join('?' for _ in pids)})", pids).fetchall()}
    c_rows = {r["child_id"]: r for r in conn.execute(f"SELECT child_id, chunk_kind, row_range FROM heritage_children WHERE child_id IN ({','.join('?' for _ in cids)})", cids).fetchall()}
    results: list[dict[str, Any]] = []
    for pid, (score, cid, f_rank, v_rank, cos_val) in sorted(best.items(), key=lambda it: (-it[1][0], it[0])):
        if (p_row := p_rows.get(pid)) is None:
            continue
        c_row = c_rows.get(cid)
        rec = _hydrate_parent_row(p_row, hide_quarantined_mermaid=True)
        rec.update({"score": round(score, 6), "matched_child_id": cid, "chunk_kind": c_row["chunk_kind"] if c_row else None, "row_range": c_row["row_range"] if c_row else None,
                    "fts_rank": f_rank, "vec_rank": v_rank, "cosine_similarity": round(cos_val, 6) if cos_val is not None else None})
        results.append(rec)
    return results


def search_heritage_docs(
    query: str, *, db_path: Path | str = DEFAULT_INDEX_DB, limit: int = 8, ecu: str | None = None,
    min_tier: int = 3, lex_weight: float = 0.55, vec_weight: float = 0.45,
    score_threshold: float = 0.014, require_verified_for_pin_or_sci: bool = True,
) -> dict[str, Any]:
    """Run offline Parent-Child Hybrid RAG search with Trust-Weighted RRF and strict abstention."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(f"limit must be a positive integer (got {limit!r})")
    for nm, val in (("lex_weight", lex_weight), ("vec_weight", vec_weight), ("score_threshold", score_threshold)):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError(f"{nm} must be a finite number (got {val!r})")
    tiers = _allowed_tiers(min_tier)
    is_pin_or_sci = bool(require_verified_for_pin_or_sci and query and (_PIN_QUERY_RE.search(query) or _SCI_QUERY_RE.search(query)))
    if is_pin_or_sci:
        tiers = _allowed_tiers(min(min_tier, 2))
    conn = _open_conn(db_path)
    try:
        if not query or not query.strip():
            return _no_match(query=query, results=[])
        backend, vec_mod = _detect_vector_backend(conn)
        eligible_by_cid, rowid_to_cid = _load_eligible_children(conn, tiers, ecu)
        if not eligible_by_cid:
            return _no_match(query=query, results=[])
        fts_ranks = _rank_fts(conn, escape_fts5_query(_strip_stopwords(query)), eligible_by_cid)
        vec_ranks, max_cos = _rank_vectors(conn, query, rowid_to_cid, backend, vec_mod)
        if max_cos < _ABSTAIN_MAX_COSINE and not fts_ranks:
            return _no_match(query=query, results=[])
        results = _fuse_and_hydrate(conn, eligible_by_cid, fts_ranks, vec_ranks, lex_weight=lex_weight, vec_weight=vec_weight, score_threshold=score_threshold)[:limit]
        if not results or (is_pin_or_sci and not any(item["trust_tier"] in {"Tier 1", "Tier 2"} for item in results)):
            return _no_match(query=query, results=[])
        return {"matched": True, "status": "MATCHED", "query": query, "count": len(results), "vector_backend": backend, "results": results}
    finally:
        conn.close()


def _filter_schematic_candidates(
    conn: sqlite3.Connection, candidates: list[sqlite3.Row], query: str | None, tiers: tuple[str, ...], ecu: str | None
) -> tuple[list[sqlite3.Row], dict[str, float]]:
    if query is None or not query.strip():
        return candidates, {}
    cand_pids = {row["parent_id"] for row in candidates}
    backend, vec_mod = _detect_vector_backend(conn)
    elig_by_cid, rowid_to_cid = _load_eligible_children(conn, tiers, ecu, parent_ids=cand_pids)
    if not elig_by_cid:
        q_low = query.strip().lower()
        return [r for r in candidates if q_low in f"{r['doc_id']} {r['section_path']} {r['body_markdown']}".lower()], {}
    fts_ranks = _rank_fts(conn, escape_fts5_query(_strip_stopwords(query)), elig_by_cid)
    vec_ranks, max_cos = _rank_vectors(conn, query, rowid_to_cid, backend, vec_mod)
    if max_cos < _ABSTAIN_MAX_COSINE and not fts_ranks:
        return [], {}
    fused = _fuse_and_hydrate(conn, elig_by_cid, fts_ranks, vec_ranks, lex_weight=0.55, vec_weight=0.45, score_threshold=0.014)
    score_map = {item["parent_id"]: item["score"] for item in fused}
    return [row for row in candidates if row["parent_id"] in score_map], score_map


def get_verified_schematic(
    *, doc_id: str | None = None, parent_id: str | None = None, query: str | None = None,
    pending_review: bool = False, ecu: str | None = None, min_tier: int = 3, db_path: Path | str = DEFAULT_INDEX_DB,
) -> dict[str, Any]:
    """Fetch verified Mermaid schematics with walkthrough and provenance, refusing quarantined-only hits."""
    tiers, conn = _allowed_tiers(min_tier), _open_conn(db_path)
    try:
        sql = f"SELECT * FROM heritage_parents WHERE mermaid_source IS NOT NULL AND TRIM(mermaid_source) != '' AND trust_tier IN ({','.join('?' for _ in tiers)})"
        params: list[Any] = list(tiers)
        for col, val in (("doc_id", doc_id), ("parent_id", parent_id)):
            if val is not None:
                sql, params = f"{sql} AND {col} = ?", [*params, val]
        candidates = [r for r in conn.execute(sql, params).fetchall() if _matches_ecu(_parse_json_list(r["ecu_scope"]), ecu)]
        candidates, score_map = _filter_schematic_candidates(conn, candidates, query, tiers, ecu)
        if not candidates:
            return _no_match(diagrams=[])
        verified = [r for r in candidates if r["mermaid_verification"] == "VERIFIED"]
        quarantined = [r for r in candidates if r["mermaid_verification"] == "NEEDS_HUMAN_REVIEW"]
        if not pending_review and not verified:
            if (doc_id is not None or parent_id is not None) and quarantined:
                return {"matched": False, "status": "QUARANTINED_NEEDS_HUMAN_REVIEW",
                        "message": f"{NO_VERIFIED_SOURCE_MATCH}: schematic is quarantined (NEEDS_HUMAN_REVIEW); pass pending_review=True to inspect", "diagrams": []}
            return _no_match(diagrams=[])
        if not (selected := verified + (quarantined if pending_review else [])):
            return _no_match(diagrams=[])
        diagrams = [{**_hydrate_parent_row(row, hide_quarantined_mermaid=False), "quarantined": row["mermaid_verification"] == "NEEDS_HUMAN_REVIEW",
                     **({"score": score_map[row["parent_id"]]} if row["parent_id"] in score_map else {})} for row in selected]
        diagrams.sort(key=lambda d: (bool(d["quarantined"]), -(d.get("score") or 0.0), _TIER_RANK.get(d["trust_tier"], 9), d["parent_id"]))
        return {"matched": True, "status": "MATCHED", "count": len(diagrams), "diagrams": diagrams}
    finally:
        conn.close()


def _parse_pin_number(pin: int | str) -> int:
    if isinstance(pin, bool):
        raise ValueError("Boolean is not a valid 60-way pin number")
    if isinstance(pin, int):
        return pin
    if not isinstance(pin, str) or not (text := pin.strip()):
        raise ValueError("Pin identifier must be a non-empty int or str")
    stripped = re.sub(r"\b(?:60|50|14|26)[\s_-]*(?:way|pin)\b", " ", text, flags=re.IGNORECASE)
    if re.search(r"(?<![A-Za-z0-9])-\d+\b", stripped):
        raise ValueError(f"Negative 60-way pin identifier is invalid: {pin!r}")
    for pattern in (r"^\s*(\d+)\s*$", r"\b(?:pin|cavity|cav)[\s_#-]*(\d+)\b", r"\b[12]-(\d{1,2})\b", r"\b(\d+)\b"):
        if match := re.search(pattern, stripped, re.IGNORECASE):
            return int(match.group(1))
    raise ValueError(f"Could not parse numeric 60-way pin from {pin!r}")


def _is_tbl_sep(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and "-" in s and all(_TBL_SEP_RE.match(c.strip()) for c in s.strip("|").split("|"))


def _cell0_mentions_60way_pin(raw_cell0: str, pin_num: int) -> bool:
    cell0 = raw_cell0.replace("`", "").replace("*", "")
    if re.search(rf"(?<![\d-])1-{pin_num}(?![\d-])", cell0):
        return True
    masked = re.sub(r"\b[12]-\d{1,2}\b", " ", cell0)
    return any(int(n) == pin_num for n in re.findall(r"\b\d{1,2}\b", masked)) or any(
        int(lo) <= pin_num <= int(hi) for lo, hi in re.findall(r"\b(\d{1,2})\s*[–-]\s*(\d{1,2})\b", masked)
    )


def _section_mentions_60way_pin(row: sqlite3.Row, child_pins: list[str], pin_num: int) -> bool:
    doc_id_low, sec_path_low = str(row["doc_id"]).replace("`", "").lower(), str(row["section_path"]).replace("`", "").lower()
    leaf_sec_low, body = sec_path_low.rsplit(">", 1)[-1].strip(), str(row["body_markdown"])
    if not any(tok in f"{doc_id_low} {sec_path_low} {body.lower()}" for tok in ("60-way", "smec", "sbec")):
        return False
    has_60way_tbl = bool(re.search(r"^\|\s*\*{0,2}`?60-way\b", body, re.I | re.M))
    explicit_60 = ("60-way" in leaf_sec_low and not _NON60_CONN_RE.search(leaf_sec_low) and not _NON60_SUB_RE.search(leaf_sec_low)) or has_60way_tbl
    if (_NON60_CONN_RE.search(f"{doc_id_low} {leaf_sec_low}") or _NON60_SUB_RE.search(sec_path_low) or "50-way" in f"{doc_id_low} {sec_path_low}" or "50way" in f"{doc_id_low} {sec_path_low}") and not explicit_60:
        return False
    if not re.search(rf"\b{pin_num}\b", body) and any(p in child_pins for p in (f"Pin {pin_num}", f"Pin_{pin_num}")):
        return True
    cleaned = _NON60_PROSE_RE.sub(" ", re.sub(r"\b(?:LM\s+[12]|2)-\d{1,2}\b", " ", body.replace("`", "").replace("*", ""), flags=re.I))
    if re.search(rf"(?<![A-Za-z0-9-])1-{pin_num}(?![A-Za-z0-9-])", cleaned):
        return True
    prose = re.sub(r"\b[12]-\d{1,2}\b", " ", cleaned)
    if re.search(rf"\b(?:pin|cavity|cav)s?[\s_#-]*(?:\d{{1,2}}(?:[\s,/&-]+|\s+and\s+))*{pin_num}\b", prose, re.I):
        return True
    lines, sub_hdr = body.splitlines(), ""
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            sub_hdr = stripped.replace("`", "").lower()
        elif stripped.startswith("|") and idx + 1 < len(lines) and _is_tbl_sep(lines[idx + 1]):
            hdr0 = stripped.strip("|").split("|")[0].replace("`", "").replace("*", "").strip().lower()
            if ("pin" in hdr0 or "cavity" in hdr0) and not _NON60_HDR_RE.search(hdr0) and not _NON60_CONN_RE.search(sub_hdr):
                r = idx + 2
                while r < len(lines) and lines[r].strip().startswith("|"):
                    if _cell0_mentions_60way_pin(lines[r].strip().strip("|").split("|")[0], pin_num):
                        return True
                    r += 1
    return False


def lookup_60way_pin(pin: int | str, *, ecu: str | None = None, db_path: Path | str = DEFAULT_INDEX_DB) -> dict[str, Any]:
    """Look up a 60-way connector pin strictly from Tier 1/Tier 2 sources with SMEC/SBEC safety notes."""
    pin_num = _parse_pin_number(pin)
    _validate_db(db_path)
    ecu_norm = (ecu or "").strip().lower()
    if pin_num < 1 or pin_num > 60 or ecu_norm == "lm" or ecu_norm.startswith(("lm_", "lm-")):
        return _no_match(pin=pin_num, results=[])
    rag_res = search_heritage_docs(f"60-way Pin {pin_num}", db_path=db_path, limit=30, ecu=ecu, min_tier=2, require_verified_for_pin_or_sci=True)
    rag_scores = {item["parent_id"]: item["score"] for item in rag_res.get("results", [])}
    conn, matches = _open_conn(db_path), []
    try:
        rows = conn.execute("SELECT p.*, GROUP_CONCAT(c.pins_referenced_json, '\n') AS all_pins_json FROM heritage_parents p LEFT JOIN heritage_children c ON c.parent_id = p.parent_id WHERE p.trust_tier IN ('Tier 1', 'Tier 2') GROUP BY p.parent_id").fetchall()
        for row in rows:
            if not _matches_ecu(_parse_json_list(row["ecu_scope"]), ecu):
                continue
            pins_list = [p for blob in (row["all_pins_json"] or "").splitlines() for p in _parse_json_list(blob)]
            if _section_mentions_60way_pin(row, pins_list, pin_num):
                item = _hydrate_parent_row(row, hide_quarantined_mermaid=True)
                item["score"] = rag_scores.get(row["parent_id"], 0.0)
                item["_has_60way"] = "60-way" in f"{row['doc_id']} {row['section_path']} {row['body_markdown']}".lower()
                matches.append(item)
    finally:
        conn.close()
    if not matches:
        return _no_match(pin=pin_num, results=[])
    matches.sort(key=lambda m: (not m.pop("_has_60way"), -m["score"], _TIER_RANK.get(m["trust_tier"], 9), m["parent_id"]))
    safety_notes = [msg for pins, msg in _PIN_WARNINGS.items() if pin_num in pins] or [_DEFAULT_PIN_WARNING]
    return {"matched": True, "status": "VERIFIED_SOURCE_MATCH", "pin": pin_num, "pin_label": f"Pin {pin_num}", "ecu": ecu, "safety_notes": safety_notes, "count": len(matches), "results": matches}


def lookup_sci_offset_hybrid(
    query_or_offset: str, *, rom_id: str | None = None, sci_table: str | None = None,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH, db_path: Path | str = DEFAULT_INDEX_DB,
) -> dict[str, Any]:
    """Cross-reference verified ROM manifests via lookup_sci and Tier 1/2 heritage index docs."""
    _validate_db(db_path)
    if not (text := (query_or_offset or "").strip()):
        return _no_match(query=query_or_offset, manifest_matches=[], heritage_results=[])
    cat_path, manifest_matches = Path(catalog_path), []
    if cat_path.is_file() or (rom_id is not None and str(rom_id).endswith(".json") and Path(rom_id).is_file()):
        target_roms, is_offset = [rom_id] if rom_id is not None else list(_DEFAULT_ROM_IDS), bool(_HEX_OR_DEC_RE.fullmatch(text))
        for rid in target_roms:
            try:
                res = lookup_sci(rid, offset=text if is_offset else None, var_name=None if is_offset else text, sci_table=sci_table, strict=True, catalog_path=cat_path)
            except (FileNotFoundError, KeyError):
                if rom_id is not None:
                    raise
                continue
            if res.get("matched"):
                manifest_matches.append(res)
    heritage_results = search_heritage_docs(text, db_path=db_path, min_tier=2, require_verified_for_pin_or_sci=True).get("results", [])
    if not manifest_matches and not heritage_results:
        return _no_match(query=query_or_offset, rom_id=rom_id, sci_table=sci_table, manifest_matches=[], heritage_results=[])
    return {"matched": True, "status": "VERIFIED_SOURCE_MATCH", "query": query_or_offset, "rom_id": rom_id, "sci_table": sci_table, "manifest_matches": manifest_matches, "heritage_results": heritage_results}


def get_provenance_card(doc_id_or_parent_id: str, *, db_path: Path | str = DEFAULT_INDEX_DB) -> dict[str, Any]:
    """Fetch full provenance metadata and section checksums for a doc_id or parent_id."""
    conn = _open_conn(db_path)
    try:
        if not (key := (doc_id_or_parent_id or "").strip()):
            return _no_match(query=doc_id_or_parent_id, sections=[])
        rows = conn.execute("SELECT * FROM heritage_parents WHERE parent_id = ? ORDER BY parent_id ASC", (key,)).fetchall()
        matched_by = "parent_id" if rows else "doc_id"
        if not rows:
            rows = conn.execute("SELECT * FROM heritage_parents WHERE doc_id = ? ORDER BY parent_id ASC", (key,)).fetchall()
        if not rows:
            return _no_match(query=doc_id_or_parent_id, sections=[])
        first = rows[0]
        sections = [{"parent_id": r["parent_id"], "section_path": r["section_path"], "sha256": r["sha256"], "mermaid_verification": r["mermaid_verification"],
                     "token_count": _parse_db_num(r["token_count"], "token_count", int, default=0), "page_ref": r["page_ref"]} for r in rows]
        return {
            "matched": True, "status": "MATCHED", "matched_by": matched_by, "doc_id": first["doc_id"], "parent_id": first["parent_id"] if matched_by == "parent_id" else None,
            "trust_tier": first["trust_tier"], "source_citation": first["source_citation"], "page_ref": first["page_ref"], "warc_capture_date": first["warc_capture_date"],
            "ecu_scope": list(dict.fromkeys(s for r in rows for s in _parse_json_list(r["ecu_scope"]))),
            "engine_scope": list(dict.fromkeys(s for r in rows for s in _parse_json_list(r["engine_scope"]))), "section_count": len(sections), "sections": sections,
        }
    finally:
        conn.close()


__all__ = ["RRF_K", "TIER_WEIGHTS", "NO_VERIFIED_SOURCE_MATCH", "escape_fts5_query", "search_heritage_docs", "get_verified_schematic", "lookup_60way_pin", "lookup_sci_offset_hybrid", "get_provenance_card"]
