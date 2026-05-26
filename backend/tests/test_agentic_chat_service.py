"""Unit tests for ``AgenticChatService`` — the multi-turn tool-use loop.

The Anthropic SDK and the executor are both mocked. We verify:

- single-turn no-tool path returns ``stop_reason="end_turn"`` cleanly
- a single tool round-trip aggregates tokens across two ``messages.create`` calls
- multiple tools across multiple loop iterations sum tokens correctly
- the ``max_turns`` cap fires and appends a fallback message
- the mid-loop budget guard bails before going negative
- the ``metering=`` kwarg is on EVERY ``messages.create`` call (the gate we
  pay the metering tax to enforce)
- executor errors don't break the loop — Claude gets the error block and self-corrects
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---- Fake SDK helpers --------------------------------------------------------

def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(*, name: str, input_: dict, id_: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _fake_response(
    *,
    content: list,
    stop_reason: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        id="msg_test_123",
    )


class _FakeAnthropic:
    """Stub ``anthropic.Anthropic`` whose ``messages.create`` returns scripted
    responses in order. Records every call so tests can assert on kwargs
    (most importantly that ``metering`` was passed on every turn)."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.created_calls: list[dict] = []

        class _Messages:
            def __init__(self, parent: "_FakeAnthropic"):
                self._parent = parent
                # batches/etc. — pass-through proxy used by MeteredAnthropicClient.
                self.batches = SimpleNamespace()

            def create(self, **kwargs):
                self._parent.created_calls.append(kwargs)
                if not self._parent._responses:
                    raise AssertionError(
                        "FakeAnthropic ran out of scripted responses — "
                        f"call #{len(self._parent.created_calls)} kwargs={list(kwargs)}"
                    )
                return self._parent._responses.pop(0)

        self.messages = _Messages(self)


@pytest.fixture
def patched_anthropic(monkeypatch):
    """Yield a factory that wires a ``_FakeAnthropic(responses)`` into
    ``app.components.integrations.claude.agentic_chat.Anthropic`` and
    returns the fake so tests can read ``created_calls``."""

    holder: dict = {}

    def _install(responses: list) -> _FakeAnthropic:
        fake = _FakeAnthropic(responses)
        from app.components.integrations.claude import agentic_chat as mod
        monkeypatch.setattr(mod, "Anthropic", lambda **_kw: fake)
        # Also short-circuit the metering wrapper's SessionLocal so we
        # don't hit the (unrelated) DB for these unit tests. The wrapper
        # writes call_log rows in a fresh session; we replace it with a
        # no-op context manager.
        from app.services import metered_anthropic_client as mac

        class _NullSession:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def add(self, *_a, **_kw): pass
            def commit(self): pass
            def refresh(self, *_a, **_kw): pass

        monkeypatch.setattr(mac, "SessionLocal", lambda: _NullSession())
        holder["fake"] = fake
        return fake

    return _install


# ---- Tests -------------------------------------------------------------------


