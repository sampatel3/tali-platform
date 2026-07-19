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
from app.services.claude_model_pricing import UnpriceableClaudeModelError
from app.services.anthropic_request_admission import (
    AnthropicRequestAdmissionError,
    anthropic_request_credit_upper_bound,
)
from app.services.anthropic_surface_guard import UnsupportedAnthropicSurfaceError
from app.services.metered_anthropic_client import (
    MeteredAnthropicClient,
    ProviderAttemptMarkerError,
)
from app.services.provider_usage_admission import (
    PROVIDER_ATTEMPT_STARTED_STATE,
    PROVIDER_SUCCEEDED_PENDING_STATE,
    PROVIDER_SUCCEEDED_USAGE_UNKNOWN_STATE,
    mark_provider_attempt_started,
    mark_provider_usage_succeeded,
    release_provider_usage_if_definitely_nonbillable,
    reserve_provider_usage,
)
from app.services.provider_request_identity import provider_request_sha256
from app.services import provider_retry_policy
from app.services.usage_credit_reservation_recovery import (
    release_stale_credit_reservations,
)
from app.services.usage_metering_service import InsufficientCreditsError
from app.services.usage_credit_reservations import CreditReservation


class _Messages:
    def __init__(
        self,
        *,
        fail: bool = False,
        failure_message: str = "provider unavailable",
    ):
        self.calls = 0
        self.fail = fail
        self.failure_message = failure_message

    def create(self, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError(self.failure_message)
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


def _call(client, org, role, *, trace: str):
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
        },
    )


def _score_request_bound() -> int:
    return anthropic_request_credit_upper_bound(
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "score this"}],
        },
        feature="score",
    )


def _call_workspace(client, org, *, trace: str):
    return client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "help with this workspace"}],
        metering={
            "feature": "taali_chat",
            "organization_id": int(org.id),
            "role_id": None,
            "entity_id": "conversation:42",
            "trace_id": trace,
        },
    )


def test_request_bound_accepts_tuple_of_ordinary_client_tools():
    assert anthropic_request_credit_upper_bound(
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "use a tool"}],
            "tools": (
                {
                    "name": "lookup",
                    "description": "Look up a record",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ),
        },
        feature="score",
    ) > 0


def test_inline_base64_image_uses_finite_serialized_bound_not_full_context():
    base_request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": []}],
    }
    inline = {
        **base_request,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "aGVsbG8=",
                        },
                    }
                ],
            }
        ],
    }
    remote = {
        **base_request,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": "https://example.invalid/x"},
                    }
                ],
            }
        ],
    }

    inline_bound = anthropic_request_credit_upper_bound(inline, feature="score")
    remote_bound = anthropic_request_credit_upper_bound(remote, feature="score")
    assert inline_bound < remote_bound


def test_media_labels_inside_user_json_do_not_trigger_full_context_hold():
    ordinary_json = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "tools": [
            {
                "name": "lookup",
                "input_schema": {
                    "type": "object",
                    "properties": {"kind": {"type": "image"}},
                },
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "lookup",
                        "input": {
                            "type": "image",
                            "source": {"type": "url", "url": "ordinary-json"},
                        },
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": {
                            "type": "document",
                            "source": {"type": "url", "url": "ordinary-json"},
                        },
                    },
                ],
            }
        ],
    }
    actual_nested_media = {
        **ordinary_json,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "url",
                                    "url": "https://example.invalid/x",
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }

    ordinary_bound = anthropic_request_credit_upper_bound(
        ordinary_json,
        feature="score",
    )
    remote_bound = anthropic_request_credit_upper_bound(
        actual_nested_media,
        feature="score",
    )
    assert ordinary_bound < remote_bound


@pytest.mark.parametrize("tools", [{"name": "lookup"}, "lookup", 1, object()])
def test_request_bound_rejects_non_sequence_tool_containers(tools):
    with pytest.raises(AnthropicRequestAdmissionError, match="list or tuple"):
        anthropic_request_credit_upper_bound(
            {
                "model": "claude-haiku-4-5",
                "max_tokens": 100,
                "messages": [],
                "tools": tools,
            },
            feature="score",
        )


