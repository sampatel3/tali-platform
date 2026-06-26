"""Unit tests for ``AgentSDKChatService``.

The ``claude_agent_sdk.query()`` async generator and the
``write_aggregated_usage_event`` writer are both mocked at the
service-module's import boundary. No subprocess is spawned; no
DB is touched.

Coverage:

1. happy path — text-only response, UsageEvent written
2. tool-call path — ``ToolUseBlock`` captured into ``tool_calls_made``
3. SDK error — ``ResultMessage(is_error=True)`` → ``success=False`` but
   UsageEvent still written
4. no ResultMessage — defensive return, no UsageEvent
5. budget pre-empt — SDK never called, no UsageEvent
6. history seeding — prior messages land in ``system_prompt``; latest
   user message is the ``prompt=`` arg
7. options shape — ``tools=[]``, ``setting_sources=[]``, MCP server +
   allowed_tools wired, ``max_budget_usd`` capped
8. ``write_aggregated_usage_event`` smoke — direct call hits SessionLocal

All async tests use ``asyncio.run`` rather than ``pytest-asyncio`` to
avoid an extra plugin dependency for the suite.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---- Fake SDK classes --------------------------------------------------------
#
# These mirror the real ``claude_agent_sdk`` dataclass shapes the service
# uses. We could ``import`` the real ones, but stubbing avoids the
# package becoming a hard test-time dependency and keeps every assertion
# fully under the test's control.


@dataclass
class _FakeTextBlock:
    text: str


@dataclass
class _FakeToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class _FakeAssistantMessage:
    content: list


@dataclass
class _FakeToolResultBlock:
    tool_use_id: str
    content: Any = None
    is_error: Optional[bool] = None


@dataclass
class _FakeUserMessage:
    content: Any  # str | list[block]


@dataclass
class _FakeResultMessage:
    subtype: str = "success"
    duration_ms: int = 100
    duration_api_ms: int = 80
    is_error: bool = False
    num_turns: int = 1
    session_id: str = "sess_test"
    stop_reason: Optional[str] = "end_turn"
    total_cost_usd: Optional[float] = 0.0
    usage: Optional[dict] = None
    result: Optional[str] = None


@dataclass
class _CapturedOptions:
    """Mirror of ``ClaudeAgentOptions`` for inspection in tests."""

    model: Optional[str] = None
    system_prompt: Optional[str] = None
    mcp_servers: dict = field(default_factory=dict)
    allowed_tools: list = field(default_factory=list)
    tools: Optional[list] = None
    setting_sources: Optional[list] = None
    permission_mode: Optional[str] = None
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    env: dict = field(default_factory=dict)


# ---- Test fixtures -----------------------------------------------------------


@pytest.fixture
def patched_sdk():
    """Patch ``claude_agent_sdk`` symbols on the service module.

    Returns a holder dict the test populates with the response stream
    BEFORE invoking the service, plus capture buffers the assertions
    read afterwards.
    """
    holder: dict[str, Any] = {
        "messages_to_yield": [],
        "prompt_received": None,
        "options_received": None,
        "query_calls": 0,
    }

    async def fake_query(*, prompt, options, transport=None):
        holder["prompt_received"] = prompt
        holder["options_received"] = options
        holder["query_calls"] += 1
        for msg in holder["messages_to_yield"]:
            yield msg

    # The service does ``from claude_agent_sdk import ...`` inside ``run()``,
    # so we patch the real module's attributes. The real claude_agent_sdk
    # is installed in this venv (see ``pip show``).
    import claude_agent_sdk

    with patch.object(claude_agent_sdk, "query", fake_query), \
         patch.object(claude_agent_sdk, "AssistantMessage", _FakeAssistantMessage), \
         patch.object(claude_agent_sdk, "ResultMessage", _FakeResultMessage), \
         patch.object(claude_agent_sdk, "TextBlock", _FakeTextBlock), \
         patch.object(claude_agent_sdk, "ToolUseBlock", _FakeToolUseBlock), \
         patch.object(claude_agent_sdk, "ToolResultBlock", _FakeToolResultBlock), \
         patch.object(claude_agent_sdk, "UserMessage", _FakeUserMessage), \
         patch.object(
             claude_agent_sdk,
             "ClaudeAgentOptions",
             lambda **kw: _CapturedOptions(**{k: v for k, v in kw.items() if k in _CapturedOptions.__dataclass_fields__}),
         ):
        yield holder


@pytest.fixture
def patched_meter(monkeypatch):
    """Replace ``write_aggregated_usage_event`` with a MagicMock that
    records every call. Returned so tests can assert on call args."""
    mock = MagicMock()
    from app.components.integrations.claude_agent import service as svc_mod
    monkeypatch.setattr(svc_mod, "write_aggregated_usage_event", mock)
    return mock


def _build_service(_executor=None):
    """Construct an ``AgentSDKChatService`` with a no-op MCP factory."""
    from app.components.integrations.claude_agent.service import AgentSDKChatService

    executor = _executor if _executor is not None else MagicMock(name="executor")
    factory = MagicMock(name="mcp_factory", return_value={"type": "fake_mcp"})
    svc = AgentSDKChatService(
        api_key="sk-test",
        organization_id=42,
        assessment_id=7,
        executor=executor,
        max_turns=8,
        _mcp_server_factory=factory,
    )
    return svc, factory


# ---- 1. Happy path -----------------------------------------------------------


def test_happy_path_writes_usage_event_and_returns_text(patched_sdk, patched_meter):
    """Single text block + ResultMessage → success=True, tokens captured,
    one UsageEvent written."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[_FakeTextBlock("Sure, here's the fix")]),
        _FakeResultMessage(
            usage={
                "input_tokens": 1200,
                "output_tokens": 250,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            total_cost_usd=0.015,
            is_error=False,
            num_turns=1,
            stop_reason="end_turn",
        ),
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "fix the bug"}],
        system="be helpful",
        budget_remaining_usd=0.5,
    ))

    assert turn.success is True
    assert turn.content == "Sure, here's the fix"
    assert turn.tool_calls_made == []
    assert turn.input_tokens == 1200
    assert turn.output_tokens == 250
    assert turn.cache_read_input_tokens == 0
    assert turn.cache_creation_input_tokens == 0
    assert turn.total_cost_usd == pytest.approx(0.015)
    assert turn.num_turns == 1
    assert turn.stop_reason == "end_turn"

    # One aggregated UsageEvent written with the right args.
    patched_meter.assert_called_once()
    kwargs = patched_meter.call_args.kwargs
    assert kwargs["organization_id"] == 42
    assert kwargs["assessment_id"] == 7
    assert kwargs["feature"] == "assessment"
    assert kwargs["sub_feature"] == "agent_sdk_chat"
    assert kwargs["input_tokens"] == 1200
    assert kwargs["output_tokens"] == 250
    assert kwargs["total_cost_usd"] == pytest.approx(0.015)
    assert kwargs["num_turns"] == 1


