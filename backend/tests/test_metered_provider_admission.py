"""Universal hard-admission fallback at the Anthropic SDK boundary."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.claude_call_log import ClaudeCallLog
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.platform.config import settings
from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.provider_usage_admission import (
    AutomaticProviderAuthorityError,
    PROVIDER_ATTEMPT_STARTED_STATE,
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
)
from app.services.usage_credit_reservation_recovery import (
    release_stale_credit_reservations,
)
from app.services.usage_credit_reservations import InsufficientRoleBudgetError
from app.services.usage_metering_service import InsufficientCreditsError


class _Messages:
    def __init__(self, *, fail: bool = False):
        self.calls = 0
        self.fail = fail

    def create(self, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(
            id=f"msg-{self.calls}",
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=10,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def _seed(db, *, balance: int, budget_cents: int = 5_000):
    org = Organization(
        name="Provider admission",
        slug=f"provider-admission-{uuid.uuid4().hex[:10]}",
        credits_balance=balance,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Platform engineer",
        source="requisition",
        monthly_usd_budget_cents=budget_cents,
    )
    db.add(role)
    db.commit()
    return org, role


def _live(monkeypatch) -> None:
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)


def _call(client, org, role, *, trace: str, require_role_authority: bool = False):
    return client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "score this"}],
        metering={
            "feature": "score",
            "organization_id": int(org.id),
            "role_id": int(role.id),
            "entity_id": "application:42",
            "trace_id": trace,
            "require_role_authority": require_role_authority,
        },
    )


def test_role_attributed_call_is_blocked_before_sdk_with_zero_credits(
    db, monkeypatch
):
    _live(monkeypatch)
    org, role = _seed(db, balance=0)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    with pytest.raises(InsufficientCreditsError):
        _call(client, org, role, trace="zero-credit")

    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_autonomous_fallback_rechecks_pause_before_sdk(db, monkeypatch):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    role.agentic_mode_enabled = True
    role.agent_paused_at = datetime.now(timezone.utc)
    db.commit()
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    with pytest.raises(AutomaticProviderAuthorityError, match="paused"):
        _call(
            client,
            org,
            role,
            trace="paused-autonomous-fallback",
            require_role_authority=True,
        )

    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_each_sdk_attempt_gets_a_fresh_role_cap_hold(db, monkeypatch):
    _live(monkeypatch)
    # SCORE reserves 30,000 microcredits per actual attempt. The first call
    # settles to a small actual charge; a second attempt cannot reserve another
    # 30,000 against this exact 3-cent cap.
    org, role = _seed(db, balance=1_000_000, budget_cents=3)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    _call(client, org, role, trace="multi-call:1")
    with pytest.raises(InsufficientRoleBudgetError):
        _call(client, org, role, trace="multi-call:2")

    assert inner.calls == 1
    events = db.query(UsageEvent).filter_by(role_id=int(role.id)).all()
    assert len(events) == 1
    assert events[0].event_metadata["credit_reservation"]["state"] == "settled"
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .count()
        == 1
    )
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .one()
    )
    receipt = hold.entry_metadata["deferred_usage_event"]
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
    assert receipt["feature"] == "score"
    assert receipt["input_tokens"] == 100
    assert receipt["output_tokens"] == 10
    assert receipt["role_id"] == int(role.id)


def test_ambiguous_sdk_failure_retains_automatic_hold(db, monkeypatch):
    _live(monkeypatch)
    starting = 1_000_000
    org, role = _seed(db, balance=starting)
    inner = _Messages(fail=True)
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        _call(client, org, role, trace="provider-failure")

    db.refresh(org)
    assert inner.calls == 1
    assert org.credits_balance == starting - 30_000
    assert db.query(UsageEvent).filter_by(role_id=int(role.id)).count() == 0
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_ATTEMPT_STARTED_STATE
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .count()
        == 1
    )


def test_explicit_provider_rejection_releases_automatic_hold(db, monkeypatch):
    _live(monkeypatch)
    starting = 1_000_000
    org, role = _seed(db, balance=starting)

    class _Rejected(_Messages):
        def create(self, **_kwargs):
            self.calls += 1
            error = RuntimeError("invalid request")
            error.status_code = 400
            raise error

    inner = _Rejected()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    with pytest.raises(RuntimeError, match="invalid request"):
        _call(client, org, role, trace="provider-rejection")

    db.expire_all()
    assert inner.calls == 1
    assert db.get(Organization, int(org.id)).credits_balance == starting
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation_release:score")
        .count()
        == 1
    )


def test_marker_and_meter_failure_after_provider_success_never_refunds_hold(
    db, monkeypatch,
):
    """The durable pre-call marker closes the post-provider DB outage gap."""

    _live(monkeypatch)
    org, role = _seed(db, balance=100_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    monkeypatch.setattr(
        "app.services.metered_anthropic_client.mark_provider_usage_succeeded",
        lambda *args, **kwargs: False,
    )

    def _metering_down(*args, **kwargs):
        raise RuntimeError("usage database unavailable")

    monkeypatch.setattr(
        "app.services.metered_anthropic_client.record_event",
        _metering_down,
    )

    response = _call(client, org, role, trace="post-provider-db-outage")
    assert response.id == "msg-1"
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_ATTEMPT_STARTED_STATE
    assert db.query(UsageEvent).count() == 0
    assert (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "metering_error")
        .count()
        == 1
    )

    now = datetime.now(timezone.utc)
    hold.created_at = now - timedelta(hours=3)
    db.commit()
    recovered = release_stale_credit_reservations(db, now=now)
    db.commit()
    db.expire_all()

    assert recovered["released"] == 0
    assert recovered["reconciled"] == 0
    assert recovered["protected_billable"] == 1
    assert db.get(Organization, int(org.id)).credits_balance == 70_000


def test_success_response_without_usage_retains_unknown_provider_hold(
    db, monkeypatch,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=100_000)

    class _NoUsageMessages:
        calls = 0

        def create(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(id="msg-no-usage", usage=None)

    inner = _NoUsageMessages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    response = _call(client, org, role, trace="sync-no-usage")

    assert response.id == "msg-no-usage"
    assert inner.calls == 1
    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .one()
    )
    assert (
        hold.entry_metadata["state"]
        == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    )
    assert db.query(UsageEvent).count() == 0
    assert db.get(Organization, int(org.id)).credits_balance == 70_000
