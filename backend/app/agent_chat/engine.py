"""Synchronous tool-use loop for the role-agent chat.

One call to :func:`run_agent_turn` runs a full user→assistant exchange:

  1. Persist the user message.
  2. Replay the conversation's message history into Anthropic format.
  3. Loop: call the model with the action tools; when it requests a tool,
     dispatch it (reads, impact simulations, constraint/threshold writes),
     collect any impact card, feed the result back, continue.
  4. Persist the final assistant message with its flattened text + the
     impact cards it produced, plus the intermediate tool plumbing (hidden
     from the rendered timeline but kept for replay fidelity).
  5. Record one aggregate UsageEvent for the meter.

Synchronous (not streaming) on purpose: every turn can MUTATE role state —
a discrete, atomic, testable request/response is the right shape for a write
path, and the impact cards are structured results the caller renders.
Bounded by ``MAX_TOOL_ROUNDS`` so a runaway loop can't drain credits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..llm import CallUsage, MeteringContext, one_call
from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from ..models.organization import Organization
from ..models.role import Role
from ..models.user import User
from ..platform.config import settings
from ..services.claude_client_resolver import get_client_for_org
from ..services.pricing_service import Feature
from ..services.usage_metering_service import record_event
from .system_prompt import PROMPT_VERSION, build_system_blocks
from .tools import AGENT_CHAT_TOOLS, CARD_TYPES, MUTATION_CARD_TYPES, dispatch_tool

logger = logging.getLogger("taali.agent_chat")

# Each round is one Anthropic call. 8 rounds covers "survey → simulate →
# commit → confirm" comfortably; past it is almost certainly a loop.
MAX_TOOL_ROUNDS = 8
MAX_TOKENS_PER_ROUND = 2048


def _extract_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(
        b.get("text", "") for b in blocks if b.get("type") == "text"
    ).strip()


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Anthropic SDK content block → JSON-safe dict for persistence/replay."""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "")}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", None) or {},
        }
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": btype or "unknown", "raw": str(block)}


def _load_history(db: Session, conversation: AgentConversation) -> list[dict[str, Any]]:
    """Persisted messages in Anthropic ``{role, content}`` format for replay.

    Includes the hidden tool plumbing (tool_use / tool_result turns) so the
    model gets full context; only the rendered timeline hides them.
    """
    rows = (
        db.query(AgentConversationMessage)
        .filter(AgentConversationMessage.conversation_id == conversation.id)
        .order_by(
            AgentConversationMessage.created_at.asc(),
            AgentConversationMessage.id.asc(),
        )
        .all()
    )
    return [{"role": r.author_role, "content": r.content} for r in rows]


def _persist(
    db: Session,
    conversation: AgentConversation,
    *,
    author_role: str,
    content: list[dict[str, Any]],
    kind: str,
    text: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    author_user_id: int | None = None,
    model: str | None = None,
    stop_reason: str | None = None,
    token_usage: dict[str, int] | None = None,
) -> AgentConversationMessage:
    msg = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role_id=conversation.role_id,
        author_role=author_role,
        author_user_id=author_user_id,
        kind=kind,
        content=content,
        text=text,
        actions=actions or None,
        model=model,
        stop_reason=stop_reason,
        token_usage=token_usage,
    )
    db.add(msg)
    db.flush()
    return msg


