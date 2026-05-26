"""Aggregated ``UsageEvent`` writer for ``claude_agent_sdk.query()`` calls.

The SDK spawns the bundled Claude Code CLI as a Node subprocess; the
billable Anthropic calls happen inside that subprocess, beyond the reach
of ``MeteredAnthropicClient``. Per-internal-call attribution for THIS
code path is therefore impossible without re-implementing the wire
protocol — which we explicitly chose not to do.

The compromise the user signed off on (2026-05-26):

  Write **one aggregated ``UsageEvent``** per ``query()`` invocation
  capturing the full chat-turn usage — input + output + cache_read +
  cache_creation tokens + ``total_cost_usd`` + num_turns. Tag the row
  ``metadata={"source": "claude_agent_sdk_aggregated"}`` so the daily
  Admin-API reconciliation knows this row spans multiple internal calls
  and doesn't try to align it to per-call billing data.

Trade-off
---------

- Total per-org spend stays accurate (the aggregated usage equals the
  sum of every internal Anthropic call the SDK made).
- Per-internal-call drill-down for chat is lost. Acceptable because
  chat is hard-bounded to ~$1 per turn and reconciliation works at
  the daily-total level.
- ``ClaudeCallLog`` rows are NOT written for SDK-managed calls. The
  agentic_chat path writes one call_log per internal turn; this path
  collapses them.

Session policy
--------------

This writer opens its own fresh ``SessionLocal()`` and commits inside —
mirrors ``MeteredAnthropicClient._write_event`` (see the metering memory
re: the #253 FK-race that came from joining the caller's open session).
The caller passes a ``db`` kwarg for API consistency, but it is
**deliberately ignored**.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ....services.pricing_service import (
    Feature,
    credits_charged,
    feature_pricing,
    raw_cost_usd_micro,
)

logger = logging.getLogger("taali.claude_agent_sdk.metering")


_SOURCE_TAG = "claude_agent_sdk_aggregated"


def write_aggregated_usage_event(
    *,
    db: Any = None,  # accepted for API parity; intentionally ignored
    organization_id: int,
    assessment_id: int,
    feature: str,
    sub_feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
    total_cost_usd: float,
    num_turns: int,
    extra_metadata: Optional[dict] = None,
) -> None:
    """Write a single ``UsageEvent`` row aggregating one full SDK invocation.

    Tagged ``metadata={"source": "claude_agent_sdk_aggregated", ...}`` so
    the daily Admin-API reconciliation knows this row covers multiple
    internal Anthropic calls and doesn't double-count.

    Pricing
    -------

    We compute ``cost_usd_micro`` two ways and prefer the SDK-reported
    ``total_cost_usd`` (converted to micro-USD) when available — the SDK
    sees the actual cache-hit / 5m vs 1h split inside the CLI subprocess
    and is therefore more accurate than our local estimate. The local
    ``raw_cost_usd_micro`` estimate is logged as a fallback only.

    ``credits_charged`` runs through ``pricing_service.credits_charged``
    with ``cache_hit=False`` (cache hits are already netted into
    ``total_cost_usd`` by the SDK; double-discounting would under-bill).

    Never raises. Metering must not break a chat call.
    """
    # Lazy imports — the metering pipeline pulls in DB engines, settings,
    # and SQLAlchemy; we don't want any of that at module-import time so
    # the service can be imported in branches without a configured DB
    # (e.g. CI architecture-gate runs).
    from ....models.usage_event import UsageEvent  # noqa: WPS433
    from ....platform.database import SessionLocal  # noqa: WPS433

    try:
        feature_enum = Feature(feature)
    except (ValueError, KeyError):
        logger.warning(
            "claude_agent_sdk meter: unknown feature=%r, falling back to OTHER",
            feature,
        )
        feature_enum = Feature.OTHER

    # Cost: prefer the SDK number (it sees per-call cache splits inside
    # the CLI subprocess). Fall back to our local estimate when the SDK
    # didn't report one (network errors etc.).
    sdk_cost_micro = int(round(max(float(total_cost_usd or 0.0), 0.0) * 1_000_000))
    estimated_cost_micro = raw_cost_usd_micro(
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cache_read_tokens=int(cache_read_input_tokens or 0),
        cache_creation_tokens=int(cache_creation_input_tokens or 0),
        cache_creation_1h_tokens=None,  # SDK doesn't expose the split
        model=model or None,
    )
    cost_usd_micro = sdk_cost_micro if sdk_cost_micro > 0 else int(estimated_cost_micro)

    pricing = feature_pricing(feature_enum)
    multiplier = pricing.markup_multiplier
    charged = credits_charged(
        feature=feature_enum,
        cost_usd_micro=cost_usd_micro,
        cache_hit=False,
    )

    metadata: dict[str, Any] = {
        "source": _SOURCE_TAG,
        "sub_feature": sub_feature,
        "assessment_id": assessment_id,
        "num_turns": int(num_turns or 0),
        "sdk_total_cost_usd": float(total_cost_usd or 0.0),
        "estimated_cost_usd_micro": int(estimated_cost_micro),
    }
    if extra_metadata:
        # Caller wins on key collisions — useful for trace_ids etc.
        metadata.update(extra_metadata)

    try:
        with SessionLocal() as session:
            event = UsageEvent(
                organization_id=int(organization_id),
                user_id=None,
                role_id=None,
                feature=feature_enum.value,
                entity_id=str(assessment_id),
                model=str(model or "(unknown)"),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cache_read_tokens=int(cache_read_input_tokens or 0),
                cache_creation_tokens=int(cache_creation_input_tokens or 0),
                cache_creation_1h_tokens=None,  # SDK doesn't expose the 5m/1h split
                cost_usd_micro=int(cost_usd_micro),
                markup_multiplier=multiplier,
                credits_charged=int(charged),
                cache_hit=0,
                event_metadata=metadata,
            )
            session.add(event)
            session.commit()
    except Exception:
        # Defensive: never propagate. A chat turn that succeeded but
        # failed to write its meter is still useful — the SDK call already
        # happened and we're not going to un-bill Anthropic.
        logger.exception(
            "claude_agent_sdk meter: failed to write aggregated UsageEvent "
            "(org=%s assessment=%s model=%s tokens_in=%s tokens_out=%s cost=$%s)",
            organization_id,
            assessment_id,
            model,
            input_tokens,
            output_tokens,
            total_cost_usd,
        )
