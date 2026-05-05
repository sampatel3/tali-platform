"""Taali Chat — agentic recruiter chat backed by the MCP tool surface.

Wraps the same handlers ``app/mcp/server.py`` exposes over HTTP, but calls
them in-process so the React app can chat with Claude without paying an
HTTP roundtrip per tool call. See ``backend/docs/TAALI_CHAT.md`` for
architecture + API reference.
"""

from .service import run_chat_turn
from .tool_registry import TAALI_CHAT_TOOLS, dispatch_tool

__all__ = ["run_chat_turn", "TAALI_CHAT_TOOLS", "dispatch_tool"]
