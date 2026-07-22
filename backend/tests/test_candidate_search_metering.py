"""Hard-admission and attribution guarantees for candidate-search LLM calls."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.candidate_search.parser import parse_nl_query
from app.components.ai_routing.adapters.anthropic_messages import RoutedAnthropicClient
from app.models.ai_routing import AIRoutingAttempt, AIRoutingInvocation
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.platform.config import settings
from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.pricing_service import Feature
from app.services.provider_usage_admission import reserve_provider_usage


class _ParserMessages:
    def __init__(self, *, input_tokens: int = 100, output_tokens: int = 10):
        self.calls = 0
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            id=f"msg-search-{self.calls}",
            model=kwargs["model"],
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="emit_parsed_filter",
                    input={
                        "locations_country": ["UK"],
                        "free_text": "worked at Google or Meta",
                    },
                )
            ],
            usage=SimpleNamespace(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def _seed(db, *, balance: int, role_budget_cents: int | None = None):
    org = Organization(
        name="Candidate search metering",
        slug=f"candidate-search-metering-{uuid.uuid4().hex[:10]}",
        credits_balance=int(balance),
    )
    db.add(org)
    db.flush()
    role = None
    if role_budget_cents is not None:
        role = Role(
            organization_id=int(org.id),
            name="Search role",
            source="requisition",
            monthly_usd_budget_cents=int(role_budget_cents),
            agentic_mode_enabled=True,
        )
        db.add(role)
    db.commit()
    return org, role


def _client(org_id: int, messages: _ParserMessages) -> MeteredAnthropicClient:
    return MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=int(org_id),
        sdk_max_retries=0,
    )


def _route_factory(client: MeteredAnthropicClient):
    return lambda execution: RoutedAnthropicClient(client, execution)


def test_workspace_search_is_hard_admitted_and_debited(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, _ = _seed(db, balance=100_000)
    inner = _ParserMessages(input_tokens=100, output_tokens=10)

    parsed = parse_nl_query(
        "worked at Google or Meta",
        route_client_factory=_route_factory(_client(int(org.id), inner)),
        organization_id=int(org.id),
    )

    assert parsed.locations_country == ["United Kingdom"]
    assert inner.calls == 1
    db.expire_all()
    # Sonnet parser: 100*3 + 10*15 = 450 microcredits at 1x markup.
    assert db.get(Organization, int(org.id)).credits_balance == 99_550
    event = db.query(UsageEvent).filter_by(feature="search_parse").one()
    assert event.organization_id == int(org.id)
    assert event.role_id is None
    assert event.credits_charged == 450
    settlement = event.event_metadata["credit_reservation"]
    assert settlement["reserved"] >= settlement["charged"]
    assert settlement["charged"] == 450
    assert settlement["shortfall"] == 0

    invocation = db.query(AIRoutingInvocation).one()
    attempt = db.query(AIRoutingAttempt).one()
    assert invocation.task == "candidate_search.parse"
    assert invocation.status == "succeeded"
    assert invocation.organization_id == int(org.id)
    assert attempt.invocation_id == invocation.invocation_id
    assert attempt.status == "succeeded"
    assert attempt.deployment_id == invocation.selected_deployment_id
    assert (
        event.event_metadata["ai_routing"]["invocation_id"] == invocation.invocation_id
    )
    assert event.event_metadata["ai_routing"]["attempt_ordinal"] == 1


def test_workspace_search_with_insufficient_credits_never_calls_provider(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, _ = _seed(db, balance=0)
    inner = _ParserMessages()

    parsed = parse_nl_query(
        "worked at Google or Meta",
        route_client_factory=_route_factory(_client(int(org.id), inner)),
        organization_id=int(org.id),
    )

    assert inner.calls == 0
    assert parsed.keywords == ["worked at Google or Meta"]
    assert db.query(UsageEvent).count() == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_role_search_enforces_role_cap_before_provider(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, role = _seed(db, balance=100_000, role_budget_cents=1)
    assert role is not None
    # Leave only 200 of the role's 10,000-microcredit cap available. The
    # request-shaped conservative hold must stop before the SDK.
    reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        feature=Feature.SEARCH_PARSE,
        trace_id="candidate-search:test:existing-role-hold",
        amount=9_800,
    )
    inner = _ParserMessages()

    parsed = parse_nl_query(
        "worked at Google or Meta",
        route_client_factory=_route_factory(_client(int(org.id), inner)),
        organization_id=int(org.id),
        role_id=int(role.id),
    )

    assert inner.calls == 0
    assert parsed.keywords == ["worked at Google or Meta"]
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:search_parse")
        .count()
        == 1
    )


@pytest.mark.parametrize("pause_scope", ("role", "workspace"))
def test_autonomous_search_admission_rechecks_pause_before_reserving(
    db, monkeypatch, pause_scope
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, role = _seed(db, balance=100_000, role_budget_cents=100)
    assert role is not None
    if pause_scope == "role":
        role.agent_paused_at = datetime.now(timezone.utc)
    else:
        org.agent_workspace_paused_at = datetime.now(timezone.utc)
    db.commit()

    before = db.query(BillingCreditLedger).count()
    inner = _ParserMessages()
    parsed = parse_nl_query(
        "worked at Google or Meta",
        route_client_factory=_route_factory(_client(int(org.id), inner)),
        organization_id=int(org.id),
        role_id=int(role.id),
        require_role_authority=True,
    )

    assert inner.calls == 0
    assert parsed.keywords == ["worked at Google or Meta"]
    assert db.query(BillingCreditLedger).count() == before


def test_conservative_hold_prevents_unfunded_overage_before_provider(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, _ = _seed(db, balance=700)
    inner = _ParserMessages(input_tokens=100, output_tokens=100)

    parse_nl_query(
        "worked at Google or Meta",
        route_client_factory=_route_factory(_client(int(org.id), inner)),
        organization_id=int(org.id),
    )

    db.expire_all()
    assert inner.calls == 0
    assert db.get(Organization, int(org.id)).credits_balance == 700
    assert db.query(UsageEvent).count() == 0
    assert db.query(BillingCreditLedger).count() == 0
