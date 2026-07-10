"""Core chat-turn orchestrator: streams Anthropic responses + dispatches tools.

One call to ``run_chat_turn`` runs the full agent loop for one user
message:

  1. Load (or create) the conversation.
  2. Build the message history from persisted ``TaaliChatMessage`` rows.
  3. Stream Anthropic's response, yielding AI-SDK protocol frames.
  4. When Claude requests a tool, dispatch to the in-process MCP handler,
     emit a ``tool_call_result`` frame, then continue the loop.
  5. Persist the assistant turn (and any user message we appended).
  6. Record one ``UsageEvent`` per Anthropic call for the billing meter.

Multi-turn tool-calling is bounded by ``MAX_TOOL_ROUNDS`` so a buggy or
adversarial tool loop can't drain credits.

This generator yields ``Frame`` objects (see ``streaming.py``); the caller
(the FastAPI route) wraps them in an ``EventSourceResponse``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Iterator

from sqlalchemy.orm import Session

from ..models.organization import Organization
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.taali_chat_message import (
    ROLE_ASSISTANT,
    ROLE_USER,
    TaaliChatMessage,
)
from ..models.user import User
from ..platform.config import settings
from ..services.claude_client_resolver import get_client_for_org
from ..services.pricing_service import Feature
from ..services.usage_metering_service import record_event
from . import streaming
from .stream_round import _RunningUsage, _stream_one_round
from .system_prompt import build_system_blocks
from .tool_registry import dispatch_tool

logger = logging.getLogger("taali.taali_chat")


# Hard cap on tool-call rounds per turn. Each round is one Anthropic call;
# anything past this is almost certainly a runaway loop. 8 rounds is enough
# headroom for "search → compare → drill into one CV" multi-step flows.
MAX_TOOL_ROUNDS = 8

# Tools whose results must stay scoped to the conversation's role. The
# system prompt tells the model it may omit role_id for these in a
# role-scoped chat ("the conversation's role scope applies"); the handlers
# only filter when role_id is not None, so we inject the conversation's
# role_id here when the model leaves it out — otherwise an omitted role_id
# leaks org-wide results.
_ROLE_SCOPED_TOOLS = frozenset(
    {"list_recent_agent_decisions", "list_recent_agent_runs"}
)


@dataclass
class ChatTurnInput:
    """Inputs for one user → assistant exchange."""

    user_message: str
    conversation_id: int | None = None  # None = create new conversation
    # Optional role scope. Used only when creating a new conversation.
    # Once a conversation exists, its role_id is fixed (recorded on the
    # TaaliChatConversation row) — passing a different role_id on a
    # follow-up turn is ignored.
    role_id: int | None = None


# ---------------------------------------------------------------------------
# Conversation persistence helpers
# ---------------------------------------------------------------------------


def _ensure_conversation(
    db: Session,
    *,
    user: User,
    conversation_id: int | None,
    first_message: str,
    role_id: int | None = None,
) -> TaaliChatConversation:
    if conversation_id is not None:
        conversation = (
            db.query(TaaliChatConversation)
            .filter(
                TaaliChatConversation.id == conversation_id,
                TaaliChatConversation.organization_id == user.organization_id,
                TaaliChatConversation.user_id == user.id,
                TaaliChatConversation.archived_at.is_(None),
            )
            .first()
        )
        if conversation is None:
            raise ValueError(f"conversation {conversation_id} not found")
        return conversation

    # New conversation: validate role_id is org-scoped before persisting,
    # so a recruiter can't open a chat scoped to another org's role even
    # if they spoof the id.
    safe_role_id: int | None = None
    if role_id is not None:
        from ..models.role import Role

        owns = (
            db.query(Role.id)
            .filter(
                Role.id == int(role_id),
                Role.organization_id == user.organization_id,
                Role.deleted_at.is_(None),
            )
            .first()
        )
        if owns is not None:
            safe_role_id = int(role_id)

    title = first_message.strip().split("\n", 1)[0][:80] or "New conversation"
    conversation = TaaliChatConversation(
        organization_id=user.organization_id,
        user_id=user.id,
        role_id=safe_role_id,
        title=title,
    )
    db.add(conversation)
    db.flush()
    return conversation


def _load_history(db: Session, *, conversation: TaaliChatConversation) -> list[dict[str, Any]]:
    """Pull persisted messages in Anthropic message format. Sanitised so a
    tool_use orphaned by an interrupted turn can't 400 the whole conversation."""
    from ..llm.tool_pairs import sanitize_tool_pairs

    rows = (
        db.query(TaaliChatMessage)
        .filter(TaaliChatMessage.conversation_id == conversation.id)
        .order_by(TaaliChatMessage.created_at.asc(), TaaliChatMessage.id.asc())
        .all()
    )
    return sanitize_tool_pairs([{"role": row.role, "content": row.content} for row in rows])


