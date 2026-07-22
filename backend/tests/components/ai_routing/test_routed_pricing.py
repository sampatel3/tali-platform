from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.components.ai_routing.adapters.anthropic_messages import RoutedAnthropicClient
from app.components.ai_routing.anthropic_estimation import estimate_anthropic_messages
from app.components.ai_routing.contracts import TaskKey
from app.components.ai_routing.execution import RoutingAttribution
from app.components.ai_routing.attempt_evidence import usage_values
from app.components.ai_routing.gateway import prepare_route
from app.components.ai_routing.model_registry import (
    ANTHROPIC_SONNET_4_6,
    DEFAULT_MODEL_REGISTRY,
)
from app.components.ai_routing.pricing import (
    RoutedPricingContractError,
    RoutedPricingOutcomeError,
    RoutedPricingReceiptError,
    resolve_routed_pricing,
    routed_cost_usd_micro,
)
from app.models.claude_call_log import ClaudeCallLog
from app.models.ai_routing import AIRoutingAttempt
from app.models.billing_credit_ledger import BillingCreditLedger
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    ProviderAttemptMarkerError,
)
from app.services.pricing_service import Feature, raw_cost_usd_micro
from app.services.provider_usage_admission import (
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
    provider_error_is_definitely_nonbillable,
)


def _usage():
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=30,
        cache_creation_input_tokens=50,
        cache_creation=SimpleNamespace(
            ephemeral_5m_input_tokens=30,
            ephemeral_1h_input_tokens=20,
        ),
    )


def _route_metadata(*, region: str | None = None) -> dict:
    route = {
        "invocation_id": "invocation-1",
        "attempt_ordinal": 1,
        "deployment_id": ANTHROPIC_SONNET_4_6,
        "registry_version": DEFAULT_MODEL_REGISTRY.version,
    }
    if region is not None:
        route["region"] = region
    return {"ai_routing": route}


class _Settings:
    AI_ROUTER_MODEL_OVERRIDES_JSON = ""
    resolved_claude_model = "claude-haiku-4-5-20251001"
    resolved_agent_autonomous_model = "claude-haiku-4-5-20251001"


def test_registry_pricing_covers_cache_ttl_batch_and_us_geo():
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None

    # Standard global: 100*3 + 20*15 + 30*.3 + 30*3.75 + 20*6 = 841.5.
    assert routed_cost_usd_micro(usage=_usage(), deployment=deployment) == 842
    # US inference applies the immutable 1.10 deployment multiplier once.
    assert (
        routed_cost_usd_micro(usage=_usage(), deployment=deployment, region="us") == 926
    )
    # Batch uses registry batch rates and discounts every cache class by 50%.
    assert (
        routed_cost_usd_micro(
            usage=_usage(), deployment=deployment, service_tier="batch"
        )
        == 421
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_tokens", True),
        ("output_tokens", "20"),
        ("cache_read_input_tokens", -1),
        ("cache_creation_input_tokens", 2.5),
    ],
)
def test_routed_pricing_rejects_inexact_or_negative_token_receipts(field, value):
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None
    usage = _usage()
    setattr(usage, field, value)

    with pytest.raises(RoutedPricingReceiptError):
        routed_cost_usd_micro(usage=usage, deployment=deployment)


def test_routed_pricing_rejects_cache_1h_count_above_total():
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None
    usage = _usage()
    usage.cache_creation_input_tokens = 19

    with pytest.raises(RoutedPricingReceiptError, match="exceed total"):
        routed_cost_usd_micro(usage=usage, deployment=deployment)


def test_attempt_evidence_never_coerces_malformed_provider_usage():
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None
    usage = _usage()
    usage.output_tokens = "20"

    with pytest.raises(RoutedPricingReceiptError, match="exact integer"):
        usage_values(usage=usage, deployment=deployment)


def test_attempt_evidence_rejects_impossible_cache_ttl_breakdown():
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None
    usage = _usage()
    usage.cache_creation_input_tokens = 19

    with pytest.raises(RoutedPricingReceiptError, match="exceed total"):
        usage_values(usage=usage, deployment=deployment)


