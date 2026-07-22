from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.components.ai_routing.adapters.anthropic_messages import (
    AnthropicRouteContractError,
    RoutedAnthropicClient,
)
from app.components.ai_routing.anthropic_estimation import estimate_anthropic_messages
from app.components.ai_routing.admission import ProviderAttemptAdmissionError
from app.components.ai_routing.contracts import TaskKey
from app.components.ai_routing.execution import (
    RouteExecutionError,
    RoutingAttribution,
)
from app.components.ai_routing.gateway import prepare_route
from app.models.ai_routing import AIRoutingAttempt, AIRoutingInvocation


@dataclass
class _Settings:
    AI_ROUTER_MODEL_OVERRIDES_JSON: str = ""
    resolved_claude_model: str = "claude-haiku-4-5-20251001"
    resolved_agent_autonomous_model: str = "claude-haiku-4-5-20251001"


def _response(
    *,
    request_id: str = "request-1",
    model: str = "claude-haiku-4-5-20251001",
):
    return SimpleNamespace(
        id=request_id,
        model=model,
        content=[],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=2,
            output_tokens=3,
            cache_read_input_tokens=4,
            cache_creation_input_tokens=5,
        ),
    )


class _Messages:
    def __init__(self, script=None) -> None:
        self.script = list(script or [_response()])
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.script.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Client:
    ai_routing_metered_transport = True
    ai_routing_sdk_max_retries = 0
    organization_id = 1

    def __init__(self, messages=None) -> None:
        self.messages = messages or _Messages()


def _execution(task: TaskKey, **kwargs):
    max_tokens = {
        TaskKey.SEARCH_RERANK: 256,
        TaskKey.SEARCH_PARSE: 512,
    }.get(task, 700)
    return prepare_route(
        task,
        request_estimate=estimate_anthropic_messages(
            messages=[],
            max_tokens=max_tokens,
        ),
        attribution=RoutingAttribution(
            organization_id=1,
            entity_id="test-entity",
        ),
        settings_obj=_Settings(),
        environ={},
        **kwargs,
    )


def test_sync_adapter_enforces_route_and_persists_content_free_attempt(db):
    execution = _execution(TaskKey.SEARCH_RERANK)
    inner = _Client()
    routed = RoutedAnthropicClient(inner, execution)

    response = routed.messages.create(
        model=execution.selected_model_id,
        max_tokens=256,
        messages=[{"role": "user", "content": "not persisted"}],
        metering={"feature": "cv_rerank", "metadata": {"round": 0}},
    )
    execution.finish("succeeded")

    sent = inner.messages.calls[0]
    route_metadata = sent["metering"]["metadata"]["ai_routing"]
    assert sent["model"] == "claude-haiku-4-5-20251001"
    assert sent["metering"]["trace_id"].endswith(":1")
    assert sent["metering"]["credit_reservation"]["external_ref"].startswith(
        "usage-hold:cv_rerank:"
    )
    assert route_metadata["invocation_id"] == execution.invocation_id
    assert route_metadata["task"] == TaskKey.SEARCH_RERANK.value
    assert response.id == "request-1"

    db.expire_all()
    invocation = db.get(AIRoutingInvocation, execution.invocation_id)
    attempts = db.scalars(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    ).all()
    assert invocation is not None and invocation.status == "succeeded"
    assert "messages" not in invocation.request_snapshot
    assert "content" not in invocation.decision_snapshot
    assert len(attempts) == 1
    assert attempts[0].status == "succeeded"
    assert attempts[0].usage_unknown is False
    # 2*Haiku input + 3*output + 4*cache-read + 5*cache-write.
    assert attempts[0].cost_usd_micro == 24


def test_adapter_rejects_model_escape_before_a_physical_attempt(db):
    execution = _execution(TaskKey.SEARCH_RERANK)
    inner = _Client()
    routed = RoutedAnthropicClient(inner, execution)

    with pytest.raises(AnthropicRouteContractError, match="caller model"):
        routed.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[],
            metering={"feature": "cv_rerank"},
        )
    execution.finish("failed")

    assert inner.messages.calls == []
    assert (
        db.scalars(
            select(AIRoutingAttempt).where(
                AIRoutingAttempt.invocation_id == execution.invocation_id
            )
        ).all()
        == []
    )