# ---- 2. Tool-call path -------------------------------------------------------


def test_tool_call_path_captures_tool_uses(patched_sdk, patched_meter):
    """ToolUseBlock → tool_calls_made entry; later TextBlock → content."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[
            _FakeToolUseBlock(id="tu_1", name="mcp__sandbox__Read", input={"path": "x.py"}),
        ]),
        _FakeAssistantMessage(content=[_FakeTextBlock("Found it")]),
        _FakeResultMessage(
            usage={"input_tokens": 200, "output_tokens": 50, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.002,
            is_error=False,
            num_turns=2,
            stop_reason="end_turn",
        ),
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "read x.py"}],
        system="task",
        budget_remaining_usd=1.0,
    ))

    assert turn.success is True
    assert turn.content == "Found it"
    assert turn.tool_calls_made == [
        {"name": "mcp__sandbox__Read", "input": {"path": "x.py"}}
    ]
    assert turn.num_turns == 2


# ---- 2b. Tool RESULTS correlated onto calls (process-visible grading) --------


def test_stringify_tool_result_variants():
    """The result flattener handles str / list-of-dicts / None and bounds
    the output so the ai_prompts JSON column can't balloon."""
    from app.components.integrations.claude_agent.service import (
        _MAX_TOOL_RESULT_CHARS,
        _stringify_tool_result,
    )

    assert _stringify_tool_result("2 failed, 7 passed") == "2 failed, 7 passed"
    assert _stringify_tool_result(None) == ""
    # SDK list form: [{"type": "text", "text": "..."}]
    assert _stringify_tool_result(
        [{"type": "text", "text": "line one"}, {"type": "text", "text": "line two"}]
    ) == "line one\nline two"
    # Bounded with a truncation marker.
    big = _stringify_tool_result("x" * (_MAX_TOOL_RESULT_CHARS + 500))
    assert big.endswith("... (truncated)")
    assert len(big) <= _MAX_TOOL_RESULT_CHARS + len("\n... (truncated)")


