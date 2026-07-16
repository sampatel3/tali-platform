"""Server-side proof that a paid chat action was confirmed on a later turn."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)


@dataclass(frozen=True)
class ConfirmationCheck:
    ok: bool
    reason: str
    payload: dict[str, Any]
    token: str | None = None


_NEGATIVE_CONFIRMATION = re.compile(
    r"\b(no|nope|cancel|stop|don't|dont|do\s+not|not\s+yet|hold\s+off|wait)\b",
    re.IGNORECASE,
)
_POSITIVE_CONFIRMATION = re.compile(
    r"\b(yes|yep|yeah|ok|okay|confirm(?:ed)?|approve(?:d)?|proceed|start)\b"
    r"|\b(go\s+ahead|run\s+it|do\s+it|please\s+(?:run|start|proceed|rescreen|re-screen|rescore|re-score))\b",
    re.IGNORECASE,
)


def _is_explicit_confirmation(text: str | None) -> bool:
    value = str(text or "").strip()
    if not value or _NEGATIVE_CONFIRMATION.search(value):
        return False
    return bool(_POSITIVE_CONFIRMATION.search(value))


def attach_confirmation(
    result: dict[str, Any],
    *,
    operation: str,
    payload: dict[str, Any],
    ttl_seconds: int = 15 * 60,
) -> dict[str, Any]:
    """Attach an opaque, expiring preview receipt to persisted tool history.

    Callers put the authorization boundary (organization, conversation and
    recruiter ids) in ``payload``.  ``require_later_turn_confirmation`` checks
    those bindings before accepting a later yes.  Keeping the receipt opaque
    means a model-supplied ``confirm=true`` can never manufacture approval.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(1, int(ttl_seconds)))
    out = dict(result)
    out["needs_confirmation"] = True
    out["_confirmation"] = {
        "token": uuid.uuid4().hex,
        "operation": operation,
        "payload": payload,
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    return out


def _confirmation_from_message(
    row: AgentConversationMessage, *, operation: str, token: str | None
) -> dict[str, Any] | None:
    for block in row.content or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        try:
            body = json.loads(str(block.get("content") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        confirmation = body.get("_confirmation") if isinstance(body, dict) else None
        if not isinstance(confirmation, dict) or confirmation.get("operation") != operation:
            continue
        if token and confirmation.get("token") != token:
            continue
        return confirmation
    return None


def _consumed_tokens(row: AgentConversationMessage) -> set[str]:
    consumed: set[str] = set()
    for block in row.content or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        try:
            body = json.loads(str(block.get("content") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        token = body.get("_confirmation_consumed") if isinstance(body, dict) else None
        if token:
            consumed.add(str(token))
    return consumed


def require_later_turn_confirmation(
    db: Session,
    *,
    conversation: AgentConversation,
    operation: str,
    token: str | None = None,
    user: Any | None = None,
) -> ConfirmationCheck:
    """Validate a bound preview followed by an explicit later recruiter turn."""
    rows = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.kind == MESSAGE_KIND_TOOL,
            AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
        )
        .order_by(AgentConversationMessage.id.desc())
        .limit(100)
        .all()
    )
    preview_row = None
    receipt = None
    consumed: set[str] = set()
    for row in rows:
        consumed.update(_consumed_tokens(row))
        receipt = _confirmation_from_message(row, operation=operation, token=token)
        if receipt is not None and str(receipt.get("token") or "") not in consumed:
            preview_row = row
            break
    if preview_row is None or receipt is None:
        return ConfirmationCheck(False, "No unused matching server preview exists.", {})

    payload = dict(receipt.get("payload") or {})
    try:
        expires_at_raw = str(receipt.get("expires_at") or "").strip()
        expires_at = (
            datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
            if expires_at_raw
            else None
        )
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return ConfirmationCheck(False, "The server preview expired; create a fresh preview.", payload)
    except (TypeError, ValueError):
        return ConfirmationCheck(False, "The server preview is invalid; create a fresh preview.", payload)

    caller_user_id = getattr(user, "id", None)
    caller_org_id = getattr(user, "organization_id", None)
    bindings = {
        "conversation_id": int(conversation.id),
        "organization_id": int(conversation.organization_id),
        "requested_by_user_id": int(caller_user_id or 0),
    }
    if (
        caller_user_id is None
        or caller_org_id is None
        or int(caller_org_id) != int(conversation.organization_id)
    ):
        return ConfirmationCheck(
            False, "The preview belongs to a different recruiter or organization.", payload
        )
    for field, expected in bindings.items():
        try:
            matches = int(payload.get(field) or 0) == expected
        except (TypeError, ValueError):
            matches = False
        if not matches:
            return ConfirmationCheck(
                False,
                "The preview belongs to a different recruiter or conversation.",
                payload,
            )

    expected_user_id = int(caller_user_id)

    later_query = db.query(AgentConversationMessage).filter(
        AgentConversationMessage.conversation_id == int(conversation.id),
        AgentConversationMessage.kind == MESSAGE_KIND_CHAT,
        AgentConversationMessage.author_role == AUTHOR_ROLE_USER,
        AgentConversationMessage.id > int(preview_row.id),
    )
    later_query = later_query.filter(
        AgentConversationMessage.author_user_id == expected_user_id
    )
    later_user = later_query.order_by(AgentConversationMessage.id.desc()).first()
    if later_user is None:
        return ConfirmationCheck(
            False,
            "The preview must be shown first and confirmed by the recruiter in a later message.",
            payload,
        )
    if not _is_explicit_confirmation(later_user.text):
        return ConfirmationCheck(
            False,
            "The latest recruiter message is not an explicit confirmation.",
            payload,
        )
    return ConfirmationCheck(
        True,
        "confirmed",
        payload,
        str(receipt.get("token") or "") or None,
    )


def mark_confirmation_consumed(
    result: dict[str, Any], *, check: ConfirmationCheck
) -> dict[str, Any]:
    """Make a receipt single-use once its approved operation has started."""
    out = dict(result)
    if check.token:
        out["_confirmation_consumed"] = check.token
    return out


def blocked_confirmation_result(operation: str, reason: str) -> dict[str, Any]:
    return {
        "type": "confirmation_required",
        "operation": operation,
        "started": False,
        "reason": reason,
        "message": "I need to show the cost/scope first, then wait for your confirmation in a new message.",
    }


__all__ = [
    "ConfirmationCheck",
    "attach_confirmation",
    "blocked_confirmation_result",
    "mark_confirmation_consumed",
    "require_later_turn_confirmation",
]
