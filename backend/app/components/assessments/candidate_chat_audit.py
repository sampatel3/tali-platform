"""Secret-free recruiter timeline evidence for ambiguous candidate chat."""

from __future__ import annotations

from typing import Any

from ...models.assessment import Assessment
from .chat_idempotency import IN_DOUBT_STATES
from .repository import append_assessment_timeline_event


def append_candidate_chat_reconciliation_required(
    assessment: Assessment,
    *,
    prior_state: str,
) -> None:
    append_assessment_timeline_event(
        assessment,
        "candidate_chat_reconciliation_required",
        {
            "prior_state": str(prior_state or "unknown"),
            "disposition": "provider_outcome_not_replayed",
        },
    )


def append_candidate_chat_no_replay_resolution(
    assessment: Assessment,
    claims: dict[str, dict[str, Any]],
    *,
    reason: str,
    current_claim_key: str | None = None,
) -> None:
    states = [
        str(claim.get("state") or "")
        for claim_key, claim in claims.items()
        if claim_key != current_claim_key
        and str(claim.get("state") or "") in IN_DOUBT_STATES
    ]
    if not states:
        return
    append_assessment_timeline_event(
        assessment,
        "candidate_chat_reconciled_no_replay",
        {
            "claim_count": len(states),
            "prior_states": sorted(set(states)),
            "disposition": "provider_outcome_not_replayed",
            "reason": str(reason),
        },
    )


__all__ = [
    "append_candidate_chat_no_replay_resolution",
    "append_candidate_chat_reconciliation_required",
]