def test_tool_results_correlated_onto_calls(patched_sdk, patched_meter):
    """A ToolResultBlock arriving as a follow-up UserMessage is merged onto
    its originating call by tool_use_id, so scoring sees what the agent
    actually OBSERVED (Bash stdout, file contents), not just what it asked
    for. Results stream AFTER the tool-use block, mirroring the real SDK."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[
            _FakeToolUseBlock(id="tu_1", name="mcp__sandbox__Bash", input={"command": "pytest -q"}),
        ]),
        _FakeUserMessage(content=[
            _FakeToolResultBlock(tool_use_id="tu_1", content="2 failed, 7 passed", is_error=False),
        ]),
        _FakeAssistantMessage(content=[
            _FakeToolUseBlock(id="tu_2", name="mcp__sandbox__Read", input={"path": "dq/gate.py"}),
        ]),
        _FakeUserMessage(content=[
            # list form, plus an error flag to prove it threads through
            _FakeToolResultBlock(
                tool_use_id="tu_2",
                content=[{"type": "text", "text": "def promotion_gate(results): return {'passed': True}"}],
                is_error=True,
            ),
        ]),
        _FakeAssistantMessage(content=[_FakeTextBlock("The gate hardcodes passed=True.")]),
        _FakeResultMessage(
            usage={"input_tokens": 300, "output_tokens": 60, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.003,
            is_error=False,
            num_turns=3,
            stop_reason="end_turn",
        ),
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "why did the bad batch promote?"}],
        system="task",
        budget_remaining_usd=1.0,
    ))

    assert turn.success is True
    assert turn.content == "The gate hardcodes passed=True."
    assert len(turn.tool_calls_made) == 2

    bash_call, read_call = turn.tool_calls_made
    assert bash_call["name"] == "mcp__sandbox__Bash"
    assert bash_call["result"] == "2 failed, 7 passed"
    assert bash_call["is_error"] is False

    assert read_call["name"] == "mcp__sandbox__Read"
    assert "promotion_gate" in read_call["result"]  # list form flattened
    assert read_call["is_error"] is True


def test_tool_call_without_result_omits_result_keys(patched_sdk, patched_meter):
    """A tool call whose result never arrives (e.g. truncated at max_turns)
    keeps the legacy {name, input} shape — no empty result keys."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[
            _FakeToolUseBlock(id="tu_1", name="mcp__sandbox__Read", input={"path": "x.py"}),
        ]),
        # No UserMessage/ToolResultBlock follows.
        _FakeResultMessage(
            usage={"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.001,
            num_turns=1,
        ),
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "read x.py"}],
        system="task",
        budget_remaining_usd=1.0,
    ))

    assert turn.tool_calls_made == [
        {"name": "mcp__sandbox__Read", "input": {"path": "x.py"}}
    ]


# ---- 3. SDK error path -------------------------------------------------------


