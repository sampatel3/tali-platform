"""Durable claims and exact response replay for candidate chat."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .claude_budget import build_claude_budget_snapshot

CHAT_CLAIMS_KEY = "_candidate_chat_requests_v1"
IN_DOUBT_STATES = {
    "classifier_started",
    "classifier_outcome_unknown",
    "agent_started",
    "agent_outcome_unknown",
    "finalization_outcome_unknown",
    "authority_changed",
    "manual_reconciliation_required",
}
RESUMABLE_STATES = {
    "claimed",
    "retryable",
    "classifier_completed",
    "agent_completed",
}
LIVE_STATES = {"classifier_started", "agent_started"}
BLOCKING_STATES = LIVE_STATES | {"agent_completed"}
CLOSED_NO_REPLAY_STATE = "reconciled_no_replay"


class RequestIdConflictError(ValueError):
    pass


class RequestOutcomeInDoubtError(ValueError):
    pass


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def candidate_chat_request_hash(
    *,
    message: str,
    code_context: str | None,
    selected_file_path: str | None,
    paste_detected: bool,
    browser_focused: bool,
    time_since_last_prompt_ms: int | None,
) -> str:
    """Hash every request field that can influence provider work or telemetry."""

    return _stable_hash(
        {
            "message": message.strip(),
            "code_context": str(code_context or "")[:12000],
            "selected_file_path": (selected_file_path or "").strip() or None,
            "paste_detected": bool(paste_detected),
            "browser_focused": bool(browser_focused),
            "time_since_last_prompt_ms": time_since_last_prompt_ms,
        }
    )


def candidate_chat_prompt_fingerprint(prompts: list[dict[str, Any]]) -> str:
    return _stable_hash(prompts)


def candidate_chat_authority_fingerprint(value: Any) -> str:
    return _stable_hash(value)


def _claims(analytics: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(analytics, dict):
        return {}
    raw = analytics.get(CHAT_CLAIMS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): deepcopy(claim)
        for key, claim in raw.items()
        if isinstance(claim, dict)
    }


def get_candidate_chat_claim(
    analytics: Any, *, claim_key: str
) -> dict[str, Any] | None:
    claim = _claims(analytics).get(str(claim_key))
    return deepcopy(claim) if claim is not None else None


def list_candidate_chat_claims(analytics: Any) -> dict[str, dict[str, Any]]:
    """Return a detached copy of every durable request claim."""

    return _claims(analytics)


def reconcile_noncurrent_candidate_chat_claims(
    analytics: Any,
    *,
    current_claim_key: str,
) -> dict[str, Any]:
    """Close old ambiguous calls after a distinct request owns the mutex.

    The provider evidence is intentionally retained, but the old request is
    terminal and can never be replayed.  This lets a later, distinctly keyed
    request become the sole live chat authority without leaving submission
    permanently blocked by an unreachable historical claim.
    """

    base = deepcopy(analytics) if isinstance(analytics, dict) else {}
    claims = _claims(base)
    timestamp = datetime.now(timezone.utc).isoformat()
    for claim_key, claim in claims.items():
        if claim_key == str(current_claim_key):
            continue
        state = str(claim.get("state") or "")
        if state not in IN_DOUBT_STATES:
            continue
        claim.update(
            state=CLOSED_NO_REPLAY_STATE,
            reconciliation_original_state=state,
            reconciliation_disposition="provider_outcome_not_replayed",
            reconciliation_reason="superseded_by_distinct_request",
            reconciled_at=timestamp,
            updated_at=timestamp,
        )
        claims[claim_key] = claim
    base[CHAT_CLAIMS_KEY] = claims
    return base


def close_in_doubt_candidate_chat_claims_without_replay(
    analytics: Any,
    *,
    reason: str,
) -> dict[str, Any]:
    """Terminally close ambiguous provider work while preserving its evidence.

    This is reserved for a terminal assessment policy such as timeout grading.
    It never treats the provider output as successful and never retries it.
    """

    base = deepcopy(analytics) if isinstance(analytics, dict) else {}
    claims = _claims(base)
    timestamp = datetime.now(timezone.utc).isoformat()
    for claim_key, claim in claims.items():
        state = str(claim.get("state") or "")
        if state not in IN_DOUBT_STATES:
            continue
        claim.update(
            state=CLOSED_NO_REPLAY_STATE,
            reconciliation_original_state=state,
            reconciliation_disposition="provider_outcome_not_replayed",
            reconciliation_reason=str(reason),
            reconciled_at=timestamp,
            updated_at=timestamp,
        )
        claims[claim_key] = claim
    base[CHAT_CLAIMS_KEY] = claims
    return base


def claim_candidate_chat_request(
    analytics: Any,
    *,
    claim_key: str,
    request_id: str | None,
    request_hash: str,
    prompt_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create/resume a claim, rejecting conflict or ambiguous paid work."""

    base = deepcopy(analytics) if isinstance(analytics, dict) else {}
    claims = _claims(base)
    existing = claims.get(str(claim_key))
    if existing is not None:
        if str(existing.get("request_hash") or "") != request_hash:
            raise RequestIdConflictError(
                "request_id was already used for a different request"
            )
        state = str(existing.get("state") or "")
        if state in IN_DOUBT_STATES or state == "completed":
            raise RequestOutcomeInDoubtError(
                "This chat request already reached the AI provider and needs reconciliation before it can be retried."
            )
        if state not in RESUMABLE_STATES:
            raise RequestOutcomeInDoubtError(
                "This chat request cannot be safely replayed yet."
            )
        existing["attempt_count"] = int(existing.get("attempt_count") or 1) + 1
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        claims[str(claim_key)] = existing
        base[CHAT_CLAIMS_KEY] = claims
        return base, deepcopy(existing)

    unresolved = next(
        (
            claim
            for claim in claims.values()
            if str(claim.get("state") or "") in BLOCKING_STATES
        ),
        None,
    )
    if unresolved is not None:
        raise RequestOutcomeInDoubtError(
            "A previous chat request reached the AI provider and needs reconciliation before another request can run."
        )

    now = datetime.now(timezone.utc).isoformat()
    claim = {
        "request_id": request_id,
        "request_hash": request_hash,
        "prompt_fingerprint": prompt_fingerprint,
        "state": "claimed",
        "attempt_count": 1,
        "created_at": now,
        "updated_at": now,
    }
    claims[str(claim_key)] = claim
    base[CHAT_CLAIMS_KEY] = claims
    return base, deepcopy(claim)


