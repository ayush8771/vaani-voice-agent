"""
vaani_mcp/server.py — Vaani's MCP server.

Exposes the guarded tools in tools.py over the Model Context Protocol, so any
MCP client can use them: Vaani's own agent (in-memory, see agent.py) or an
external client such as Claude Desktop or the MCP Inspector (stdio):

    python -m vaani_mcp.server
"""

import json
import logging
import time

from mcp.server.fastmcp import FastMCP

from config import settings
from database import init_db
from vaani_mcp import tools

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    f"Tools for {settings.restaurant_name}'s reservation desk. Dates are YYYY-MM-DD, times HH:MM "
    "(24-hour). To book: call prepare_booking, read its message back to the caller, and only after "
    "a clear yes call book_table with the hold_token and caller_confirmed=true. To cancel: call "
    "start_cancellation, then confirm_cancellation with the 6-digit code the caller received."
)


def _logged(fn, path: str):
    """Wrap a tool so every call through this server is recorded."""
    def wrapper(**kwargs):
        started = time.time()
        result = fn(**kwargs)
        tools.log_call(path, fn.__name__, kwargs, result, int((time.time() - started) * 1000))
        return result
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__signature__ = __import__("inspect").signature(fn)
    wrapper.__annotations__ = fn.__annotations__
    return wrapper


def build_server(log_path: str = "") -> FastMCP:
    """Create the MCP server. With log_path set, the server records each call itself."""
    server = FastMCP("vaani", instructions=INSTRUCTIONS)
    for fn in tools.TOOLS.values():
        server.add_tool(_logged(fn, log_path) if log_path else fn,
                        name=fn.__name__, description=(fn.__doc__ or "").strip())
    return server


def result_to_dict(call_result) -> dict:
    """Turn an MCP CallToolResult into the dict the tool returned."""
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                value = json.loads(text)
                return value if isinstance(value, dict) else {"result": value}
            except json.JSONDecodeError:
                if call_result.isError:
                    # Never let raw error text reach the caller's ears.
                    return {"ok": False, "reason": "tool_error", "error": text[:500]}
                return {"message": text}
    return {}


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    init_db()
    build_server(log_path="mcp-external").run()