def test_sdk_error_returns_failure_but_still_writes_meter(patched_sdk, patched_meter):
    """``is_error=True`` → success=False, but the call cost money so we
    still write a UsageEvent."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[_FakeTextBlock("starting...")]),
        _FakeResultMessage(
            usage={"input_tokens": 500, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.0005,
            is_error=True,
            num_turns=1,
            stop_reason="error",
            subtype="error_during_execution",
        ),
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "trigger error"}],
        system="task",
        budget_remaining_usd=1.0,
    ))

    assert turn.success is False
    assert turn.input_tokens == 500
    # UsageEvent still written — the call already cost money.
    patched_meter.assert_called_once()


# ---- 3b. SDK soft-error recovery ---------------------------------------------


def test_max_turns_hit_returns_partial_text_as_success(patched_sdk, patched_meter):
    """When the SDK raises ``Reached maximum number of turns`` AFTER the
    model already produced useful text + tool calls, the service must
    surface that work as a successful turn instead of throwing it away.

    Regression for assessment 76 (2026-05-26): the model had explained
    the bug across two text blocks AND invoked Read 6 times, but the
    candidate saw "The chat service hit an error. Please retry." because
    the SDK's ``Reached maximum number of turns`` raise short-circuited
    our exception handler.
    """
    # Stream some legitimate text + tool calls, then have ``query``
    # raise mid-stream the same way the real SDK does.
    async def raising_query(*, prompt, options, transport=None):
        patched_sdk["prompt_received"] = prompt
        patched_sdk["options_received"] = options
        patched_sdk["query_calls"] += 1
        yield _FakeAssistantMessage(content=[
            _FakeTextBlock("The gate is hardcoded to return passed."),
            _FakeToolUseBlock(id="tool-1", name="mcp__sandbox__Read", input={"path": "dq/gate.py"}),
        ])
        yield _FakeAssistantMessage(content=[
            _FakeTextBlock("It ignores the severity input entirely."),
        ])
        raise Exception("Claude Code returned an error result: Reached maximum number of turns (6)")

    import claude_agent_sdk
    with patch.object(claude_agent_sdk, "query", raising_query):
        svc, _factory = _build_service()
        turn = asyncio.run(svc.run(
            messages=[{"role": "user", "content": "why is the gate broken?"}],
            system="task",
            budget_remaining_usd=1.0,
        ))

    assert turn.success is True, "max-turns hit with content should NOT be a failure"
    assert "The gate is hardcoded to return passed." in turn.content
    assert "ignores the severity input" in turn.content
    assert "tool budget" in turn.content.lower(), "should include the soft-recovery trailer"
    assert turn.stop_reason == "max_turns_soft"
    assert len(turn.tool_calls_made) == 1
    # No ResultMessage was emitted, so no meter write (Admin-API
    # reconciliation catches the spend).
    patched_meter.assert_not_called()


def test_hard_sdk_crash_with_partial_content_returns_partial_failure(patched_sdk, patched_meter):
    """A non-soft SDK crash with already-emitted content still returns
    the partial reply (with a hard-fail trailer) instead of the generic
    'please retry' message. Better signal than nothing."""
    async def crashing_query(*, prompt, options, transport=None):
        patched_sdk["query_calls"] += 1
        yield _FakeAssistantMessage(content=[_FakeTextBlock("I see the issue: ")])
        raise RuntimeError("transport process died unexpectedly")

    import claude_agent_sdk
    with patch.object(claude_agent_sdk, "query", crashing_query):
        svc, _factory = _build_service()
        turn = asyncio.run(svc.run(
            messages=[{"role": "user", "content": "diagnose"}],
            system="task",
            budget_remaining_usd=1.0,
        ))

    assert turn.success is False
    assert "I see the issue:" in turn.content
    assert "errored mid-response" in turn.content.lower()
    assert turn.stop_reason == "sdk_exception_partial"


def test_hard_sdk_crash_no_content_returns_generic_retry(patched_sdk, patched_meter):
    """A crash with no text yet falls back to the generic retry copy."""
    async def crashing_query(*, prompt, options, transport=None):
        patched_sdk["query_calls"] += 1
        if False:  # pragma: no cover — generator must be a real async gen
            yield None
        raise RuntimeError("CLI startup failed")

    import claude_agent_sdk
    with patch.object(claude_agent_sdk, "query", crashing_query):
        svc, _factory = _build_service()
        turn = asyncio.run(svc.run(
            messages=[{"role": "user", "content": "x"}],
            system="task",
            budget_remaining_usd=1.0,
        ))

    assert turn.success is False
    assert turn.content == "The chat service hit an error. Please retry in a moment."
    assert turn.stop_reason == "sdk_exception"


def test_max_turns_hit_with_tool_calls_but_no_text_returns_progress_message(
    patched_sdk, patched_meter,
):
    """When the model uses all turns on tool calls (e.g. multi-file
    "fix it" requests) and never emits text, the SOFT cap should still
    surface a successful turn — with a "I made N tool calls, retry
    tighter" message naming the tools the model invoked.

    Regression for assessment 77 retry (2026-05-26): model used 4 tool
    calls to inspect files for a "fix it" request, hit max_turns=4 with
    zero text content, candidate saw "The chat service hit an error.
    Please retry." instead of useful guidance.
    """
    async def raising_query(*, prompt, options, transport=None):
        patched_sdk["query_calls"] += 1
        yield _FakeAssistantMessage(content=[
            _FakeToolUseBlock(id="t1", name="mcp__sandbox__Read", input={"path": "dq/checks.py"}),
        ])
        yield _FakeAssistantMessage(content=[
            _FakeToolUseBlock(id="t2", name="mcp__sandbox__Read", input={"path": "dq/contract.py"}),
        ])
        raise Exception("Claude Code returned an error result: Reached maximum number of turns (4)")

    import claude_agent_sdk
    with patch.object(claude_agent_sdk, "query", raising_query):
        svc, _factory = _build_service()
        turn = asyncio.run(svc.run(
            messages=[{"role": "user", "content": "fix it"}],
            system="task",
            budget_remaining_usd=1.0,
        ))

    assert turn.success is True, "soft cap + tool calls should be a successful turn"
    assert turn.stop_reason == "max_turns_soft_no_text"
    assert "2 tool call" in turn.content
    # Tool summary mentions the actual files the model was investigating
    assert "Read(dq/checks.py)" in turn.content
    assert "Read(dq/contract.py)" in turn.content
    # Helpful retry guidance present
    assert "one file at a time" in turn.content
    assert len(turn.tool_calls_made) == 2
    patched_meter.assert_not_called()


# ---- 4. No ResultMessage -----------------------------------------------------


def test_no_result_message_returns_failure_and_no_meter(patched_sdk, patched_meter):
    """Pathological: SDK closes without a ResultMessage. We don't know
    the cost so we MUST NOT write a UsageEvent."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[_FakeTextBlock("hello")]),
        # No ResultMessage.
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "anything"}],
        system="task",
        budget_remaining_usd=1.0,
    ))

    assert turn.success is False
    assert turn.stop_reason == "no_result_message"
    patched_meter.assert_not_called()


