"""Hard credit admission for one provider-owned Agent SDK query."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Optional

from ....services.claude_model_pricing import require_priceable_claude_model
from ....services.pricing_service import credits_charged, raw_cost_usd_micro
from ....services.usage_credit_reservations import CreditReservation, reserve_credits


# Exact synchronous Messages limits for every model this service may send.
# Keep this narrow: enabling a model without reviewing its provider limits must
# fail closed. Values are (context window, maximum output tokens).
_MODEL_TOKEN_LIMITS: dict[str, tuple[int, int]] = {
    "claude-haiku-4-5": (200_000, 64_000),
    "claude-sonnet-4-5": (200_000, 64_000),
    "claude-sonnet-4-6": (1_000_000, 128_000),
    "claude-opus-4-5": (200_000, 64_000),
}


def _one_call_raw_upper_bound(*, model: str) -> int:
    """Return the maximum raw micro-USD for one model request.

    Input plus generated output cannot exceed the context window. The maximum
    of the two linear-cost endpoints covers both possibilities: all context as
    the most expensive one-hour cache write, or maximal output plus the
    remaining context as that cache-write tier.
    """

    family = require_priceable_claude_model(model)
    try:
        context_tokens, max_output_tokens = _MODEL_TOKEN_LIMITS[family]
    except KeyError as exc:
        raise ValueError(
            "Claude Agent SDK model limits have not been reviewed"
        ) from exc
    output_tokens = min(max_output_tokens, context_tokens)

    def _cost(*, cached_input: int, output: int) -> int:
        return raw_cost_usd_micro(
            input_tokens=0,
            output_tokens=output,
            cache_creation_tokens=cached_input,
            cache_creation_1h_tokens=cached_input,
            model=model,
        )

    return max(
        _cost(cached_input=context_tokens, output=0),
        _cost(
            cached_input=context_tokens - output_tokens,
            output=output_tokens,
        ),
    )


def _query_raw_upper_bound(
    *, model: str, max_turns: int, stop_threshold_usd: float
) -> tuple[int, int]:
    """Return ``(query, one-call)`` raw micro-USD bounds."""

    if int(max_turns) <= 0:
        raise ValueError("Claude Agent SDK max_turns must be positive")
    try:
        threshold = Decimal(str(stop_threshold_usd))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Claude Agent SDK budget threshold is invalid") from exc
    if not threshold.is_finite() or threshold <= 0:
        raise ValueError("Claude Agent SDK budget threshold must be positive")
    threshold_micro = int(
        (threshold * Decimal(1_000_000)).to_integral_value(rounding=ROUND_UP)
    )
    one_call = _one_call_raw_upper_bound(model=model)
    # The SDK checks max_budget_usd after an internal request. It can exceed
    # that stop threshold by at most one request, while max_turns independently
    # bounds the total number of requests.
    return min(int(max_turns) * one_call, threshold_micro + one_call), one_call


def reserve_sdk_query_credits(
    *,
    organization_id: int,
    assessment_id: int,
    role_id: Optional[int],
    feature: str,
    sub_feature: str,
    trace_id: str,
    model: str,
    max_turns: int,
    stop_threshold_usd: float,
    request_sha256: str,
) -> CreditReservation:
    """Commit a conservative org hold and the optional role-budget hold."""

    from ....platform.database import SessionLocal  # noqa: WPS433

    query_bound, one_call_bound = _query_raw_upper_bound(
        model=model,
        max_turns=max_turns,
        stop_threshold_usd=stop_threshold_usd,
    )
    held = credits_charged(feature=feature, cost_usd_micro=query_bound)
    with SessionLocal() as meter_db:
        reservation = reserve_credits(
            meter_db,
            organization_id=int(organization_id),
            feature=feature,
            amount=held,
            external_ref=f"usage-hold:{trace_id}:{uuid.uuid4().hex}",
            metadata={
                "source": "claude_agent_sdk_aggregated",
                "sub_feature": sub_feature,
                "assessment_id": int(assessment_id),
                "trace_id": trace_id,
                "provider_budget_usd": float(stop_threshold_usd),
                "provider_one_call_raw_upper_bound_micro": one_call_bound,
                "provider_query_raw_upper_bound_micro": query_bound,
                "provider_max_turns": int(max_turns),
            },
            role_id=int(role_id) if role_id is not None else None,
            entity_id=f"assessment:{int(assessment_id)}",
            provider="claude_agent_sdk",
            model=model,
            request_sha256=request_sha256,
            enforce_role_budget=role_id is not None,
        )
        meter_db.commit()
        return reservation


__all__ = ["reserve_sdk_query_credits"]