def run_agent_turn(
    *,
    db: Session,
    role: Role,
    user: User,
    organization: Organization,
    conversation: AgentConversation,
    user_message: str,
) -> list[AgentConversationMessage]:
    """Run one turn; return the new VISIBLE messages (user + final assistant).

    Flushes at message boundaries so ids populate; the caller commits.
    """
    text_in = (user_message or "").strip()
    if not text_in:
        raise ValueError("empty message")

    user_row = _persist(
        db,
        conversation,
        author_role=AUTHOR_ROLE_USER,
        content=[{"type": "text", "text": text_in}],
        kind=MESSAGE_KIND_CHAT,
        text=text_in,
        author_user_id=int(user.id),
    )

    client = get_client_for_org(organization)
    model = settings.resolved_claude_model
    system_blocks = build_system_blocks(db, role=role)
    messages = _load_history(db, conversation)

    usage = CallUsage()
    trace_id = uuid.uuid4().hex
    # Skip the wrapper's per-call UsageEvent; we record one aggregate below
    # (the orchestrator's metering pattern). claude_call_log rows still land.
    meter = MeteringContext.skipped(metered_by="agent_chat", trace_id=trace_id)

    collected_cards: list[dict[str, Any]] = []
    final_text = ""
    final_stop = None
    assistant_row: AgentConversationMessage | None = None

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            response = one_call(
                client,
                model=model,
                system=system_blocks,
                messages=messages,
                max_tokens=MAX_TOKENS_PER_ROUND,
                tools=AGENT_CHAT_TOOLS,
                metering=meter,
                usage_sink=usage,
            )
        except Exception as exc:
            logger.exception("agent_chat model call failed: %s", exc)
            final_text = f"Sorry — I hit an error reaching the model: {exc}"
            final_stop = "error"
            break

        blocks = [_block_to_dict(b) for b in (response.content or [])]
        stop_reason = getattr(response, "stop_reason", None)
        final_stop = stop_reason
        messages.append({"role": "assistant", "content": blocks})

        if stop_reason != "tool_use":
            # Terminal turn — this is the visible answer.
            final_text = _extract_text(blocks)
            break

        # Tool round: persist the assistant tool_use turn (hidden), dispatch,
        # collect cards, feed results back.
        _persist(
            db,
            conversation,
            author_role=AUTHOR_ROLE_ASSISTANT,
            content=blocks,
            kind=MESSAGE_KIND_TOOL,
            text=_extract_text(blocks) or None,
            model=model,
            stop_reason=stop_reason,
        )

        tool_results: list[dict[str, Any]] = []
        for block in blocks:
            if block.get("type") != "tool_use":
                continue
            tool_use_id = str(block.get("id") or "")
            name = str(block.get("name") or "")
            args = block.get("input") or {}
            try:
                result = dispatch_tool(name, args, db=db, role=role, user=user)
                is_error = False
                if isinstance(result, dict) and result.get("type") in CARD_TYPES:
                    collected_cards.append(result)
            except Exception as exc:
                logger.exception("agent_chat tool %s failed: %s", name, exc)
                result = {"error": str(exc), "tool": name}
                is_error = True
            import json as _json

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": _json.dumps(result, default=str),
                    "is_error": is_error,
                }
            )

        _persist(
            db,
            conversation,
            author_role=AUTHOR_ROLE_USER,
            content=tool_results,
            kind=MESSAGE_KIND_TOOL,
        )
        messages.append({"role": "user", "content": tool_results})
    else:
        # Exhausted rounds without a terminal answer.
        final_text = (
            "I ran several steps but didn't land a final answer — try a more "
            "specific ask, e.g. name the role change you want."
        )

    if not final_text:
        final_text = "Done."

    has_mutation = any(c.get("type") in MUTATION_CARD_TYPES for c in collected_cards)
    assistant_row = _persist(
        db,
        conversation,
        author_role=AUTHOR_ROLE_ASSISTANT,
        content=[{"type": "text", "text": final_text}],
        kind=MESSAGE_KIND_ACTION if has_mutation else MESSAGE_KIND_CHAT,
        text=final_text,
        actions=collected_cards or None,
        model=model,
        stop_reason=final_stop,
        token_usage={
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "cache_read": usage.cache_read_tokens,
            "cache_creation": usage.cache_creation_tokens,
        },
    )

    now = datetime.now(timezone.utc)
    conversation.last_message_at = now
    conversation.updated_at = now
    db.flush()

    try:
        record_event(
            db,
            organization_id=int(organization.id),
            feature=Feature.AGENT_CHAT,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            user_id=int(user.id),
            role_id=int(role.id),
            entity_id=str(conversation.id),
            metadata={"feature": "agent_chat", "prompt_version": PROMPT_VERSION},
        )
    except Exception:
        logger.exception("agent_chat: failed to record usage_event")

    return [user_row, assistant_row]


__all__ = ["MAX_TOOL_ROUNDS", "run_agent_turn"]
