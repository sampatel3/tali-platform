"""E2B-sandbox-backed MCP toolset for the ``claude-agent-sdk``.

Leaf A of the Option-4 migration that swaps our hand-rolled Anthropic
tool-use loop for Anthropic's official ``claude-agent-sdk``. The SDK's
built-in Read/Edit/Bash tools operate on the *server's* local
filesystem, which is useless to us: the candidate's repo lives in an
E2B sandbox, not on the API host. So we register custom MCP tools that
proxy each call through the existing
:class:`AssessmentToolExecutor`, which already wraps E2B and enforces
path sanitization.

Public surface
--------------
- :func:`build_sandbox_mcp_server` — factory that returns an in-process
  MCP server config (from ``create_sdk_mcp_server``) wired to a given
  executor instance. The agentic-chat service (separate PR) will pass
  the result into :class:`ClaudeAgentOptions.mcp_servers` so the SDK
  routes tool calls to our handlers in-process (no IPC).

SDK naming contract
-------------------
The SDK exposes registered tools to the model as
``mcp__<server_name>__<tool_name>``. We pass ``name="sandbox"`` so the
agent sees ``mcp__sandbox__Read``, ``mcp__sandbox__Write``,
``mcp__sandbox__Edit``, ``mcp__sandbox__Bash``.

Result shape contract
---------------------
Every handler returns a dict shaped::

    {"content": [{"type": "text", "text": "<message>"}]}                      # ok
    {"content": [{"type": "text", "text": "<error>"}], "is_error": True}      # err

Errors are returned (not raised) so the agent loop can self-correct on
the next turn — Claude reads the error text and picks a different tool
or input. See https://code.claude.com/docs/en/agent-sdk/custom-tools.

Path sanitization is NOT duplicated here. ``executor.dispatch`` already
runs the same byte-for-byte rules as the legacy candidate routes.
"""

from __future__ import annotations

from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.components.assessments.claude_tool_executor import (
    AssessmentToolExecutor,
)


# Server name baked into the MCP tool prefix the model sees. Changing
# this also changes ``mcp__sandbox__*`` everywhere — keep aligned with
# whatever ``allowed_tools=[...]`` the chat service passes.
_SERVER_NAME = "sandbox"


# Tool descriptions are written like Anthropic's stock Claude Code
# tools: action-first, mention the relevant constraint (path rules,
# match-uniqueness, command timeout) so the model picks the right tool
# without trial-and-error.
_READ_DESCRIPTION = (
    "Read a UTF-8 file from the candidate's sandbox repo and return its "
    "full contents. Use this before Edit so the `old` string matches "
    "byte-for-byte. Path must be repo-relative; absolute paths or "
    "traversal (..) are rejected."
)

_WRITE_DESCRIPTION = (
    "Create a new file or overwrite an existing one in the candidate's "
    "sandbox repo with the given UTF-8 content. Use for new files or "
    "wholesale rewrites; prefer Edit for surgical in-place changes. "
    "Path must be repo-relative; absolute paths or traversal (..) are "
    "rejected."
)

_EDIT_DESCRIPTION = (
    "Replace an exact string in a sandbox file. `old` must occur "
    "EXACTLY ONCE in the file — zero matches returns `no_match`, more "
    "than one returns `ambiguous_match`. In either case make `old` more "
    "specific and retry. Whitespace and indentation must match byte-for-"
    "byte. Path must be repo-relative; absolute paths or traversal (..) "
    "are rejected."
)

_BASH_DESCRIPTION = (
    "Execute a shell command inside the candidate's sandbox at the repo "
    "root with a 10-second timeout. Returns stdout, stderr, and the exit "
    "code. Use for running tests, linters, or quick filesystem "
    "inspection. The command is not interactive — anything that prompts "
    "for input will hang and time out."
)


def _ok(text: str) -> Dict[str, Any]:
    """SDK-shaped success block."""
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> Dict[str, Any]:
    """SDK-shaped error block. ``is_error=True`` tells the SDK to surface
    this as a failed tool call so Claude can self-correct on the next
    turn instead of treating the error message as normal output.
    """
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _format_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate an :meth:`AssessmentToolExecutor.dispatch` payload into
    the SDK's content-block shape.

    The executor's contract is ``{"ok": bool, "result": ..., "error":
    str?}``. We coerce ``result`` to a string (it's usually already a
    string; ``run_command`` returns a dict that we ``str()`` for the
    model — that's fine, Claude parses it back fluently).
    """
    if payload.get("ok"):
        result = payload.get("result")
        return _ok(result if isinstance(result, str) else str(result))
    return _err(str(payload.get("error") or "unknown_error"))


def build_sandbox_mcp_server(executor: AssessmentToolExecutor) -> Dict[str, Any]:
    """Build the in-process MCP server that proxies tools to ``executor``.

    The returned object is the ``McpSdkServerConfig`` dict produced by
    :func:`claude_agent_sdk.create_sdk_mcp_server`. Pass it to
    :class:`ClaudeAgentOptions` like::

        options = ClaudeAgentOptions(
            mcp_servers={"sandbox": build_sandbox_mcp_server(executor)},
            allowed_tools=[
                "mcp__sandbox__Read",
                "mcp__sandbox__Write",
                "mcp__sandbox__Edit",
                "mcp__sandbox__Bash",
            ],
        )

    The executor instance is captured in each handler's closure, so a
    single server is bound to a single candidate's sandbox lifecycle.
    Build a fresh server per chat session.
    """

    @tool("Read", _READ_DESCRIPTION, {"path": str})
    async def read_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        return _format_result(
            executor.dispatch("read_file", {"path": args.get("path")})
        )

    @tool("Write", _WRITE_DESCRIPTION, {"path": str, "content": str})
    async def write_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        return _format_result(
            executor.dispatch(
                "write_file",
                {"path": args.get("path"), "content": args.get("content")},
            )
        )

    @tool("Edit", _EDIT_DESCRIPTION, {"path": str, "old": str, "new": str})
    async def edit_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        return _format_result(
            executor.dispatch(
                "apply_edit",
                {
                    "path": args.get("path"),
                    "old": args.get("old"),
                    "new": args.get("new"),
                },
            )
        )

    @tool("Bash", _BASH_DESCRIPTION, {"command": str})
    async def bash_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        return _format_result(
            executor.dispatch("run_command", {"command": args.get("command")})
        )

    return create_sdk_mcp_server(
        name=_SERVER_NAME,
        tools=[read_tool, write_tool, edit_tool, bash_tool],
    )


__all__ = ["build_sandbox_mcp_server"]
