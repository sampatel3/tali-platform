"""Core chat-turn orchestrator: streams routed AI responses and dispatches tools.

One call to ``run_chat_turn`` runs the full agent loop for one user
message:

  1. Load (or create) the conversation.
  2. Build the message history from persisted ``TaaliChatMessage`` rows.
  3. Stream the selected provider's response, yielding AI-SDK protocol frames.
  4. When the model requests a tool, dispatch to the canonical MCP handler,
     emit a ``tool_call_result`` frame, then continue the loop.
  5. Persist the assistant turn (and any user message we appended).
  6. Record one ``UsageEvent`` per routed model call for the billing meter.

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
from typing import Any, Iterator

from sqlalchemy.orm import Session

from ..candidate_search.tool_failure_contract import (
    CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE,
)
from ..components.ai_routing import (
    RouteExecution,
    finish_route_with_transaction,
    routed_messages_client,
    routing_scope,
)
from ..models.organization import Organization
from ..models.taali_chat_message import (
    ROLE_ASSISTANT,
    ROLE_USER,
)
from ..models.user import User
from ..mcp.provenance import (
    grounding_required_message,
)
from ..mcp.required_reads import (
    ROLE_SCOPE_REQUIRED_MESSAGE,
    RequiredReadController,
)
from ..mcp.shared_reads import GroundingLedger
from ..services.pricing_service import Feature
from ..services.usage_metering_service import record_event as record_event
from ..services.usage_metering_service import InsufficientCreditsError, reserve
from . import streaming
from .conversation_store import (
    ChatTurnInput,
    ensure_conversation as _ensure_conversation,
    load_history as _load_history,
    persist_message as _persist_message,
)
from .route_setup import prepare_chat_route
from .stream_round import _RunningUsage, _stream_one_round
from .system_prompt import build_system_blocks
from .tool_execution import (
    _arguments_with_role_scope as _arguments_with_role_scope,
    execute_tool_round,
)

logger = logging.getLogger("taali.taali_chat")


# Hard cap on tool-call rounds per turn. Each round is one Anthropic call;
# anything past this is almost certainly a runaway loop. 8 rounds is enough
# headroom for "search → compare → drill into one CV" multi-step flows.
MAX_TOOL_ROUNDS = 8
MAX_IDENTICAL_TOOL_ROUNDS = 2
MAX_CONSECUTIVE_ERROR_ROUNDS = 2


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
    """Run one routed chat workflow and close its telemetry on all exits."""

    route_holder: list[RouteExecution] = []
    try:
        yield from _run_chat_turn(
            db=db,
            user=user,
            organization=organization,
            turn=turn,
            route_holder=route_holder,
        )
    except BaseException:
        finish_route_with_transaction(
            db,
            route_holder[0] if route_holder else None,
            succeeded=False,
        )
        raise


def _run_chat_turn(
    *,
    db: Session,
    user: User,
    organization: Organization,
    turn: ChatTurnInput,
    route_holder: list[RouteExecution],
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

    # Gate the first paid round before creating a conversation or persisting
    # the optimistic user message. Previously an exhausted workspace still
    # committed that message, so every press of "Try again" added another
    # unanswered row even though no model request was made.
    try:
        reserve(
            db,
            organization_id=int(user.organization_id),
            feature=Feature.TAALI_CHAT,
        )
    except InsufficientCreditsError:
        yield streaming.error(
            "Your workspace is out of AI credits. Add credits in Settings → Billing to continue."
        )
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

    conversation_db_id = int(conversation.id)
    conversation_role_id = int(conversation.role_id) if conversation.role_id else None
    organization_id = int(user.organization_id)
    user_id = int(user.id)
    yield streaming.data({"conversation_id": conversation_db_id})

    # User message: one text content block.
    user_content = [{"type": "text", "text": text}]
    _persist_message(
        db, conversation=conversation, role=ROLE_USER, content=user_content
    )
    history = _load_history(db, conversation=conversation)

    client = None
    running_usage = _RunningUsage()
    final_stop_reason: str | None = None
    previous_tool_signature: str | None = None
    identical_tool_rounds = 0
    consecutive_error_rounds = 0
    route: RouteExecution | None = None
    workflow_succeeded = False
    grounding_ledger = GroundingLedger(text)
    required_reads = RequiredReadController(
        grounding_ledger,
        role_bound=conversation_role_id is not None,
        current_user_id=user_id,
    )

    # Anthropic-side message log: starts as the persisted history (which
    # already includes the just-added user message).
    messages: list[dict[str, Any]] = list(history)

    # Compose system blocks once per turn — the base SYSTEM_PROMPT plus
    # an optional role-context block when the conversation is role-scoped.
    system_blocks = build_system_blocks(db, conversation=conversation)
    if required_reads.requires_role_scope:
        yield streaming.progress(round_index=0)
        yield streaming.start_step(
            message_id=f"msg-{conversation_db_id}-{uuid.uuid4().hex[:8]}"
        )
        yield streaming.text_delta(ROLE_SCOPE_REQUIRED_MESSAGE)
        _persist_message(
            db,
            conversation=conversation,
            role=ROLE_ASSISTANT,
            content=[{"type": "text", "text": ROLE_SCOPE_REQUIRED_MESSAGE}],
            stop_reason="role_scope_required",
        )
        conversation.updated_at = datetime.now(timezone.utc)
        db.flush()
        finish_route_with_transaction(db, None, succeeded=True)
        empty_usage = {"promptTokens": 0, "completionTokens": 0}
        yield streaming.finish_step(
            stop_reason="role_scope_required",
            usage=empty_usage,
        )
        yield streaming.finish_message(
            stop_reason="role_scope_required",
            usage=empty_usage,
        )
        return

    for round_index in range(MAX_TOOL_ROUNDS):
        # The first round was gated before any conversation state was written.
        # Tool follow-up rounds still need their own balance check.
        if round_index > 0:
            try:
                reserve(
                    db,
                    organization_id=int(user.organization_id),
                    feature=Feature.TAALI_CHAT,
                )
            except InsufficientCreditsError:
                yield streaming.error(
                    "Your workspace is out of AI credits. Add credits in Settings → Billing to continue."
                )
                final_stop_reason = "stop"
                break
        yield streaming.progress(round_index=round_index)
        # Usage metering records and charges the provider call in its own
        # session. Release this session's FK/row locks before entering Claude;
        # otherwise the metering session's organization FOR UPDATE waits on
        # the pending chat inserts while this generator waits on metering — a
        # self-deadlock that leaves the SSE stream silent indefinitely.
        db.commit()
        # Each round is a fresh "step" in AI SDK terms; the message id is
        # synthetic but useful for the React client when annotating.
        yield streaming.start_step(
            message_id=f"msg-{conversation_db_id}-{uuid.uuid4().hex[:8]}"
        )
        required_read = required_reads.next_plan()
        try:
            if route is None:
                route = prepare_chat_route(
                    system_blocks=system_blocks,
                    messages=messages,
                    organization_id=organization_id,
                    user_id=user_id,
                    role_id=conversation_role_id,
                    conversation_id=conversation_db_id,
                    tool_choice=(
                        required_read.tool_choice if required_read is not None else None
                    ),
                )
                route_holder.append(route)
                client = routed_messages_client(route)
            assistant_blocks, stop_reason, round_usage = yield from _stream_one_round(
                client=client,
                model=route.selected_model_id,
                messages=messages,
                system=system_blocks,
                metering={
                    "feature": Feature.TAALI_CHAT,
                    "organization_id": organization_id,
                    "user_id": user_id,
                    "role_id": conversation_role_id,
                    "entity_id": str(conversation_db_id),
                    "metadata": {"feature": "taali_chat", "round": round_index},
                },
                tool_choice=(
                    required_read.tool_choice if required_read is not None else None
                ),
                forced_tool_name=(
                    required_read.tool_name if required_read is not None else None
                ),
                forced_tool_input=(
                    required_read.arguments if required_read is not None else None
                ),
                # Buffer model prose until the terminal block can be classified
                # against both the recruiter's request and the model's actual
                # claims. Tool-call frames still stream normally. This prevents
                # an unprompted hard zero or historical assertion from reaching
                # the UI before the runtime can enforce its canonical-read
                # requirement.
                emit_text_deltas=False,
            )
        except Exception as exc:
            logger.exception("Anthropic stream failed: %s", exc)
            yield streaming.error(
                "Sorry — I hit a problem answering that. Please try again."
            )
            final_stop_reason = "stop"
            break

        running_usage.input_tokens += round_usage.input_tokens
        running_usage.output_tokens += round_usage.output_tokens
        running_usage.cache_read_tokens += round_usage.cache_read_tokens
        running_usage.cache_creation_tokens += round_usage.cache_creation_tokens
        final_stop_reason = stop_reason

        assistant_blocks = required_reads.bind_assistant_blocks(
            required_read,
            assistant_blocks,
        )

        messages.append({"role": "assistant", "content": assistant_blocks})

        if stop_reason != "tool_use":
            assistant_text = "\n".join(
                str(block.get("text") or "")
                for block in assistant_blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            missing_grounding = grounding_ledger.missing_for_answer(assistant_text)
            if missing_grounding:
                safe_message = grounding_required_message(missing_grounding)
                assistant_blocks = [{"type": "text", "text": safe_message}]
                stop_reason = "grounding_required"
                final_stop_reason = stop_reason
                yield streaming.text_delta(safe_message)
            elif assistant_text:
                yield streaming.text_delta(assistant_text)
            _persist_message(
                db,
                conversation=conversation,
                role=ROLE_ASSISTANT,
                content=assistant_blocks,
                model=route.selected_model_id,
                stop_reason=stop_reason,
                token_usage={
                    "input": round_usage.input_tokens,
                    "output": round_usage.output_tokens,
                    "cache_read": round_usage.cache_read_tokens,
                    "cache_creation": round_usage.cache_creation_tokens,
                },
            )
            workflow_succeeded = True
            break

        tool_blocks = [
            block for block in assistant_blocks if block.get("type") == "tool_use"
        ]
        signature = json.dumps(
            [
                {"name": b.get("name"), "input": b.get("input") or {}}
                for b in assistant_blocks
                if b.get("type") == "tool_use"
            ],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if signature == previous_tool_signature:
            identical_tool_rounds += 1
        else:
            identical_tool_rounds = 0
        previous_tool_signature = signature
        if identical_tool_rounds >= MAX_IDENTICAL_TOOL_ROUNDS:
            yield streaming.error(
                "Stopped a repeated tool loop before it could waste more credits."
            )
            final_stop_reason = "stop"
            break

        with routing_scope(route):
            round_result = execute_tool_round(
                db=db,
                user=user,
                conversation=conversation,
                assistant_blocks=assistant_blocks,
                messages=messages,
            )
        tool_results = round_result.live_results
        stored_tool_results = round_result.stored_results
        tool_names_by_id = {
            str(block.get("id") or ""): str(block.get("name") or "")
            for block in tool_blocks
        }
        tool_arguments_by_id = {
            str(block.get("id") or ""): dict(block.get("input") or {})
            for block in tool_blocks
        }
        for result in tool_results:
            if not bool(result.get("is_error")):
                try:
                    payload = json.loads(str(result["content"]))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    payload = None
                observed_name = tool_names_by_id.get(
                    str(result.get("tool_use_id") or ""), ""
                )
                observed_arguments = tool_arguments_by_id.get(
                    str(result.get("tool_use_id") or ""),
                    {},
                )
                grounding_ledger.observe(
                    observed_name,
                    payload,
                    arguments=observed_arguments,
                )
                required_reads.observe(
                    required_read,
                    tool_name=observed_name,
                    result=payload,
                    arguments=observed_arguments,
                )
            yield streaming.tool_result(
                tool_call_id=str(result["tool_use_id"]),
                result=json.loads(str(result["content"])),
            )

        # Persist the assistant turn (with tool_use blocks) and the
        # synthetic user turn (with tool_result blocks) so the next call
        # to ``run_chat_turn`` for this conversation has full context.
        _persist_message(
            db,
            conversation=conversation,
            role=ROLE_ASSISTANT,
            content=assistant_blocks,
            model=route.selected_model_id,
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
            content=stored_tool_results,
        )
        messages.append({"role": "user", "content": tool_results})
        if round_result.search_failure_incident is not None:
            safe_content = [
                {"type": "text", "text": CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE}
            ]
            yield streaming.text_delta(CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE)
            _persist_message(
                db,
                conversation=conversation,
                role=ROLE_ASSISTANT,
                content=safe_content,
                model=route.selected_model_id,
                stop_reason="stop",
            )
            final_stop_reason = "stop"
            break
        if tool_blocks and round_result.error_count == len(tool_blocks):
            consecutive_error_rounds += 1
        else:
            consecutive_error_rounds = 0
        if consecutive_error_rounds >= MAX_CONSECUTIVE_ERROR_ROUNDS:
            yield streaming.error(
                "Stopped after repeated tool errors. Try a narrower request."
            )
            final_stop_reason = "stop"
            break
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
    finish_route_with_transaction(db, route, succeeded=workflow_succeeded)

    aisdk_usage = {
        "promptTokens": running_usage.input_tokens,
        "completionTokens": running_usage.output_tokens,
    }
    yield streaming.finish_step(stop_reason=final_stop_reason, usage=aisdk_usage)
    yield streaming.finish_message(stop_reason=final_stop_reason, usage=aisdk_usage)


__all__ = ["ChatTurnInput", "run_chat_turn"]