@pytest.mark.parametrize(
    "cache_control",
    [
        {"type": "future"},
        {"type": "ephemeral", "ttl": "2h"},
        {"type": "ephemeral", "ttl": None},
        None,
        "ephemeral",
    ],
)
def test_request_bound_rejects_unknown_cache_controls(cache_control):
    with pytest.raises(AnthropicRequestAdmissionError, match="cache-control"):
        anthropic_request_credit_upper_bound(
            {
                "model": "claude-haiku-4-5",
                "max_tokens": 100,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "cached",
                                "cache_control": cache_control,
                            }
                        ],
                    }
                ],
            },
            feature="score",
        )


def test_request_bound_ignores_cache_control_named_inside_user_tool_json():
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "tools": [
            {
                "name": "lookup",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "cache_control": {"type": "string", "enum": ["future"]},
                    },
                },
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": {"cache_control": {"type": "user-data"}},
                    }
                ],
            }
        ],
    }

    assert anthropic_request_credit_upper_bound(request, feature="score") > 0


def test_request_bound_prices_cache_control_on_nested_tool_result_blocks():
    base = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": [{"type": "text", "text": "result"}],
                    }
                ],
            }
        ],
    }
    cached = {
        **base,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": [
                            {
                                "type": "text",
                                "text": "result",
                                "cache_control": {
                                    "type": "ephemeral",
                                    "ttl": "1h",
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }

    assert anthropic_request_credit_upper_bound(
        cached,
        feature="score",
    ) > anthropic_request_credit_upper_bound(base, feature="score")


def test_request_bound_prices_top_level_automatic_cache_control_like_blocks():
    text = "cache this exact automatic prefix " * 4_000
    base = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }
    automatic_1h = {
        **base,
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }
    block_1h = {
        **base,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            }
        ],
    }

    uncached_bound = anthropic_request_credit_upper_bound(base, feature="score")
    automatic_bound = anthropic_request_credit_upper_bound(
        automatic_1h,
        feature="score",
    )
    block_bound = anthropic_request_credit_upper_bound(block_1h, feature="score")

    # One-hour writes are 2x input price. The small output/protocol component
    # keeps the total just below exactly 2x, but it must be materially above an
    # uncached hold and equivalent to an explicit block breakpoint.
    assert automatic_bound * 10 >= uncached_bound * 19
    assert abs(automatic_bound - block_bound) <= 100


@pytest.mark.parametrize(
    "cache_control",
    [
        {"type": "future"},
        {"type": "ephemeral", "ttl": "2h"},
        {"type": "ephemeral", "ttl": None},
        None,
        "ephemeral",
    ],
)
def test_request_bound_rejects_unknown_top_level_cache_controls(cache_control):
    with pytest.raises(AnthropicRequestAdmissionError, match="cache-control"):
        anthropic_request_credit_upper_bound(
            {
                "model": "claude-haiku-4-5",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "cached"}],
                "cache_control": cache_control,
            },
            feature="score",
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


def test_deadline_scoped_client_still_enforces_provider_admission(db, monkeypatch):
    _live(monkeypatch)
    org, role = _seed(db, balance=0)
    messages = _Messages()

    class _ConfigurableInner:
        def __init__(self):
            self.messages = messages
            self.options = []

        def with_options(self, **kwargs):
            self.options.append(kwargs)
            return self

    inner = _ConfigurableInner()
    client = MeteredAnthropicClient(
        inner=inner,
        organization_id=int(org.id),
    ).with_options(timeout=3.0, max_retries=0)

    with pytest.raises(InsufficientCreditsError):
        _call(client, org, role, trace="deadline-zero-credit")

    assert inner.options == [{"timeout": 3.0, "max_retries": 0}]
    assert messages.calls == 0
    with pytest.raises(UnsupportedAnthropicSurfaceError):
        _ = client.inner