def test_routed_pricing_fails_closed_on_stale_or_mismatched_provenance():
    wrong_model = {"metadata": _route_metadata()}
    with pytest.raises(RoutedPricingContractError, match="provider model"):
        resolve_routed_pricing(
            wrong_model,
            model="claude-haiku-4-5-20251001",
        )

    stale = {"metadata": _route_metadata()}
    stale["metadata"]["ai_routing"]["registry_version"] = "stale-registry"
    with pytest.raises(RoutedPricingContractError, match="registry version"):
        resolve_routed_pricing(stale, model="claude-sonnet-4-6")

    geo_mismatch = {"metadata": _route_metadata(region="us")}
    with pytest.raises(RoutedPricingContractError, match="inference geography"):
        resolve_routed_pricing(geo_mismatch, model="claude-sonnet-4-6")


def test_routed_call_uses_one_registry_cost_for_event_and_call_log(db, monkeypatch):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    org = Organization(name="Routed pricing", slug=f"routed-pricing-{id(db)}")
    db.add(org)
    db.commit()

    response = SimpleNamespace(
        id="request-routed",
        model="claude-sonnet-4-6",
        usage=SimpleNamespace(**vars(_usage()), inference_geo="us"),
        content=[],
    )
    inner = MagicMock()
    inner.messages.create.return_value = response
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    client.messages.create(
        model="claude-sonnet-4-6",
        inference_geo="us",
        max_tokens=128,
        messages=[{"role": "user", "content": "hi"}],
        metering={
            "feature": Feature.OTHER,
            "metadata": _route_metadata(region="us"),
        },
    )

    db.expire_all()
    event = db.query(UsageEvent).filter_by(organization_id=org.id).one()
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    assert event.cost_usd_micro == call_log.cost_usd_micro == 926
    assert event.event_metadata["cost_source"] == "ai_routing.model_registry"
    provenance = event.event_metadata["ai_routing"]
    assert provenance["pricing_id"] == (
        DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6).pricing.pricing_id
    )
    assert provenance["region"] == "us"
    assert provenance["pricing_registry_version"] == DEFAULT_MODEL_REGISTRY.version


def test_full_route_persists_the_same_registry_cost_in_all_receipts(db, monkeypatch):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    org = Organization(name="Full route pricing", slug=f"route-cost-{id(db)}")
    db.add(org)
    db.commit()

    response = SimpleNamespace(
        id="request-full-route",
        model="claude-sonnet-4-6",
        usage=SimpleNamespace(**vars(_usage()), inference_geo="us"),
        content=[],
    )
    raw = MagicMock()
    raw.messages.create.return_value = response
    metered = MeteredAnthropicClient(
        inner=raw,
        organization_id=int(org.id),
        sdk_max_retries=0,
    )
    execution = prepare_route(
        TaskKey.SEARCH_GROUNDING,
        request_estimate=estimate_anthropic_messages(
            messages=[{"role": "user", "content": "ground this"}],
            max_tokens=700,
        ),
        attribution=RoutingAttribution(
            organization_id=int(org.id), entity_id="candidate:1"
        ),
        region="us",
        settings_obj=_Settings(),
        environ={},
    )
    routed = RoutedAnthropicClient(metered, execution)
    routed.messages.create(
        model=execution.selected_model_id,
        max_tokens=700,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "text", "data": "ground this"},
                        "citations": {"enabled": True},
                    }
                ],
            }
        ],
        metering={"feature": Feature.CANDIDATE_GROUNDING},
    )
    execution.finish("succeeded")

    db.expire_all()
    event = db.query(UsageEvent).filter_by(organization_id=org.id).one()
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    attempt = db.scalar(
        select(AIRoutingAttempt).where(
            AIRoutingAttempt.invocation_id == execution.invocation_id
        )
    )
    assert attempt is not None
    assert event.cost_usd_micro == call_log.cost_usd_micro == 926
    assert attempt.cost_usd_micro == 926
    assert attempt.pricing_id == event.event_metadata["ai_routing"]["pricing_id"]
    assert attempt.region == "us"


