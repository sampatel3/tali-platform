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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from anthropic import Anthropic
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
from .system_prompt import SYSTEM_PROMPT
from .tool_registry import TAALI_CHAT_TOOLS, dispatch_tool

logger = logging.getLogger("taali.taali_chat")


# Hard cap on tool-call rounds per turn. Each round is one Anthropic call;
# anything past this is almost certainly a runaway loop. 8 rounds is enough
# headroom for "search → compare → drill into one CV" multi-step flows.
MAX_TOOL_ROUNDS = 8

# Cap on tokens per turn — protects against runaway responses; 4k is large
# enough for a comparison table + commentary.
MAX_TOKENS_PER_TURN = 4096


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


@dataclass
class _RunningUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


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
    """Pull persisted messages and return them in Anthropic message format."""
    rows = (
        db.query(TaaliChatMessage)
        .filter(TaaliChatMessage.conversation_id == conversation.id)
        .order_by(TaaliChatMessage.created_at.asc(), TaaliChatMessage.id.asc())
        .all()
    )
    return [{"role": row.role, "content": row.content} for row in rows]


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
    system_blocks = _build_system_blocks(db, conversation=conversation)

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
            yield streaming.error(f"Model call failed: {exc}")
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


# ---------------------------------------------------------------------------
# One Anthropic streaming round
# ---------------------------------------------------------------------------


def _build_system_blocks(
    db: Session, *, conversation: TaaliChatConversation
) -> list[dict[str, Any]]:
    """Compose the system prompt for this conversation.

    Always includes the cached base SYSTEM_PROMPT as the first block so
    the prompt cache hit applies across every conversation in the
    org/window. When the conversation is role-scoped, appends a second
    cached block with the role's name + recent agent activity so the
    chat tools can default to that role and Claude can reason about
    'this role' without the user having to say 'role 42.'
    """
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    role_id = getattr(conversation, "role_id", None)
    if role_id is None:
        return blocks
    context = _role_context_block(
        db,
        role_id=int(role_id),
        organization_id=int(conversation.organization_id),
    )
    if context:
        blocks.append(
            {
                "type": "text",
                "text": context,
                "cache_control": {"type": "ephemeral"},
            }
        )
    return blocks


def _role_context_block(db: Session, *, role_id: int, organization_id: int) -> str | None:
    """Render a short prompt block summarising this role + recent agent
    activity. Pulled fresh each turn so the recruiter sees current state
    when they ask 'what's the agent doing on this role?'."""
    from ..models.agent_decision import AgentDecision
    from ..models.agent_run import AgentRun
    from ..models.role import Role

    role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
            Role.deleted_at.is_(None),
        )
        .first()
    )
    if role is None:
        return None

    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role_id,
            AgentDecision.organization_id == organization_id,
            AgentDecision.status == "pending",
        )
        .count()
    )
    last_run = (
        db.query(AgentRun)
        .filter(
            AgentRun.role_id == role_id,
            AgentRun.organization_id == organization_id,
        )
        .order_by(AgentRun.started_at.desc())
        .first()
    )
    last_run_line = "no agent cycles yet"
    if last_run is not None:
        ts = last_run.started_at.isoformat() if last_run.started_at else "?"
        last_run_line = (
            f"last agent cycle: {last_run.trigger} trigger, status={last_run.status}, "
            f"{int(last_run.decisions_emitted or 0)} decision(s) emitted, started {ts}"
        )

    return (
        f"# Role-scoped conversation\n"
        f"This chat is about role_id={role_id}: {role.name!r}.\n"
        f"When the user asks about 'the agent' / 'this role' / 'pending decisions' / "
        f"'why did you queue X' without naming a role, default to this role.\n"
        f"For agent-aware tools (list_recent_agent_decisions, list_recent_agent_runs, "
        f"explain_agent_decision) you may omit role_id — the conversation's "
        f"role scope applies.\n"
        f"Current state: {pending} pending agent decision(s) awaiting recruiter review. "
        f"{last_run_line}.\n"
    )


def _stream_one_round(
    *,
    client: Anthropic,
    model: str,
    messages: list[dict[str, Any]],
    system: list[dict[str, Any]],
) -> Iterator[streaming.Frame]:
    """Stream one Anthropic call. Yields frames; returns (blocks, stop, usage)."""
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS_PER_TURN,
        system=system,
        tools=TAALI_CHAT_TOOLS,
        messages=messages,
    ) as stream:
        # Per-block accumulator for tool_use input JSON (Anthropic streams
        # arguments as ``input_json`` partial deltas; we have to glue them
        # back into a dict for the AI-SDK ``b`` frame).
        tool_args_buffer: dict[str, str] = {}
        tool_names: dict[str, str] = {}

        for event in stream:
            etype = getattr(event, "type", None)

            if etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if block is None:
                    continue
                if getattr(block, "type", None) == "tool_use":
                    tool_id = block.id
                    tool_args_buffer[tool_id] = ""
                    tool_names[tool_id] = block.name
                    yield streaming.tool_call_streaming_start(
                        tool_call_id=tool_id, tool_name=block.name
                    )

            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                if delta is None:
                    continue
                dtype = getattr(delta, "type", None)
                if dtype == "text_delta":
                    yield streaming.text_delta(delta.text)
                elif dtype == "input_json_delta":
                    block_index = getattr(event, "index", None)
                    # Match the running tool_use block by index → id.
                    tool_id = _tool_id_at_index(stream, block_index)
                    if tool_id is None:
                        continue
                    partial = delta.partial_json or ""
                    tool_args_buffer[tool_id] = tool_args_buffer.get(tool_id, "") + partial
                    yield streaming.tool_call_delta(
                        tool_call_id=tool_id, args_text_delta=partial
                    )

            elif etype == "content_block_stop":
                block_index = getattr(event, "index", None)
                tool_id = _tool_id_at_index(stream, block_index)
                if tool_id is not None and tool_id in tool_args_buffer:
                    raw = tool_args_buffer.get(tool_id, "")
                    try:
                        args = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        args = {}
                    name = tool_names.get(tool_id, "")
                    yield streaming.tool_call(
                        tool_call_id=tool_id, tool_name=name, args=args
                    )

        # Final message snapshot.
        final = stream.get_final_message()

    blocks = [_block_to_dict(b) for b in final.content]
    usage = _RunningUsage(
        input_tokens=int(getattr(final.usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(final.usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(final.usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(
            getattr(final.usage, "cache_creation_input_tokens", 0) or 0
        ),
    )
    return blocks, final.stop_reason, usage


def _tool_id_at_index(stream, index: int | None) -> str | None:
    """Look up the running tool_use block id by its position in the stream."""
    if index is None:
        return None
    try:
        message = stream.current_message_snapshot
    except Exception:  # pragma: no cover — older SDKs
        return None
    blocks = getattr(message, "content", []) or []
    if 0 <= index < len(blocks):
        block = blocks[index]
        if getattr(block, "type", None) == "tool_use":
            return getattr(block, "id", None)
    return None


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Anthropic SDK content blocks → plain JSON-safe dicts for persistence."""
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input or {},
        }
    if btype == "thinking":
        return {"type": "thinking", "thinking": getattr(block, "thinking", "")}
    # Fallback: model_dump if pydantic, else str()
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": btype or "unknown", "raw": str(block)}


__all__ = ["ChatTurnInput", "run_chat_turn"]