def test_unpriceable_model_is_blocked_before_reservation_or_sdk(
    db, monkeypatch,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )
    unknown = "claude-opus-99-untrusted-secret-marker"

    with pytest.raises(UnpriceableClaudeModelError) as error:
        client.messages.create(
            model=unknown,
            max_tokens=100,
            messages=[{"role": "user", "content": "score this"}],
            metering={
                "feature": "score",
                "organization_id": int(org.id),
                "role_id": int(role.id),
            },
        )

    assert unknown not in str(error.value)
    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0
    assert db.query(UsageEvent).count() == 0
    assert db.query(ClaudeCallLog).count() == 0


def test_workspace_call_gets_hard_org_reservation_without_fake_role(
    db, monkeypatch
):
    _live(monkeypatch)
    org, _role = _seed(db, balance=1_000_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    _call_workspace(client, org, trace="workspace-chat")

    event = db.query(UsageEvent).filter_by(
        organization_id=int(org.id),
        feature="taali_chat",
    ).one()
    assert event.role_id is None
    assert event.event_metadata["credit_reservation"]["state"] == "settled"
    assert db.query(BillingCreditLedger).filter(
        BillingCreditLedger.reason == "reservation:taali_chat",
    ).count() == 1


def test_workspace_call_is_blocked_before_sdk_with_zero_credits(
    db, monkeypatch
):
    _live(monkeypatch)
    org, _role = _seed(db, balance=0)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    with pytest.raises(InsufficientCreditsError):
        _call_workspace(client, org, trace="workspace-zero-credit")

    assert inner.calls == 0


@pytest.mark.parametrize("user_id", [0, -1, True, False, 1.5, "1"])
def test_sync_call_rejects_coercible_user_attribution_before_sdk(
    db,
    monkeypatch,
    user_id,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    with pytest.raises(ProviderAttemptMarkerError, match="user_id"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "score this"}],
            metering={
                "feature": "score",
                "organization_id": int(org.id),
                "role_id": int(role.id),
                "user_id": user_id,
            },
        )

    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0


@pytest.mark.parametrize("declared_live", [False, True])
def test_forged_reservation_payload_never_reaches_provider(
    db,
    monkeypatch,
    declared_live,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )
    forged = {
        "organization_id": int(org.id),
        "feature": "score",
        "amount": _score_request_bound(),
        "external_ref": f"forged:{declared_live}",
        "live": declared_live,
        "role_id": int(role.id),
    }

    with pytest.raises(ProviderAttemptMarkerError, match="does not match"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "score this"}],
            metering={
                "feature": "score",
                "organization_id": int(org.id),
                "role_id": int(role.id),
                "credit_reservation": forged,
            },
        )

    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_forged_shadow_reservation_payload_never_reaches_provider(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    org, role = _seed(db, balance=1_000_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )
    forged = {
        "organization_id": int(org.id),
        "feature": "score",
        "amount": _score_request_bound(),
        "external_ref": "forged:shadow-no-proof",
        "live": False,
        "role_id": int(role.id),
    }

    with pytest.raises(ProviderAttemptMarkerError, match="does not match"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "score this"}],
            metering={
                "feature": "score",
                "organization_id": int(org.id),
                "role_id": int(role.id),
                "credit_reservation": forged,
            },
        )

    assert inner.calls == 0
    assert db.query(BillingCreditLedger).count() == 0


def test_authenticated_shadow_reservation_preserves_role_admission(db, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    org, role = _seed(db, balance=1_000_000, budget_cents=5)
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "score this"}],
    }
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        feature="score",
        trace_id="trusted-shadow",
        amount=_score_request_bound(),
        provider="anthropic",
        model=request["model"],
        request_sha256=provider_request_sha256(request),
    )
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    client.messages.create(
        **request,
        metering={
            "feature": "score",
            "organization_id": int(org.id),
            "role_id": int(role.id),
            "credit_reservation": reservation.as_metering_payload(),
        },
    )

    assert reservation.shadow_proof
    assert inner.calls == 1
    assert db.query(UsageEvent).filter_by(role_id=int(role.id)).count() == 1


