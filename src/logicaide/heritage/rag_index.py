"""Deterministic offline Parent-Child Hybrid RAG SQLite index builder."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
from typing import Any, Sequence

from logicaide.heritage.chunker import chunk_heritage_document, is_chrome_partial
from logicaide.heritage.doc_curator import validate_cleanroom_digest
from logicaide.heritage.wiki_sync import validate_wiki_tree

DEFAULT_INDEX_DB = Path("data/heritage_index.sqlite")
EMBEDDING_DIM = 1024

_HEX_TOKEN_RE = re.compile(r"(?:(?<!\w)\$[0-9A-Fa-f]{2,4}\b|\b0x[0-9A-Fa-f]{2,4}\b)")
_PIN_TOKEN_RE = re.compile(r"\b(?:Pin|Cavity|Cav)[\s_]+(\d{1,2})\b", re.IGNORECASE)
_CIRCUIT_TOKEN_RE = re.compile(
    r"(?:\b(?:(?:DK|[ACDGJKNRSTVXYZ])\d{1,3}|P\d{7}|\d+[Ww]-\d+)\b|(?<!\w)#\d{3,4}\b)"
)
_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9$_#.-]+")

HERITAGE_CORE_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE heritage_parents (
    parent_id            TEXT PRIMARY KEY,
    doc_id               TEXT NOT NULL,
    trust_tier           TEXT NOT NULL CHECK (trust_tier IN ('Tier 1', 'Tier 2', 'Tier 3')),
    ecu_scope            TEXT NOT NULL,
    engine_scope         TEXT NOT NULL,
    section_path         TEXT NOT NULL,
    source_citation      TEXT NOT NULL,
    page_ref             TEXT,
    warc_capture_date    TEXT,
    body_markdown        TEXT NOT NULL,
    mermaid_source       TEXT,
    mermaid_verification TEXT NOT NULL DEFAULT 'NONE'
                         CHECK (mermaid_verification IN ('VERIFIED', 'NEEDS_HUMAN_REVIEW', 'NONE')),
    token_count          INTEGER NOT NULL,
    sha256               TEXT NOT NULL
);

CREATE TABLE heritage_children (
    child_id                TEXT PRIMARY KEY,
    parent_id               TEXT NOT NULL REFERENCES heritage_parents(parent_id) ON DELETE CASCADE,
    doc_id                  TEXT NOT NULL,
    chunk_kind              TEXT NOT NULL CHECK (chunk_kind IN ('TREE_BRANCH', 'TABLE_SLICE', 'SCHEMATIC_EDGE', 'NOTE_ROUTINE')),
    row_range               TEXT,
    prefixed_text           TEXT NOT NULL,
    symbols_referenced_json TEXT NOT NULL DEFAULT '[]',
    pins_referenced_json    TEXT NOT NULL DEFAULT '[]'
);

CREATE VIRTUAL TABLE heritage_fts USING fts5(
    child_id UNINDEXED,
    parent_id UNINDEXED,
    title,
    symbols_text,
    pins_text,
    prefixed_text,
    tokenize = "unicode61 remove_diacritics 0 tokenchars '$_-#.'"
);

CREATE TABLE heritage_index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX idx_heritage_parents_doc_tier ON heritage_parents (doc_id, trust_tier);
CREATE INDEX idx_heritage_parents_mermaid ON heritage_parents (mermaid_verification);
CREATE INDEX idx_heritage_children_parent ON heritage_children (parent_id);
CREATE INDEX idx_heritage_children_kind ON heritage_children (chunk_kind);
"""

HERITAGE_VEC_DDL = """
CREATE VIRTUAL TABLE heritage_vec USING vec0(
    child_rowid INTEGER PRIMARY KEY,
    embedding float[1024] distance_metric=cosine
);
"""

HERITAGE_VEC_FALLBACK_DDL = """
CREATE TABLE heritage_vec_fallback (
    child_rowid    INTEGER PRIMARY KEY,
    embedding_blob BLOB NOT NULL,
    norm           REAL NOT NULL
);
"""


def _hash_feature_into(vec: list[float], feature: str, weight: float, dim: int) -> None:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    h_val = int.from_bytes(digest, "little")
    idx = h_val % dim
    sign = 1.0 if ((h_val >> 63) & 1) == 0 else -1.0
    vec[idx] += sign * weight


