"""Later-turn confirmation receipts for mutations initiated in global Chat."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..agent_chat.confirmations import ConfirmationCheck
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.taali_chat_message import ROLE_USER, TaaliChatMessage

_NEGATIVE = re.compile(
    r"\b(no|nope|cancel|stop|don't|dont|do\s+not|not\s+yet|hold\s+off|wait)\b",
    re.IGNORECASE,
)
_POSITIVE = re.compile(
    r"\b(yes|yep|yeah|ok|okay|confirm(?:ed)?|approve(?:d)?|proceed|start)\b"
    r"|\b(go\s+ahead|run\s+it|do\s+it|please\s+(?:run|start|proceed|create))\b",
    re.IGNORECASE,
)


def _is_confirmation(text: str | None) -> bool:
    value = str(text or "").strip()
    return bool(value and not _NEGATIVE.search(value) and _POSITIVE.search(value))


def _tool_bodies(row: TaaliChatMessage) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    for block in row.content or []:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        try:
            body = json.loads(str(block.get("content") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(body, dict):
            bodies.append(body)
    return bodies


def require_later_turn_confirmation(
    db: Session,
    *,
    conversation: TaaliChatConversation,
    operation: str,
    token: str | None = None,
) -> ConfirmationCheck:
    """Require an unused server preview and a newer plain user-text row."""
    rows = (
        db.query(TaaliChatMessage)
        .filter(
            TaaliChatMessage.conversation_id == int(conversation.id),
            TaaliChatMessage.role == ROLE_USER,
        )
        .order_by(TaaliChatMessage.id.desc())
        .limit(100)
        .all()
    )
    consumed: set[str] = set()
    preview_row: TaaliChatMessage | None = None
    receipt: dict[str, Any] | None = None
    for row in rows:
        for body in _tool_bodies(row):
            used = body.get("_confirmation_consumed")
            if used:
                consumed.add(str(used))
            candidate = body.get("_confirmation")
            if not isinstance(candidate, dict):
                continue
            candidate_token = str(candidate.get("token") or "")
            if candidate.get("operation") != operation:
                continue
            if token and candidate_token != token:
                continue
            if candidate_token in consumed:
                continue
            preview_row = row
            receipt = candidate
            break
        if preview_row is not None:
            break
    if preview_row is None or receipt is None:
        return ConfirmationCheck(False, "No unused matching server preview exists.", {})

    later_rows = (
        db.query(TaaliChatMessage)
        .filter(
            TaaliChatMessage.conversation_id == int(conversation.id),
            TaaliChatMessage.role == ROLE_USER,
            TaaliChatMessage.id > int(preview_row.id),
        )
        .order_by(TaaliChatMessage.id.desc())
        .all()
    )
    latest_text = None
    for row in later_rows:
        for block in row.content or []:
            if isinstance(block, dict) and block.get("type") == "text":
                latest_text = str(block.get("text") or "")
                break
        if latest_text is not None:
            break
    if latest_text is None:
        return ConfirmationCheck(
            False,
            "The preview must be confirmed by the recruiter in a later message.",
            dict(receipt.get("payload") or {}),
        )
    if not _is_confirmation(latest_text):
        return ConfirmationCheck(
            False,
            "The latest recruiter message is not an explicit confirmation.",
            dict(receipt.get("payload") or {}),
        )
    return ConfirmationCheck(
        True,
        "confirmed",
        dict(receipt.get("payload") or {}),
        str(receipt.get("token") or "") or None,
    )


__all__ = ["require_later_turn_confirmation"]