@pytest.mark.parametrize(
    "mutation",
    ["user_id", "entity_id", "candidate_id", "model", "request"],
)
def test_v2_reservation_cannot_cross_paid_request_identity(
    db,
    monkeypatch,
    mutation,
):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    org, role = _seed(db, balance=1_000_000)
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "original"}],
    }
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        user_id=101,
        entity_id="application:201",
        candidate_id=301,
        feature="score",
        trace_id=f"immutable-{mutation}",
        amount=_score_request_bound(),
        provider="anthropic",
        model=request["model"],
        request_sha256=provider_request_sha256(request),
    )
    attempted = {**request, "messages": list(request["messages"])}
    metering = {
        "feature": "score",
        "organization_id": int(org.id),
        "role_id": int(role.id),
        "user_id": 101,
        "entity_id": "application:201",
        "candidate_id": 301,
        "credit_reservation": reservation.as_metering_payload(),
    }
    if mutation == "request":
        attempted["messages"] = [{"role": "user", "content": "changed"}]
    elif mutation == "model":
        attempted["model"] = "claude-sonnet-4-5"
    else:
        metering[mutation] = {
            "user_id": 102,
            "entity_id": "application:202",
            "candidate_id": 302,
        }[mutation]
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    with pytest.raises(ProviderAttemptMarkerError, match="does not match"):
        client.messages.create(**attempted, metering=metering)

    assert inner.calls == 0


def test_historical_v1_live_hold_cannot_authorize_a_new_provider_call(
    db,
    monkeypatch,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    amount = _score_request_bound()
    external_ref = "historical:v1:provider-authority"
    org.credits_balance -= amount
    db.add(
        BillingCreditLedger(
            organization_id=int(org.id),
            delta=-amount,
            balance_after=int(org.credits_balance),
            reason="reservation:score",
            external_ref=external_ref,
            entry_metadata={
                "feature": "score",
                "reserved": amount,
                "role_id": int(role.id),
                "state": "held",
            },
        )
    )
    db.commit()
    reservation = CreditReservation(
        organization_id=int(org.id),
        feature="score",
        amount=amount,
        external_ref=external_ref,
        live=True,
        role_id=int(role.id),
    )
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    with pytest.raises(ProviderAttemptMarkerError, match="does not match"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "new request"}],
            metering={
                "feature": "score",
                "organization_id": int(org.id),
                "role_id": int(role.id),
                "credit_reservation": reservation.as_metering_payload(),
            },
        )

    assert inner.calls == 0
    assert db.query(BillingCreditLedger).filter_by(external_ref=external_ref).count() == 1


def test_historical_v1_shadow_identity_cannot_mark_a_provider_attempt():
    historical = CreditReservation(
        organization_id=1,
        feature="score",
        amount=10,
        external_ref="historical:v1:shadow-marker",
        live=False,
    )

    assert mark_provider_attempt_started(
        historical,
        provider="anthropic",
    ) is False


def test_forged_v2_shadow_proof_cannot_mark_a_provider_attempt(monkeypatch):
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False)
    forged = CreditReservation(
        organization_id=1,
        feature="score",
        amount=10,
        external_ref="forged:v2:shadow-marker",
        live=False,
        version=2,
        provider="anthropic",
        model="claude-haiku-4-5",
        request_sha256="a" * 64,
        shadow_proof="not-a-valid-proof",
    )

    assert mark_provider_attempt_started(
        forged,
        provider="anthropic",
    ) is False