# ---- 5. Budget pre-empt ------------------------------------------------------


def test_budget_pre_empt_skips_sdk_and_meter(patched_sdk, patched_meter):
    """``budget_remaining_usd < 0.05`` → no SDK call, no UsageEvent."""
    patched_sdk["messages_to_yield"] = [
        _FakeAssistantMessage(content=[_FakeTextBlock("should not be reached")]),
        _FakeResultMessage(usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
    ]

    svc, _factory = _build_service()
    turn = asyncio.run(svc.run(
        messages=[{"role": "user", "content": "hi"}],
        system="task",
        budget_remaining_usd=0.01,  # below floor
    ))

    assert turn.success is False
    assert "budget" in turn.content.lower()
    assert patched_sdk["query_calls"] == 0
    patched_meter.assert_not_called()


# ---- 6. History seeding ------------------------------------------------------


def test_history_seeding_into_system_prompt(patched_sdk, patched_meter):
    """Prior messages → ``<PRIOR_CONVERSATION>`` block in system prompt.
    Latest user message → ``prompt=`` arg."""
    patched_sdk["messages_to_yield"] = [
        _FakeResultMessage(
            usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.0001,
            is_error=False,
            num_turns=1,
            stop_reason="end_turn",
        ),
    ]

    svc, _factory = _build_service()
    asyncio.run(svc.run(
        messages=[
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "user", "content": "now"},
        ],
        system="be nice",
        budget_remaining_usd=1.0,
    ))

    assert patched_sdk["prompt_received"] == "now"
    sys_prompt = patched_sdk["options_received"].system_prompt
    assert "earlier" in sys_prompt
    assert "earlier reply" in sys_prompt
    assert "be nice" in sys_prompt
    # The history block label is present so reconciliation can grep it.
    assert "<PRIOR_CONVERSATION>" in sys_prompt
    assert "</PRIOR_CONVERSATION>" in sys_prompt


