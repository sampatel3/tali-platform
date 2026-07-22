from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.components.ai_routing import estimate_anthropic_messages
from app.components.ai_routing.adapters.anthropic_messages import RoutedAnthropicClient
from app.components.ai_routing.contracts import TaskKey
from app.components.ai_routing.execution import RouteExecutionError, RoutingAttribution
from app.components.ai_routing.gateway import prepare_route
from app.models.ai_routing import AIRoutingAttempt


class _Rejected(Exception):
    status_code = 429


def _response():
    return SimpleNamespace(
        id="stream-message",
        model="claude-haiku-4-5-20251001",
        content=[],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=2,
            output_tokens=3,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class _Stream:
    def get_final_message(self):
        return _response()


class _Context:
    def __init__(self, *, enter_error: BaseException | None = None) -> None:
        self.enter_error = enter_error

    def __enter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return _Stream()

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False


class _Messages:
    def __init__(self, contexts: list[_Context]) -> None:
        self.contexts = contexts
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.contexts.pop(0)


class _Client:
    ai_routing_metered_transport = True
    ai_routing_sdk_max_retries = 0
    organization_id = 1

    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


def _execution():
    return prepare_route(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        request_estimate=estimate_anthropic_messages(messages=[], max_tokens=512),
        attribution=RoutingAttribution(organization_id=1),
        explicit_model_override="haiku",
        environ={},
    )


def _stream(routed: RoutedAnthropicClient):
    return routed.messages.stream(
        model=routed.execution.selected_model_id,
        max_tokens=512,
        messages=[],
        metering={"feature": "taali_chat"},
    )


def test_preacceptance_stream_rejection_is_known_zero_and_retryable(db):
    execution = _execution()
    messages = _Messages([_Context(enter_error=_Rejected()), _Context()])
    routed = RoutedAnthropicClient(_Client(messages), execution)

    with _stream(routed):
        pass
    execution.finish("succeeded")

    db.expire_all()
    attempts = db.scalars(
        select(AIRoutingAttempt)
        .where(AIRoutingAttempt.invocation_id == execution.invocation_id)
        .order_by(AIRoutingAttempt.ordinal)
    ).all()
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert attempts[0].usage_unknown is False
    assert attempts[0].cost_usd_micro == 0


def test_accepted_stream_cancellation_is_ambiguous_and_blocks_replay(db):
    execution = _execution()
    messages = _Messages([_Context(), _Context()])
    routed = RoutedAnthropicClient(_Client(messages), execution)

    with pytest.raises(GeneratorExit):
        with _stream(routed):
            raise GeneratorExit()
    with pytest.raises(RouteExecutionError, match="outcome-ambiguous"):
        with _stream(routed):
            pass
    execution.finish("failed")

    db.expire_all()
    attempts = db.scalars(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    ).all()
    assert len(messages.calls) == 1
    assert len(attempts) == 1
    assert attempts[0].status == "ambiguous"
    assert attempts[0].usage_unknown is True
