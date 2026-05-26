"""Unit tests for the E2B-backed MCP toolset wired into ``claude-agent-sdk``.

Locks in the leaf-A contract:

- The factory returns an MCP server config exposing exactly the four
  tool names ``Read / Write / Edit / Bash`` — the SDK turns these into
  ``mcp__sandbox__*`` for the agent.
- Each handler forwards to ``AssessmentToolExecutor.dispatch`` with the
  expected ``(tool_name, input)`` pair (we do NOT re-implement path
  sanitization here; the executor owns it).
- Successful executor results map to ``{"content": [{"type": "text",
  "text": ...}]}`` and errors map to the same shape with
  ``"is_error": True`` so the SDK lets Claude self-correct.
- Every tool ships a non-empty description (regression guard against an
  accidental deletion — the agent picks tools by reading them).

The executor is stubbed with :class:`unittest.mock.MagicMock` — these
tests never touch real E2B.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from mcp import types as mcp_types

from app.components.assessments.claude_tool_executor import (
    AssessmentToolExecutor,
)
from app.components.integrations.claude_agent.sandbox_tools import (
    build_sandbox_mcp_server,
)


# ---------------------------------------------------------------------
# Helpers — drive the SDK-built MCP server through its actual MCP
# request handlers so the assertions exercise the same code path the
# agent will at runtime, not some internal-only attribute that could
# silently change.
# ---------------------------------------------------------------------


def _make_executor(dispatch_result: Dict[str, Any]) -> MagicMock:
    executor = MagicMock(spec=AssessmentToolExecutor)
    executor.dispatch.return_value = dispatch_result
    return executor


def _list_tools(server: Dict[str, Any]) -> List[mcp_types.Tool]:
    """Drive the server's ``ListToolsRequest`` handler and return tools."""
    handler = server["instance"].request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list", params=None)
    result = asyncio.run(handler(request))
    return list(result.root.tools)


def _call_tool(
    server: Dict[str, Any], name: str, arguments: Dict[str, Any]
) -> mcp_types.CallToolResult:
    """Drive the server's ``CallToolRequest`` handler for one tool call."""
    handler = server["instance"].request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = asyncio.run(handler(request))
    return result.root


def _text_blocks(result: mcp_types.CallToolResult) -> List[str]:
    return [block.text for block in result.content if block.type == "text"]


# ---------------------------------------------------------------------
# Server-level guarantees
# ---------------------------------------------------------------------


def test_server_name_is_sandbox() -> None:
    """The SDK prefixes tool names with the server name. ``sandbox``
    means the agent sees ``mcp__sandbox__Read`` etc. — changing this
    silently breaks any ``allowed_tools=[...]`` whitelist downstream.
    """
    server = build_sandbox_mcp_server(_make_executor({"ok": True, "result": ""}))
    assert server["type"] == "sdk"
    assert server["name"] == "sandbox"


def test_registers_exactly_four_tools() -> None:
    server = build_sandbox_mcp_server(_make_executor({"ok": True, "result": ""}))
    names = sorted(tool.name for tool in _list_tools(server))
    assert names == ["Bash", "Edit", "Read", "Write"]


def test_every_tool_has_a_non_empty_description() -> None:
    """Regression guard: the agent picks tools by reading these. If
    someone deletes a description (or the docstring) the model loses
    its picking signal — fail loudly here instead of in production.
    """
    server = build_sandbox_mcp_server(_make_executor({"ok": True, "result": ""}))
    for tool in _list_tools(server):
        assert tool.description, f"{tool.name} is missing a description"
        # Sanity floor — Anthropic's stock tool descriptions are dozens
        # of chars; anything under 40 is almost certainly a stub.
        assert len(tool.description) >= 40, (
            f"{tool.name} description suspiciously short: {tool.description!r}"
        )