def compute_offline_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Generate a deterministic L2-normalized float32 feature-hashed embedding vector."""
    if dim <= 0:
        raise ValueError("Embedding dimension must be positive")
    if not text or not text.strip():
        return [0.0] * dim

    vec = [0.0] * dim
    for m in _HEX_TOKEN_RE.finditer(text):
        _hash_feature_into(vec, f"hex:{m.group(0).lower()}", 4.0, dim)
    for m in _PIN_TOKEN_RE.finditer(text):
        _hash_feature_into(vec, f"pin_num:{int(m.group(1))}", 3.5, dim)
        _hash_feature_into(vec, f"pin_tok:{m.group(0).lower()}", 2.0, dim)
    for m in _CIRCUIT_TOKEN_RE.finditer(text):
        _hash_feature_into(vec, f"code:{m.group(0).lower()}", 3.5, dim)

    words: list[str] = []
    for m in _WORD_TOKEN_RE.finditer(text):
        tok = m.group(0).lower().strip(".-")
        if not tok:
            continue
        words.append(tok)
        _hash_feature_into(vec, f"w:{tok}", 2.0, dim)
        if "_" in tok or "-" in tok or "." in tok:
            for part in re.split(r"[_.-]+", tok):
                if part and part != tok:
                    _hash_feature_into(vec, f"sw:{part}", 0.75, dim)

    for left, right in zip(words, words[1:]):
        _hash_feature_into(vec, f"bg:{left}|{right}", 1.25, dim)

    norm_chars = re.sub(r"\s+", " ", text.lower()).strip()[:4096]
    for idx in range(max(0, len(norm_chars) - 2)):
        tri = norm_chars[idx : idx + 3]
        if tri.strip():
            _hash_feature_into(vec, f"c3:{tri}", 0.5, dim)

    raw_f32 = struct.unpack(f"<{dim}f", struct.pack(f"<{dim}f", *vec))
    norm = math.sqrt(sum(v * v for v in raw_f32))
    if norm <= 0.0:
        return [0.0] * dim
    packed = struct.pack(f"<{dim}f", *(v / norm for v in raw_f32))
    return list(struct.unpack(f"<{dim}f", packed))


def pack_float32_embedding(vec: Sequence[float]) -> bytes:
    """Serialize a float sequence as packed little-endian IEEE-754 float32 bytes."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_float32_embedding(blob: bytes) -> tuple[float, ...]:
    """Deserialize packed little-endian IEEE-754 float32 bytes into a float tuple."""
    if len(blob) % 4 != 0:
        raise ValueError(f"Corrupt float32 blob byte length: {len(blob)}")
    return struct.unpack(f"<{len(blob) // 4}f", blob)


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    if len(vec_a) != len(vec_b):
        raise ValueError(f"Vector dimension mismatch: {len(vec_a)} vs {len(vec_b)}")
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> Any | None:
    """Load sqlite_vec extension onto conn if available; return module or None."""
    try:
        import sqlite_vec  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return sqlite_vec
    except (sqlite3.Error, AttributeError):
        return None
    finally:
        try:
            conn.enable_load_extension(False)
        except (sqlite3.Error, AttributeError):
            pass


def _resolve_repo_root(knowledge_path: Path | None, wiki_path: Path | None) -> Path:
    candidates = [p.resolve() for p in (knowledge_path, wiki_path) if p is not None and p.exists()]
    if len(candidates) >= 2:
        common = Path(os.path.commonpath([str(c) for c in candidates]))
        if common.is_dir():
            return common
    if candidates:
        return candidates[0].parent
    return Path.cwd().resolve()


def _is_safe_source_file(md: Path, root: Path, root_resolved: Path) -> bool:
    """Return True only when md is a non-symlink regular file strictly inside root."""
    if md.is_symlink():
        return False
    try:
        cur = root
        for part in md.relative_to(root).parts:
            cur = cur / part
            if cur.is_symlink():
                return False
        resolved = md.resolve()
    except (OSError, ValueError):
        return False
    return resolved.is_relative_to(root_resolved) and md.is_file()


