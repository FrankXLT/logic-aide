"""Thin MCP wrapper for Chrysler heritage RAG, discovery, and wiki synchronization."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, Iterable, TextIO

_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[1]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from mcp_servers._common import (
    PROTOCOL_VERSION, REPO_ROOT, SRC_ROOT, ToolEntry as _ToolEntry,
    create_fastmcp_server as _create_fastmcp_server, default_error_payload,
    ensure_repo_sys_path, handle_jsonrpc_request as _handle_jsonrpc_request,
    json_safe as _json_safe, reject_bool_ints, run_mcp_server,
    schema as _schema, serve_stdio as _serve_stdio, validate_path_arg,
)

ensure_repo_sys_path()

from logicaide.heritage.discovery import run_discover
from logicaide.heritage.rag_index import DEFAULT_INDEX_DB
from logicaide.heritage.rag_query import (
    NO_VERIFIED_SOURCE_MATCH, get_provenance_card as _get_provenance_card,
    get_verified_schematic as _get_verified_schematic, lookup_60way_pin as _lookup_60way_pin,
    lookup_sci_offset_hybrid as _lookup_sci_offset_hybrid, search_heritage_docs as _search_heritage_docs,
)
from logicaide.heritage.wiki_sync import WikiValidationError, run_sync_wiki
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH

SERVER_NAME = "chrysler-heritage"
SERVER_VERSION = "0.1.0"
_PROTECTED_ROM_DIRS: tuple[str, ...] = ("rom/inputs", "rom/stock_baselines", "rom/src")


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Return a structured non-raising error payload aligned with strict abstention."""
    payload = default_error_payload(exc, status="NO_VERIFIED_SOURCE_MATCH")
    payload["message"] = f"{NO_VERIFIED_SOURCE_MATCH}: {exc}"
    return payload


def _reject_bool_ints(**params: Any) -> dict[str, Any] | None:
    """Return a structured error payload if any integer parameter was passed as a bool."""
    return reject_bool_ints(_error_payload, **params)


def _validate_path_arg(
    name: str, raw_path: Path | str, *, repo_root: Path = REPO_ROOT, reject_protected: Iterable[Path | str] = (),
) -> Path:
    """Reject symlinks, keep relative paths within repo_root, and enforce protected dirs."""
    return validate_path_arg(name, raw_path, repo_root=repo_root, reject_protected=reject_protected)