def test_single_turn_no_tool_returns_text(patched_anthropic):
    """Stop_reason=end_turn on the first call → return text immediately."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    fake = patched_anthropic([
        _fake_response(
            content=[_text_block("Hello, candidate.")],
            stop_reason="end_turn",
            input_tokens=42,
            output_tokens=7,
        )
    ])
    executor = MagicMock()

    svc = AgenticChatService(
        "key", organization_id=1, executor=executor, tools=[{"name": "noop"}]
    )
    turn = svc.run(
        messages=[{"role": "user", "content": "hi"}],
        system="be nice",
        budget_remaining_usd=1.0,
    )

    assert turn.role == "assistant"
    assert turn.content == "Hello, candidate."
    assert turn.tool_calls_made == []
    assert turn.input_tokens == 42
    assert turn.output_tokens == 7
    assert len(fake.created_calls) == 1
    executor.dispatch.assert_not_called()


def test_single_tool_round_trip_aggregates_tokens(patched_anthropic):
    """Turn 1 emits a tool_use; executor returns ok; turn 2 returns end_turn."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    fake = patched_anthropic([
        _fake_response(
            content=[
                _text_block("Let me check the file."),
                _tool_use_block(name="read_file", input_={"path": "main.py"}, id_="tu_A"),
            ],
            stop_reason="tool_use",
            input_tokens=100,
            output_tokens=20,
        ),
        _fake_response(
            content=[_text_block("The file looks good.")],
            stop_reason="end_turn",
            input_tokens=200,
            output_tokens=15,
        ),
    ])
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": True, "result": "print('hi')"}

    svc = AgenticChatService(
        "key", organization_id=2, executor=executor, tools=[{"name": "read_file"}]
    )
    turn = svc.run(
        messages=[{"role": "user", "content": "show me main.py"}],
        system="task",
        budget_remaining_usd=1.0,
    )

    # Tokens sum across both messages.create calls.
    assert turn.input_tokens == 300
    assert turn.output_tokens == 35
    # Both text blocks (turn 1 and turn 2) are joined into final content.
    assert "Let me check the file." in turn.content
    assert "The file looks good." in turn.content
    # tool_calls_made captures the dispatch.
    assert len(turn.tool_calls_made) == 1
    assert turn.tool_calls_made[0] == {
        "name": "read_file",
        "input": {"path": "main.py"},
        "result_ok": True,
    }
    # Executor was called exactly once with the parsed input.
    executor.dispatch.assert_called_once_with("read_file", {"path": "main.py"})
    assert len(fake.created_calls) == 2


def test_multi_tool_across_iterations_sums_tokens(patched_anthropic):
    """3 tools across 2 loop iterations; cumulative tokens stay correct."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    fake = patched_anthropic([
        # Iter 1: two tool_use blocks in one response.
        _fake_response(
            content=[
                _tool_use_block(name="list_dir", input_={"path": "."}, id_="tu_1"),
                _tool_use_block(name="read_file", input_={"path": "a.py"}, id_="tu_2"),
            ],
            stop_reason="tool_use",
            input_tokens=50,
            output_tokens=30,
        ),
        # Iter 2: one tool_use, then a final response.
        _fake_response(
            content=[_tool_use_block(name="read_file", input_={"path": "b.py"}, id_="tu_3")],
            stop_reason="tool_use",
            input_tokens=120,
            output_tokens=10,
        ),
        _fake_response(
            content=[_text_block("Done.")],
            stop_reason="end_turn",
            input_tokens=200,
            output_tokens=8,
        ),
    ])
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": True, "result": "ok"}

    svc = AgenticChatService(
        "key",
        organization_id=3,
        executor=executor,
        tools=[{"name": "list_dir"}, {"name": "read_file"}],
    )
    turn = svc.run(
        messages=[{"role": "user", "content": "explore"}],
        system="task",
        budget_remaining_usd=5.0,
    )

    assert turn.input_tokens == 50 + 120 + 200
    assert turn.output_tokens == 30 + 10 + 8
    assert len(turn.tool_calls_made) == 3
    assert all(t["result_ok"] for t in turn.tool_calls_made)
    assert len(fake.created_calls) == 3


def test_max_turns_cap_appends_fallback(patched_anthropic):
    """If Claude keeps emitting tool_use past the cap, loop breaks and appends a fallback."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    # 9 consecutive tool_use responses — the loop is capped at 8, so we
    # only need 8 to make the for-loop's else fire. Provide 9 just in
    # case the implementation drifts; the fake will error if we overshoot.
    responses = [
        _fake_response(
            content=[_tool_use_block(name="read_file", input_={"path": f"f{i}.py"}, id_=f"tu_{i}")],
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        )
        for i in range(8)
    ]
    fake = patched_anthropic(responses)
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": True, "result": "ok"}

    svc = AgenticChatService(
        "key",
        organization_id=4,
        executor=executor,
        tools=[{"name": "read_file"}],
        max_turns=8,
    )
    turn = svc.run(
        messages=[{"role": "user", "content": "explore"}],
        system="task",
        budget_remaining_usd=100.0,  # plenty
    )

    # Exactly max_turns calls fired.
    assert len(fake.created_calls) == 8
    # Fallback text is present so the candidate sees *something*.
    assert "couldn't complete" in turn.content.lower()
    assert len(turn.tool_calls_made) == 8