def test_registered_provider_model_substitution_is_priced_then_rejected(
    db, monkeypatch
):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    org = Organization(name="Model mismatch", slug=f"model-mismatch-{id(db)}")
    db.add(org)
    db.commit()
    response = SimpleNamespace(
        id="request-substituted",
        model="claude-haiku-4-5-20251001",
        usage=SimpleNamespace(**vars(_usage()), inference_geo="global"),
        content=[],
    )
    inner = MagicMock()
    inner.messages.create.return_value = response
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with pytest.raises(RoutedPricingOutcomeError) as exc:
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=128,
            messages=[],
            metering={
                "feature": Feature.OTHER,
                "metadata": _route_metadata(region="global"),
            },
        )

    assert not provider_error_is_definitely_nonbillable(exc.value)
    db.expire_all()
    event = db.query(UsageEvent).filter_by(organization_id=org.id).one()
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    # Actual Haiku: 100*1 + 20*5 + 30*.1 + 30*1.25 + 20*2 = 280.5.
    assert event.model == call_log.model == "claude-haiku-4-5-20251001"
    assert event.cost_usd_micro == call_log.cost_usd_micro == 281
    provenance = event.event_metadata["ai_routing"]
    assert provenance["model_mismatch"] is True
    assert provenance["executed_model_id"] == "claude-haiku-4-5-20251001"
    assert provenance["billed_pricing_id"].startswith("anthropic.claude-haiku-4-5")
    assert call_log.status == "routed_contract_mismatch"


def test_actual_us_geo_is_charged_before_global_route_mismatch_fails(db, monkeypatch):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    org = Organization(name="Geo mismatch", slug=f"geo-mismatch-{id(db)}")
    db.add(org)
    db.commit()
    response = SimpleNamespace(
        id="request-wrong-geo",
        model="claude-sonnet-4-6",
        usage=SimpleNamespace(**vars(_usage()), inference_geo="us"),
        content=[],
    )
    inner = MagicMock()
    inner.messages.create.return_value = response
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with pytest.raises(RoutedPricingOutcomeError):
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=128,
            messages=[],
            metering={
                "feature": Feature.OTHER,
                "metadata": _route_metadata(region="global"),
            },
        )

    db.expire_all()
    event = db.query(UsageEvent).filter_by(organization_id=org.id).one()
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    assert event.cost_usd_micro == call_log.cost_usd_micro == 926
    provenance = event.event_metadata["ai_routing"]
    assert provenance["region"] == "global"
    assert provenance["pricing_region"] == "us"
    assert provenance["region_mismatch"] is True
    assert call_log.status == "routed_contract_mismatch"


def test_us_route_requires_response_geo_evidence_after_exact_settlement(
    db, monkeypatch
):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    org = Organization(name="Missing geo", slug=f"missing-geo-{id(db)}")
    db.add(org)
    db.commit()
    response = SimpleNamespace(
        id="request-missing-geo",
        model="claude-sonnet-4-6",
        usage=_usage(),
        content=[],
    )
    inner = MagicMock()
    inner.messages.create.return_value = response
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with pytest.raises(RoutedPricingOutcomeError):
        client.messages.create(
            model="claude-sonnet-4-6",
            inference_geo="us",
            max_tokens=128,
            messages=[],
            metering={
                "feature": Feature.OTHER,
                "metadata": _route_metadata(region="us"),
            },
        )

    db.expire_all()
    event = db.query(UsageEvent).filter_by(organization_id=org.id).one()
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    assert event.cost_usd_micro == call_log.cost_usd_micro == 926
    assert event.event_metadata["ai_routing"]["region_evidence_missing"] is True
    assert call_log.status == "routed_contract_mismatch"


def test_post_provider_receipt_error_marks_usage_unknown_and_traced_evidence(
    db, monkeypatch
):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        "app.services.usage_credit_reservations.settings.USAGE_METER_LIVE", True
    )
    monkeypatch.setattr(
        "app.services.usage_metering_service.settings.USAGE_METER_LIVE", True
    )
    org = Organization(
        name="Bad receipt",
        slug=f"bad-receipt-{id(db)}",
        credits_balance=1_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Bad receipt role")
    db.add(role)
    db.commit()

    invalid_usage = _usage()
    invalid_usage.input_tokens = "not-a-number"
    response = SimpleNamespace(
        id="request-bad-receipt",
        model="claude-sonnet-4-6",
        usage=invalid_usage,
        content=[],
    )
    inner = MagicMock()
    inner.messages.create.return_value = response
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))

    with pytest.raises(RoutedPricingReceiptError, match="exact integer"):
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=128,
            messages=[],
            metering={
                "feature": Feature.OTHER,
                "role_id": int(role.id),
                "trace_id": "ai-route:bad-receipt:1",
                "metadata": {
                    "ai_routing": {
                        **_route_metadata(region="global")["ai_routing"],
                        "invocation_id": "bad-receipt",
                    }
                },
            },
        )

    db.expire_all()
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:other")
        .one()
    )
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    assert hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE
    assert call_log.status == "routed_pricing_receipt_error"
    assert call_log.trace_id == "ai-route:bad-receipt:1"
    assert call_log.anthropic_request_id == "request-bad-receipt"
    assert call_log.cost_usd_micro == 0
    assert db.query(UsageEvent).filter_by(organization_id=org.id).count() == 0