def _call_upstream(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke an upstream domain function and normalize expected errors to JSON dicts."""
    try:
        for key in ("db_path", "catalog_path"):
            if key in kwargs and kwargs[key] is not None:
                kwargs[key] = _validate_path_arg(key, kwargs[key])
        if (rom_id := kwargs.get("rom_id")) is not None and (
            isinstance(rom_id, Path) or (isinstance(rom_id, str) and rom_id.strip().lower().endswith(".json"))
        ):
            kwargs["rom_id"] = str(_validate_path_arg("rom_id", rom_id))
        return _json_safe(fn(*args, **kwargs))
    except (WikiValidationError, sqlite3.Error, FileNotFoundError, KeyError, ValueError, TypeError, OSError) as exc:
        return _error_payload(exc)


def search_heritage_docs(
    query: str, *, db_path: Path | str = DEFAULT_INDEX_DB, limit: int = 8, ecu: str | None = None,
    min_tier: int = 3, lex_weight: float = 0.55, vec_weight: float = 0.45,
    score_threshold: float = 0.014, require_verified_for_pin_or_sci: bool = True,
) -> dict[str, Any]:
    """Run Trust-Weighted RRF hybrid search across indexed heritage digests."""
    if err := _reject_bool_ints(limit=limit, min_tier=min_tier):
        return err
    return _call_upstream(
        _search_heritage_docs, query, db_path=db_path, limit=limit, ecu=ecu, min_tier=min_tier,
        lex_weight=lex_weight, vec_weight=vec_weight, score_threshold=score_threshold,
        require_verified_for_pin_or_sci=require_verified_for_pin_or_sci,
    )


def get_verified_schematic(
    doc_id: str | None = None, parent_id: str | None = None, query: str | None = None,
    pending_review: bool = False, ecu: str | None = None, min_tier: int = 3,
    db_path: Path | str = DEFAULT_INDEX_DB,
) -> dict[str, Any]:
    """Fetch VERIFIED Mermaid schematics, gating NEEDS_HUMAN_REVIEW unless requested."""
    if err := _reject_bool_ints(min_tier=min_tier):
        return err
    return _call_upstream(
        _get_verified_schematic, doc_id=doc_id, parent_id=parent_id, query=query,
        pending_review=pending_review, ecu=ecu, min_tier=min_tier, db_path=db_path,
    )


def lookup_60way_pin(
    pin: int | str, ecu: str | None = None, db_path: Path | str = DEFAULT_INDEX_DB,
) -> dict[str, Any]:
    """Look up a 60-way connector pin strictly in Tier 1/2 sources with SMEC/SBEC gates."""
    if err := _reject_bool_ints(pin=pin):
        return err
    return _call_upstream(_lookup_60way_pin, pin, ecu=ecu, db_path=db_path)


def lookup_sci_offset(
    query_or_offset: str, rom_id: str | None = None, sci_table: str | None = None,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH, db_path: Path | str = DEFAULT_INDEX_DB,
) -> dict[str, Any]:
    """Cross-reference verified ROM manifest SCI symbols and Tier 1/2 heritage docs."""
    return _call_upstream(
        _lookup_sci_offset_hybrid, query_or_offset, rom_id=rom_id, sci_table=sci_table,
        catalog_path=catalog_path, db_path=db_path,
    )


def get_provenance_card(
    doc_id_or_parent_id: str, db_path: Path | str = DEFAULT_INDEX_DB,
) -> dict[str, Any]:
    """Fetch source provenance, trust tier, WARC capture date, and section SHA-256 hashes."""
    return _call_upstream(_get_provenance_card, doc_id_or_parent_id, db_path=db_path)


def discover_heritage_leads(
    wave: str = "D", fetch_cdx: bool = False, private_root: Path | str | None = None,
    manifest_path: Path | str | None = None, queue_dir: Path | str | None = None,
    output_yaml: Path | str | None = None,
) -> dict[str, Any]:
    """Scan read-only reference manifests for deduplicated discovery leads (default Wave D)."""
    def _run() -> dict[str, Any]:
        optional: dict[str, Path] = {}
        for key, raw_val in (
            ("private_root", private_root), ("manifest_path", manifest_path),
            ("queue_dir", queue_dir), ("output_yaml", output_yaml),
        ):
            if raw_val is not None:
                protected = _PROTECTED_ROM_DIRS if key in ("queue_dir", "output_yaml") else ()
                optional[key] = _validate_path_arg(key, raw_val, reject_protected=protected)
        return run_discover(wave=wave, fetch_cdx=fetch_cdx, **optional)

    return _call_upstream(_run)


def sync_heritage_wiki(
    wiki_dir: Path | str = Path("wiki"), target_dir: Path | str | None = None,
    dry_run: bool = True, validate_only: bool = False, strip_frontmatter: bool = True,
    flatten_for_github_wiki: bool = True,
) -> dict[str, Any]:
    """Validate and mirror wiki/ into target_dir without mutating wiki/ in place (dry_run=True default)."""
    def _run() -> dict[str, Any]:
        safe_wiki = _validate_path_arg("wiki_dir", wiki_dir)
        safe_target = (
            _validate_path_arg("target_dir", target_dir, reject_protected=_PROTECTED_ROM_DIRS)
            if target_dir is not None
            else None
        )
        return run_sync_wiki(
            wiki_dir=safe_wiki, target_dir=safe_target, dry_run=dry_run,
            validate_only=validate_only, strip_frontmatter=strip_frontmatter,
            flatten_for_github_wiki=flatten_for_github_wiki,
        )

    return _call_upstream(_run)


TOOL_REGISTRY: dict[str, _ToolEntry] = {
    entry["name"]: entry
    for entry in (
        _ToolEntry(
            search_heritage_docs,
            "Trust-weighted RRF hybrid search across indexed Chrysler heritage digests.",
            _schema({
                "query": {"type": "string"}, "db_path": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "default": 8}, "ecu": {"type": "string"},
                "min_tier": {"type": "integer", "enum": [1, 2, 3], "default": 3},
                "lex_weight": {"type": "number", "default": 0.55}, "vec_weight": {"type": "number", "default": 0.45},
                "score_threshold": {"type": "number", "default": 0.014},
                "require_verified_for_pin_or_sci": {"type": "boolean", "default": True},
            }, required=("query",)),
        ),
        _ToolEntry(
            get_verified_schematic,
            "Return VERIFIED Mermaid schematics and walkthroughs; excludes NEEDS_HUMAN_REVIEW by default.",
            _schema({
                "doc_id": {"type": "string"}, "parent_id": {"type": "string"}, "query": {"type": "string"},
                "pending_review": {"type": "boolean", "default": False}, "ecu": {"type": "string"},
                "min_tier": {"type": "integer", "enum": [1, 2, 3], "default": 3}, "db_path": {"type": "string"},
            }),
        ),
        _ToolEntry(
            lookup_60way_pin,
            "Look up a 60-way ECU connector pin strictly from Tier 1/2 sources with SMEC/SBEC safety warnings.",
            _schema({"pin": {"type": ["integer", "string"]}, "ecu": {"type": "string"}, "db_path": {"type": "string"}}, required=("pin",)),
        ),
        _ToolEntry(
            lookup_sci_offset,
            "Cross-reference Tier 1/2 heritage digests with verified rom_manifest.json SCI symbols.",
            _schema({
                "query_or_offset": {"type": "string"}, "rom_id": {"type": "string"},
                "sci_table": {"type": "string"}, "catalog_path": {"type": "string"}, "db_path": {"type": "string"},
            }, required=("query_or_offset",)),
        ),
        _ToolEntry(
            get_provenance_card,
            "Expose full doc_id, trust_tier, source_citation, page_ref, warc_capture_date, and section SHA-256.",
            _schema({"doc_id_or_parent_id": {"type": "string"}, "db_path": {"type": "string"}}, required=("doc_id_or_parent_id",)),
        ),
        _ToolEntry(
            discover_heritage_leads,
            "Read-only reference manifest scan + SHA-256 dedup emitting discovery leads and queue JSON.",
            _schema({
                "wave": {"type": "string", "default": "D"}, "fetch_cdx": {"type": "boolean", "default": False},
                "private_root": {"type": "string"}, "manifest_path": {"type": "string"},
                "queue_dir": {"type": "string"}, "output_yaml": {"type": "string"},
            }),
        ),
        _ToolEntry(
            sync_heritage_wiki,
            "Validate and mirror wiki/ into target_dir without mutating wiki/ in place (dry_run=True default).",
            _schema({
                "wiki_dir": {"type": "string", "default": "wiki"}, "target_dir": {"type": "string"},
                "dry_run": {"type": "boolean", "default": True}, "validate_only": {"type": "boolean", "default": False},
                "strip_frontmatter": {"type": "boolean", "default": True},
                "flatten_for_github_wiki": {"type": "boolean", "default": True},
            }),
        ),
    )
}
TOOLS = TOOL_REGISTRY


def create_fastmcp_server() -> Any | None:
    """Build a FastMCP('chrysler-heritage') instance if mcp/fastmcp is installed, else None."""
    return _create_fastmcp_server(SERVER_NAME, TOOL_REGISTRY)


def handle_jsonrpc_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an offline MCP JSON-RPC 2.0 request deterministically."""
    return _handle_jsonrpc_request(
        payload, server_name=SERVER_NAME, server_version=SERVER_VERSION,
        tool_registry=TOOL_REGISTRY, protocol_version=PROTOCOL_VERSION,
    )


def serve_stdio(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Serve newline-delimited MCP JSON-RPC 2.0 requests over stdio."""
    return _serve_stdio(handle_jsonrpc_request, stdin=stdin, stdout=stdout)


def main(argv: list[str] | None = None) -> int:
    """Entrypoint supporting FastMCP when installed or offline stdio JSON-RPC fallback."""
    return run_mcp_server(SERVER_NAME, SERVER_VERSION, TOOL_REGISTRY, argv=argv)


__all__ = [
    "SERVER_NAME", "SERVER_VERSION", "TOOL_REGISTRY", "TOOLS", "create_fastmcp_server",
    "discover_heritage_leads", "get_provenance_card", "get_verified_schematic",
    "handle_jsonrpc_request", "lookup_60way_pin", "lookup_sci_offset", "main",
    "search_heritage_docs", "serve_stdio", "sync_heritage_wiki",
]

if __name__ == "__main__":
    raise SystemExit(main())
