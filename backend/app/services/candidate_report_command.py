"""Shared, confirmed publishing for candidate-evidence report snapshots.

Candidate discovery itself is deliberately read-only. Chat adapters supply
their own later-turn confirmation lookup; this core normalizes the request,
recomputes it server-side, detects drift, and creates one scrubbed 30-day
bearer report behind a durable command receipt.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Literal

from sqlalchemy.orm import Session

from ..agent_chat.command_receipts import (
    abandon_uncommitted_command,
    begin_command,
    complete_command,
    lookup_command,
)
from ..agent_chat.confirmations import (
    ConfirmationCheck,
    attach_confirmation,
    mark_confirmation_consumed,
)
from ..domains.top_reports.service import create_report, report_public_url
from ..models.role import Role

CandidateReportKind = Literal["top_candidates", "screen_pool"]
ConfirmationResolver = Callable[[str], ConfirmationCheck | None]

_RANK_FIELDS = {
    "taali",
    "pre_screen",
    "rank",
    "cv_match",
    "workable",
    "assessment",
    "role_fit",
}


def _normalize(
    kind: CandidateReportKind,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if kind == "top_candidates":
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query must be non-empty")
        if len(query) > 2000:
            raise ValueError("query must be 2000 characters or fewer")
        rank_by = str(arguments.get("rank_by") or "taali").strip().lower()
        if rank_by not in _RANK_FIELDS:
            raise ValueError(f"rank_by must be one of {sorted(_RANK_FIELDS)}")
        return {
            "query": query,
            "limit": max(1, min(int(arguments.get("limit") or 10), 25)),
            "rank_by": rank_by,
        }

    requirement = str(arguments.get("requirement_text") or "").strip()
    if not requirement:
        raise ValueError("requirement_text must be non-empty")
    if len(requirement) > 2000:
        raise ValueError("requirement_text must be 2000 characters or fewer")
    return {
        "requirement_text": requirement,
        "limit": max(1, min(int(arguments.get("limit") or 20), 50)),
        "offset": max(0, int(arguments.get("offset") or 0)),
        "deep_verify": bool(arguments.get("deep_verify", False)),
    }


def _operation(kind: CandidateReportKind, arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    prefix = (
        "create_top_candidates_report"
        if kind == "top_candidates"
        else "create_screen_pool_report"
    )
    return f"{prefix}:{digest}"


def _fingerprint(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _recompute(
    db: Session,
    *,
    kind: CandidateReportKind,
    role: Role,
    user: Any,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    # Both canonical handlers enforce tenant scoping and re-read the live
    # actionable/scored population. Neither handler publishes a report.
    from ..mcp import handlers

    if kind == "top_candidates":
        return handlers.find_top_candidates(
            db,
            user,
            role_id=int(role.id),
            **arguments,
        )
    return handlers.screen_pool_against_requirement(
        db,
        user,
        role_id=int(role.id),
        **arguments,
    )


def _available_count(snapshot: dict[str, Any]) -> int:
    candidates = snapshot.get("candidates")
    candidate_count = len(candidates) if isinstance(candidates, list) else 0
    for field in ("shown", "returned"):
        raw = snapshot.get(field)
        if raw is not None:
            return max(0, int(raw or 0))
    return candidate_count


def execute_confirmed_candidate_report(
    db: Session,
    *,
    kind: CandidateReportKind,
    role: Role,
    user: Any,
    conversation_kind: Literal["agent", "taali"],
    conversation_id: int,
    binding: dict[str, int],
    arguments: dict[str, Any],
    resolve_confirmation: ConfirmationResolver,
) -> dict[str, Any]:
    """Preview, later-turn confirm, re-authorize upstream, and publish once."""

    user_org_id = getattr(user, "organization_id", None)
    if user_org_id is None or int(user_org_id) != int(role.organization_id):
        raise ValueError(f"role {role.id} not found")

    normalized = _normalize(kind, arguments)
    operation = _operation(kind, normalized)
    check = resolve_confirmation(operation)
    receipt_arguments = {"report_kind": kind, **normalized}
    confirmation_scope_matches = bool(
        check
        and check.ok
        and int(check.payload.get("role_id") or 0) == int(role.id)
        and check.payload.get("report_kind") == kind
        and check.payload.get("arguments") == normalized
    )
    if confirmation_scope_matches:
        prior = lookup_command(
            db,
            check=check,
            conversation_kind=conversation_kind,
            conversation_id=int(conversation_id),
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            requested_by_user_id=int(user.id),
            operation=operation,
            arguments=receipt_arguments,
        )
        if prior is not None and prior.completed_result is not None:
            return prior.completed_result

    snapshot = _recompute(
        db,
        kind=kind,
        role=role,
        user=user,
        arguments=normalized,
    )
    fingerprint = _fingerprint(snapshot)
    if _available_count(snapshot) <= 0:
        return {
            **snapshot,
            "type": "candidate_evidence",
            "share_blocked": True,
            "message": "No candidates are available to publish from this search.",
        }

    state_matches = bool(
        check
        and check.ok
        and int(check.payload.get("role_id") or 0) == int(role.id)
        and check.payload.get("report_kind") == kind
        and check.payload.get("arguments") == normalized
        and check.payload.get("fingerprint") == fingerprint
    )
    if not state_matches:
        changed = bool(check and check.ok)
        message = (
            "The candidate result changed since approval. This refreshed preview "
            "must be confirmed in a new message; no public link was created."
            if changed
            else "No public link has been created. Show this exact evidence preview "
            "and ask the recruiter to confirm sharing it in a new message."
        )
        return attach_confirmation(
            {
                **snapshot,
                "type": "candidate_evidence",
                "share_preview": True,
                "report_kind": kind,
                "message": message,
            },
            operation=operation,
            payload={
                **binding,
                "role_id": int(role.id),
                "report_kind": kind,
                "arguments": normalized,
                "fingerprint": fingerprint,
            },
        )

    claim = begin_command(
        db,
        check=check,
        conversation_kind=conversation_kind,
        conversation_id=int(conversation_id),
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        requested_by_user_id=int(user.id),
        operation=operation,
        arguments=receipt_arguments,
    )
    if claim.completed_result is not None:
        return claim.completed_result

    report_query = str(
        normalized.get("query") or normalized.get("requirement_text") or ""
    )
    try:
        report = create_report(
            db,
            organization_id=int(role.organization_id),
            created_by_user_id=int(user.id),
            role_id=int(role.id),
            query=report_query,
            snapshot=snapshot,
        )
    except Exception:
        abandon_uncommitted_command(db, claim)
        raise

    url = report_public_url(report.token)
    result = {
        **dict(report.snapshot or {}),
        "type": "candidate_report",
        "report_kind": kind,
        "report_token": report.token,
        "report_url": url,
        "expires_at": report.expires_at.isoformat() if report.expires_at else None,
        "message": "Shareable candidate report created. The link expires in 30 days.",
        "_terminal_message": f"Shareable candidate report created: {url}",
    }
    result = mark_confirmation_consumed(result, check=check)
    return complete_command(db, claim, result)


__all__ = [
    "CandidateReportKind",
    "execute_confirmed_candidate_report",
]