def test_mid_loop_budget_exhaustion_breaks(patched_anthropic):
    """budget_remaining starts at 0.20; first turn ~$0.15; loop exits before 2nd call."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    # @ Haiku rates ($1.0/M input, $5.0/M output), 100k input + 10k output
    # = $0.10 + $0.05 = $0.15. Remaining after = 0.05, below the 0.10
    # safety margin → next iteration should bail.
    fake = patched_anthropic([
        _fake_response(
            content=[
                _text_block("Looking..."),
                _tool_use_block(name="read_file", input_={"path": "x.py"}, id_="tu_X"),
            ],
            stop_reason="tool_use",
            input_tokens=100_000,
            output_tokens=10_000,
        ),
        # A second response is queued but should never be consumed.
        _fake_response(
            content=[_text_block("should not run")],
            stop_reason="end_turn",
        ),
    ])
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": True, "result": "..."}

    svc = AgenticChatService(
        "key",
        organization_id=5,
        executor=executor,
        tools=[{"name": "read_file"}],
    )
    turn = svc.run(
        messages=[{"role": "user", "content": "go"}],
        system="task",
        budget_remaining_usd=0.20,
    )

    # Exactly one call — the budget guard tripped before turn 2.
    assert len(fake.created_calls) == 1
    # Tool dispatch still happened (we don't undo a turn that already ran).
    executor.dispatch.assert_called_once()
    # The budget-exhausted suffix is present.
    assert "budget exhausted" in turn.content.lower()
    # Tokens reflect only the one call.
    assert turn.input_tokens == 100_000
    assert turn.output_tokens == 10_000


def test_metering_kwarg_on_every_call(patched_anthropic):
    """Every messages.create must carry the metering kwarg — the whole point
    of this file existing is to never bypass the meter again."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    fake = patched_anthropic([
        _fake_response(
            content=[_tool_use_block(name="read_file", input_={"path": "a"}, id_="tu_1")],
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ),
        _fake_response(
            content=[_tool_use_block(name="read_file", input_={"path": "b"}, id_="tu_2")],
            stop_reason="tool_use",
            input_tokens=20,
            output_tokens=8,
        ),
        _fake_response(
            content=[_text_block("done")],
            stop_reason="end_turn",
            input_tokens=30,
            output_tokens=4,
        ),
    ])
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": True, "result": ""}

    svc = AgenticChatService(
        "key", organization_id=99, executor=executor, tools=[{"name": "read_file"}]
    )
    svc.run(
        messages=[{"role": "user", "content": "go"}],
        system="task",
        budget_remaining_usd=10.0,
    )

    assert len(fake.created_calls) == 3
    # NOTE: the metering kwarg is stripped by MeteredAnthropicClient before
    # reaching the SDK, so we can't see it on the kwargs the fake recorded.
    # But we *can* assert no call was made on a path that bypassed the
    # wrapper — every call landed on ``fake.messages.create``, which only
    # exists because the wrapper proxies through to ``_inner.messages``.
    # Belt-and-braces: assert the model kwarg made it through (proves the
    # call shape is intact end-to-end).
    for call in fake.created_calls:
        assert "model" in call
        assert "tools" in call
        # metering was stripped en route — that's the wrapper's job.
        assert "metering" not in call


