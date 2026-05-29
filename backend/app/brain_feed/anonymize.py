"""Pure transforms: source rows -> anonymized brain-feed payloads.

This module is the privacy boundary for the outbound mainspring feed. Every
record shipped to the cross-vertical brain passes through here, and the
invariant is strict: **no PII, no free text, no raw row ids, no role titles**.

What we keep is the aggregable *learning shape*:
  - decisions: the agent's recommendation + confidence + model/prompt version,
    and crucially the human's disposition (did the recruiter agree, override,
    or teach) — that agree/disagree signal is what lets the brain learn.
  - outcomes: the teach-loop's structured attribution (failure mode, which
    sub-agent, over/under), correlatable to its decision via an opaque ref.
  - usage: per-day, per-(feature, model) token + cost rollups.

Identity is carried only as salted, namespaced one-way hashes (``_ref`` /
``_cohort``): enough for the brain to correlate a decision with its outcome and
to group by role, never enough to recover who or which role. Free-text columns
(``reasoning``, ``evidence``, ``correction_text``, ``graph_write_hints``) and
foreign keys to people are deliberately dropped, not hashed.

Functions are pure (no DB, no clock) so the no-leak invariant is unit-testable.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

# The vertical this platform represents in the cross-vertical brain. Constant
# for Tali; mainspring groups/segregates signal by it.
VERTICAL = "hiring"

# Namespacing salt for the one-way identity hashes. Not a secret (the hashes
# are opaque regardless), but namespacing keeps a Tali "decision-7" ref from
# ever colliding with another vertical's "decision-7".
_HASH_NAMESPACE = "mainspring-brain-feed:hiring"
_REF_LEN = 16


def _ref(kind: str, raw_id: Any) -> str:
    """Stable, opaque, one-way reference for a row id.

    The same ``(kind, raw_id)`` always yields the same ref, so a decision and
    the outcome that teaches against it share a join key — without either
    payload carrying the raw id.
    """
    h = hashlib.sha256(f"{_HASH_NAMESPACE}:{kind}:{raw_id}".encode()).hexdigest()
    return h[:_REF_LEN]


def _cohort(organization_id: Any, role_id: Any) -> str:
    """Opaque grouping key for a role, so the brain can aggregate per-role
    without learning which org or which role."""
    h = hashlib.sha256(
        f"{_HASH_NAMESPACE}:cohort:{organization_id}:{role_id}".encode()
    ).hexdigest()
    return h[:_REF_LEN]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _round_conf(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _token_shape(token_spend: dict[str, Any] | None) -> dict[str, int]:
    """Keep only numeric cost totals from a decision's token_spend roll-up.

    Drops ``by_agent`` and anything non-numeric — the brain gets the cost shape,
    not the internal decomposition.
    """
    src = token_spend or {}
    out: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "total_micro_usd",
    ):
        raw = src.get(key)
        try:
            out[key] = int(raw or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


def decision_event_id(decision_id: int) -> str:
    return f"decision-{int(decision_id)}"


def outcome_event_id(feedback_id: int) -> str:
    return f"outcome-{int(feedback_id)}"


def usage_event_id(day: str, feature: str, model: str) -> str:
    return f"usage-{day}-{feature}-{model}"


def decision_payload(decision: Any) -> dict[str, Any]:
    """Anonymize a *resolved* AgentDecision into a brain-feed payload.

    Only resolved decisions carry learning signal (the human disposition);
    callers must filter to resolved rows before calling.
    """
    return {
        "vertical": VERTICAL,
        "ref": _ref("decision", decision.id),
        "cohort": _cohort(decision.organization_id, decision.role_id),
        "decision_type": decision.decision_type,
        "recommendation": decision.recommendation,
        "confidence": _round_conf(decision.confidence),
        "model_version": decision.model_version,
        "prompt_version": decision.prompt_version,
        "status": decision.status,
        "human_disposition": decision.human_disposition,
        "override_action": decision.override_action,
        # Derived convenience: did the human agree with the agent's call?
        "agreed": decision.human_disposition == "approved",
        "active_capabilities": dict(decision.active_capabilities or {}),
        "token_shape": _token_shape(decision.token_spend),
        "created_at": _iso(decision.created_at),
        "resolved_at": _iso(decision.resolved_at),
    }


def outcome_payload(feedback: Any) -> dict[str, Any]:
    """Anonymize a DecisionFeedback (teach-loop) row into a brain-feed payload."""
    return {
        "vertical": VERTICAL,
        "ref": _ref("outcome", feedback.id),
        # Correlates to the decision payload's ``ref`` — same one-way function.
        "decision_ref": _ref("decision", feedback.decision_id),
        "failure_mode": feedback.failure_mode,
        "scope": feedback.scope,
        "attributed_to": feedback.attributed_to,
        "direction": feedback.direction,
        "applied": feedback.applied_at is not None,
        "reverted": feedback.reverted_at is not None,
        "created_at": _iso(feedback.created_at),
    }


def usage_payload(
    *,
    day: str,
    feature: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    cost_usd_micro: int,
    event_count: int,
) -> dict[str, Any]:
    """Build a per-day, per-(feature, model) usage rollup payload.

    Aggregated upstream (see ``sweep``); carries no org / user / role / entity
    ids — only the cost shape of a vertical's spend.
    """
    return {
        "vertical": VERTICAL,
        "day": day,
        "feature": feature,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cache_creation_tokens": int(cache_creation_tokens),
        "cost_usd_micro": int(cost_usd_micro),
        "event_count": int(event_count),
    }


__all__ = [
    "VERTICAL",
    "decision_event_id",
    "outcome_event_id",
    "usage_event_id",
    "decision_payload",
    "outcome_payload",
    "usage_payload",
]