# ---- 7. Options built correctly ---------------------------------------------


def test_options_shape_locks_down_built_ins_and_settings(patched_sdk, patched_meter):
    """``tools=[]``, ``setting_sources=[]``, MCP server + allowed_tools
    wired, ``max_budget_usd`` capped at ``min(remaining, 1.0)``."""
    patched_sdk["messages_to_yield"] = [
        _FakeResultMessage(
            usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.0001,
            num_turns=1,
        ),
    ]

    svc, factory = _build_service()
    asyncio.run(svc.run(
        messages=[{"role": "user", "content": "hi"}],
        system="task",
        budget_remaining_usd=0.7,   # below the 1.0 ceiling
        max_budget_usd=1.0,
    ))

    opts = patched_sdk["options_received"]
    assert opts.tools == []
    assert opts.setting_sources == []
    assert opts.permission_mode == "bypassPermissions"
    assert opts.max_turns == 8
    # max_budget_usd capped at the lower of remaining and ceiling.
    assert opts.max_budget_usd == pytest.approx(0.7)
    # MCP server: key is "sandbox", value is whatever the factory returned.
    assert "sandbox" in opts.mcp_servers
    assert opts.mcp_servers["sandbox"] == {"type": "fake_mcp"}
    # Factory was called with the executor.
    factory.assert_called_once()
    # All four sandbox MCP tools are allowed.
    assert sorted(opts.allowed_tools) == sorted([
        "mcp__sandbox__Read",
        "mcp__sandbox__Write",
        "mcp__sandbox__Edit",
        "mcp__sandbox__Bash",
    ])
    # API key threaded into env so the spawned CLI authenticates.
    assert opts.env.get("ANTHROPIC_API_KEY") == "sk-test"


def test_max_budget_usd_capped_by_ceiling(patched_sdk, patched_meter):
    """When ``remaining`` > ``max_budget_usd``, options use the ceiling."""
    patched_sdk["messages_to_yield"] = [
        _FakeResultMessage(
            usage={"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            total_cost_usd=0.0001,
            num_turns=1,
        ),
    ]

    svc, _factory = _build_service()
    asyncio.run(svc.run(
        messages=[{"role": "user", "content": "hi"}],
        system="task",
        budget_remaining_usd=5.0,   # above ceiling
        max_budget_usd=1.0,
    ))

    opts = patched_sdk["options_received"]
    assert opts.max_budget_usd == pytest.approx(1.0)


# ---- 8. write_aggregated_usage_event smoke ----------------------------------


def test_write_aggregated_usage_event_creates_row_with_source_tag(monkeypatch):
    """Direct call to the writer → SessionLocal opened, UsageEvent added
    with ``source=claude_agent_sdk_aggregated`` in metadata."""
    from app.components.integrations.claude_agent import usage_reconciler as ur

    added_rows: list = []

    class _FakeSession:
        def __init__(self):
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add(self, row):
            added_rows.append(row)

        def commit(self):
            self.committed = True

        def refresh(self, row):
            row.id = 123

    # Patch SessionLocal *inside* the function's lazy-import scope. The
    # writer does ``from ...platform.database import SessionLocal`` inside
    # the function body, so we must patch the module it imports from.
    from app.platform import database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeSession())

    ur.write_aggregated_usage_event(
        db=None,
        organization_id=11,
        assessment_id=22,
        feature="assessment",
        sub_feature="agent_sdk_chat",
        model="claude-sonnet-4-5",
        input_tokens=1000,
        output_tokens=300,
        cache_read_input_tokens=500,
        cache_creation_input_tokens=0,
        total_cost_usd=0.025,
        num_turns=3,
    )

    assert len(added_rows) == 1
    row = added_rows[0]
    assert row.organization_id == 11
    # entity_id uses the namespaced ``assessment:{id}`` format (2026-06-01).
    assert row.entity_id == "assessment:22"
    assert row.feature == "assessment"
    assert row.model == "claude-sonnet-4-5"
    assert row.input_tokens == 1000
    assert row.output_tokens == 300
    assert row.cache_read_tokens == 500
    assert row.cache_creation_tokens == 0
    # SDK-reported cost wins over the local estimate (0.025 USD = 25_000 micro).
    assert row.cost_usd_micro == 25_000
    # Metadata carries the source tag and sub_feature.
    assert row.event_metadata["source"] == "claude_agent_sdk_aggregated"
    assert row.event_metadata["sub_feature"] == "agent_sdk_chat"
    assert row.event_metadata["assessment_id"] == 22
    assert row.event_metadata["num_turns"] == 3