def update_candidate_chat_claim(
    analytics: Any,
    *,
    claim_key: str,
    request_hash: str,
    state: str,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return analytics with one exact claim advanced to ``state``."""

    base = deepcopy(analytics) if isinstance(analytics, dict) else {}
    claims = _claims(base)
    claim = claims.get(str(claim_key))
    if claim is None or str(claim.get("request_hash") or "") != request_hash:
        raise RequestOutcomeInDoubtError("The durable chat request claim changed")
    claim["state"] = str(state)
    claim["updated_at"] = datetime.now(timezone.utc).isoformat()
    if updates:
        claim.update(deepcopy(updates))
    claims[str(claim_key)] = claim
    base[CHAT_CLAIMS_KEY] = claims
    return base


def compact_completed_candidate_chat_claim(
    analytics: Any,
    *,
    claim_key: str,
    request_hash: str,
    completed_at: str,
    stop_reason: str | None,
) -> dict[str, Any]:
    """Replace bulky provider evidence with the minimal committed tombstone."""

    base = deepcopy(analytics) if isinstance(analytics, dict) else {}
    claims = _claims(base)
    claim = claims.get(str(claim_key))
    if claim is None or str(claim.get("request_hash") or "") != request_hash:
        raise RequestOutcomeInDoubtError("The durable chat request claim changed")
    claims[str(claim_key)] = {
        "request_id": claim.get("request_id"),
        "request_hash": request_hash,
        "state": "completed",
        "attempt_count": int(claim.get("attempt_count") or 1),
        "created_at": claim.get("created_at"),
        "completed_at": completed_at,
        "updated_at": completed_at,
        "provider_disposition": "succeeded",
        "provider_stop_reason": stop_reason,
    }
    base[CHAT_CLAIMS_KEY] = claims
    return base


def replay_candidate_chat_request(
    *,
    prompts: list[dict[str, Any]],
    request_id: str | None,
    message: str,
    budget_limit_usd: float | None,
    request_hash: str | None = None,
) -> dict[str, Any] | None:
    """Return a committed response for ``request_id``, or ``None`` if new."""

    if not request_id:
        return None
    prior_request = None
    prior_hash = ""
    for record in reversed(prompts):
        if not isinstance(record, dict):
            continue
        if str(record.get("request_id") or "") == request_id:
            prior_request = record
            prior_hash = str(record.get("request_hash") or "")
            break
        aliases = record.get("request_aliases")
        if not isinstance(aliases, list):
            continue
        alias = next(
            (
                item
                for item in aliases
                if isinstance(item, dict)
                and str(item.get("request_id") or "") == request_id
            ),
            None,
        )
        if alias is not None:
            prior_request = record
            prior_hash = str(alias.get("request_hash") or "")
            break
    if prior_request is None:
        return None
    if request_hash and prior_hash:
        conflict = prior_hash != request_hash
    else:
        conflict = str(prior_request.get("message") or "").strip() != message.strip()
    if conflict:
        raise RequestIdConflictError(
            "request_id was already used for a different request"
        )
    return {
        "content": str(prior_request.get("response") or ""),
        "tool_calls_made": list(prior_request.get("tool_calls_made") or []),
        "input_tokens": int(prior_request.get("input_tokens") or 0),
        "output_tokens": int(prior_request.get("output_tokens") or 0),
        "latency_ms": int(prior_request.get("response_latency_ms") or 0),
        "claude_budget": build_claude_budget_snapshot(
            budget_limit_usd=budget_limit_usd,
            prompts=prompts,
        ),
        "assessment_voided": bool(prior_request.get("assessment_voided", False)),
        "request_id": request_id,
        "idempotent_replay": True,
    }


__all__ = [
    "CHAT_CLAIMS_KEY",
    "BLOCKING_STATES",
    "IN_DOUBT_STATES",
    "LIVE_STATES",
    "RequestIdConflictError",
    "RequestOutcomeInDoubtError",
    "candidate_chat_authority_fingerprint",
    "candidate_chat_prompt_fingerprint",
    "candidate_chat_request_hash",
    "claim_candidate_chat_request",
    "compact_completed_candidate_chat_claim",
    "get_candidate_chat_claim",
    "list_candidate_chat_claims",
    "reconcile_noncurrent_candidate_chat_claims",
    "replay_candidate_chat_request",
    "update_candidate_chat_claim",
]
