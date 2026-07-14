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
- One aggregated ``ClaudeCallLog`` row is linked to the aggregated event;
  the event's source tag identifies it without pretending to expose
  individual SDK-internal requests.

Session policy
--------------

This writer opens its own fresh ``SessionLocal()`` and commits the canonical
``record_event`` result, live credit debit, and linked call-log row together.
It mirrors ``MeteredAnthropicClient._write_event`` (see the metering memory
re: the #253 FK-race that came from joining the caller's open session).  The
caller passes a ``db`` kwarg for API consistency, but it is deliberately
ignored.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Any, Optional

from ....services.pricing_service import Feature, raw_cost_usd_micro
from ....services.provider_usage_admission import mark_provider_usage_succeeded

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
    role_id: Optional[int] = None,
    trace_id: Optional[str] = None,
    call_status: str = "ok",
    extra_metadata: Optional[dict] = None,
    credit_reservation: Optional[dict] = None,
) -> Optional[int]:
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

    The event then flows through ``usage_metering_service.record_event`` so
    feature markup and the optional live ledger debit use the single canonical
    implementation.

    Never raises. Metering must not break a chat call.
    """
    # Lazy imports — the metering pipeline pulls in DB engines, settings,
    # and SQLAlchemy; we don't want any of that at module-import time so
    # the service can be imported in branches without a configured DB
    # (e.g. CI architecture-gate runs).
    from ....models.claude_call_log import ClaudeCallLog  # noqa: WPS433
    from ....platform.database import SessionLocal  # noqa: WPS433
    from ....services.usage_metering_service import record_event  # noqa: WPS433

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
    try:
        sdk_cost_usd = float(total_cost_usd or 0.0)
        if not math.isfinite(sdk_cost_usd) or sdk_cost_usd <= 0:
            sdk_cost_usd = 0.0
    except (TypeError, ValueError):
        sdk_cost_usd = 0.0
    sdk_cost_micro = int(round(sdk_cost_usd * 1_000_000))
    try:
        estimated_cost_micro = raw_cost_usd_micro(
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            cache_read_tokens=int(cache_read_input_tokens or 0),
            cache_creation_tokens=int(cache_creation_input_tokens or 0),
            cache_creation_1h_tokens=None,  # SDK doesn't expose the split
            model=model or None,
        )
    except Exception:
        logger.exception(
            "claude_agent_sdk meter: local token-cost estimate failed "
            "(org=%s assessment=%s model=%s)",
            organization_id,
            assessment_id,
            model,
        )
        estimated_cost_micro = 0
    cost_usd_micro = sdk_cost_micro if sdk_cost_micro > 0 else int(estimated_cost_micro)

    metadata: dict[str, Any] = {
        "source": _SOURCE_TAG,
        "sub_feature": sub_feature,
        "assessment_id": assessment_id,
        "num_turns": int(num_turns or 0),
        "sdk_total_cost_usd": sdk_cost_usd,
        "estimated_cost_usd_micro": int(estimated_cost_micro),
        "cost_source": "sdk_reported" if sdk_cost_micro > 0 else "token_estimate",
    }
    if extra_metadata:
        # Preserve call-specific diagnostics supplied by the service.
        metadata.update(
            json.loads(json.dumps(dict(extra_metadata), default=str))
        )
    if trace_id:
        # The explicit attribution parameter is authoritative so UsageEvent
        # metadata and ClaudeCallLog can never diverge on correlation.
        metadata["trace_id"] = str(trace_id)

    event_payload = {
        "organization_id": int(organization_id),
        "feature": feature_enum.value,
        "model": str(model or "(unknown)"),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": int(cache_read_input_tokens or 0),
        "cache_creation_tokens": int(cache_creation_input_tokens or 0),
        "cache_creation_1h_tokens": None,
        "cache_hit": False,
        "role_id": int(role_id) if role_id is not None else None,
        "entity_id": f"assessment:{int(assessment_id)}",
        "metadata": metadata,
        # A positive SDK total sees the provider's internal cache mix and is
        # more accurate than local token pricing. Recovery must preserve this
        # override instead of re-pricing aggregated internal calls locally.
        "provider_cost_usd_micro": (
            sdk_cost_micro if sdk_cost_micro > 0 else None
        ),
    }
    if credit_reservation:
        mark_provider_usage_succeeded(
            credit_reservation,
            deferred_usage_event=event_payload,
            provider="claude_agent_sdk",
        )

    try:
        with SessionLocal() as session:
            event = record_event(
                session,
                **event_payload,
                credit_reservation=credit_reservation,
            )
            # The provider-owned SDK loop cannot expose one row per internal
            # Anthropic request.  Persist one explicitly-aggregated call-log
            # row linked to the UsageEvent so reconciliation sees both the
            # paid total and its customer/role attribution.
            session.add(
                ClaudeCallLog(
                    organization_id=int(organization_id),
                    model=str(model or "(unknown)"),
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    cache_read_tokens=int(cache_read_input_tokens or 0),
                    cache_creation_tokens=int(cache_creation_input_tokens or 0),
                    cache_creation_1h_tokens=None,
                    cost_usd_micro=int(event.cost_usd_micro or cost_usd_micro),
                    feature_hint=feature_enum.value,
                    status=str(call_status or "ok"),
                    error_reason=None,
                    anthropic_request_id=None,
                    usage_event_id=int(event.id),
                    trace_id=str(trace_id) if trace_id else None,
                )
            )
            session.commit()
            return int(event.id)
    except Exception as exc:
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
        # The provider call already returned trustworthy totals.  If the
        # canonical usage/debit transaction itself fails, preserve those
        # totals in the reconciliation oracle with a NULL usage_event_id.
        # This does not debit credits twice and makes the attribution gap
        # repairable instead of silently losing paid spend.
        _write_call_log_evidence(
            organization_id=int(organization_id),
            feature=feature_enum,
            model=model,
            status="usage_event_write_failed",
            error_reason=f"canonical usage/debit write failed: {exc}",
            trace_id=trace_id,
            error_class="other",
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            cache_read_tokens=int(cache_read_input_tokens or 0),
            cache_creation_tokens=int(cache_creation_input_tokens or 0),
            cost_usd_micro=int(cost_usd_micro),
        )
        return None


def _write_call_log_evidence(
    *,
    organization_id: int,
    feature: Feature,
    model: str,
    status: str,
    error_reason: str,
    trace_id: Optional[str],
    error_class: Optional[str],
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    cost_usd_micro: int,
) -> bool:
    """Write one reconciliation-only call row in an independent session."""
    from ....models.claude_call_log import ClaudeCallLog  # noqa: WPS433
    from ....platform.database import SessionLocal  # noqa: WPS433

    try:
        with SessionLocal() as session:
            session.add(
                ClaudeCallLog(
                    organization_id=int(organization_id),
                    model=str(model or "(unknown)"),
                    input_tokens=max(int(input_tokens or 0), 0),
                    output_tokens=max(int(output_tokens or 0), 0),
                    cache_read_tokens=max(int(cache_read_tokens or 0), 0),
                    cache_creation_tokens=max(int(cache_creation_tokens or 0), 0),
                    cache_creation_1h_tokens=None,
                    cost_usd_micro=max(int(cost_usd_micro or 0), 0),
                    feature_hint=feature.value,
                    status=str(status or "incomplete"),
                    error_reason=str(error_reason or "")[:2000] or None,
                    anthropic_request_id=None,
                    usage_event_id=None,
                    error_class=str(error_class) if error_class else None,
                    trace_id=str(trace_id) if trace_id else None,
                )
            )
            session.commit()
        return True
    except Exception:
        logger.exception(
            "claude_agent_sdk meter: failed to persist reconciliation evidence "
            "(org=%s status=%s)",
            organization_id,
            status,
        )
        return False


def write_incomplete_call_evidence(
    *,
    organization_id: int,
    assessment_id: int,
    feature: str,
    sub_feature: str,
    model: str,
    status: str,
    error_reason: str,
    trace_id: Optional[str] = None,
    error_class: Optional[str] = None,
) -> bool:
    """Persist an SDK invocation that ended without trustworthy usage totals.

    A missing ``ResultMessage`` does *not* prove that Anthropic billed zero; it
    only means this process cannot know the tokens or cost.  Recording a zero-
    cost ``UsageEvent`` would fabricate a completed billable event and could
    debit the customer incorrectly.  Instead we write a reconciliation-only
    ``ClaudeCallLog`` attempt with zero token/cost fields and an explicit
    incomplete status.  Admin-API reconciliation can then surface the gap.
    """
    try:
        feature_enum = Feature(feature)
    except (ValueError, KeyError):
        feature_enum = Feature.OTHER

    reason = (
        f"assessment_id={int(assessment_id)} sub_feature={sub_feature}; "
        f"{str(error_reason or 'usage totals unavailable')}"
    )[:2000]
    return _write_call_log_evidence(
        organization_id=int(organization_id),
        feature=feature_enum,
        model=model,
        status=status,
        error_reason=reason,
        trace_id=trace_id,
        error_class=error_class,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        cost_usd_micro=0,
    )