def _collect_source_files(knowledge_path: Path | None, wiki_path: Path | None) -> list[Path]:
    files: list[Path] = []
    for root in (knowledge_path, wiki_path):
        if root is None or not root.is_dir():
            continue
        root_resolved = root.resolve()
        for md in sorted(root.rglob("*.md")):
            if _is_safe_source_file(md, root, root_resolved) and not is_chrome_partial(md, repo_root=root.parent):
                files.append(md)
    return files


def _validate_inputs(knowledge_path: Path | None, wiki_path: Path | None) -> None:
    if knowledge_path is not None and knowledge_path.is_dir():
        root_resolved = knowledge_path.resolve()
        for tier_dir in sorted(knowledge_path.glob("tier*")):
            if tier_dir.is_dir() and not tier_dir.is_symlink() and tier_dir.resolve().is_relative_to(root_resolved):
                for md_path in sorted(tier_dir.rglob("*.md")):
                    if _is_safe_source_file(md_path, knowledge_path, root_resolved):
                        validate_cleanroom_digest(md_path)
    if wiki_path is not None and wiki_path.is_dir():
        validate_wiki_tree(wiki_path)


def _disambiguate_ids(
    parents: list[dict[str, Any]],
    children: list[dict[str, Any]],
    seen_parents: set[str],
    relpath: str,
) -> None:
    if not any(p["parent_id"] in seen_parents for p in parents):
        return
    suffix = hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:6]
    remap: dict[str, str] = {}
    for p in parents:
        old_pid = p["parent_id"]
        new_pid = f"{old_pid}::{suffix}"
        remap[old_pid] = new_pid
        p["parent_id"] = new_pid
    for ch in children:
        old_pid = ch["parent_id"]
        new_pid = remap.get(old_pid, old_pid)
        ch["parent_id"] = new_pid
        ch["child_id"] = ch["child_id"].replace(f"{old_pid}::", f"{new_pid}::", 1)