def test_v2_hold_requires_explicit_null_owner_metadata_before_provider(
    db,
    monkeypatch,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "score this"}],
    }
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        feature="score",
        trace_id="missing-explicit-null",
        entity_id="application:42",
        provider="anthropic",
        model=request["model"],
        request_sha256=provider_request_sha256(request),
        amount=_score_request_bound(),
    )
    hold = db.query(BillingCreditLedger).filter_by(
        external_ref=reservation.external_ref,
    ).one()
    metadata = dict(hold.entry_metadata)
    metadata.pop("reservation_candidate_id")
    hold.entry_metadata = metadata
    db.commit()
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )

    with pytest.raises(ProviderAttemptMarkerError, match="does not match"):
        client.messages.create(
            **request,
            metering={
                "feature": "score",
                "organization_id": int(org.id),
                "role_id": int(role.id),
                "entity_id": "application:42",
                "credit_reservation": reservation.as_metering_payload(),
            },
        )

    assert inner.calls == 0


@pytest.mark.parametrize(
    ("request_feature", "request_role"),
    [("assessment", None), ("taali_chat", "same")],
)
def test_mismatched_valid_hold_is_not_released_or_used(
    db,
    monkeypatch,
    request_feature,
    request_role,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        feature="assessment",
        trace_id="unrelated-valid-hold",
        amount=60_000,
    )
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )
    role_id = int(role.id) if request_role == "same" else None

    with pytest.raises(ProviderAttemptMarkerError, match="does not match"):
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=100,
            messages=[{"role": "user", "content": "unrelated request"}],
            metering={
                "feature": request_feature,
                "organization_id": int(org.id),
                "role_id": role_id,
                "credit_reservation": reservation.as_metering_payload(),
            },
        )

    db.expire_all()
    hold = db.query(BillingCreditLedger).filter_by(
        external_ref=reservation.external_ref
    ).one()
    assert inner.calls == 0
    assert hold.reason == "reservation:assessment"
    assert hold.entry_metadata["state"] == "held"
    assert db.query(BillingCreditLedger).filter(
        BillingCreditLedger.external_ref == f"{reservation.external_ref}:settled"
    ).count() == 0
    assert db.get(Organization, int(org.id)).credits_balance == 940_000


def test_role_scoped_live_payload_must_carry_exact_role_for_markers(db, monkeypatch):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=int(role.id),
        feature="score",
        trace_id="strict-marker-role",
        amount=20_000,
    )
    forged = reservation.as_metering_payload()
    forged.pop("role_id")

    assert mark_provider_attempt_started(forged, provider="anthropic") is False
    assert mark_provider_usage_succeeded(
        forged,
        deferred_usage_event=None,
        provider="anthropic",
    ) is False
    db.expire_all()
    hold = db.query(BillingCreditLedger).filter_by(
        external_ref=reservation.external_ref
    ).one()
    assert hold.entry_metadata["state"] == "held"


def test_mismatched_payload_cannot_claim_success_or_report_release(db, monkeypatch):
    _live(monkeypatch)
    org, _role = _seed(db, balance=1_000_000)
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=None,
        feature="score",
        trace_id="strict-marker-amount",
        amount=20_000,
    )
    forged = {**reservation.as_metering_payload(), "amount": 19_999}
    rejection = RuntimeError("invalid request")
    rejection.status_code = 400

    assert mark_provider_attempt_started(forged, provider="anthropic") is False
    assert mark_provider_usage_succeeded(
        forged,
        deferred_usage_event=None,
        provider="anthropic",
    ) is False
    assert release_provider_usage_if_definitely_nonbillable(
        forged,
        error=rejection,
        reason="forged-release",
    ) is False

    db.expire_all()
    hold = db.query(BillingCreditLedger).filter_by(
        external_ref=reservation.external_ref
    ).one()
    assert hold.entry_metadata["state"] == "held"
    assert db.query(BillingCreditLedger).filter_by(
        external_ref=f"{reservation.external_ref}:settled"
    ).count() == 0