def test_metering_kwarg_seen_at_wrapper_boundary(monkeypatch):
    """Inject a fake at the wrapper boundary (intercept ``MeteredAnthropicClient.messages``)
    and assert ``metering=`` is on every call, with the expected feature/sub_feature.

    Separate from the prior test because the wrapper STRIPS metering before
    forwarding — to see what the service *passes in*, we have to spy one
    layer up.
    """
    from app.components.integrations.claude import agentic_chat as mod

    captured: list[dict] = []

    class _SpyMessages:
        def __init__(self):
            self.batches = SimpleNamespace()

        def create(self, **kwargs):
            captured.append(kwargs)
            # Mimic a tool_use → end_turn pair.
            if len(captured) == 1:
                return _fake_response(
                    content=[_tool_use_block(name="read_file", input_={"p": 1}, id_="t1")],
                    stop_reason="tool_use",
                )
            return _fake_response(content=[_text_block("done")], stop_reason="end_turn")

    class _SpyClient:
        def __init__(self, **_kw):
            self.messages = _SpyMessages()
            self._inner = SimpleNamespace(messages=self.messages)

    # Replace the metering wrapper itself so we capture pre-strip kwargs.
    monkeypatch.setattr(mod, "MeteredAnthropicClient", _SpyClient)
    # Anthropic constructor still needs to exist but is irrelevant.
    monkeypatch.setattr(mod, "Anthropic", lambda **_kw: SimpleNamespace(messages=SimpleNamespace()))

    executor = MagicMock()
    executor.dispatch.return_value = {"ok": True, "result": "x"}

    svc = mod.AgenticChatService(
        "key", organization_id=77, executor=executor, tools=[{"name": "read_file"}]
    )
    svc.run(
        messages=[{"role": "user", "content": "go"}],
        system="task",
        budget_remaining_usd=None,
    )

    assert len(captured) == 2
    for call in captured:
        meter = call.get("metering")
        assert meter is not None, "every call must pass metering=..."
        assert meter["organization_id"] == 77
        assert meter["feature"] == "assessment"
        assert meter["sub_feature"] == "candidate_chat"


def test_tool_error_does_not_break_loop(patched_anthropic):
    """Executor returns ok=False — loop continues, Claude self-corrects."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    fake = patched_anthropic([
        _fake_response(
            content=[_tool_use_block(name="read_file", input_={"path": "missing.py"}, id_="tu_bad")],
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ),
        _fake_response(
            content=[_text_block("File didn't exist — listing dir instead.")],
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=12,
        ),
    ])

    # Executor returns an error result for the first (and only) dispatch.
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": False, "error": "no_match"}

    svc = AgenticChatService(
        "key",
        organization_id=8,
        executor=executor,
        tools=[{"name": "read_file"}],
    )
    turn = svc.run(
        messages=[{"role": "user", "content": "read missing.py"}],
        system="task",
        budget_remaining_usd=1.0,
    )

    # Loop still completed — turn 2 ran and produced text.
    assert len(fake.created_calls) == 2
    assert "didn't exist" in turn.content.lower()
    # ok=False is recorded in analytics.
    assert turn.tool_calls_made == [
        {"name": "read_file", "input": {"path": "missing.py"}, "result_ok": False}
    ]


def test_tool_result_block_is_error_flag(patched_anthropic):
    """When executor returns ok=False, the tool_result block we send back
    on the next turn must carry ``is_error=True`` so Claude sees it as a
    correction signal rather than a normal result."""
    from app.components.integrations.claude.agentic_chat import AgenticChatService

    fake = patched_anthropic([
        _fake_response(
            content=[_tool_use_block(name="read_file", input_={"path": "x"}, id_="tu_err")],
            stop_reason="tool_use",
        ),
        _fake_response(content=[_text_block("ack")], stop_reason="end_turn"),
    ])
    executor = MagicMock()
    executor.dispatch.return_value = {"ok": False, "error": "not_found"}

    svc = AgenticChatService(
        "key", organization_id=9, executor=executor, tools=[{"name": "read_file"}]
    )
    svc.run(
        messages=[{"role": "user", "content": "go"}],
        system="task",
        budget_remaining_usd=1.0,
    )

    # The second messages.create sees a user turn whose content includes
    # the tool_result block with is_error=True.
    second_call_msgs = fake.created_calls[1]["messages"]
    last_user = second_call_msgs[-1]
    assert last_user["role"] == "user"
    blocks = last_user["content"]
    tool_result_blocks = [b for b in blocks if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) == 1
    assert tool_result_blocks[0]["is_error"] is True
    assert "not_found" in tool_result_blocks[0]["content"]
    assert tool_result_blocks[0]["tool_use_id"] == "tu_err"