def _insert_corpus(
    conn: sqlite3.Connection,
    md_files: list[Path],
    repo_root: Path,
    vec_module: Any | None,
) -> dict[str, int]:
    seen_parents: set[str] = set()
    parent_count = child_count = verified_mermaid = quarantined_mermaid = 0

    for md_path in md_files:
        parents, children = chunk_heritage_document(md_path, repo_root=repo_root, verify_mermaid=True)
        if not parents:
            continue
        _disambiguate_ids(parents, children, seen_parents, md_path.as_posix())
        for p in parents:
            seen_parents.add(p["parent_id"])
            conn.execute(
                """
                INSERT INTO heritage_parents (
                    parent_id, doc_id, trust_tier, ecu_scope, engine_scope,
                    section_path, source_citation, page_ref, warc_capture_date,
                    body_markdown, mermaid_source, mermaid_verification, token_count, sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p["parent_id"], p["doc_id"], p["trust_tier"], p["ecu_scope"], p["engine_scope"],
                    p["section_path"], p["source_citation"], p["page_ref"], p["warc_capture_date"],
                    p["body_markdown"], p["mermaid_source"], p["mermaid_verification"],
                    p["token_count"], p["sha256"],
                ),
            )
            parent_count += 1
            if p["mermaid_verification"] == "VERIFIED":
                verified_mermaid += 1
            elif p["mermaid_verification"] == "NEEDS_HUMAN_REVIEW":
                quarantined_mermaid += 1

        for ch in children:
            cur = conn.execute(
                """
                INSERT INTO heritage_children (
                    child_id, parent_id, doc_id, chunk_kind, row_range,
                    prefixed_text, symbols_referenced_json, pins_referenced_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ch["child_id"], ch["parent_id"], ch["doc_id"], ch["chunk_kind"], ch["row_range"],
                    ch["prefixed_text"], ch["symbols_referenced_json"], ch["pins_referenced_json"],
                ),
            )
            child_rowid = int(cur.lastrowid)
            symbols_text = " ".join(json.loads(ch["symbols_referenced_json"]))
            pins_text = " ".join(json.loads(ch["pins_referenced_json"]))
            conn.execute(
                """
                INSERT INTO heritage_fts (child_id, parent_id, title, symbols_text, pins_text, prefixed_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ch["child_id"], ch["parent_id"], ch["title"], symbols_text, pins_text, ch["prefixed_text"]),
            )
            embed_input = f"{ch['title']}\n{symbols_text}\n{pins_text}\n{ch['prefixed_text']}"
            vec = compute_offline_embedding(embed_input, dim=EMBEDDING_DIM)
            blob = vec_module.serialize_float32(vec) if vec_module is not None else pack_float32_embedding(vec)
            if vec_module is not None:
                conn.execute("INSERT INTO heritage_vec (child_rowid, embedding) VALUES (?, ?)", (child_rowid, blob))
            else:
                vec_norm = math.sqrt(sum(v * v for v in vec))
                conn.execute(
                    "INSERT INTO heritage_vec_fallback (child_rowid, embedding_blob, norm) VALUES (?, ?, ?)",
                    (child_rowid, blob, vec_norm),
                )
            child_count += 1

    return {
        "parent_count": parent_count,
        "child_count": child_count,
        "verified_mermaid_count": verified_mermaid,
        "quarantined_mermaid_count": quarantined_mermaid,
    }


def _write_index_meta(conn: sqlite3.Connection, counts: dict[str, int], vector_backend: str) -> None:
    meta_rows = {
        "schema_version": "1.0",
        "vector_backend": vector_backend,
        "embedding_dim": str(EMBEDDING_DIM),
        "embedding_model": "offline-feature-hash-f32-1024",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        **{k: str(v) for k, v in counts.items()},
    }
    conn.executemany(
        "INSERT INTO heritage_index_meta (key, value) VALUES (?, ?)",
        sorted(meta_rows.items()),
    )


def build_heritage_index(
    knowledge_dir: Path | str = Path("knowledge"),
    wiki_dir: Path | str | None = Path("wiki"),
    db_path: Path | str = DEFAULT_INDEX_DB,
    *,
    verify_frontmatter: bool = True,
) -> dict[str, Any]:
    """Validate cleanroom heritage sources and atomically rebuild the hybrid SQLite RAG index."""
    knowledge_path = Path(knowledge_dir) if knowledge_dir is not None else None
    wiki_path = Path(wiki_dir) if wiki_dir is not None else None
    target_db = Path(db_path)

    if verify_frontmatter:
        _validate_inputs(knowledge_path, wiki_path)

    md_files = _collect_source_files(knowledge_path, wiki_path)
    if not md_files:
        raise FileNotFoundError(f"No heritage markdown files found under {knowledge_dir!r} / {wiki_dir!r}")

    repo_root = _resolve_repo_root(knowledge_path, wiki_path)
    target_db.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target_db.parent, prefix=f".{target_db.stem}.", suffix=".sqlite.tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("PRAGMA journal_mode = DELETE;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            vec_module = _try_load_sqlite_vec(conn)
            vector_backend = "sqlite_vec" if vec_module is not None else "heritage_vec_fallback"
            with conn:
                conn.executescript(HERITAGE_CORE_DDL)
                conn.executescript(HERITAGE_VEC_DDL if vec_module is not None else HERITAGE_VEC_FALLBACK_DDL)
                counts = _insert_corpus(conn, md_files, repo_root, vec_module)
                _write_index_meta(conn, counts, vector_backend)
        finally:
            conn.close()

        for sidecar in (f"{tmp_path}-wal", f"{tmp_path}-shm", f"{tmp_path}-journal"):
            Path(sidecar).unlink(missing_ok=True)
        for stale in (f"{target_db}-wal", f"{target_db}-shm"):
            Path(stale).unlink(missing_ok=True)
        os.replace(tmp_path, target_db)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        **counts,
        "vector_backend": vector_backend,
        "db_path": str(target_db),
    }


__all__ = [
    "DEFAULT_INDEX_DB",
    "EMBEDDING_DIM",
    "HERITAGE_CORE_DDL",
    "HERITAGE_VEC_DDL",
    "HERITAGE_VEC_FALLBACK_DDL",
    "compute_offline_embedding",
    "pack_float32_embedding",
    "unpack_float32_embedding",
    "cosine_similarity",
    "build_heritage_index",
]
