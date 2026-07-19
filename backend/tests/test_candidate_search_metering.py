"""Hard-admission and attribution guarantees for candidate-search LLM calls."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.candidate_search import parser as parser_module
from app.candidate_search.parser import parse_nl_query
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.platform.config import settings
from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.pricing_service import Feature, estimate_reservation
from app.services.provider_usage_admission import reserve_provider_usage


SEARCH_PARSE_HOLD = 50_000


class _ParserMessages:
    def __init__(self, *, input_tokens: int = 100, output_tokens: int = 10):
        self.calls = 0
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            id=f"msg-search-{self.calls}",
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
        )
        db.add(role)
    db.commit()
    return org, role


def _client(org_id: int, messages: _ParserMessages) -> MeteredAnthropicClient:
    return MeteredAnthropicClient(
        inner=SimpleNamespace(messages=messages),
        organization_id=int(org_id),
    )


def test_workspace_search_is_hard_admitted_and_debited(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, _ = _seed(db, balance=100_000)
    inner = _ParserMessages(input_tokens=100, output_tokens=10)

    parsed = parse_nl_query(
        "worked at Google or Meta",
        client=_client(int(org.id), inner),
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
    assert estimate_reservation(Feature.SEARCH_PARSE) == SEARCH_PARSE_HOLD
    assert settlement["reserved"] >= SEARCH_PARSE_HOLD
    assert settlement["charged"] == 450
    assert settlement["adjustment"] == settlement["reserved"] - 450
    assert settlement["shortfall"] == 0


def test_workspace_search_with_insufficient_credits_never_calls_provider(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, _ = _seed(db, balance=0)
    inner = _ParserMessages()

    parsed = parse_nl_query(
        "worked at Google or Meta",
        client=_client(int(org.id), inner),
        organization_id=int(org.id),
    )

    assert inner.calls == 0
    assert parsed.keywords == ["worked at Google or Meta"]
    assert db.query(UsageEvent).count() == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_unpriceable_parser_override_never_reserves_or_calls_provider(
    db, monkeypatch,
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    monkeypatch.setattr(
        parser_module,
        "PARSER_MODEL",
        "claude-opus-99-untrusted-secret-marker",
    )
    org, _ = _seed(db, balance=100_000)
    inner = _ParserMessages()

    parsed = parse_nl_query(
        "worked at Google or Meta",
        client=_client(int(org.id), inner),
        organization_id=int(org.id),
    )

    assert parsed.keywords == ["worked at Google or Meta"]
    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0
    assert db.query(UsageEvent).count() == 0


def test_active_workspace_search_hold_blocks_a_concurrent_parser_attempt(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, _ = _seed(db, balance=(2 * SEARCH_PARSE_HOLD) - 1)
    reserve_provider_usage(
        organization_id=int(org.id),
        role_id=None,
        feature=Feature.SEARCH_PARSE,
        trace_id="candidate-search:test:active-workspace-hold",
    )
    inner = _ParserMessages()

    parsed = parse_nl_query(
        "worked at Google or Meta",
        client=_client(int(org.id), inner),
        organization_id=int(org.id),
    )

    assert inner.calls == 0
    assert parsed.keywords == ["worked at Google or Meta"]
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:search_parse")
        .all()
    )
    assert len(holds) == 1
    assert -int(holds[0].delta) == SEARCH_PARSE_HOLD


def test_active_role_search_hold_consumes_cap_before_concurrent_attempt(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    org, role = _seed(db, balance=100_000, role_budget_cents=3)
    assert role is not None
    # Keep organization credits ample, but leave the active role with one
    # microcredit less than a fresh parser hold. This models a second worker
    # arriving while the first provider attempt is still unsettled.
    reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        feature=Feature.SEARCH_PARSE,
        trace_id="candidate-search:test:existing-role-hold",
        amount=10_001,
    )
    inner = _ParserMessages()

    parsed = parse_nl_query(
        "worked at Google or Meta",
        client=_client(int(org.id), inner),
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


def test_max_authorized_parser_usage_has_no_reservation_shortfall(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", True)
    starting_balance = 200_000
    org, _ = _seed(db, balance=starting_balance)
    inner = _ParserMessages(input_tokens=11_000, output_tokens=512)

    parse_nl_query(
        "worked at Google or Meta",
        client=_client(int(org.id), inner),
        organization_id=int(org.id),
    )

    db.expire_all()
    assert inner.calls == 1
    assert db.get(Organization, int(org.id)).credits_balance == 159_320
    event = db.query(UsageEvent).filter_by(feature="search_parse").one()
    # Actual Sonnet charge is 40,680: 11,000*3 + 512*15. The conservative
    # request envelope covers it and settlement refunds the remainder.
    settlement = event.event_metadata["credit_reservation"]
    assert event.credits_charged == 40_680
    assert settlement["reserved"] >= event.credits_charged
    assert settlement["adjustment"] == settlement["reserved"] - 40_680
    assert settlement["shortfall"] == 0
    assert settlement["state"] == "settled"
