"""Thin MCP wrapper for Chrysler heritage RAG, discovery, and wiki synchronization."""

from __future__ import annotations

from datetime import date, datetime
import importlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from logicaide.heritage.discovery import run_discover
from logicaide.heritage.rag_index import DEFAULT_INDEX_DB
from logicaide.heritage.rag_query import (
    NO_VERIFIED_SOURCE_MATCH,
    get_provenance_card as _get_provenance_card,
    get_verified_schematic as _get_verified_schematic,
    lookup_60way_pin as _lookup_60way_pin,
    lookup_sci_offset_hybrid as _lookup_sci_offset_hybrid,
    search_heritage_docs as _search_heritage_docs,
)
from logicaide.heritage.wiki_sync import WikiValidationError, run_sync_wiki
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH

SERVER_NAME = "chrysler-heritage"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"


def _json_safe(value: Any) -> Any:
    """Recursively convert Paths and dates into JSON-serializable primitives."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _error_payload(exc: Exception) -> dict[str, Any]:
    """Return a structured non-raising error payload aligned with strict abstention."""
    return {
        "matched": False,
        "status": "NO_VERIFIED_SOURCE_MATCH",
        "message": f"{NO_VERIFIED_SOURCE_MATCH}: {exc}",
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def _reject_bool_ints(**params: Any) -> dict[str, Any] | None:
    """Return a structured error payload if any integer parameter was passed as a bool."""
    for name, value in params.items():
        if isinstance(value, bool):
            return _error_payload(ValueError(f"{name} must be an integer, not bool"))
    return None


def _validate_path_arg(name: str, raw_path: Path | str, *, repo_root: Path = REPO_ROOT) -> Path:
    """Reject symlinks and keep relative paths contained within repo_root."""
    if not isinstance(raw_path, (Path, str)) or (isinstance(raw_path, str) and not raw_path.strip()):
        raise ValueError(f"{name} must be a non-empty path")
    candidate = Path(raw_path)
    if candidate.is_symlink():
        raise ValueError(f"Refusing symlink for {name}: {candidate}")
    if not candidate.is_absolute():
        base = Path(repo_root)
        joined = base / candidate
        if joined.is_symlink():
            raise ValueError(f"Refusing symlink for {name}: {joined}")
        cur = joined.parent
        while cur != base and cur != cur.parent:
            if cur.is_symlink():
                raise ValueError(f"Refusing symlink parent for {name}: {cur}")
            cur = cur.parent
        resolved = joined.resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise ValueError(f"Relative path for {name} escapes repo_root: {raw_path}")
        return resolved
    return candidate


def _call_upstream(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Invoke an upstream domain function and normalize expected errors to JSON dicts."""
    try:
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
                optional[key] = _validate_path_arg(key, raw_val)
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
        safe_target = _validate_path_arg("target_dir", target_dir) if target_dir is not None else None
        return run_sync_wiki(
            wiki_dir=safe_wiki, target_dir=safe_target, dry_run=dry_run,
            validate_only=validate_only, strip_frontmatter=strip_frontmatter,
            flatten_for_github_wiki=flatten_for_github_wiki,
        )

    return _call_upstream(_run)


