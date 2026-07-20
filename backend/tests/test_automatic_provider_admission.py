"""Hard-admission wiring for automatic, non-assessment Anthropic work."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.cv_parsing.runner import parse_cv
from app.platform.config import settings
from app.services.interview_focus_service import generate_interview_focus_sync
from app.services.interview_tech_prompt import (
    MODEL_VERSION,
    OUTPUT_TOKEN_CEILING,
    generate_tech_questions,
)
from app.services.metered_anthropic_client import MeteredAnthropicClient
from app.services.pricing_service import (
    Feature,
    credits_charged,
    estimate_reservation,
    raw_cost_usd_micro,
)


def _seed_role(db, *, balance: int, budget_cents: int = 5_000):
    org = Organization(
        name="Automatic admission",
        slug=f"automatic-admission-{uuid.uuid4().hex[:10]}",
        credits_balance=balance,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Platform engineer",
        source="requisition",
        job_spec_text="Build resilient Python services.",
        monthly_usd_budget_cents=budget_cents,
    )
    db.add(role)
    db.commit()
    return org, role


def _live_meter(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE",
        True,
    )
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)


def _metering(org, role, *, feature: str) -> dict:
    return {
        "feature": feature,
        "organization_id": int(org.id),
        "role_id": int(role.id),
        "entity_id": f"role:{int(role.id)}",
        "trace_id": f"test:{feature}:role:{int(role.id)}",
    }


def test_role_tech_questions_do_not_call_provider_with_zero_credits(
    db, monkeypatch
):
    _live_meter(monkeypatch)
    org, role = _seed_role(db, balance=0)
    client = MagicMock()

    result = generate_tech_questions(
        job_spec_text=role.job_spec_text,
        client=client,
        metering=_metering(org, role, feature="interview_tech"),
    )

    assert result is None
    client.messages.create.assert_not_called()
    assert db.query(BillingCreditLedger).count() == 0


def test_role_tech_questions_reject_truncated_provider_output(
    monkeypatch, caplog
):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="max_tokens",
        content=[SimpleNamespace(text='{"questions": [{"question": "cut off"}')],
        usage=SimpleNamespace(
            input_tokens=1_500,
            output_tokens=OUTPUT_TOKEN_CEILING,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )

    result = generate_tech_questions(
        job_spec_text="Build resilient distributed systems.",
        client=client,
    )

    assert result is None
    assert OUTPUT_TOKEN_CEILING >= 2_200
    assert client.messages.create.call_args.kwargs["max_tokens"] == OUTPUT_TOKEN_CEILING
    assert "truncated" in caplog.text


def test_role_tech_reservation_covers_completed_six_question_response():
    marked_up_ceiling = credits_charged(
        feature=Feature.INTERVIEW_TECH,
        cost_usd_micro=raw_cost_usd_micro(
            input_tokens=2_000,
            output_tokens=OUTPUT_TOKEN_CEILING,
            model=MODEL_VERSION,
        ),
    )
    assert estimate_reservation(Feature.INTERVIEW_TECH) >= marked_up_ceiling


def test_application_cv_parse_does_not_call_provider_with_zero_credits(
    db, monkeypatch
):
    _live_meter(monkeypatch)
    org, role = _seed_role(db, balance=0)
    client = MagicMock()
    unique_cv = f"Python engineer {uuid.uuid4().hex}"

    result = parse_cv(
        unique_cv,
        client=client,
        metering={
            "feature": "cv_parse",
            "organization_id": int(org.id),
            "role_id": int(role.id),
            "entity_id": "application:99999",
            "trace_id": "cv-parse:application:99999",
        },
    )

    assert result.parse_failed is True
    assert (result.error_reason or "").startswith("usage_admission_failed")
    client.messages.create.assert_not_called()


def test_role_interview_focus_does_not_call_provider_past_role_cap(
    db, monkeypatch
):
    _live_meter(monkeypatch)
    org, role = _seed_role(db, balance=1_000_000, budget_cents=1)
    # One cent is 10,000 microcredits. Leave only 5,000, below the 6,000
    # INTERVIEW_FOCUS reservation estimate.
    db.add(
        UsageEvent(
            organization_id=int(org.id),
            role_id=int(role.id),
            feature="other",
            entity_id=f"role:{int(role.id)}",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd_micro=5_000,
            markup_multiplier=1,
            credits_charged=5_000,
            cache_hit=0,
        )
    )
    db.commit()
    with patch(
        "app.services.metered_anthropic_client._MeteredMessages.create"
    ) as create:
        result = generate_interview_focus_sync(
            role.job_spec_text,
            "test-key",
            metering=_metering(org, role, feature="interview_focus"),
        )

    assert result is None
    create.assert_not_called()
    db.refresh(org)
    assert org.credits_balance == 1_000_000


class _Messages:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    def create(self, **_kwargs):
        if self.fail:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(
            id="msg_admission_success",
            content=[
                SimpleNamespace(
                    text=json.dumps(
                        {
                            "questions": [
                                {
                                    "question": "How would you make this service resilient?",
                                    "positive_signals": ["Specific tradeoffs"],
                                    "red_flags": ["No failure model"],
                                }
                            ]
                        }
                    )
                )
            ],
            usage=SimpleNamespace(
                input_tokens=500,
                output_tokens=100,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def _metered_client(org, *, fail: bool = False):
    return MeteredAnthropicClient(
        inner=SimpleNamespace(messages=_Messages(fail=fail)),
        organization_id=int(org.id),
    )


def test_role_tech_success_settles_hold_to_actual_usage(db, monkeypatch):
    _live_meter(monkeypatch)
    starting = 1_000_000
    org, role = _seed_role(db, balance=starting)

    result = generate_tech_questions(
        job_spec_text=role.job_spec_text,
        client=_metered_client(org),
        metering=_metering(org, role, feature="interview_tech"),
    )

    assert result and result[0]["question"].startswith("How would")
    event = (
        db.query(UsageEvent)
        .filter(
            UsageEvent.organization_id == int(org.id),
            UsageEvent.feature == "interview_tech",
        )
        .one()
    )
    db.refresh(org)
    assert org.credits_balance == starting - int(event.credits_charged)
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation_settle:interview_tech")
        .count()
        == 1
    )


def test_role_tech_ambiguous_provider_failure_retains_hold(db, monkeypatch):
    _live_meter(monkeypatch)
    starting = 1_000_000
    org, role = _seed_role(db, balance=starting)

    result = generate_tech_questions(
        job_spec_text=role.job_spec_text,
        client=_metered_client(org, fail=True),
        metering=_metering(org, role, feature="interview_tech"),
    )

    assert result is None
    db.expire_all()
    assert db.get(Organization, int(org.id)).credits_balance < starting
    assert db.query(UsageEvent).filter_by(organization_id=int(org.id)).count() == 0
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:interview_tech")
        .one()
    )
    assert hold.entry_metadata["state"] == "provider_attempt_started"
