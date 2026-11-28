"""Shared MCP JSON-RPC 2.0 protocol, FastMCP adapter, and path/type guard utilities."""

from __future__ import annotations

from datetime import date, datetime
import importlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PROTOCOL_VERSION = "2024-11-05"
_SIMPLE_TYPES: dict[str, type] = {"string": str, "boolean": bool, "object": dict, "array": list}
_OS_SYSTEM_LINK_PREFIXES = frozenset({"/tmp", "/var", "/var/tmp"})


def ensure_repo_sys_path() -> None:
    """Insert REPO_ROOT and SRC_ROOT onto sys.path if absent."""
    for path in (REPO_ROOT, SRC_ROOT):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))


def json_safe(value: Any) -> Any:
    """Recursively convert Paths, dates, and non-finite floats into JSON-serializable primitives."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return [json_safe(v) for v in value] if isinstance(value, (list, tuple)) else value


def default_error_payload(exc: Exception, *, status: str = "ERROR") -> dict[str, Any]:
    """Return a structured non-raising error payload."""
    return {"matched": False, "status": status, "message": f"{status}: {exc}", "error": str(exc), "error_type": type(exc).__name__}


def reject_bool_ints(error_fn: Callable[[Exception], dict[str, Any]] = default_error_payload, **params: Any) -> dict[str, Any] | None:
    """Return a structured error payload if any integer parameter was passed as a bool."""
    for name, value in params.items():
        if isinstance(value, bool):
            return error_fn(ValueError(f"{name} must be an integer, not bool"))
    return None


def _reject_symlink_parents(target: Path, stop_at: Path | None, name: str) -> None:
    cur = target.parent
    while cur != cur.parent:
        if str(cur) not in _OS_SYSTEM_LINK_PREFIXES and cur.is_symlink():
            raise ValueError(f"Refusing symlink parent for {name}: {cur}")
        if stop_at is not None and (cur == stop_at or not cur.is_relative_to(stop_at)):
            break
        cur = cur.parent


def validate_path_arg(name: str, raw_path: Path | str, *, repo_root: Path = REPO_ROOT, require_within: Path | str | None = None, reject_protected: Iterable[Path | str] = ()) -> Path:
    """Reject symlinks, escaping paths, and optional protected directories."""
    if not isinstance(raw_path, (Path, str)) or (isinstance(raw_path, str) and not raw_path.strip()):
        raise ValueError(f"{name} must be a non-empty path")
    candidate = Path(raw_path)
    if candidate.is_symlink():
        raise ValueError(f"Refusing symlink for {name}: {candidate}")
    base = Path(repo_root)
    if require_within is not None or not candidate.is_absolute():
        anchor = base if require_within is None else (Path(require_within) if Path(require_within).is_absolute() else base / require_within)
        if anchor.is_symlink():
            raise ValueError(f"Refusing symlink anchor for {name}: {anchor}")
        target = candidate if candidate.is_absolute() else anchor / candidate
        if target.is_symlink():
            raise ValueError(f"Refusing symlink for {name}: {target}")
        stop_at = base if anchor.is_relative_to(base) else None
        _reject_symlink_parents(target, stop_at, name)
        resolved = target.resolve()
        if not resolved.is_relative_to(anchor.resolve()):
            raise ValueError(f"Path for {name} escapes {anchor}: {raw_path}")
        result = resolved
    else:
        _reject_symlink_parents(candidate, None, name)
        resolved, result = candidate.resolve(), candidate
    base_resolved = base.resolve()
    for prot in reject_protected:
        prot_path = Path(prot) if Path(prot).is_absolute() else base_resolved / prot
        if resolved.is_relative_to(prot_path.resolve()):
            raise ValueError(f"Refusing protected path for {name}: {raw_path}")
    return result


def display_repo_path(raw_path: Path | str, *, repo_root: Path = REPO_ROOT) -> str:
    """Return a repo-relative POSIX path when inside repo_root, else str(raw_path)."""
    resolved = Path(raw_path).resolve()
    root_resolved = Path(repo_root).resolve()
    if resolved.is_relative_to(root_resolved):
        return resolved.relative_to(root_resolved).as_posix()
    return str(raw_path)


class ToolEntry(dict):
    """JSON-serializable MCP tool metadata dict that is also directly callable."""

    def __init__(self, fn: Callable[..., dict[str, Any]], description: str, input_schema: dict[str, Any]) -> None:
        super().__init__(name=fn.__name__, description=description, inputSchema=input_schema)
        self._fn = fn

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._fn(*args, **kwargs)


def schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    """Build a strict JSON Schema object definition for an MCP tool."""
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def create_fastmcp_server(server_name: str, tool_registry: dict[str, ToolEntry]) -> Any | None:
    """Build a FastMCP(server_name) instance if mcp/fastmcp is installed, else None."""
    for module_name in ("mcp.server.fastmcp", "fastmcp"):
        try:
            fastmcp_cls = getattr(importlib.import_module(module_name), "FastMCP", None)
        except ImportError:
            continue
        if fastmcp_cls is not None:
            server = fastmcp_cls(server_name)
            for entry in tool_registry.values():
                server.tool(name=entry["name"], description=entry["description"])(entry._fn)
            return server
    return None


def rpc_result(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def matches_json_type(value: Any, json_type: str) -> bool:
    """Return True when value satisfies a JSON Schema primitive type (excluding bool from int/number)."""
    if json_type in _SIMPLE_TYPES:
        return isinstance(value, _SIMPLE_TYPES[json_type])
    if json_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if json_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return value is None if json_type == "null" else False


def validate_tool_arguments(tool_name: str, tool_schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Validate tool arguments against JSON inputSchema, returning an error message or None."""
    properties = tool_schema.get("properties") or {}
    if tool_schema.get("additionalProperties") is False and (unknown := sorted(k for k in arguments if k not in properties)):
        return f"Invalid arguments for {tool_name}: unexpected argument(s): {', '.join(unknown)}"
    for req in tool_schema.get("required") or ():
        if req not in arguments:
            return f"Invalid arguments for {tool_name}: missing required argument '{req}'"
    for key, value in arguments.items():
        if (prop := properties.get(key)) is None:
            continue
        if (expected := prop.get("type")) is not None:
            allowed = [expected] if isinstance(expected, str) else list(expected)
            if not any(matches_json_type(value, t) for t in allowed):
                return f"Invalid arguments for {tool_name}: '{key}' must be of type {' or '.join(allowed)}"
        if "enum" in prop and not any(value == opt and isinstance(value, bool) == isinstance(opt, bool) for opt in prop["enum"]):
            return f"Invalid arguments for {tool_name}: '{key}' must be one of {prop['enum']}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in prop and value < prop["minimum"]:
                return f"Invalid arguments for {tool_name}: '{key}' must be >= {prop['minimum']}"
            if "maximum" in prop and value > prop["maximum"]:
                return f"Invalid arguments for {tool_name}: '{key}' must be <= {prop['maximum']}"
    return None