def test_adapter_enforces_task_request_shape_before_provider(db):
    parser_execution = _execution(TaskKey.SEARCH_PARSE)
    parser_inner = _Client()
    parser = RoutedAnthropicClient(parser_inner, parser_execution)

    with pytest.raises(AnthropicRouteContractError, match="non-empty tools"):
        parser.messages.create(
            model=parser_execution.selected_model_id,
            max_tokens=512,
            messages=[],
            metering={"feature": "search_parse"},
        )
    parser_execution.finish("failed")
    assert parser_inner.messages.calls == []

    grounding_execution = _execution(TaskKey.SEARCH_GROUNDING)
    grounding_inner = _Client()
    grounding = RoutedAnthropicClient(grounding_inner, grounding_execution)
    with pytest.raises(AnthropicRouteContractError, match="citations-enabled"):
        grounding.messages.create(
            model=grounding_execution.selected_model_id,
            max_tokens=700,
            messages=[{"role": "user", "content": "uncited CV"}],
            metering={"feature": "candidate_grounding"},
        )
    grounding_execution.finish("failed")
    assert grounding_inner.messages.calls == []


def test_adapter_rejects_parameter_escape_and_nonfinite_numbers(db):
    execution = _execution(TaskKey.SEARCH_RERANK)
    routed = RoutedAnthropicClient(_Client(), execution)
    base = {
        "model": execution.selected_model_id,
        "max_tokens": 256,
        "messages": [],
        "metering": {"feature": "cv_rerank"},
    }

    with pytest.raises(AnthropicRouteContractError, match="unsupported"):
        routed.messages.create(**base, service_tier="auto")
    with pytest.raises(AnthropicRouteContractError, match="finite"):
        routed.messages.create(**base, temperature=float("nan"))
    with pytest.raises(AnthropicRouteContractError, match="integer max_tokens"):
        routed.messages.create(**{**base, "max_tokens": True})
    execution.finish("failed")


def test_adapter_rejects_transport_with_hidden_sdk_retries(db):
    execution = _execution(TaskKey.SEARCH_RERANK)
    unsafe = _Client()
    unsafe.ai_routing_sdk_max_retries = 1

    with pytest.raises(AnthropicRouteContractError, match="max_retries=0"):
        RoutedAnthropicClient(unsafe, execution)

    execution.finish("failed")


def test_adapter_caps_provider_timeout_to_task_latency_slo(db):
    execution = _execution(TaskKey.SEARCH_RERANK)
    inner = _Client()
    routed = RoutedAnthropicClient(inner, execution)

    routed.messages.create(
        model=execution.selected_model_id,
        max_tokens=256,
        messages=[],
        timeout=90,
        metering={"feature": "cv_rerank"},
    )
    execution.finish("succeeded")

    assert 14.9 <= inner.messages.calls[0]["timeout"] <= 15.0


def test_adapter_rejects_missing_organization_before_attempt_or_provider(db):
    execution = prepare_route(
        TaskKey.SEARCH_RERANK,
        request_estimate=estimate_anthropic_messages(messages=[], max_tokens=256),
        attribution=RoutingAttribution(entity_id="unattributed"),
        settings_obj=_Settings(),
        environ={},
    )
    inner = _Client()
    inner.organization_id = None
    routed = RoutedAnthropicClient(inner, execution)

    with pytest.raises(ProviderAttemptAdmissionError, match="organization"):
        routed.messages.create(
            model=execution.selected_model_id,
            max_tokens=256,
            messages=[],
            metering={"feature": "cv_rerank"},
        )
    execution.finish("failed")

    assert inner.messages.calls == []
    assert (
        db.scalars(
            select(AIRoutingAttempt).where(
                AIRoutingAttempt.invocation_id == execution.invocation_id
            )
        ).all()
        == []
    )


def test_adapter_rejects_caller_supplied_reservation_before_attempt(db):
    execution = _execution(TaskKey.SEARCH_RERANK)
    inner = _Client()
    routed = RoutedAnthropicClient(inner, execution)

    with pytest.raises(ProviderAttemptAdmissionError, match="adapter-owned"):
        routed.messages.create(
            model=execution.selected_model_id,
            max_tokens=256,
            messages=[],
            metering={
                "feature": "cv_rerank",
                "credit_reservation": {
                    "organization_id": 1,
                    "feature": "taali_chat",
                    "amount": 100,
                    "external_ref": "usage-hold:test:wrong-feature",
                    "live": False,
                },
            },
        )
    execution.finish("failed")

    assert inner.messages.calls == []


def test_adapter_marks_failed_and_releases_hold_when_transport_never_started(
    db, monkeypatch
):
    execution = _execution(TaskKey.SEARCH_RERANK)
    inner = _Client()
    routed = RoutedAnthropicClient(inner, execution)
    releases: list[dict] = []
    monkeypatch.setattr(
        "app.components.ai_routing.admission.mark_provider_attempt_started",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "app.components.ai_routing.admission.release_provider_usage",
        lambda reservation, **kwargs: releases.append(
            {"reservation": reservation, **kwargs}
        ),
    )

    with pytest.raises(ProviderAttemptAdmissionError, match="marker failed"):
        routed.messages.create(
            model=execution.selected_model_id,
            max_tokens=256,
            messages=[],
            metering={"feature": "cv_rerank"},
        )
    execution.finish("failed")

    assert inner.messages.calls == []
    db.expire_all()
    attempt = db.scalar(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    )
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.usage_unknown is False
    assert attempt.cost_usd_micro == 0
    assert len(releases) == 1
    assert releases[0]["allow_started"] is True