def _persist_message(
    db: Session,
    *,
    conversation: TaaliChatConversation,
    role: str,
    content: list[dict[str, Any]],
    model: str | None = None,
    stop_reason: str | None = None,
    token_usage: dict[str, int] | None = None,
) -> TaaliChatMessage:
    msg = TaaliChatMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role=role,
        content=content,
        model=model,
        stop_reason=stop_reason,
        token_usage=token_usage,
    )
    db.add(msg)
    db.flush()
    return msg


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_chat_turn(
    *,
    db: Session,
    user: User,
    organization: Organization,
    turn: ChatTurnInput,
) -> Iterator[streaming.Frame]:
    """Run one chat turn end-to-end. Generator yields AI-SDK protocol frames.

    Side effects: creates/updates ``TaaliChatConversation`` row, appends one
    user + one assistant ``TaaliChatMessage`` row, records one
    ``UsageEvent`` per Anthropic call. The caller is responsible for
    committing the DB session — we ``flush`` at message boundaries so ids
    populate, but never ``commit`` ourselves.
    """
    text = (turn.user_message or "").strip()
    if not text:
        yield streaming.error("Empty message.")
        yield streaming.finish_message(stop_reason="stop", usage=None)
        return

    try:
        conversation = _ensure_conversation(
            db,
            user=user,
            conversation_id=turn.conversation_id,
            first_message=text,
            role_id=turn.role_id,
        )
    except ValueError as exc:
        yield streaming.error(str(exc))
        yield streaming.finish_message(stop_reason="stop", usage=None)
        return

    yield streaming.data({"conversation_id": conversation.id})

    # User message: one text content block.
    user_content = [{"type": "text", "text": text}]
    _persist_message(db, conversation=conversation, role=ROLE_USER, content=user_content)
    history = _load_history(db, conversation=conversation)

    client = get_client_for_org(organization)
    model = settings.resolved_claude_model
    running_usage = _RunningUsage()
    final_stop_reason: str | None = None

    # Anthropic-side message log: starts as the persisted history (which
    # already includes the just-added user message).
    messages: list[dict[str, Any]] = list(history)

    # Compose system blocks once per turn — the base SYSTEM_PROMPT plus
    # an optional role-context block when the conversation is role-scoped.
    system_blocks = build_system_blocks(db, conversation=conversation)

    for round_index in range(MAX_TOOL_ROUNDS):
        # Each round is a fresh "step" in AI SDK terms; the message id is
        # synthetic but useful for the React client when annotating.
        yield streaming.start_step(
            message_id=f"msg-{conversation.id}-{uuid.uuid4().hex[:8]}"
        )
        try:
            assistant_blocks, stop_reason, round_usage = yield from _stream_one_round(
                client=client,
                model=model,
                messages=messages,
                system=system_blocks,
            )
        except Exception as exc:
            logger.exception("Anthropic stream failed: %s", exc)
            yield streaming.error("Sorry — I hit a problem answering that. Please try again.")
            final_stop_reason = "stop"
            break

        running_usage.input_tokens += round_usage.input_tokens
        running_usage.output_tokens += round_usage.output_tokens
        running_usage.cache_read_tokens += round_usage.cache_read_tokens
        running_usage.cache_creation_tokens += round_usage.cache_creation_tokens
        final_stop_reason = stop_reason

        messages.append({"role": "assistant", "content": assistant_blocks})

        if stop_reason != "tool_use":
            _persist_message(
                db,
                conversation=conversation,
                role=ROLE_ASSISTANT,
                content=assistant_blocks,
                model=model,
                stop_reason=stop_reason,
                token_usage={
                    "input": round_usage.input_tokens,
                    "output": round_usage.output_tokens,
                    "cache_read": round_usage.cache_read_tokens,
                    "cache_creation": round_usage.cache_creation_tokens,
                },
            )
            break

        tool_results: list[dict[str, Any]] = []
        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_call_id = str(block["id"])
            name = str(block["name"])
            args = block.get("input") or {}
            # Enforce role scope: when the chat is role-scoped and the model
            # omitted role_id for a role-scoped tool, inject the
            # conversation's role_id so the handler doesn't fall back to
            # org-wide results.
            if (
                name in _ROLE_SCOPED_TOOLS
                and conversation.role_id is not None
                and args.get("role_id") is None
            ):
                args = {**args, "role_id": int(conversation.role_id)}
            try:
                result = dispatch_tool(name, args, db=db, user=user)
                is_error = False
            except Exception as exc:
                logger.exception("Tool %s failed: %s", name, exc)
                result = {"error": str(exc), "tool": name}
                is_error = True
            yield streaming.tool_result(tool_call_id=tool_call_id, result=result)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": json.dumps(result, default=str),
                    "is_error": is_error,
                }
            )

        # Persist the assistant turn (with tool_use blocks) and the
        # synthetic user turn (with tool_result blocks) so the next call
        # to ``run_chat_turn`` for this conversation has full context.
        _persist_message(
            db,
            conversation=conversation,
            role=ROLE_ASSISTANT,
            content=assistant_blocks,
            model=model,
            stop_reason=stop_reason,
            token_usage={
                "input": round_usage.input_tokens,
                "output": round_usage.output_tokens,
                "cache_read": round_usage.cache_read_tokens,
                "cache_creation": round_usage.cache_creation_tokens,
            },
        )
        _persist_message(
            db,
            conversation=conversation,
            role=ROLE_USER,
            content=tool_results,
        )
        messages.append({"role": "user", "content": tool_results})
    else:
        # Exhausted MAX_TOOL_ROUNDS without a terminal stop_reason.
        yield streaming.error(
            "Reached the tool-call limit for this turn — "
            "ask a more specific question or try again."
        )
        final_stop_reason = "stop"

    # Bump conversation.updated_at + meter the call.
    conversation.updated_at = datetime.now(timezone.utc)
    db.flush()

    try:
        record_event(
            db,
            organization_id=user.organization_id,
            feature=Feature.TAALI_CHAT,
            model=model,
            input_tokens=running_usage.input_tokens,
            output_tokens=running_usage.output_tokens,
            cache_read_tokens=running_usage.cache_read_tokens,
            cache_creation_tokens=running_usage.cache_creation_tokens,
            user_id=user.id,
            entity_id=str(conversation.id),
            metadata={"feature": "taali_chat"},
        )
    except Exception:
        # Metering must never break the chat — log and continue.
        logger.exception("Failed to record usage_event for taali_chat turn")

    aisdk_usage = {
        "promptTokens": running_usage.input_tokens,
        "completionTokens": running_usage.output_tokens,
    }
    yield streaming.finish_step(stop_reason=final_stop_reason, usage=aisdk_usage)
    yield streaming.finish_message(stop_reason=final_stop_reason, usage=aisdk_usage)


__all__ = ["ChatTurnInput", "run_chat_turn"]
