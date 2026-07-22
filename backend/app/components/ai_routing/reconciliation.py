"""Crash recovery for routing telemetry after provider-side completion."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.ai_routing import AIRoutingAttempt, AIRoutingInvocation
from ...models.billing_credit_ledger import BillingCreditLedger
from ...models.claude_call_log import ClaudeCallLog
from ...services.usage_credit_reservation_recovery import (
    load_credit_reservation,
    reconcile_usage_event_receipt,
)
from ...services.usage_credit_reservations import release_credit_reservation
from .attempt_evidence import evidence_has_known_usage, evidence_usage_values
from .telemetry import finish_attempt, finish_invocation

_EXPLICIT_REJECTION_STATUSES = frozenset(
    {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}
)
_COMPLETED_PROVIDER_STATUSES = frozenset({"ok", "metering_error_completed"})

logger = logging.getLogger("taali.ai_routing.reconciliation")


def _elapsed_ms(started_at: datetime | None, now: datetime) -> int:
    if started_at is None:
        return 0
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((now - started).total_seconds() * 1000))


def _usage_receipt_payload(
    *,
    invocation: AIRoutingInvocation,
    attempt: AIRoutingAttempt,
    evidence: ClaudeCallLog,
) -> dict:
    decision = (
        invocation.decision_snapshot
        if isinstance(invocation.decision_snapshot, dict)
        else {}
    )
    feature = str(decision.get("feature") or "").strip()
    if not feature:
        raise ValueError("routing invocation is missing its metering feature")
    if invocation.organization_id is None:
        raise ValueError("routing invocation is missing organization attribution")
    return {
        "organization_id": int(invocation.organization_id),
        "feature": feature,
        "model": str(evidence.model),
        "input_tokens": int(evidence.input_tokens or 0),
        "output_tokens": int(evidence.output_tokens or 0),
        "cache_read_tokens": int(evidence.cache_read_tokens or 0),
        "cache_creation_tokens": int(evidence.cache_creation_tokens or 0),
        "cache_creation_1h_tokens": (
            int(evidence.cache_creation_1h_tokens)
            if evidence.cache_creation_1h_tokens is not None
            else None
        ),
        "cache_hit": False,
        "service_tier": "standard",
        "user_id": invocation.user_id,
        "role_id": invocation.role_id,
        "entity_id": invocation.entity_id,
        "provider_cost_usd_micro": int(evidence.cost_usd_micro or 0),
        "metadata": {
            "ai_routing": {
                "invocation_id": str(invocation.invocation_id),
                "route_id": str(invocation.route_id),
                "attempt_ordinal": int(attempt.ordinal),
                "deployment_id": str(attempt.deployment_id),
                "registry_version": str(invocation.registry_version),
                "region": str(attempt.region),
                "pricing_id": attempt.pricing_id,
                "cost_authority": "ai_routing.model_registry",
            },
            "recovered_from_claude_call_log_id": int(evidence.id),
        },
    }


def _reconcile_reservation(
    db: Session,
    *,
    invocation: AIRoutingInvocation,
    attempt: AIRoutingAttempt,
    evidence: ClaudeCallLog | None,
    known_usage: bool,
    explicit_rejection: bool,
) -> int | None:
    """Settle or release the exact hold bound to a physical attempt."""

    reservation = load_credit_reservation(
        db,
        external_ref=attempt.credit_reservation_ref,
    )
    if reservation is None:
        return evidence.usage_event_id if evidence is not None else None
    if invocation.organization_id is None or int(invocation.organization_id) != int(
        reservation.organization_id
    ):
        raise ValueError("routing reservation organization attribution mismatch")
    if (
        evidence is not None
        and evidence.organization_id is not None
        and int(evidence.organization_id) != int(reservation.organization_id)
    ):
        raise ValueError("provider evidence organization attribution mismatch")

    if explicit_rejection:
        release_credit_reservation(
            db,
            reservation=reservation,
            reason="routing_reconciled_explicit_provider_rejection",
            allow_started=True,
        )
        settlement = db.scalar(
            select(BillingCreditLedger).where(
                BillingCreditLedger.external_ref
                == f"{reservation.external_ref}:settled"
            )
        )
        if settlement is None or not str(settlement.reason).startswith(
            "reservation_release:"
        ):
            raise ValueError(
                "explicit rejection could not safely release its provider hold"
            )
        return None
    if not known_usage or evidence is None:
        # Ambiguous/no-usage evidence intentionally retains the started hold.
        return evidence.usage_event_id if evidence is not None else None

    event = reconcile_usage_event_receipt(
        db,
        reservation=reservation,
        payload=_usage_receipt_payload(
            invocation=invocation,
            attempt=attempt,
            evidence=evidence,
        ),
        existing_event_id=(
            int(evidence.usage_event_id)
            if evidence.usage_event_id is not None
            else None
        ),
    )
    if evidence.usage_event_id is None:
        evidence.usage_event_id = int(event.id)
        db.flush()
    return int(event.id)


def _repair_attempt(
    db: Session,
    attempt: AIRoutingAttempt,
    *,
    now: datetime,
) -> str:
    invocation = db.get(AIRoutingInvocation, str(attempt.invocation_id))
    if invocation is None:
        raise ValueError("routing attempt has no logical invocation")
    trace_id = f"ai-route:{attempt.invocation_id}:{attempt.ordinal}"
    evidence = db.scalar(
        select(ClaudeCallLog)
        .where(ClaudeCallLog.trace_id == trace_id)
        .order_by(ClaudeCallLog.id.desc())
        .limit(1)
    )
    known_usage = evidence is not None and evidence_has_known_usage(evidence)
    explicit_rejection = (
        evidence is not None
        and evidence.http_status in _EXPLICIT_REJECTION_STATUSES
        and not known_usage
    )
    status = (
        "succeeded"
        if evidence is not None
        and evidence.status in _COMPLETED_PROVIDER_STATUSES
        and known_usage
        else "failed" if explicit_rejection else "ambiguous"
    )
    usage = (
        evidence_usage_values(evidence)
        if evidence is not None and known_usage
        else (
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cost_usd_micro": 0,
            }
            if explicit_rejection
            else {}
        )
    )
    try:
        with db.begin_nested():
            recovered_usage_event_id = _reconcile_reservation(
                db,
                invocation=invocation,
                attempt=attempt,
                evidence=evidence,
                known_usage=known_usage,
                explicit_rejection=explicit_rejection,
            )
    except Exception:
        # Keep both the running attempt and its started hold eligible for the
        # next health tick. Terminalizing one without the other would strand
        # capacity or permit a duplicate usage event on retry.
        logger.exception(
            "route reservation recovery deferred invocation=%s ordinal=%s",
            attempt.invocation_id,
            attempt.ordinal,
        )
        return "deferred"

    finish_attempt(
        db,
        str(attempt.invocation_id),
        int(attempt.ordinal),
        status=status,
        latency_ms=_elapsed_ms(attempt.started_at, now),
        usage_unknown=not (known_usage or explicit_rejection),
        error_class=(
            None
            if status == "succeeded"
            else (
                "provider.reconciled_explicit_rejection.v1"
                if status == "failed"
                else "provider.reconciled_outcome_ambiguous.v1"
            )
        ),
        error_reason=(
            None if status == "succeeded" else "routing_post_provider_write_recovered"
        ),
        provider_request_id=(
            evidence.anthropic_request_id if evidence is not None else None
        ),
        usage_event_id=recovered_usage_event_id,
        claude_call_log_id=(evidence.id if evidence is not None else None),
        finished_at=now,
        **usage,
    )
    return status


def _latest_succeeded_deployment(db: Session, invocation_id: str) -> str | None:
    return db.scalar(
        select(AIRoutingAttempt.deployment_id)
        .where(
            AIRoutingAttempt.invocation_id == invocation_id,
            AIRoutingAttempt.status == "succeeded",
        )
        .order_by(AIRoutingAttempt.ordinal.desc())
        .limit(1)
    )


def reconcile_stale_route_telemetry(
    db: Session,
    *,
    stale_after_minutes: int = 120,
    limit: int = 500,
    now: datetime | None = None,
) -> dict[str, object]:
    """Terminalize stale rows without ever declaring unknown workflow success."""

    effective_now = now or datetime.now(timezone.utc)
    cutoff = effective_now - timedelta(minutes=max(int(stale_after_minutes), 1))
    batch_limit = max(1, min(int(limit), 5_000))
    attempts = db.scalars(
        select(AIRoutingAttempt)
        .where(
            AIRoutingAttempt.status == "running",
            AIRoutingAttempt.started_at <= cutoff,
        )
        .order_by(AIRoutingAttempt.started_at, AIRoutingAttempt.id)
        .limit(batch_limit)
        .with_for_update(skip_locked=True)
    ).all()
    repaired = Counter()
    for attempt in attempts:
        repaired[_repair_attempt(db, attempt, now=effective_now)] += 1

    remaining = max(batch_limit - len(attempts), 0)
    invocations_repaired = 0
    if remaining:
        invocations = db.scalars(
            select(AIRoutingInvocation)
            .where(
                AIRoutingInvocation.status == "running",
                AIRoutingInvocation.started_at <= cutoff,
                ~AIRoutingInvocation.attempts.any(
                    AIRoutingAttempt.status.in_(("pending", "running"))
                ),
            )
            .order_by(AIRoutingInvocation.started_at, AIRoutingInvocation.invocation_id)
            .limit(remaining)
            .with_for_update(skip_locked=True)
        ).all()
        for invocation in invocations:
            # Provider evidence can repair the physical result, but a crashed
            # post-commit callback cannot prove the feature transaction's
            # semantic outcome. Conservatively exclude it from success labels.
            selected_deployment_id = _latest_succeeded_deployment(
                db, str(invocation.invocation_id)
            )
            finish_invocation(
                db,
                str(invocation.invocation_id),
                status="failed",
                selected_deployment_id=(
                    selected_deployment_id or invocation.selected_deployment_id
                ),
                finished_at=effective_now,
            )
            invocations_repaired += 1

    return {
        "attempts_scanned": len(attempts),
        "attempts_repaired": dict(sorted(repaired.items())),
        "invocations_repaired_failed": invocations_repaired,
        "stale_after_minutes": max(int(stale_after_minutes), 1),
    }


__all__ = ["reconcile_stale_route_telemetry"]
