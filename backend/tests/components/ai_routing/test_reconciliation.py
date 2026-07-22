from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.components.ai_routing import estimate_anthropic_messages
from app.components.ai_routing.contracts import TaskKey
from app.components.ai_routing.execution import RoutingAttribution
from app.components.ai_routing.execution_types import AdmittedAttemptBudget
from app.components.ai_routing.gateway import prepare_route
from app.components.ai_routing.reconciliation import reconcile_stale_route_telemetry
from app.models.ai_routing import AIRoutingAttempt, AIRoutingInvocation
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services.pricing_service import Feature
from app.services.usage_credit_reservation_recovery import (
    release_stale_credit_reservations,
)
from app.services.usage_credit_reservations import reserve_credits


def _running_attempt(
    db,
    *,
    label: str,
    organization_id: int = 1,
    role_id: int | None = None,
    reservation_ref: str | None = None,
) -> AIRoutingAttempt:
    execution = prepare_route(
        TaskKey.SEARCH_GROUNDING,
        request_estimate=estimate_anthropic_messages(messages=[], max_tokens=700),
        attribution=RoutingAttribution(
            organization_id=organization_id,
            role_id=role_id,
            entity_id=label,
        ),
        environ={},
    )
    plan = execution.plan_next_attempt(start_new_iteration=True)
    execution.begin_attempt(
        plan,
        admitted_budget=AdmittedAttemptBudget(
            credit_reservation_ref=reservation_ref or f"shadow:{label}",
            estimated_input_tokens=0,
            estimated_output_tokens=700,
            estimated_input_cost_basis="standard",
            estimated_cost_usd_micro=1,
        ),
    )
    attempt = db.scalar(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    )
    assert attempt is not None
    attempt.started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    invocation = db.get(AIRoutingInvocation, execution.invocation_id)
    assert invocation is not None
    invocation.started_at = attempt.started_at
    db.commit()
    return attempt


def _started_hold(
    db,
    *,
    organization_id: int,
    role_id: int,
    external_ref: str,
    amount: int = 50_000,
):
    reservation = reserve_credits(
        db,
        organization_id=organization_id,
        feature=Feature.CANDIDATE_GROUNDING,
        external_ref=external_ref,
        amount=amount,
        role_id=role_id,
    )
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == external_ref)
        .one()
    )
    hold.entry_metadata = {
        **dict(hold.entry_metadata or {}),
        "state": "provider_attempt_started",
        "provider": "anthropic",
    }
    db.commit()
    return reservation


def test_reconciler_links_known_provider_usage_and_excludes_unknown_workflow_success(
    db,
):
    attempt = _running_attempt(db, label="known")
    db.add(
        ClaudeCallLog(
            model=attempt.model,
            input_tokens=11,
            output_tokens=7,
            cache_read_tokens=3,
            cache_creation_tokens=5,
            cost_usd_micro=222,
            status="ok",
            anthropic_request_id="msg-recovered",
            trace_id=f"ai-route:{attempt.invocation_id}:{attempt.ordinal}",
        )
    )
    db.commit()

    result = reconcile_stale_route_telemetry(db)
    db.commit()
    db.expire_all()

    repaired = db.get(AIRoutingAttempt, attempt.id)
    invocation = db.get(AIRoutingInvocation, attempt.invocation_id)
    assert result["attempts_repaired"] == {"succeeded": 1}
    assert repaired is not None and repaired.status == "succeeded"
    assert repaired.cost_usd_micro == 222
    assert repaired.claude_call_log_id is not None
    # Provider success cannot prove that the feature transaction committed its
    # semantic result, so recovery never manufactures a positive feedback label.
    assert invocation is not None and invocation.status == "failed"


def test_reconciler_distinguishes_explicit_rejection_from_unknown_acceptance(db):
    rejected = _running_attempt(db, label="rejected")
    unknown = _running_attempt(db, label="unknown")
    db.add(
        ClaudeCallLog(
            model=rejected.model,
            status="sdk_error",
            error_class="rate_limit",
            http_status=429,
            trace_id=f"ai-route:{rejected.invocation_id}:{rejected.ordinal}",
        )
    )
    db.commit()

    result = reconcile_stale_route_telemetry(db)
    db.commit()
    db.expire_all()

    rejected = db.get(AIRoutingAttempt, rejected.id)
    unknown = db.get(AIRoutingAttempt, unknown.id)
    assert result["attempts_repaired"] == {"ambiguous": 1, "failed": 1}
    assert rejected is not None and rejected.status == "failed"
    assert rejected.usage_unknown is False and rejected.cost_usd_micro == 0
    assert unknown is not None and unknown.status == "ambiguous"
    assert unknown.usage_unknown is True