def dispatch_tool_call(req_id: Any, payload: dict[str, Any], tool_registry: dict[str, ToolEntry]) -> dict[str, Any]:
    """Validate params/arguments against tool inputSchema and execute a tools/call request."""
    params = payload.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        return rpc_error(req_id, -32602, "Invalid params: expected object with tool 'name'")
    if (tool := tool_registry.get(tool_name := params["name"])) is None:
        return rpc_error(req_id, -32602, f"Unknown tool: {tool_name}")
    arguments = {} if params.get("arguments") is None else params["arguments"]
    if not isinstance(arguments, dict):
        return rpc_error(req_id, -32602, "Invalid params: 'arguments' must be a JSON object")
    if err := validate_tool_arguments(tool_name, tool["inputSchema"], arguments):
        return rpc_error(req_id, -32602, err)
    try:
        output = tool(**arguments)
    except TypeError as exc:
        return rpc_error(req_id, -32602, f"Invalid arguments for {tool_name}: {exc}")
    return rpc_result(req_id, {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=True, allow_nan=False)}], "structuredContent": output, "isError": "error" in output})


def handle_jsonrpc_request(payload: dict[str, Any], *, server_name: str, server_version: str, tool_registry: dict[str, ToolEntry], protocol_version: str = PROTOCOL_VERSION) -> dict[str, Any]:
    """Dispatch an offline MCP JSON-RPC 2.0 request deterministically."""
    req_id = payload.get("id") if isinstance(payload, dict) else None
    try:
        if not isinstance(payload, dict):
            return rpc_error(None, -32600, "Invalid Request: payload must be a JSON object")
        if not isinstance(method := payload.get("method"), str) or not method:
            return rpc_error(req_id, -32600, "Invalid Request: missing method")
        if method == "initialize":
            return rpc_result(req_id, {"protocolVersion": protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": server_name, "version": server_version}})
        if method in ("notifications/initialized", "ping"):
            return rpc_result(req_id, {})
        if method == "tools/list":
            return rpc_result(req_id, {"tools": [dict(entry) for entry in tool_registry.values()]})
        if method == "tools/call":
            return dispatch_tool_call(req_id, payload, tool_registry)
        return rpc_error(req_id, -32601, f"Method not found: {method}")
    except Exception as exc:
        return rpc_error(req_id, -32603, f"Internal error: {exc}")


def serve_stdio(handler: Callable[[dict[str, Any]], dict[str, Any]], stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Serve newline-delimited MCP JSON-RPC 2.0 requests over stdio."""
    for raw_line in stdin:
        if not (line := raw_line.strip()):
            continue
        req_id: Any = None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            response = rpc_error(None, -32700, f"Parse error: {exc}")
        else:
            if isinstance(payload, dict):
                req_id, method = payload.get("id"), payload.get("method")
                if isinstance(method, str) and method.startswith("notifications/") and "id" not in payload:
                    continue
            try:
                response = handler(payload)
            except Exception as exc:
                response = rpc_error(req_id, -32603, f"Internal error: {exc}")
        try:
            serialized = json.dumps(response, ensure_ascii=True, allow_nan=False)
        except Exception as exc:
            serialized = json.dumps(rpc_error(None, -32603, f"Internal error: {exc}"), ensure_ascii=True, allow_nan=False)
        stdout.write(serialized + "\n")
        stdout.flush()
    return 0


def run_mcp_server(server_name: str, server_version: str, tool_registry: dict[str, ToolEntry], argv: list[str] | None = None, *, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Entrypoint supporting FastMCP when installed or offline stdio JSON-RPC fallback."""
    args = sys.argv[1:] if argv is None else argv
    if "--stdio" not in args and (server := create_fastmcp_server(server_name, tool_registry)) is not None:
        server.run()
        return 0
    return serve_stdio(lambda p: handle_jsonrpc_request(p, server_name=server_name, server_version=server_version, tool_registry=tool_registry), stdin=stdin, stdout=stdout)