class _ToolEntry(dict):
    """JSON-serializable MCP tool metadata dict that is also directly callable."""

    def __init__(self, fn: Callable[..., dict[str, Any]], description: str, input_schema: dict[str, Any]) -> None:
        super().__init__(name=fn.__name__, description=description, inputSchema=input_schema)
        self._fn = fn

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._fn(*args, **kwargs)


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


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
    fastmcp_cls = None
    for module_name in ("mcp.server.fastmcp", "fastmcp"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        fastmcp_cls = getattr(module, "FastMCP", None)
        if fastmcp_cls is not None:
            break
    if fastmcp_cls is None:
        return None
    server = fastmcp_cls(SERVER_NAME)
    for entry in TOOL_REGISTRY.values():
        server.tool(name=entry["name"], description=entry["description"])(entry._fn)
    return server


def _rpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _matches_json_type(value: Any, json_type: str) -> bool:
    """Return True when value satisfies a JSON Schema primitive type (excluding bool from int/number)."""
    if json_type == "string":
        return isinstance(value, str)
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if json_type == "boolean":
        return isinstance(value, bool)
    if json_type == "object":
        return isinstance(value, dict)
    if json_type == "array":
        return isinstance(value, list)
    if json_type == "null":
        return value is None
    return False


def _validate_tool_arguments(tool_name: str, schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Validate tool arguments against JSON inputSchema, returning an error message or None."""
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unknown = sorted(k for k in arguments if k not in properties)
        if unknown:
            return f"Invalid arguments for {tool_name}: unexpected argument(s): {', '.join(unknown)}"
    for req in schema.get("required") or ():
        if req not in arguments:
            return f"Invalid arguments for {tool_name}: missing required argument '{req}'"
    for key, value in arguments.items():
        prop = properties.get(key)
        if prop is None:
            continue
        expected = prop.get("type")
        if expected is not None:
            allowed = [expected] if isinstance(expected, str) else list(expected)
            if not any(_matches_json_type(value, t) for t in allowed):
                return f"Invalid arguments for {tool_name}: '{key}' must be of type {' or '.join(allowed)}"
        if "enum" in prop:
            enum_vals = prop["enum"]
            if not any(value == opt and isinstance(value, bool) == isinstance(opt, bool) for opt in enum_vals):
                return f"Invalid arguments for {tool_name}: '{key}' must be one of {enum_vals}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in prop and value < prop["minimum"]:
                return f"Invalid arguments for {tool_name}: '{key}' must be >= {prop['minimum']}"
            if "maximum" in prop and value > prop["maximum"]:
                return f"Invalid arguments for {tool_name}: '{key}' must be <= {prop['maximum']}"
    return None


def _dispatch_tool_call(req_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate params/arguments against tool inputSchema and execute a tools/call request."""
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return _rpc_error(req_id, -32602, "Invalid params: expected object with tool 'name'")
    tool_name = params["name"]
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return _rpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
    if "arguments" in params and params["arguments"] is not None:
        arguments = params["arguments"]
        if not isinstance(arguments, dict):
            return _rpc_error(req_id, -32602, "Invalid params: 'arguments' must be a JSON object")
    else:
        arguments = {}
    validation_error = _validate_tool_arguments(tool_name, tool["inputSchema"], arguments)
    if validation_error is not None:
        return _rpc_error(req_id, -32602, validation_error)
    try:
        output = tool(**arguments)
    except TypeError as exc:
        return _rpc_error(req_id, -32602, f"Invalid arguments for {tool_name}: {exc}")
    return _rpc_result(req_id, {
        "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=True)}],
        "structuredContent": output,
        "isError": "error" in output,
    })


def handle_jsonrpc_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch an offline MCP JSON-RPC 2.0 request deterministically."""
    req_id = payload.get("id") if isinstance(payload, dict) else None
    try:
        if not isinstance(payload, dict):
            return _rpc_error(None, -32600, "Invalid Request: payload must be a JSON object")
        method = payload.get("method")
        if not isinstance(method, str) or not method:
            return _rpc_error(req_id, -32600, "Invalid Request: missing method")
        if method == "initialize":
            return _rpc_result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method in ("notifications/initialized", "ping"):
            return _rpc_result(req_id, {})
        if method == "tools/list":
            return _rpc_result(req_id, {"tools": [dict(entry) for entry in TOOL_REGISTRY.values()]})
        if method == "tools/call":
            return _dispatch_tool_call(req_id, payload)
        return _rpc_error(req_id, -32601, f"Method not found: {method}")
    except Exception as exc:
        return _rpc_error(req_id, -32603, f"Internal error: {exc}")


def serve_stdio(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Serve newline-delimited MCP JSON-RPC 2.0 requests over stdio."""
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        req_id: Any = None
        try:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _rpc_error(None, -32700, f"Parse error: {exc}")
            else:
                if isinstance(payload, dict):
                    req_id = payload.get("id")
                    method = payload.get("method")
                    if isinstance(method, str) and method.startswith("notifications/") and "id" not in payload:
                        continue
                response = handle_jsonrpc_request(payload)
            serialized = json.dumps(response, ensure_ascii=True)
        except Exception as exc:
            try:
                serialized = json.dumps(_rpc_error(req_id, -32603, f"Internal error: {exc}"), ensure_ascii=True)
            except Exception:
                serialized = json.dumps(_rpc_error(None, -32603, f"Internal error: {exc}"), ensure_ascii=True)
        stdout.write(serialized + "\n")
        stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entrypoint supporting FastMCP when installed or offline stdio JSON-RPC fallback."""
    args = sys.argv[1:] if argv is None else argv
    if "--stdio" not in args:
        server = create_fastmcp_server()
        if server is not None:
            server.run()
            return 0
    return serve_stdio()


__all__ = [
    "SERVER_NAME", "SERVER_VERSION", "TOOL_REGISTRY", "TOOLS", "create_fastmcp_server",
    "discover_heritage_leads", "get_provenance_card", "get_verified_schematic",
    "handle_jsonrpc_request", "lookup_60way_pin", "lookup_sci_offset", "main",
    "search_heritage_docs", "serve_stdio", "sync_heritage_wiki",
]

if __name__ == "__main__":
    raise SystemExit(main())