def test_non_routed_call_keeps_historical_pricing_path(db, monkeypatch):
    from app.services import metered_anthropic_client as mac
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr(mac, "SessionLocal", TestingSessionLocal)
    org = Organization(name="Legacy pricing", slug=f"legacy-pricing-{id(db)}")
    db.add(org)
    db.commit()

    response = SimpleNamespace(id="request-legacy", usage=_usage(), content=[])
    inner = MagicMock()
    inner.messages.create.return_value = response
    client = MeteredAnthropicClient(inner=inner, organization_id=int(org.id))
    client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=128,
        messages=[{"role": "user", "content": "hi"}],
        metering={"feature": Feature.OTHER},
    )

    expected = raw_cost_usd_micro(
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=30,
        cache_creation_tokens=50,
        cache_creation_1h_tokens=20,
        model="claude-sonnet-4-6",
    )
    db.expire_all()
    event = db.query(UsageEvent).filter_by(organization_id=org.id).one()
    call_log = db.query(ClaudeCallLog).filter_by(organization_id=org.id).one()
    assert event.cost_usd_micro == call_log.cost_usd_micro == expected == 842
    assert event.event_metadata is None


def test_routed_wrapper_reuses_exact_adapter_attempt_marker(monkeypatch):
    from app.services import metered_anthropic_client as mac

    marker = MagicMock(return_value=True)
    monkeypatch.setattr(mac, "mark_provider_attempt_started", marker)
    monkeypatch.setattr(
        mac,
        "reservation_from_payload",
        lambda payload: SimpleNamespace(external_ref="hold-1"),
    )
    messages = mac._MeteredMessages(inner=MagicMock(), organization_id=1)
    messages._ensure_provider_reservation(
        {
            "feature": Feature.OTHER,
            "credit_reservation": {"external_ref": "hold-1"},
            "metadata": _route_metadata(),
        }
    )

    marker.assert_called_once_with(
        {"external_ref": "hold-1"},
        provider="anthropic",
        attempt_ref="invocation-1:1",
    )


def test_routed_messages_reject_provider_service_tier_override_before_call():
    inner = MagicMock()
    client = MeteredAnthropicClient(inner=inner, organization_id=1)

    with pytest.raises(RoutedPricingContractError, match="control-plane-owned") as exc:
        client.messages.create(
            model="claude-sonnet-4-6",
            service_tier="auto",
            max_tokens=128,
            messages=[],
            metering={
                "feature": Feature.OTHER,
                "metadata": _route_metadata(region="global"),
            },
        )

    assert exc.value.provider_not_called is True
    inner.messages.create.assert_not_called()


def test_pretransport_contract_failures_are_explicitly_nonbillable():
    assert provider_error_is_definitely_nonbillable(
        RoutedPricingContractError("bad route price provenance")
    )
    assert provider_error_is_definitely_nonbillable(
        ProviderAttemptMarkerError("marker unavailable before transport")
    )


def test_posttransport_pricing_receipt_error_never_claims_zero_spend():
    deployment = DEFAULT_MODEL_REGISTRY.get(ANTHROPIC_SONNET_4_6)
    assert deployment is not None

    with pytest.raises(RoutedPricingReceiptError) as exc:
        routed_cost_usd_micro(
            usage=SimpleNamespace(
                input_tokens="not-an-integer",
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            deployment=deployment,
        )

    assert not getattr(exc.value, "provider_not_called", False)
    assert not provider_error_is_definitely_nonbillable(exc.value)