def test_write_aggregated_usage_event_falls_back_to_estimate_when_sdk_cost_zero(monkeypatch):
    """SDK didn't report ``total_cost_usd`` → use our local estimate so
    the row isn't zero-valued."""
    from app.components.integrations.claude_agent import usage_reconciler as ur

    added_rows: list = []

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add(self, row):
            added_rows.append(row)

        def commit(self):
            pass

        def refresh(self, row):
            pass

    from app.platform import database as db_mod
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeSession())

    ur.write_aggregated_usage_event(
        db=None,
        organization_id=1,
        assessment_id=2,
        feature="assessment",
        sub_feature="agent_sdk_chat",
        model="claude-sonnet-4-5",
        input_tokens=1000,
        output_tokens=500,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        total_cost_usd=0.0,        # SDK didn't report a cost
        num_turns=1,
    )

    assert len(added_rows) == 1
    row = added_rows[0]
    # cost_usd_micro must be > 0 (we fell back to the local estimate).
    assert row.cost_usd_micro > 0
    # The fallback estimate is recorded in metadata for audit.
    assert row.event_metadata["estimated_cost_usd_micro"] > 0


# ---- 9. Default model resolution --------------------------------------------


def test_default_model_resolution_uses_sonnet_when_env_unset(monkeypatch):
    """When ``CLAUDE_CHAT_MODEL`` env var isn't explicitly set, the
    service defaults to Sonnet (not the legacy Haiku default in
    ``resolved_claude_chat_model``)."""
    monkeypatch.delenv("CLAUDE_CHAT_MODEL", raising=False)

    from app.components.integrations.claude_agent.service import (
        AgentSDKChatService,
        _DEFAULT_AGENT_SDK_MODEL,
    )

    svc = AgentSDKChatService(
        api_key="sk-test",
        organization_id=1,
        assessment_id=1,
        executor=MagicMock(),
        _mcp_server_factory=MagicMock(),
    )
    assert svc._model == _DEFAULT_AGENT_SDK_MODEL


def test_default_model_resolution_honours_env_override(monkeypatch):
    """An explicit ``CLAUDE_CHAT_MODEL`` env var wins over the
    module default."""
    monkeypatch.setenv("CLAUDE_CHAT_MODEL", "claude-haiku-4-5")

    from app.components.integrations.claude_agent.service import AgentSDKChatService

    svc = AgentSDKChatService(
        api_key="sk-test",
        organization_id=1,
        assessment_id=1,
        executor=MagicMock(),
        _mcp_server_factory=MagicMock(),
    )
    assert svc._model == "claude-haiku-4-5"


def test_explicit_model_kwarg_overrides_env_and_default(monkeypatch):
    """Caller-supplied ``model=`` wins over both env and the default."""
    monkeypatch.setenv("CLAUDE_CHAT_MODEL", "claude-haiku-4-5")

    from app.components.integrations.claude_agent.service import AgentSDKChatService

    svc = AgentSDKChatService(
        api_key="sk-test",
        organization_id=1,
        assessment_id=1,
        executor=MagicMock(),
        model="claude-opus-4-5",
        _mcp_server_factory=MagicMock(),
    )
    assert svc._model == "claude-opus-4-5"
