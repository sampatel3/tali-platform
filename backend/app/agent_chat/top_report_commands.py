"""Confirmed publishing of grounded, role-scoped candidate reports."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ..domains.top_reports.service import create_report, report_public_url
from ..models.role import Role
from .confirmations import (
    attach_confirmation,
    mark_confirmation_consumed,
    require_later_turn_confirmation,
)
from .command_receipts import (
    abandon_uncommitted_command,
    begin_command,
    complete_command,
)


_RANK_FIELDS = {
    "taali",
    "pre_screen",
    "rank",
    "cv_match",
    "workable",
    "assessment",
    "role_fit",
}


def _normalized(arguments: dict[str, Any]) -> dict[str, Any]:
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


def _fingerprint(snapshot: dict[str, Any]) -> str:
    """Bind approval to the exact candidate/evidence snapshot shown."""
    return hashlib.sha256(
        json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _operation(arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"create_top_candidates_report:{digest}"


def _recompute(
    db: Session,
    *,
    role: Role,
    user: Any,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    # The canonical handler validates role ownership, scopes applications to
    # the caller's organization, applies the actionable-pool filter, and
    # regenerates grounded evidence on the server.
    from ..mcp import handlers

    return handlers.find_top_candidates(
        db,
        user,
        role_id=int(role.id),
        **arguments,
    )


def create_top_candidates_report(
    db: Session,
    *,
    role: Role,
    user: Any,
    conversation: Any,
    binding: dict[str, int],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Preview, later-turn confirm, revalidate, then publish one report."""
    user_org_id = getattr(user, "organization_id", None)
    if user_org_id is None or int(user_org_id) != int(role.organization_id):
        raise ValueError(f"role {role.id} not found")

    normalized = _normalized(arguments)
    operation = _operation(normalized)
    check = None
    if conversation is not None:
        check = require_later_turn_confirmation(
            db,
            conversation=conversation,
            operation=operation,
            token=str(arguments.get("confirmation_token") or "") or None,
            user=user,
        )

    snapshot = _recompute(db, role=role, user=user, arguments=normalized)
    fingerprint = _fingerprint(snapshot)
    shown = int(snapshot.get("shown") or len(snapshot.get("candidates") or []))
    if shown <= 0:
        return {
            **snapshot,
            "type": "candidate_evidence",
            "share_blocked": True,
            "message": "No candidates are available to publish from this grounded search.",
        }

    state_matches = bool(
        check
        and check.ok
        and int(check.payload.get("role_id") or 0) == int(role.id)
        and check.payload.get("arguments") == normalized
        and check.payload.get("fingerprint") == fingerprint
    )
    if not state_matches:
        changed = bool(check and check.ok)
        message = (
            "The shortlist changed since approval. This refreshed grounded preview "
            "must be confirmed in a new message; no public link was created."
            if changed
            else "No public link has been created. Show this grounded shortlist and "
            "ask the recruiter to confirm sharing it in a new message."
        )
        return attach_confirmation(
            {
                **snapshot,
                "type": "candidate_evidence",
                "share_preview": True,
                "message": message,
            },
            operation=operation,
            payload={
                **binding,
                "role_id": int(role.id),
                "arguments": normalized,
                "fingerprint": fingerprint,
            },
        )

    claim = begin_command(
        db,
        check=check,
        conversation_kind="agent",
        conversation_id=int(conversation.id),
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        requested_by_user_id=int(user.id),
        operation=operation,
        arguments=normalized,
    )
    if claim.completed_result is not None:
        return claim.completed_result

    try:
        report = create_report(
            db,
            organization_id=int(role.organization_id),
            created_by_user_id=int(user.id),
            role_id=int(role.id),
            query=normalized["query"],
            snapshot=snapshot,
        )
    except Exception:
        abandon_uncommitted_command(db, claim)
        raise
    url = report_public_url(report.token)
    result = {
        **dict(report.snapshot or {}),
        "type": "candidate_report",
        "report_token": report.token,
        "report_url": url,
        "expires_at": report.expires_at.isoformat() if report.expires_at else None,
        "message": "Shareable grounded shortlist created. The link expires in 30 days.",
        "_terminal_message": f"Shareable grounded shortlist created: {url}",
    }
    result = mark_confirmation_consumed(result, check=check)
    return complete_command(db, claim, result)


__all__ = ["create_top_candidates_report"]