def test_each_physical_attempt_gets_a_fresh_adapter_owned_reservation(db):
    execution = _execution(
        TaskKey.ROLE_CHAT_ORCHESTRATION,
        explicit_model_override="haiku",
    )
    inner = _Client(_Messages([_response(), _response(request_id="request-2")]))
    routed = RoutedAnthropicClient(inner, execution)
    metering = {"feature": "agent_chat"}

    routed.messages.create(
        model=execution.selected_model_id,
        max_tokens=256,
        messages=[],
        metering=metering,
    )
    routed.messages.create(
        model=execution.selected_model_id,
        max_tokens=256,
        messages=[],
        metering=metering,
    )
    execution.finish("succeeded")

    refs = [
        call["metering"]["credit_reservation"]["external_ref"]
        for call in inner.messages.calls
    ]
    assert len(refs) == 2
    assert len(set(refs)) == 2


class _Rejected(Exception):
    status_code = 429


def test_explicit_nonbillable_rejection_can_retry_within_route(db):
    execution = _execution(TaskKey.SEARCH_GROUNDING)
    messages = _Messages(
        [
            _Rejected("rate limited"),
            _response(request_id="request-2", model="claude-sonnet-4-6"),
        ]
    )
    routed = RoutedAnthropicClient(_Client(messages), execution)

    routed.messages.create(
        model=execution.selected_model_id,
        max_tokens=700,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "text", "data": "candidate"},
                        "citations": {"enabled": True},
                    }
                ],
            }
        ],
        metering={"feature": "candidate_grounding"},
    )
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
    assert attempts[1].fallback_from_deployment_id is None


def test_ambiguous_failure_blocks_replay_or_failover(db):
    execution = _execution(TaskKey.SEARCH_GROUNDING)
    messages = _Messages([TimeoutError("acceptance unknown"), _response()])
    routed = RoutedAnthropicClient(_Client(messages), execution)

    with pytest.raises(TimeoutError):
        routed.messages.create(
            model=execution.selected_model_id,
            max_tokens=700,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "text", "data": "candidate"},
                            "citations": {"enabled": True},
                        }
                    ],
                }
            ],
            metering={"feature": "candidate_grounding"},
        )
    with pytest.raises(RouteExecutionError, match="outcome-ambiguous"):
        routed.messages.create(
            model=execution.selected_model_id,
            max_tokens=700,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {"type": "text", "data": "candidate"},
                            "citations": {"enabled": True},
                        }
                    ],
                }
            ],
            metering={"feature": "candidate_grounding"},
        )
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


class _Stream:
    def __init__(self) -> None:
        self.final = _response()

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.final


class _StreamContext:
    def __init__(self, stream) -> None:
        self.stream = stream

    def __enter__(self):
        return self.stream

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _StreamingMessages(_Messages):
    def __init__(self) -> None:
        super().__init__([])

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _StreamContext(_Stream())


def test_stream_construction_does_not_reserve_or_start_attempt(db, monkeypatch):
    execution = _execution(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        explicit_model_override="haiku",
    )
    messages = _StreamingMessages()
    routed = RoutedAnthropicClient(_Client(messages), execution)
    reservations: list[dict] = []
    monkeypatch.setattr(
        "app.components.ai_routing.admission.reserve_provider_usage",
        lambda **kwargs: reservations.append(kwargs),
    )

    routed.messages.stream(
        model=execution.selected_model_id,
        max_tokens=512,
        messages=[],
        metering={"feature": "taali_chat"},
    )
    execution.finish("failed")

    assert reservations == []
    assert messages.calls == []
    assert (
        db.scalars(
            select(AIRoutingAttempt).where(
                AIRoutingAttempt.invocation_id == execution.invocation_id
            )
        ).all()
        == []
    )


def test_stream_attempt_finishes_only_after_context_exit(db):
    execution = _execution(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        explicit_model_override="haiku",
    )
    messages = _StreamingMessages()
    routed = RoutedAnthropicClient(_Client(messages), execution)

    with routed.messages.stream(
        model=execution.selected_model_id,
        max_tokens=512,
        messages=[],
        metering={"feature": "taali_chat"},
    ) as stream:
        assert stream.get_final_message().stop_reason == "end_turn"
    execution.finish("succeeded")

    db.expire_all()
    attempt = db.scalar(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    )
    assert attempt is not None and attempt.status == "succeeded"
    assert (
        messages.calls[0]["metering"]["metadata"]["ai_routing"]["attempt_ordinal"] == 1
    )