def test_reconciler_reconstructs_usage_settles_hold_and_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    org = Organization(
        name="Route recovery",
        slug=f"route-recovery-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Recovery role")
    db.add(role)
    db.commit()
    ref = f"usage-hold:candidate_grounding:recover:{org.id}"
    _started_hold(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        external_ref=ref,
    )
    attempt = _running_attempt(
        db,
        label="recover-known",
        organization_id=int(org.id),
        role_id=int(role.id),
        reservation_ref=ref,
    )
    invocation = db.get(AIRoutingInvocation, attempt.invocation_id)
    assert invocation is not None
    invocation.selected_deployment_id = None
    db.add(
        ClaudeCallLog(
            organization_id=int(org.id),
            model=attempt.model,
            input_tokens=11,
            output_tokens=7,
            cache_read_tokens=3,
            cache_creation_tokens=5,
            cache_creation_1h_tokens=2,
            cost_usd_micro=222,
            status="metering_error_completed",
            anthropic_request_id="msg-recovered-hold",
            trace_id=f"ai-route:{attempt.invocation_id}:{attempt.ordinal}",
        )
    )
    db.commit()

    first = reconcile_stale_route_telemetry(db)
    db.commit()
    second = reconcile_stale_route_telemetry(db)
    db.commit()
    db.expire_all()

    repaired = db.get(AIRoutingAttempt, attempt.id)
    invocation = db.get(AIRoutingInvocation, attempt.invocation_id)
    events = db.query(UsageEvent).filter_by(organization_id=int(org.id)).all()
    settlements = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == f"{ref}:settled")
        .all()
    )
    call_log = (
        db.query(ClaudeCallLog)
        .filter_by(anthropic_request_id="msg-recovered-hold")
        .one()
    )
    assert first["attempts_repaired"] == {"succeeded": 1}
    assert second["attempts_scanned"] == 0
    assert len(events) == 1
    assert len(settlements) == 1
    assert call_log.usage_event_id == int(events[0].id)
    assert repaired is not None and repaired.usage_event_id == int(events[0].id)
    assert invocation is not None
    assert invocation.selected_deployment_id == attempt.deployment_id


def test_reconciler_releases_only_explicit_rejection_hold(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    org = Organization(
        name="Route rejection",
        slug=f"route-rejection-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Rejection role")
    db.add(role)
    db.commit()
    ref = f"usage-hold:candidate_grounding:reject:{org.id}"
    _started_hold(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        external_ref=ref,
    )
    attempt = _running_attempt(
        db,
        label="recover-rejection",
        organization_id=int(org.id),
        role_id=int(role.id),
        reservation_ref=ref,
    )
    db.add(
        ClaudeCallLog(
            organization_id=int(org.id),
            model=attempt.model,
            status="sdk_error",
            error_class="rate_limit",
            http_status=429,
            trace_id=f"ai-route:{attempt.invocation_id}:{attempt.ordinal}",
        )
    )
    db.commit()

    result = reconcile_stale_route_telemetry(db)
    db.commit()
    db.expire_all()

    settlement = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == f"{ref}:settled")
        .one()
    )
    assert result["attempts_repaired"] == {"failed": 1}
    assert settlement.reason == "reservation_release:candidate_grounding"
    assert db.get(Organization, int(org.id)).credits_balance == 1_000_000
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 0


def test_stale_hold_recovery_retries_terminal_routed_rejection_refund(
    db, monkeypatch
):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    org = Organization(
        name="Terminal route rejection",
        slug=f"terminal-route-rejection-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Terminal rejection role")
    db.add(role)
    db.commit()
    ref = f"usage-hold:candidate_grounding:terminal-reject:{org.id}"
    _started_hold(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        external_ref=ref,
    )
    attempt = _running_attempt(
        db,
        label="terminal-rejection",
        organization_id=int(org.id),
        role_id=int(role.id),
        reservation_ref=ref,
    )
    attempt.status = "failed"
    attempt.error_class = "provider.rate_limited.v1"
    attempt.error_reason = "provider.rate_limited.v1"
    attempt.usage_unknown = False
    attempt.input_tokens = 0
    attempt.output_tokens = 0
    attempt.cache_read_tokens = 0
    attempt.cache_creation_tokens = 0
    attempt.cost_usd_micro = 0
    attempt.latency_ms = 1
    attempt.finished_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db.commit()

    result = release_stale_credit_reservations(
        db,
        now=datetime.now(timezone.utc) + timedelta(hours=3),
    )
    db.commit()
    db.expire_all()

    settlement = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == f"{ref}:settled")
        .one()
    )
    assert result["released"] == 1
    assert settlement.reason == "reservation_release:candidate_grounding"
    assert db.get(Organization, int(org.id)).credits_balance == 1_000_000


def test_reconciler_retries_after_usage_settlement_outage(db, monkeypatch):
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    org = Organization(
        name="Route outage",
        slug=f"route-outage-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Outage role")
    db.add(role)
    db.commit()
    ref = f"usage-hold:candidate_grounding:outage:{org.id}"
    _started_hold(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        external_ref=ref,
    )
    attempt = _running_attempt(
        db,
        label="recover-outage",
        organization_id=int(org.id),
        role_id=int(role.id),
        reservation_ref=ref,
    )
    db.add(
        ClaudeCallLog(
            organization_id=int(org.id),
            model=attempt.model,
            input_tokens=4,
            output_tokens=2,
            cost_usd_micro=50,
            status="ok",
            trace_id=f"ai-route:{attempt.invocation_id}:{attempt.ordinal}",
        )
    )
    db.commit()

    from app.components.ai_routing import reconciliation as recovery

    original = recovery.reconcile_usage_event_receipt
    monkeypatch.setattr(
        recovery,
        "reconcile_usage_event_receipt",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("metering unavailable")),
    )
    first = reconcile_stale_route_telemetry(db)
    db.commit()
    assert first["attempts_repaired"] == {"deferred": 1}
    assert db.get(AIRoutingAttempt, attempt.id).status == "running"
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 0

    monkeypatch.setattr(recovery, "reconcile_usage_event_receipt", original)
    second = reconcile_stale_route_telemetry(db)
    db.commit()
    db.expire_all()

    assert second["attempts_repaired"] == {"succeeded": 1}
    assert db.get(AIRoutingAttempt, attempt.id).status == "succeeded"
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 1
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.external_ref == f"{ref}:settled")
        .count()
        == 1
    )