# ---------------------------------------------------------------------
# Per-tool happy path — handler -> executor.dispatch -> text block
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name, arguments, expected_dispatch",
    [
        (
            "Read",
            {"path": "src/foo.py"},
            ("read_file", {"path": "src/foo.py"}),
        ),
        (
            "Write",
            {"path": "src/foo.py", "content": "print('hi')\n"},
            ("write_file", {"path": "src/foo.py", "content": "print('hi')\n"}),
        ),
        (
            "Edit",
            {"path": "src/foo.py", "old": "hi", "new": "hello"},
            (
                "apply_edit",
                {"path": "src/foo.py", "old": "hi", "new": "hello"},
            ),
        ),
        (
            "Bash",
            {"command": "pytest -q"},
            ("run_command", {"command": "pytest -q"}),
        ),
    ],
)
def test_each_tool_forwards_to_executor_dispatch(
    tool_name: str,
    arguments: Dict[str, Any],
    expected_dispatch: tuple,
) -> None:
    """Every tool must route through ``executor.dispatch`` — this is the
    point of leaf A. The executor already enforces path rules, output
    truncation, and 10s timeouts. Re-implementing any of that here
    would silently fork the rules.
    """
    executor = _make_executor({"ok": True, "result": "OK"})
    server = build_sandbox_mcp_server(executor)

    result = _call_tool(server, tool_name, arguments)

    executor.dispatch.assert_called_once_with(*expected_dispatch)
    assert result.isError is False
    assert _text_blocks(result) == ["OK"]


def test_bash_stringifies_dict_result() -> None:
    """``run_command`` returns a ``{stdout, stderr, exit_code}`` dict.
    The MCP content block is text-only, so we ``str()`` it for the
    model — Claude parses it back fluently.
    """
    payload = {"stdout": "hello\n", "stderr": "", "exit_code": 0}
    executor = _make_executor({"ok": True, "result": payload})
    server = build_sandbox_mcp_server(executor)

    result = _call_tool(server, "Bash", {"command": "echo hello"})

    assert result.isError is False
    [text] = _text_blocks(result)
    assert "hello" in text
    assert "exit_code" in text


# ---------------------------------------------------------------------
# Per-tool error path — executor returns ok=False -> is_error=True
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name, arguments, error_msg",
    [
        ("Read", {"path": "../etc/passwd"}, "invalid_path: ..."),
        ("Write", {"path": "/abs", "content": "x"}, "invalid_path: ..."),
        ("Edit", {"path": "f.py", "old": "x", "new": "y"}, "no_match"),
        ("Bash", {"command": "false"}, "run_failed: CalledProcessError: 1"),
    ],
)
def test_each_tool_propagates_executor_error_as_is_error(
    tool_name: str,
    arguments: Dict[str, Any],
    error_msg: str,
) -> None:
    """``ok=False`` from the executor must become ``is_error=True`` on
    the SDK side so Claude treats it as a failed call and self-corrects
    next turn — not as normal tool output.
    """
    executor = _make_executor({"ok": False, "error": error_msg})
    server = build_sandbox_mcp_server(executor)

    result = _call_tool(server, tool_name, arguments)

    assert result.isError is True
    assert _text_blocks(result) == [error_msg]


def test_error_without_error_key_falls_back_to_unknown() -> None:
    """Defensive: the executor's contract guarantees an ``error`` key
    when ``ok=False``, but a malformed payload shouldn't crash the
    handler — we surface ``unknown_error`` so Claude still sees a
    self-correcting signal.
    """
    executor = _make_executor({"ok": False})
    server = build_sandbox_mcp_server(executor)

    result = _call_tool(server, "Read", {"path": "f.py"})

    assert result.isError is True
    [text] = _text_blocks(result)
    assert text == "unknown_error"


# ---------------------------------------------------------------------
# Executor reuse — no re-implementation
# ---------------------------------------------------------------------


def test_handlers_do_not_touch_e2b_directly() -> None:
    """If a future change tried to reach into ``executor._sandbox`` or
    ``executor._e2b`` directly (bypassing dispatch and its sanitizer),
    the executor mock would record the attribute access. Assert nothing
    but ``dispatch`` is called.
    """
    executor = _make_executor({"ok": True, "result": "ok"})
    server = build_sandbox_mcp_server(executor)

    _call_tool(server, "Read", {"path": "a"})
    _call_tool(server, "Write", {"path": "a", "content": "b"})
    _call_tool(server, "Edit", {"path": "a", "old": "b", "new": "c"})
    _call_tool(server, "Bash", {"command": "ls"})

    # Every recorded call on the mock must be ``dispatch(...)``. Any
    # other attribute access (``executor._sandbox.files.read``, etc.)
    # would show up in ``method_calls``.
    method_names = {call[0] for call in executor.method_calls}
    assert method_names == {"dispatch"}, (
        f"sandbox tools must only call executor.dispatch; saw: {method_names}"
    )
    assert executor.dispatch.call_count == 4
