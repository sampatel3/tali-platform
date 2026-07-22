"""Flush-only, content-free persistence for provider-neutral routing telemetry.

All helpers use the caller's ``Session`` and never commit its transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.ai_routing import AIRoutingAttempt, AIRoutingInvocation
from .telemetry_validation import (
    AIRoutingIdempotencyConflict,
    AIRoutingStatusTransitionError,
    AIRoutingTelemetryError,
    attempt_ordinal as _attempt_ordinal,
    json_safe_snapshot,
    nonnegative_int as _nonnegative_int,
    optional_id as _optional_id,
    optional_text as _optional_text,
    positive_int as _positive_int,
    reason as _reason,
    require_same as _same,
    required_text as _required_text,
    timestamp as _timestamp,
    uuid_string as _uuid_string,
)

INVOCATION_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
ATTEMPT_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "ambiguous", "cancelled"})


def _locked(session: Session, model: Any, *criteria: Any) -> Any:
    row = session.scalar(select(model).where(*criteria).with_for_update())
    if row is None:
        raise AIRoutingTelemetryError("Unknown routing telemetry row")
    return row


def _invocation(session: Session, invocation_id: str) -> AIRoutingInvocation:
    return _locked(
        session, AIRoutingInvocation, AIRoutingInvocation.invocation_id == invocation_id
    )


def _attempt(session: Session, invocation_id: str, ordinal: int) -> AIRoutingAttempt:
    return _locked(
        session,
        AIRoutingAttempt,
        AIRoutingAttempt.invocation_id == invocation_id,
        AIRoutingAttempt.ordinal == ordinal,
    )


def create_invocation(
    session: Session,
    *,
    route_id: str | UUID,
    operation: str,
    workflow: str,
    task: str,
    profile_version: str,
    policy_version: str,
    registry_version: str,
    request_snapshot: Mapping[str, Any],
    decision_snapshot: Mapping[str, Any],
    invocation_id: str | UUID | None = None,
    root_invocation_id: str | UUID | None = None,
    parent_invocation_id: str | UUID | None = None,
    selected_deployment_id: str | None = None,
    organization_id: int | None = None,
    user_id: int | None = None,
    role_id: int | None = None,
    agent_run_id: int | None = None,
    entity_id: str | None = None,
) -> AIRoutingInvocation:
    """Create a planned invocation, or return its identical existing row."""
    invocation_id = _uuid_string(invocation_id, field="invocation_id", generate=True)
    route_id = _uuid_string(route_id, field="route_id", generate=False)
    parent_id = (
        None
        if parent_invocation_id is None
        else _uuid_string(
            parent_invocation_id, field="parent_invocation_id", generate=False
        )
    )
    supplied_root_id = (
        None
        if root_invocation_id is None
        else _uuid_string(
            root_invocation_id, field="root_invocation_id", generate=False
        )
    )
    existing = session.get(AIRoutingInvocation, invocation_id)
    if parent_id is None:
        root_id = supplied_root_id or invocation_id
        if root_id != invocation_id:
            raise AIRoutingTelemetryError("A root invocation must reference itself")
    elif existing is not None and supplied_root_id is None:
        root_id = existing.root_invocation_id
    else:
        parent = session.get(AIRoutingInvocation, parent_id)
        if parent is None:
            raise AIRoutingTelemetryError(f"Unknown parent invocation: {parent_id}")
        root_id = supplied_root_id or parent.root_invocation_id
        if root_id != parent.root_invocation_id:
            raise AIRoutingTelemetryError(
                "Child root_invocation_id does not match its parent"
            )

    expected = {
        "route_id": route_id,
        "root_invocation_id": root_id,
        "parent_invocation_id": parent_id,
        "operation": _required_text(operation, field="operation", max_length=80),
        "workflow": _required_text(workflow, field="workflow", max_length=120),
        "task": _required_text(task, field="task", max_length=160),
        "profile_version": _required_text(
            profile_version, field="profile_version", max_length=120
        ),
        "policy_version": _required_text(
            policy_version, field="policy_version", max_length=120
        ),
        "registry_version": _required_text(
            registry_version, field="registry_version", max_length=120
        ),
        "request_snapshot": json_safe_snapshot(request_snapshot),
        "decision_snapshot": json_safe_snapshot(decision_snapshot),
        "selected_deployment_id": _optional_text(
            selected_deployment_id, field="selected_deployment_id", max_length=160
        ),
        "organization_id": _optional_id(organization_id, field="organization_id"),
        "user_id": _optional_id(user_id, field="user_id"),
        "role_id": _optional_id(role_id, field="role_id"),
        "agent_run_id": _optional_id(agent_run_id, field="agent_run_id"),
        "entity_id": _optional_text(entity_id, field="entity_id", max_length=160),
    }
    if existing is not None:
        _same(existing, expected, key=invocation_id)
        return existing
    row = AIRoutingInvocation(invocation_id=invocation_id, status="planned", **expected)
    session.add(row)
    session.flush()
    return row


def start_invocation(
    session: Session,
    invocation_id: str | UUID,
    *,
    started_at: datetime | None = None,
) -> AIRoutingInvocation:
    invocation_id = _uuid_string(invocation_id, field="invocation_id", generate=False)
    row = _invocation(session, invocation_id)
    if row.status == "running":
        return row
    if row.status != "planned":
        raise AIRoutingStatusTransitionError(
            f"Cannot start invocation in {row.status!r} status"
        )
    row.status = "running"
    row.started_at = _timestamp(started_at)
    session.flush()
    return row


def finish_invocation(
    session: Session,
    invocation_id: str | UUID,
    *,
    status: str,
    selected_deployment_id: str | None = None,
    finished_at: datetime | None = None,
) -> AIRoutingInvocation:
    if status not in INVOCATION_TERMINAL_STATUSES:
        raise AIRoutingTelemetryError(f"Invalid terminal invocation status: {status!r}")
    invocation_id = _uuid_string(invocation_id, field="invocation_id", generate=False)
    selected_deployment_id = _optional_text(
        selected_deployment_id, field="selected_deployment_id", max_length=160
    )
    row = _invocation(session, invocation_id)
    if row.status == status:
        if (
            selected_deployment_id is not None
            and row.selected_deployment_id != selected_deployment_id
        ):
            raise AIRoutingIdempotencyConflict(
                "Terminal invocation was reused with a different selected deployment"
            )
        return row
    allowed = status in {"failed", "cancelled"} if row.status == "planned" else True
    if row.status != "running" and not (row.status == "planned" and allowed):
        raise AIRoutingStatusTransitionError(
            f"Cannot finish invocation from {row.status!r} as {status!r}"
        )
    active_attempt = session.scalar(
        select(AIRoutingAttempt.id)
        .where(
            AIRoutingAttempt.invocation_id == invocation_id,
            AIRoutingAttempt.status.in_(("pending", "running")),
        )
        .limit(1)
    )
    if active_attempt is not None:
        raise AIRoutingStatusTransitionError(
            "Cannot finish an invocation while a physical attempt is active"
        )
    row.status = status
    if selected_deployment_id is not None:
        row.selected_deployment_id = selected_deployment_id
    row.finished_at = _timestamp(finished_at)
    session.flush()
    return row


def create_attempt(
    session: Session,
    *,
    invocation_id: str | UUID,
    ordinal: int,
    iteration_ordinal: int,
    attempt_in_iteration: int,
    provider: str,
    runtime: str,
    deployment_id: str,
    model: str,
    region: str = "global",
    pricing_id: str | None = None,
    credit_reservation_ref: str,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    estimated_input_cost_basis: str,
    admitted_cost_usd_micro: int,
    fallback_from_deployment_id: str | None = None,
    fallback_reason: str | None = None,
) -> AIRoutingAttempt:
    invocation_id = _uuid_string(invocation_id, field="invocation_id", generate=False)
    ordinal = _attempt_ordinal(ordinal)
    iteration_ordinal = _attempt_ordinal(iteration_ordinal)
    attempt_in_iteration = _attempt_ordinal(attempt_in_iteration)
    invocation = _invocation(session, invocation_id)
    existing = session.scalar(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == invocation_id,
            AIRoutingAttempt.ordinal == ordinal,
        )
    )
    expected = {
        "iteration_ordinal": iteration_ordinal,
        "attempt_in_iteration": attempt_in_iteration,
        "provider": _required_text(provider, field="provider", max_length=80),
        "runtime": _required_text(runtime, field="runtime", max_length=80),
        "deployment_id": _required_text(
            deployment_id, field="deployment_id", max_length=160
        ),
        "model": _required_text(model, field="model", max_length=160),
        "region": _required_text(region, field="region", max_length=32),
        "pricing_id": _optional_text(
            pricing_id, field="pricing_id", max_length=160
        ),
        "credit_reservation_ref": _required_text(
            credit_reservation_ref,
            field="credit_reservation_ref",
            max_length=255,
        ),
        "estimated_input_tokens": _nonnegative_int(
            estimated_input_tokens,
            field="estimated_input_tokens",
        ),
        "estimated_output_tokens": _positive_int(
            estimated_output_tokens,
            field="estimated_output_tokens",
        ),
        "estimated_input_cost_basis": _required_text(
            estimated_input_cost_basis,
            field="estimated_input_cost_basis",
            max_length=32,
        ),
        "admitted_cost_usd_micro": _nonnegative_int(
            admitted_cost_usd_micro,
            field="admitted_cost_usd_micro",
        ),
        "fallback_from_deployment_id": _optional_text(
            fallback_from_deployment_id,
            field="fallback_from_deployment_id",
            max_length=160,
        ),
        "fallback_reason": _reason(fallback_reason, field="fallback_reason"),
    }
    if expected["estimated_input_cost_basis"] not in {
        "standard",
        "cache_write_5m",
        "cache_write_1h",
    }:
        raise AIRoutingTelemetryError(
            "estimated_input_cost_basis is not a registered price class"
        )
    if existing is not None:
        _same(existing, expected, key=f"{invocation_id}/{ordinal}")
        return existing
    if invocation.status != "running":
        raise AIRoutingStatusTransitionError(
            f"Cannot create an attempt for invocation in {invocation.status!r} status"
        )
    if ordinal == 1 and any(
        expected[field] is not None
        for field in ("fallback_from_deployment_id", "fallback_reason")
    ):
        raise AIRoutingTelemetryError("The first attempt cannot be a fallback")
    if ordinal > 1:
        previous = session.scalar(
            select(AIRoutingAttempt).where(
                AIRoutingAttempt.invocation_id == invocation_id,
                AIRoutingAttempt.ordinal == ordinal - 1,
            )
        )
        if previous is None or previous.status not in ATTEMPT_TERMINAL_STATUSES:
            raise AIRoutingTelemetryError(
                "A fallback requires a terminal prior attempt"
            )
        fallback_fields = (
            expected["fallback_from_deployment_id"],
            expected["fallback_reason"],
        )
        if any(value is not None for value in fallback_fields):
            if any(value is None for value in fallback_fields):
                raise AIRoutingTelemetryError(
                    "Fallback source and reason must be recorded together"
                )
            if expected["fallback_from_deployment_id"] != previous.deployment_id:
                raise AIRoutingTelemetryError(
                    "fallback_from_deployment_id must identify the prior attempt"
                )
        elif expected["deployment_id"] != previous.deployment_id:
            raise AIRoutingTelemetryError(
                "A deployment change requires fallback source and reason"
            )
    row = AIRoutingAttempt(
        invocation_id=invocation_id,
        ordinal=ordinal,
        status="pending",
        usage_unknown=False,
        **expected,
    )
    session.add(row)
    session.flush()
    return row


def start_attempt(
    session: Session,
    invocation_id: str | UUID,
    ordinal: int,
    *,
    started_at: datetime | None = None,
) -> AIRoutingAttempt:
    invocation_id = _uuid_string(invocation_id, field="invocation_id", generate=False)
    ordinal = _attempt_ordinal(ordinal)
    invocation = _invocation(session, invocation_id)
    row = _attempt(session, invocation_id, ordinal)
    if row.status == "running":
        raise AIRoutingStatusTransitionError(
            "A running physical attempt has an outcome-ambiguous owner and "
            "cannot be replayed"
        )
    if invocation.status != "running" or row.status != "pending":
        raise AIRoutingStatusTransitionError(
            f"Cannot start attempt from invocation={invocation.status!r}, "
            f"attempt={row.status!r}"
        )
    row.status = "running"
    row.started_at = _timestamp(started_at)
    session.flush()
    return row


def _usage_values(
    *,
    usage_unknown: bool,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_creation_tokens: int | None,
    cost_usd_micro: int | None,
) -> dict[str, int | None]:
    if not isinstance(usage_unknown, bool):
        raise AIRoutingTelemetryError("usage_unknown must be explicitly true or false")
    values = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cost_usd_micro": cost_usd_micro,
    }
    if usage_unknown:
        if any(value is not None for value in values.values()):
            raise AIRoutingTelemetryError(
                "Unknown usage cannot include token or cost estimates"
            )
        return values
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AIRoutingTelemetryError(
                f"Known usage requires a non-negative integer {field}"
            )
    return values


def finish_attempt(
    session: Session,
    invocation_id: str | UUID,
    ordinal: int,
    *,
    status: str,
    latency_ms: int,
    usage_unknown: bool,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    cost_usd_micro: int | None = None,
    error_class: str | None = None,
    error_reason: str | None = None,
    provider_request_id: str | None = None,
    usage_event_id: int | None = None,
    claude_call_log_id: int | None = None,
    finished_at: datetime | None = None,
) -> AIRoutingAttempt:
    """Finish one attempt with known usage or an explicit unknown marker."""
    if status not in ATTEMPT_TERMINAL_STATUSES:
        raise AIRoutingTelemetryError(f"Invalid terminal attempt status: {status!r}")
    ordinal = _attempt_ordinal(ordinal)
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, int)
        or latency_ms < 0
    ):
        raise AIRoutingTelemetryError("latency_ms must be a non-negative integer")
    usage = _usage_values(
        usage_unknown=usage_unknown,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cost_usd_micro=cost_usd_micro,
    )
    error_class = _reason(error_class, field="error_class")
    error_reason = _reason(error_reason, field="error_reason")
    if status == "succeeded" and (error_class is not None or error_reason is not None):
        raise AIRoutingTelemetryError("A successful attempt cannot carry an error")
    if status in {"failed", "ambiguous"} and error_class is None:
        raise AIRoutingTelemetryError(f"A {status} attempt requires an error_class")

    invocation_id = _uuid_string(invocation_id, field="invocation_id", generate=False)
    invocation = _invocation(session, invocation_id)
    row = _attempt(session, invocation_id, ordinal)
    expected: dict[str, Any] = {
        "latency_ms": latency_ms,
        "usage_unknown": usage_unknown,
        "error_class": error_class,
        "error_reason": error_reason,
        "provider_request_id": _optional_text(
            provider_request_id, field="provider_request_id", max_length=255
        ),
        "usage_event_id": _optional_id(usage_event_id, field="usage_event_id"),
        "claude_call_log_id": _optional_id(
            claude_call_log_id, field="claude_call_log_id"
        ),
        **usage,
    }
    if row.status == status:
        _same(row, expected, key=f"{invocation_id}/{ordinal} terminal result")
        return row
    if invocation.status != "running" or row.status != "running":
        raise AIRoutingStatusTransitionError(
            f"Cannot finish attempt from invocation={invocation.status!r}, "
            f"attempt={row.status!r}"
        )
    row.status = status
    row.finished_at = _timestamp(finished_at)
    for field, value in expected.items():
        setattr(row, field, value)
    session.flush()
    return row