def test_release_helper_reports_false_when_ledger_write_fails(db, monkeypatch):
    _live(monkeypatch)
    org, _role = _seed(db, balance=1_000_000)
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=None,
        feature="score",
        trace_id="release-db-failure",
        amount=20_000,
    )
    rejection = RuntimeError("invalid request")
    rejection.status_code = 400
    monkeypatch.setattr(
        "app.services.provider_usage_admission.release_credit_reservation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    assert release_provider_usage_if_definitely_nonbillable(
        reservation,
        error=rejection,
        reason="db-failure",
    ) is False


def test_other_org_settlement_suffix_cannot_change_exact_hold_marker_scope(
    db,
    monkeypatch,
):
    _live(monkeypatch)
    org, _role = _seed(db, balance=1_000_000)
    other = Organization(name="Other marker org", slug=f"other-marker-{id(db)}")
    db.add(other)
    db.commit()
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=None,
        feature="score",
        trace_id="cross-org-settlement-suffix",
        amount=20_000,
        provider="anthropic",
    )
    db.add(
        BillingCreditLedger(
            organization_id=int(other.id),
            delta=0,
            balance_after=0,
            reason="reservation_release:score",
            external_ref=f"{reservation.external_ref}:settled",
            entry_metadata={"state": "released"},
        )
    )
    db.commit()

    assert mark_provider_attempt_started(reservation, provider="anthropic") is True


def test_provider_marker_identity_cannot_be_swapped_after_attempt(db, monkeypatch):
    _live(monkeypatch)
    org, _role = _seed(db, balance=1_000_000)
    reservation = reserve_provider_usage(
        organization_id=int(org.id),
        role_id=None,
        feature="graph_sync",
        trace_id="provider-identity",
        amount=20_000,
        provider="voyage",
    )

    assert mark_provider_attempt_started(reservation, provider="voyage") is True
    assert mark_provider_attempt_started(reservation, provider="anthropic") is False
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event=None,
        provider="anthropic",
    ) is False
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event=None,
        provider="voyage",
    ) is True
    assert mark_provider_usage_succeeded(
        reservation,
        deferred_usage_event=None,
        provider="anthropic",
    ) is False

    db.expire_all()
    hold = db.query(BillingCreditLedger).filter_by(
        external_ref=reservation.external_ref
    ).one()
    assert hold.entry_metadata["provider"] == "voyage"


def test_each_sdk_attempt_uses_fresh_request_bound_hold(db, monkeypatch):
    _live(monkeypatch)
    # Request-specific bounds release unused capacity after settlement, so a
    # second small call remains admissible under this exact 3-cent cap.
    org, role = _seed(db, balance=1_000_000, budget_cents=3)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    _call(client, org, role, trace="multi-call:1")
    _call(client, org, role, trace="multi-call:2")

    assert inner.calls == 2
    events = db.query(UsageEvent).filter_by(role_id=int(role.id)).all()
    assert len(events) == 2
    assert all(
        event.event_metadata["credit_reservation"]["state"] == "settled"
        for event in events
    )
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .count()
        == 2
    )
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .all()
    )
    assert all(
        hold.entry_metadata["state"] == PROVIDER_SUCCEEDED_PENDING_STATE
        and hold.entry_metadata["deferred_usage_event"]["feature"] == "score"
        and hold.entry_metadata["deferred_usage_event"]["role_id"] == int(role.id)
        for hold in holds
    )


def test_reused_caller_metering_dict_gets_fresh_hold_per_sdk_call(
    db,
    monkeypatch,
):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    inner = _Messages()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner),
        organization_id=int(org.id),
    )
    metering = {
        "feature": "score",
        "organization_id": int(org.id),
        "role_id": int(role.id),
        "entity_id": "application:42",
        "trace_id": "same-caller-context",
    }
    request = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "score this"}],
    }

    client.messages.create(**request, metering=metering)
    client.messages.create(**request, metering=metering)

    assert inner.calls == 2
    assert "credit_reservation" not in metering
    assert (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .count()
        == 2
    )


