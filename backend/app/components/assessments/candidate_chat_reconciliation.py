"""Secret-free visibility for unresolved candidate-chat provider work.

The paid chat boundary stores exact request claims inside ``prompt_analytics``.
Those claims can contain checkpoints and finalization inputs, so recruiter APIs
must expose only opaque identities and bounded status metadata.  This module is
pure: it discovers operations and builds public summaries without mutating the
assessment or importing task/provider code.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .candidate_chat_checkpoint import (
    restore_candidate_chat_input,
    restore_candidate_chat_turn,
)
from .chat_idempotency import (
    CHAT_CLAIMS_KEY,
    CLOSED_NO_REPLAY_STATE,
    IN_DOUBT_STATES,
)

CHAT_RECONCILIATION_ARCHIVE_KEY = "_candidate_chat_reconciliation_archive_v1"
CHAT_RECONCILIATION_HISTORY_KEY = "operator_reconciliation_history"

_MAX_CLAIMS_INSPECTED = 5_000
_MAX_RECONCILABLE_EVIDENCE_BYTES = 256 * 1024
_MAX_RECONCILABLE_EVIDENCE_NODES = 10_000
_MAX_RECONCILABLE_EVIDENCE_DEPTH = 64
_PUBLIC_STATES = frozenset(
    {
        "agent_completed",
        "agent_outcome_unknown",
        "agent_started",
        "authority_changed",
        "classifier_outcome_unknown",
        "classifier_started",
        "finalization_outcome_unknown",
        "manual_reconciliation_required",
    }
)
_NON_RECONCILABLE_STATES = frozenset(
    {
        "claimed",
        "retryable",
        "classifier_completed",
        "completed",
        CLOSED_NO_REPLAY_STATE,
    }
)
_PROVIDER_EVIDENCE_DISPOSITIONS = frozenset(
    {
        "succeeded",
        "manual_reconciliation_required",
        "provider_outcome_unknown",
    }
)


@dataclass(frozen=True)
class CandidateChatReconciliationRecord:
    """Internal locator plus the secret-free representation of one operation."""

    operation_id: str
    request_reference: str
    scope: str
    issue_code: str
    claim_key: str | None
    public: dict[str, Any]


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scalar_json_size(value: Any, *, byte_limit: int) -> int | None:
    """Return exact encoded size for one bounded JSON scalar."""

    if isinstance(value, str) and len(value) > byte_limit:
        return None
    if value is not None and not isinstance(value, (str, bool, int, float)):
        return None
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    size = len(encoded.encode("utf-8"))
    return size if size <= byte_limit else None


def candidate_chat_evidence_within_limits(
    value: Any,
    *,
    max_bytes: int = _MAX_RECONCILABLE_EVIDENCE_BYTES,
    max_nodes: int = _MAX_RECONCILABLE_EVIDENCE_NODES,
    max_depth: int = _MAX_RECONCILABLE_EVIDENCE_DEPTH,
) -> bool:
    """Bound work before hashing/copying corrupt JSON evidence.

    The traversal stops as soon as a byte, node, or nesting cap is crossed.
    Large strings are rejected by length before encoding, and large containers
    are rejected by cardinality before their children are queued.
    """

    pending = [(value, 0)]
    nodes = 0
    encoded_bytes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            return False
        if isinstance(current, dict):
            if len(current) > max_nodes - nodes + 1:
                return False
            encoded_bytes += 2 + max(len(current) - 1, 0)
            for key, item in current.items():
                if not isinstance(key, str):
                    return False
                key_size = _scalar_json_size(key, byte_limit=max_bytes)
                if key_size is None:
                    return False
                encoded_bytes += key_size + 1
                if encoded_bytes > max_bytes:
                    return False
                pending.append((item, depth + 1))
            if len(pending) + nodes > max_nodes:
                return False
            continue
        if isinstance(current, list):
            if len(current) > max_nodes - nodes + 1:
                return False
            encoded_bytes += 2 + max(len(current) - 1, 0)
            if encoded_bytes > max_bytes:
                return False
            pending.extend((item, depth + 1) for item in current)
            if len(pending) + nodes > max_nodes:
                return False
            continue
        scalar_size = _scalar_json_size(current, byte_limit=max_bytes)
        if scalar_size is None:
            return False
        encoded_bytes += scalar_size
        if encoded_bytes > max_bytes:
            return False
    return True


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    return candidate


def _bounded_nonempty_string(value: Any, *, max_length: int = 512) -> bool:
    return bool(
        isinstance(value, str)
        and value.strip()
        and len(value) <= max_length
    )


def _agent_completed_issue(claim: dict[str, Any]) -> str | None:
    required_identity = (
        "request_hash",
        "prompt_fingerprint",
        "task_fingerprint",
        "e2b_session_id",
    )
    if any(
        not _bounded_nonempty_string(claim.get(key))
        for key in required_identity
    ):
        return "claim_identity_malformed"
    role_fingerprint = claim.get("role_fingerprint")
    if role_fingerprint is not None and not _bounded_nonempty_string(
        role_fingerprint
    ):
        return "claim_identity_malformed"
    try:
        turn = restore_candidate_chat_turn(claim.get("chat_turn_checkpoint"))
    except (TypeError, ValueError, OverflowError):
        return "provider_checkpoint_malformed"
    if not turn.success:
        return "provider_checkpoint_unsuccessful"
    try:
        restore_candidate_chat_input(claim.get("finalization_input"))
    except (TypeError, ValueError, OverflowError):
        return "finalization_input_malformed"
    try:
        int(claim.get("attempt_count") or 1)
        int(claim.get("latency_ms") or 0)
        dict(claim.get("persist_state") or {})
    except (TypeError, ValueError, OverflowError):
        return "claim_finalization_state_malformed"
    return None


def _claim_issue(claim: dict[str, Any]) -> str | None:
    state = str(claim.get("state") or "")
    if state in IN_DOUBT_STATES:
        return "ambiguous_provider_outcome"
    if state == "agent_completed":
        return _agent_completed_issue(claim)
    if state in _NON_RECONCILABLE_STATES:
        return None
    has_provider_evidence = (
        "chat_turn_checkpoint" in claim
        or str(claim.get("provider_disposition") or "")
        in _PROVIDER_EVIDENCE_DISPOSITIONS
        or "started" in state
        or "outcome" in state
    )
    return "claim_state_malformed" if has_provider_evidence else None


def _record(
    *,
    assessment_id: int,
    claim_key: str | None,
    raw_claim: Any,
    scope: str,
    issue_code: str,
) -> CandidateChatReconciliationRecord:
    identity = {
        "assessment_id": int(assessment_id),
        "claim_key": claim_key,
        "scope": scope,
        "claim": raw_claim,
    }
    evidence_digest = _stable_hash(identity)
    operation_id = f"chatrec_{evidence_digest}"
    request_digest = hashlib.sha256(
        f"candidate-chat-request:{evidence_digest}".encode("ascii")
    ).hexdigest()
    request_reference = f"chatreq_{request_digest[:32]}"
    claim = raw_claim if isinstance(raw_claim, dict) else {}
    raw_state = str(claim.get("state") or "")
    public = {
        "operation_id": operation_id,
        "request_reference": request_reference,
        "scope": scope,
        "issue_code": issue_code,
        "state": raw_state if raw_state in _PUBLIC_STATES else "unrecognized",
        "created_at": _safe_timestamp(claim.get("created_at")),
        "updated_at": _safe_timestamp(claim.get("updated_at")),
        "checkpoint_present": "chat_turn_checkpoint" in claim,
        "finalization_input_present": "finalization_input" in claim,
        "can_close_without_replay": True,
    }
    return CandidateChatReconciliationRecord(
        operation_id=operation_id,
        request_reference=request_reference,
        scope=scope,
        issue_code=issue_code,
        claim_key=claim_key,
        public=public,
    )


def _nonactionable_record(
    *,
    assessment_id: int,
    scope: str,
    issue_code: str,
    ordinal: int = 0,
) -> CandidateChatReconciliationRecord:
    """Return constant-size visibility without reading oversized evidence."""

    digest = _stable_hash(
        {
            "assessment_id": int(assessment_id),
            "scope": scope,
            "issue_code": issue_code,
            "ordinal": int(ordinal),
        }
    )
    request_digest = hashlib.sha256(
        f"candidate-chat-nonactionable:{digest}".encode("ascii")
    ).hexdigest()
    public = {
        "operation_id": f"chatrec_{digest}",
        "request_reference": f"chatreq_{request_digest[:32]}",
        "scope": scope,
        "issue_code": issue_code,
        "state": "unrecognized",
        "created_at": None,
        "updated_at": None,
        "checkpoint_present": False,
        "finalization_input_present": False,
        "can_close_without_replay": False,
    }
    return CandidateChatReconciliationRecord(
        operation_id=public["operation_id"],
        request_reference=public["request_reference"],
        scope=scope,
        issue_code=issue_code,
        claim_key=None,
        public=public,
    )


def discover_candidate_chat_reconciliation_records(
    assessment: Any,
) -> list[CandidateChatReconciliationRecord]:
    """Find ambiguous or corrupt claims without exposing their stored payloads."""

    analytics = getattr(assessment, "prompt_analytics", None)
    if not isinstance(analytics, dict):
        return []
    raw_claims = analytics.get(CHAT_CLAIMS_KEY)
    if raw_claims is None:
        return []
    assessment_id = int(getattr(assessment, "id"))
    if not isinstance(raw_claims, dict):
        if not candidate_chat_evidence_within_limits(raw_claims):
            return [
                _nonactionable_record(
                    assessment_id=assessment_id,
                    scope="claims_container",
                    issue_code="claims_container_oversized",
                )
            ]
        return [
            _record(
                assessment_id=assessment_id,
                claim_key=None,
                raw_claim=raw_claims,
                scope="claims_container",
                issue_code="claims_container_malformed",
            )
        ]
    if len(raw_claims) > _MAX_CLAIMS_INSPECTED:
        return [
            _nonactionable_record(
                assessment_id=assessment_id,
                scope="claims_container",
                issue_code="claims_container_oversized",
            )
        ]

    records: list[CandidateChatReconciliationRecord] = []
    oversized_count = 0
    for raw_key, raw_claim in raw_claims.items():
        claim_key = str(raw_key)
        if len(claim_key) > 128:
            records.append(
                _nonactionable_record(
                    assessment_id=assessment_id,
                    scope="request",
                    issue_code="claim_record_oversized",
                    ordinal=oversized_count,
                )
            )
            oversized_count += 1
            continue
        raw_state = raw_claim.get("state") if isinstance(raw_claim, dict) else None
        if (
            isinstance(raw_state, str)
            and len(raw_state) <= 64
            and raw_state in _NON_RECONCILABLE_STATES
        ):
            continue
        evidence_identity = {"claim_key": claim_key, "claim": raw_claim}
        if not candidate_chat_evidence_within_limits(evidence_identity):
            records.append(
                _nonactionable_record(
                    assessment_id=assessment_id,
                    scope="request",
                    issue_code="claim_record_oversized",
                    ordinal=oversized_count,
                )
            )
            oversized_count += 1
            continue
        issue_code = (
            _claim_issue(raw_claim)
            if isinstance(raw_claim, dict)
            else "claim_record_malformed"
        )
        if issue_code is None:
            continue
        records.append(
            _record(
                assessment_id=assessment_id,
                claim_key=claim_key,
                raw_claim=raw_claim,
                scope="request",
                issue_code=issue_code,
            )
        )
    return records


def public_candidate_chat_reconciliation(
    assessment: Any,
    *,
    can_reconcile: bool,
) -> dict[str, Any]:
    records = discover_candidate_chat_reconciliation_records(assessment)
    return {
        "reconciliation_required": bool(records),
        "operation_count": len(records),
        "can_reconcile": bool(can_reconcile and records),
        "operations": [deepcopy(record.public) for record in records]
        if can_reconcile
        else [],
    }


def public_candidate_prompt_analytics(value: Any) -> dict[str, Any] | None:
    """Keep scoring analytics public while redacting internal replay evidence."""

    if not isinstance(value, dict):
        return None
    internal_keys = {CHAT_CLAIMS_KEY, CHAT_RECONCILIATION_ARCHIVE_KEY}
    return {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in internal_keys
    }


__all__ = [
    "CHAT_RECONCILIATION_ARCHIVE_KEY",
    "CHAT_RECONCILIATION_HISTORY_KEY",
    "CandidateChatReconciliationRecord",
    "candidate_chat_evidence_within_limits",
    "discover_candidate_chat_reconciliation_records",
    "public_candidate_chat_reconciliation",
    "public_candidate_prompt_analytics",
]
