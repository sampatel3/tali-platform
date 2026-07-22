from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.components.ai_routing.contracts import (
    FallbackClass,
    RouteRequest,
    TaskKey,
    WorkflowDefinition,
)
from app.components.ai_routing.execution import (
    RouteExecution,
    RouteExecutionError,
    RoutingTelemetryUnavailable,
)
from app.components.ai_routing.execution_types import AdmittedAttemptBudget
from app.components.ai_routing.model_registry import (
    ANTHROPIC_HAIKU_4_5,
    ANTHROPIC_SONNET_4_6,
    DEFAULT_MODEL_REGISTRY,
    ModelRegistry,
)
from app.components.ai_routing.policy import RoutingPolicy
from app.components.ai_routing.task_registry import (
    DEFAULT_TASK_REGISTRY,
    TaskRegistry,
)
from app.models.ai_routing import AIRoutingAttempt


class _Rejected(Exception):
    status_code = 429


class _MissingDeployment(Exception):
    status_code = 404


class _BadRequest(Exception):
    status_code = 400


def _response(request_id: str = "request-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=request_id,
        usage=SimpleNamespace(
            input_tokens=2,
            output_tokens=3,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def _route(
    fallback_classes: frozenset[FallbackClass] = frozenset(
        {FallbackClass.RETRYABLE_TRANSPORT}
    ),
    *,
    max_cost_micro_usd: int | None = None,
) -> tuple[RouteRequest, object, RouteExecution]:
    profile = DEFAULT_TASK_REGISTRY.get(TaskKey.ROLE_CHAT_ORCHESTRATION)
    assert profile is not None
    profile = replace(
        profile,
        fallback_deployment_ids=(ANTHROPIC_SONNET_4_6,),
        fallback_classes=(
            fallback_classes | frozenset({FallbackClass.REGISTERED_REPLACEMENT})
        ),
    )
    deployments = tuple(
        replace(
            deployment,
            replacement_deployment_id=ANTHROPIC_SONNET_4_6,
            evaluated_tasks=frozenset({TaskKey.ROLE_CHAT_ORCHESTRATION}),
        )
        if deployment.deployment_id == ANTHROPIC_HAIKU_4_5
        else deployment
        for deployment in DEFAULT_MODEL_REGISTRY.deployments
    )
    model_registry = ModelRegistry(
        version="execution-attempt-plan-models.v1",
        deployments=deployments,
    )
    task_registry = TaskRegistry(
        version="execution-attempt-plan-tasks.v1",
        profiles=(profile,),
        workflows=(WorkflowDefinition(profile.workflow, "workflow.v1"),),
    )
    policy = RoutingPolicy(
        model_registry=model_registry,
        task_registry=task_registry,
        policy_version="execution-attempt-plan-policy.v1",
    )
    invocation_id = str(uuid4())
    request = RouteRequest(
        task=profile.key,
        invocation_id=invocation_id,
        root_invocation_id=invocation_id,
        estimated_input_tokens=100,
        estimated_output_tokens=50,
        max_cost_micro_usd=max_cost_micro_usd,
    )
    decision = policy.plan(request)
    execution = RouteExecution(
        request=request,
        decision=decision,
        registry=model_registry,
        task_registry=task_registry,
    )
    return request, decision, execution


def _begin(execution: RouteExecution, *, new_iteration: bool):
    plan = execution.plan_next_attempt(start_new_iteration=new_iteration)
    budget = AdmittedAttemptBudget(
        credit_reservation_ref=(
            f"test-reservation:{execution.invocation_id}:{plan.ordinal}"
        ),
        estimated_input_tokens=100,
        estimated_output_tokens=50,
        estimated_input_cost_basis="standard",
        estimated_cost_usd_micro=1_000,
    )
    return execution.begin_attempt(plan, admitted_budget=budget)


def test_decision_and_first_physical_attempt_must_start_with_selection(db):
    request, decision, execution = _route()
    malformed = replace(
        decision,
        attempts=(decision.attempts[1], decision.attempts[0], *decision.attempts[2:]),
    )

    with pytest.raises(RouteExecutionError, match="selected deployment must be first"):
        RouteExecution(
            request=request,
            decision=malformed,
            registry=execution.registry,
            task_registry=execution.task_registry,
        )

    execution.start()
    first = _begin(execution, new_iteration=True)
    assert first.deployment.deployment_id == decision.selected_deployment_id
    execution.finish_error(first, _BadRequest("invalid"))
    execution.finish("failed")


def test_deployment_switch_follows_plan_and_uses_allowed_reason(db):
    _, _, execution = _route(frozenset({FallbackClass.REGISTERED_REPLACEMENT}))
    execution.start()
    first = _begin(execution, new_iteration=True)
    result = execution.finish_error(first, _MissingDeployment("gone"))

    assert result.next_attempt is not None
    assert result.next_attempt.deployment_id == ANTHROPIC_SONNET_4_6
    assert result.next_attempt.fallback_class is FallbackClass.REGISTERED_REPLACEMENT
    fallback = _begin(execution, new_iteration=False)
    execution.finish_success(fallback, _response("request-fallback"))
    execution.finish("succeeded")

    attempts = db.scalars(
        select(AIRoutingAttempt)
        .where(AIRoutingAttempt.invocation_id == execution.invocation_id)
        .order_by(AIRoutingAttempt.ordinal)
    ).all()
    assert [attempt.deployment_id for attempt in attempts] == [
        execution.decision.selected_deployment_id,
        ANTHROPIC_SONNET_4_6,
    ]
    assert attempts[1].fallback_from_deployment_id == attempts[0].deployment_id
    assert attempts[1].fallback_reason == FallbackClass.REGISTERED_REPLACEMENT.value


def test_explicit_failure_retry_requires_retryable_transport_class(db):
    _, _, execution = _route(frozenset({FallbackClass.PRE_ACCEPTANCE_TRANSPORT}))
    execution.start()
    first = _begin(execution, new_iteration=True)
    execution.finish_error(first, _Rejected("rate limited"))

    with pytest.raises(RouteExecutionError, match="no logical iteration"):
        _begin(execution, new_iteration=False)
    execution.finish("failed")


def test_retryable_failure_and_successful_multi_turn_calls_stay_on_deployment(db):
    _, _, execution = _route()
    execution.start()
    first = _begin(execution, new_iteration=True)
    execution.finish_error(first, _Rejected("rate limited"))

    retry = _begin(execution, new_iteration=False)
    execution.finish_success(retry, _response("request-retry"))
    next_turn = _begin(execution, new_iteration=True)
    execution.finish_success(next_turn, _response("request-next-turn"))
    execution.finish("succeeded")

    attempts = db.scalars(
        select(AIRoutingAttempt)
        .where(AIRoutingAttempt.invocation_id == execution.invocation_id)
        .order_by(AIRoutingAttempt.ordinal)
    ).all()
    assert [attempt.status for attempt in attempts] == [
        "failed",
        "succeeded",
        "succeeded",
    ]
    assert len({attempt.deployment_id for attempt in attempts}) == 1
    assert all(attempt.fallback_reason is None for attempt in attempts)


def test_nonretryable_four_hundred_never_issues_retry_authorization(db):
    _, _, execution = _route()
    execution.start()
    first = _begin(execution, new_iteration=True)
    result = execution.finish_error(first, _BadRequest("invalid request"))

    assert result.next_attempt is None
    with pytest.raises(RouteExecutionError, match="no logical iteration"):
        _begin(execution, new_iteration=False)
    execution.finish("failed")


def test_ambiguous_attempt_still_blocks_retry_and_failover(db):
    _, _, execution = _route()
    execution.start()
    first = _begin(execution, new_iteration=True)
    execution.finish_error(first, TimeoutError("provider acceptance unknown"))

    with pytest.raises(RouteExecutionError, match="outcome-ambiguous"):
        _begin(execution, new_iteration=False)
    execution.finish("failed")


def test_reconstructed_execution_cannot_replay_running_attempt(db):
    request, decision, original = _route()
    original.start()
    _begin(original, new_iteration=True)

    reconstructed = RouteExecution(
        request=request,
        decision=decision,
        registry=original.registry,
        task_registry=original.task_registry,
    ).start()
    with pytest.raises(RoutingTelemetryUnavailable, match="start attempt 1"):
        _begin(reconstructed, new_iteration=True)


def test_unknown_usage_consumes_conservative_route_cost_budget(db):
    _, _, execution = _route(max_cost_micro_usd=1_500)
    attempt = _begin(execution, new_iteration=True)
    execution.finish_success(
        attempt,
        SimpleNamespace(id="usage-omitted", usage=None),
    )

    assert execution.cumulative_cost_usd_micro == 1_000
    with pytest.raises(RouteExecutionError, match="cost ceiling"):
        execution.authorize_estimated_attempt(
            input_tokens=10,
            output_tokens=10,
            raw_cost_usd_micro=501,
        )
    execution.finish("succeeded")


def test_malformed_usage_terminalizes_with_conservative_cost(db):
    _, _, execution = _route(max_cost_micro_usd=1_500)
    attempt = _begin(execution, new_iteration=True)
    malformed = _response("malformed-usage")
    malformed.usage.input_tokens = "2"

    execution.finish_success(attempt, malformed)
    execution.finish("succeeded")

    db.expire_all()
    persisted = db.scalar(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    )
    assert persisted is not None
    assert persisted.status == "succeeded"
    assert persisted.usage_unknown is True
    assert persisted.cost_usd_micro is None
    assert execution.cumulative_cost_usd_micro == 1_000