def test_ambiguous_sdk_failure_retains_automatic_hold(db, monkeypatch, caplog):
    _live(monkeypatch)
    starting = 1_000_000
    org, role = _seed(db, balance=starting)
    secret_marker = "sk-ant-secret-sync-marker"
    inner = _Messages(
        fail=True,
        failure_message=f"provider unavailable body={secret_marker}",
    )
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        _call(client, org, role, trace="provider-failure")

    db.refresh(org)
    assert inner.calls == 1
    assert org.credits_balance == starting - _score_request_bound()
    assert db.query(UsageEvent).filter_by(role_id=int(role.id)).count() == 0
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_ATTEMPT_STARTED_STATE
    call_log = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.status == "sdk_ambiguous_error")
        .one()
    )
    assert call_log.error_reason == "anthropic_create:RuntimeError"
    assert secret_marker not in str(call_log.error_reason)
    assert secret_marker not in caplog.text


def test_timeout_retry_uses_fresh_hold_and_logs_each_wire_attempt(db, monkeypatch):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    monkeypatch.setattr(
        provider_retry_policy,
        "sleep_before_retry",
        lambda **_kwargs: None,
    )

    class _TimeoutThenSuccess(_Messages):
        def create(self, **kwargs):
            if self.calls == 0:
                self.calls += 1
                raise TimeoutError("first response timed out")
            return super().create(**kwargs)

    inner = _TimeoutThenSuccess()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )

    response = _call(client, org, role, trace="timeout-then-success")

    assert response.id == "msg-2"
    assert inner.calls == 2
    holds = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .order_by(BillingCreditLedger.id.asc())
        .all()
    )
    assert len(holds) == 2
    assert holds[0].external_ref != holds[1].external_ref
    assert (
        holds[0].entry_metadata["reservation_request_sha256"]
        == holds[1].entry_metadata["reservation_request_sha256"]
    )
    assert holds[0].entry_metadata["state"] == PROVIDER_ATTEMPT_STARTED_STATE
    logs = (
        db.query(ClaudeCallLog)
        .filter(ClaudeCallLog.organization_id == int(org.id))
        .order_by(ClaudeCallLog.id.asc())
        .all()
    )
    assert [(row.status, row.retry_attempt) for row in logs] == [
        ("sdk_ambiguous_error", 0),
        ("ok", 1),
    ]
    assert logs[1].parent_call_log_id == logs[0].id
    assert db.query(UsageEvent).filter_by(role_id=int(role.id)).count() == 1


def test_timeout_retry_stops_when_failure_log_cannot_persist(db, monkeypatch):
    _live(monkeypatch)
    org, role = _seed(db, balance=1_000_000)
    monkeypatch.setattr(
        provider_retry_policy,
        "sleep_before_retry",
        lambda **_kwargs: None,
    )

    class _Timeout(_Messages):
        def create(self, **_kwargs):
            self.calls += 1
            raise TimeoutError("provider result uncertain")

    inner = _Timeout()
    client = MeteredAnthropicClient(
        inner=SimpleNamespace(messages=inner), organization_id=int(org.id)
    )
    monkeypatch.setattr(client.messages, "_record_call_log_safe", lambda **_: None)

    with pytest.raises(
        provider_retry_policy.ProviderRetryEvidenceUnavailableError,
        match="retry evidence",
    ):
        _call(client, org, role, trace="timeout-evidence-down")

    assert inner.calls == 1
    hold = (
        db.query(BillingCreditLedger)
        .filter(BillingCreditLedger.reason == "reservation:score")
        .one()
    )
    assert hold.entry_metadata["state"] == PROVIDER_ATTEMPT_STARTED_STATE


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
    assert (
        db.get(Organization, int(org.id)).credits_balance
        == 100_000 - _score_request_bound()
    )


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
    assert (
        db.get(Organization, int(org.id)).credits_balance
        == 100_000 - _score_request_bound()
    )
