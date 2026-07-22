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
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..candidate_search.tool_failure_contract import (
    CANDIDATE_SEARCH_UNAVAILABLE_CODE,
    CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE,
    candidate_search_failure_result,
    candidate_search_result_failed,
    candidate_search_tools_first,
    is_candidate_search_tool,
    new_candidate_search_incident_id,
    skipped_after_search_failure_result,
    unexpected_tool_failure_result,
)
from ..components.ai_routing import (
    RouteExecution,
    RoutingAttribution,
    TaskKey,
    estimate_anthropic_messages,
    finish_route_with_transaction,
    prepare_route,
    routed_messages_client,
    routing_scope,
)
from ..llm import CallUsage, MeteringContext, one_call
from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_EVENT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from ..models.organization import Organization
from ..models.role import Role
from ..models.user import User
from ..llm.tool_pairs import sanitize_tool_pairs
from ..services.pricing_service import Feature
from ..services.usage_metering_service import InsufficientCreditsError, reserve
from .system_prompt import build_system_blocks
from .tools import (
    AGENT_CHAT_TOOLS,
    CARD_TYPES,
    MUTATING_TOOL_NAMES,
    MUTATION_CARD_TYPES,
    MUTATION_TOOL_NAMES,
    dispatch_tool,
)

logger = logging.getLogger("taali.agent_chat")

# Each round is one Anthropic call. 8 rounds covers "survey → simulate →
# commit → confirm" comfortably; past it is almost certainly a loop.
MAX_TOOL_ROUNDS = 8
# Per-call output ceiling. The model only generates what it needs, so this is a
# cap not a target — but at 2048 a detailed multi-candidate ranking (Workable
# stage + criteria breakdown per person) got truncated mid-word. 4096 gives the
# analytical answers room; the terminal turn also appends a "say continue" note
# if it still hits the ceiling, so the user never sees a bare mid-word cutoff.
MAX_TOKENS_PER_ROUND = 4096
MAX_IDENTICAL_TOOL_ROUNDS = 2
MAX_CONSECUTIVE_ERROR_ROUNDS = 2
# Role-agent calls are interactive. Do not inherit the batch-oriented client
# default (120 seconds plus a retry), which can leave the dock silent for four
# minutes before the worker can post a useful error.
AGENT_CHAT_MODEL_TIMEOUT_SECONDS = 60.0


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
    model gets full context; only the rendered timeline hides them. Sanitised
    so an interrupted-turn orphan can't 400 the whole conversation.
    """
    rows = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == conversation.id,
            # Background events are transcript notifications, not dialogue.
            # They can be inserted between a recruiter's message and the
            # interactive reply, so replaying them would corrupt the model's
            # user/assistant/tool sequence.
            AgentConversationMessage.kind != MESSAGE_KIND_EVENT,
        )
        .order_by(
            AgentConversationMessage.created_at.asc(),
            AgentConversationMessage.id.asc(),
        )
        .all()
    )
    return sanitize_tool_pairs(
        [{"role": r.author_role, "content": r.content} for r in rows]
    )


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


def persist_user_message(
    *,
    db: Session,
    conversation: AgentConversation,
    user: User,
    user_message: str,
) -> AgentConversationMessage:
    """Persist the recruiter's message and return it.

    Split from the model loop so the web request can commit this synchronously —
    the message is durable the instant you hit send, surviving navigation, an
    agent switch, even a failed turn — and hand the slow response to a worker.
    """
    text_in = (user_message or "").strip()
    if not text_in:
        raise ValueError("empty message")
    return _persist(
        db,
        conversation,
        author_role=AUTHOR_ROLE_USER,
        content=[{"type": "text", "text": text_in}],
        kind=MESSAGE_KIND_CHAT,
        text=text_in,
        author_user_id=int(user.id),
    )


def run_agent_response(
    *,
    db: Session,
    role: Role,
    user: User,
    organization: Organization,
    conversation: AgentConversation,
    accepted_role_version: int | None = None,
) -> AgentConversationMessage:
    """Run the routed response workflow and close its telemetry on all exits."""

    route_holder: list[RouteExecution] = []
    try:
        return _run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=organization,
            conversation=conversation,
            accepted_role_version=accepted_role_version,
            route_holder=route_holder,
        )
    except BaseException:
        finish_route_with_transaction(
            db,
            route_holder[0] if route_holder else None,
            succeeded=False,
        )
        raise


def _run_agent_response(
    *,
    db: Session,
    role: Role,
    user: User,
    organization: Organization,
    conversation: AgentConversation,
    accepted_role_version: int | None = None,
    route_holder: list[RouteExecution],
) -> AgentConversationMessage:
    """Run the tool-use loop for a turn whose user message is ALREADY in history,
    then persist + return the final assistant message. The slow, credit-spending,
    role-mutating half of a turn — handed to a Celery worker by the web path
    (see ``run_agent_chat_turn``) so the request returns immediately.

    Flushes at message boundaries so ids populate; the caller commits.
    """
    # Snapshot identifiers before the commit boundaries below. SQLAlchemy
    # expires ORM attributes on commit; touching one between a commit and a
    # metered call would silently open a new transaction and recreate the lock
    # inversion this function is designed to avoid.
    organization_id = int(organization.id)
    role_id = int(role.id)
    user_id = int(user.id)
    conversation_id = int(conversation.id)

    client = None
    system_blocks = build_system_blocks(db, role=role)
    messages = _load_history(db, conversation)
    # Immutable-at-enqueue baseline, advanced only after this turn completes one
    # of its own successful mutation tools. Read-only tools deliberately ignore
    # this cursor so a stale turn can still answer using the latest role state.
    expected_role_version = int(
        accepted_role_version
        if accepted_role_version is not None
        else (role.version or 1)
    )

    usage = CallUsage()
    trace_id = uuid.uuid4().hex
    # Per-call metering is independently committed by the wrapper, so a failed
    # chat transaction cannot erase spend attribution.
    meter = MeteringContext(
        feature=Feature.AGENT_CHAT,
        organization_id=organization_id,
        role_id=role_id,
        entity_id=str(conversation_id),
        user_id=user_id,
        trace_id=trace_id,
    )

    collected_cards: list[dict[str, Any]] = []
    final_text = ""
    final_stop = None
    assistant_row: AgentConversationMessage | None = None
    previous_tool_signature: str | None = None
    identical_tool_rounds = 0
    consecutive_error_rounds = 0
    route: RouteExecution | None = None
    workflow_succeeded = False

    for _round in range(MAX_TOOL_ROUNDS):
        try:
            reserve(
                db,
                organization_id=organization_id,
                feature=Feature.AGENT_CHAT,
            )
        except InsufficientCreditsError:
            final_text = (
                "This organization does not have enough credits for another agent step."
            )
            final_stop = "insufficient_credits"
            break
        # ``one_call`` reserves and records provider usage in an independent
        # SessionLocal transaction. Never hold this worker session's FK/role
        # locks while waiting for that inner transaction: after a tool round it
        # would wait on us while we wait on it (an application-level deadlock
        # PostgreSQL cannot detect). This checkpoint also makes hidden tool
        # plumbing durable and replay-safe if the worker later fails.
        db.commit()
        try:
            if route is None:
                route = prepare_route(
                    TaskKey.ROLE_CHAT_ORCHESTRATION,
                    request_estimate=estimate_anthropic_messages(
                        system=system_blocks,
                        messages=messages,
                        tools=AGENT_CHAT_TOOLS,
                        max_tokens=MAX_TOKENS_PER_ROUND,
                    ),
                    attribution=RoutingAttribution(
                        organization_id=organization_id,
                        user_id=user_id,
                        role_id=role_id,
                        entity_id=str(conversation_id),
                    ),
                    operation="agent_chat.turn",
                )
                route_holder.append(route)
                client = routed_messages_client(route)
            response = one_call(
                client,
                model=route.selected_model_id,
                system=system_blocks,
                messages=messages,
                max_tokens=MAX_TOKENS_PER_ROUND,
                tools=AGENT_CHAT_TOOLS,
                metering=meter,
                usage_sink=usage,
            )
        except Exception as exc:
            logger.exception("agent_chat model call failed: %s", exc)
            final_text = "Sorry — I hit a problem answering that. Please try again."
            final_stop = "error"
            break

        blocks = [_block_to_dict(b) for b in (response.content or [])]
        stop_reason = getattr(response, "stop_reason", None)
        final_stop = stop_reason
        messages.append({"role": "assistant", "content": blocks})

        if stop_reason != "tool_use":
            # Terminal turn — this is the visible answer.
            final_text = _extract_text(blocks)
            if stop_reason == "max_tokens":
                # The model was cut off at the length ceiling. Don't leave a bare
                # mid-word cutoff — flag it so the recruiter can pick it up.
                final_text = (final_text or "").rstrip() + (
                    "\n\n_(I hit my response length limit here — say “continue” "
                    "and I'll pick up where I left off.)_"
                )
            workflow_succeeded = True
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
            model=route.selected_model_id,
            stop_reason=stop_reason,
        )

        tool_blocks = [block for block in blocks if block.get("type") == "tool_use"]
        signature = json.dumps(
            [
                {"name": b.get("name"), "input": b.get("input") or {}}
                for b in blocks
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
            final_text = (
                "I stopped a repeated tool loop before it could waste more credits."
            )
            final_stop = "circuit_breaker"
            break

        tool_count = len(tool_blocks)
        error_count = 0
        terminal_receipt_message: str | None = None
        round_cards: list[dict[str, Any]] = []
        search_failure_incident: str | None = None
        tool_results_by_id: dict[str, dict[str, Any]] = {}
        requested_mutations = [
            str(block.get("name") or "")
            for block in blocks
            if block.get("type") == "tool_use"
            and str(block.get("name") or "") in MUTATING_TOOL_NAMES
        ]
        mutation_batch_blocked = len(requested_mutations) > 1
        for block in candidate_search_tools_first(blocks):
            tool_use_id = str(block.get("id") or "")
            name = str(block.get("name") or "")
            args = block.get("input") or {}
            if search_failure_incident is not None:
                result = skipped_after_search_failure_result(
                    tool=name,
                    incident_id=search_failure_incident,
                )
                is_error = True
            else:
                try:
                    if mutation_batch_blocked and name in MUTATING_TOOL_NAMES:
                        result = {
                            "error": (
                                "Only one state-changing command is allowed per model "
                                "round. Re-read live state and run these one at a time."
                            ),
                            "tool": name,
                            "requested_mutations": requested_mutations,
                        }
                        is_error = True
                    else:
                        # A tool may itself call a metered provider (candidate
                        # search/grounding). Release hidden-message or previous-tool
                        # locks before dispatch for the same reason as ``one_call``.
                        db.commit()
                        with routing_scope(route):
                            result = dispatch_tool(
                                name,
                                args,
                                db=db,
                                role=role,
                                user=user,
                                conversation=conversation,
                                expected_role_version=expected_role_version,
                            )
                        is_error = False
                        if candidate_search_result_failed(name, result):
                            search_failure_incident = new_candidate_search_incident_id()
                            logger.warning(
                                "agent_chat candidate search returned an unavailable "
                                "result tool=%s incident_id=%s",
                                name,
                                search_failure_incident,
                            )
                            db.rollback()
                            result = candidate_search_failure_result(
                                tool=name,
                                incident_id=search_failure_incident,
                            )
                            is_error = True
                        elif name in MUTATION_TOOL_NAMES:
                            # A successful role mutation may have advanced the
                            # revision. Carry that turn-owned revision into a
                            # deliberate follow-up mutation in a later round.
                            expected_role_version = int(
                                role.version or expected_role_version
                            )
                        if not is_error and isinstance(result, dict):
                            if result.get("type") in CARD_TYPES:
                                round_cards.append(result)
                            if result.get("_terminal_message"):
                                terminal_receipt_message = str(
                                    result["_terminal_message"]
                                )
                except HTTPException as exc:
                    # Preserve the same structured 409 contract used by direct UI
                    # writes so the model can truthfully tell the recruiter to
                    # review the newer job instead of retrying a stale mutation.
                    logger.info(
                        "agent_chat tool %s rejected with HTTP %s",
                        name,
                        exc.status_code,
                    )
                    if is_candidate_search_tool(name):
                        search_failure_incident = new_candidate_search_incident_id()
                        db.rollback()
                        result = candidate_search_failure_result(
                            tool=name,
                            incident_id=search_failure_incident,
                        )
                    else:
                        result = {
                            "error": exc.detail,
                            "status_code": int(exc.status_code),
                            "tool": name,
                        }
                    is_error = True
                except Exception as exc:
                    incident_id = new_candidate_search_incident_id()
                    logger.exception(
                        "agent_chat tool %s failed incident_id=%s: %s",
                        name,
                        incident_id,
                        exc,
                    )
                    db.rollback()
                    if is_candidate_search_tool(name):
                        search_failure_incident = incident_id
                        result = candidate_search_failure_result(
                            tool=name,
                            incident_id=incident_id,
                        )
                    else:
                        result = unexpected_tool_failure_result(
                            tool=name,
                            incident_id=incident_id,
                        )
                    is_error = True
            if is_error:
                error_count += 1

            tool_results_by_id[tool_use_id] = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": json.dumps(result, default=str),
                "is_error": is_error,
            }

        if search_failure_incident is not None:
            # Discard partial search output and keep every tool_use paired. This
            # prevents a later conversation replay from treating a partial round
            # as grounded evidence or executing a mutation requested beside it.
            tool_results_by_id = {
                str(block.get("id") or ""): {
                    "type": "tool_result",
                    "tool_use_id": str(block.get("id") or ""),
                    "content": json.dumps(
                        (
                            candidate_search_failure_result(
                                tool=str(block.get("name") or ""),
                                incident_id=search_failure_incident,
                            )
                            if is_candidate_search_tool(str(block.get("name") or ""))
                            else skipped_after_search_failure_result(
                                tool=str(block.get("name") or ""),
                                incident_id=search_failure_incident,
                            )
                        ),
                        default=str,
                    ),
                    "is_error": True,
                }
                for block in tool_blocks
            }
            error_count = tool_count
        else:
            collected_cards.extend(round_cards)

        tool_results = [
            tool_results_by_id[str(block.get("id") or "")] for block in tool_blocks
        ]

        _persist(
            db,
            conversation,
            author_role=AUTHOR_ROLE_USER,
            content=tool_results,
            kind=MESSAGE_KIND_TOOL,
        )
        messages.append({"role": "user", "content": tool_results})
        if search_failure_incident is not None:
            final_text = CANDIDATE_SEARCH_UNAVAILABLE_MESSAGE
            final_stop = CANDIDATE_SEARCH_UNAVAILABLE_CODE
            break
        if terminal_receipt_message:
            # The state change has a committed/queued domain receipt. Do not make
            # another model call that could fail after success and leave the
            # recruiter seeing a contradictory error; close deterministically.
            final_text = terminal_receipt_message
            final_stop = "operation_complete"
            workflow_succeeded = True
            break
        if tool_count > 0 and error_count == tool_count:
            consecutive_error_rounds += 1
        else:
            consecutive_error_rounds = 0
        if consecutive_error_rounds >= MAX_CONSECUTIVE_ERROR_ROUNDS:
            final_text = (
                "I stopped after repeated tool errors. Please try a narrower request."
            )
            final_stop = "circuit_breaker"
            break
    else:
        # Exhausted rounds without a terminal answer.
        final_text = (
            "I ran several steps but didn't land a final answer — try a more "
            "specific ask, e.g. name the role change you want."
        )

    if not final_text:
        final_text = "Done."

    has_mutation = any(c.get("type") in MUTATION_CARD_TYPES for c in collected_cards)
    selected_model_id = route.selected_model_id if route is not None else None
    assistant_row = _persist(
        db,
        conversation,
        author_role=AUTHOR_ROLE_ASSISTANT,
        content=[{"type": "text", "text": final_text}],
        kind=MESSAGE_KIND_ACTION if has_mutation else MESSAGE_KIND_CHAT,
        text=final_text,
        actions=collected_cards or None,
        model=selected_model_id,
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
    finish_route_with_transaction(db, route, succeeded=workflow_succeeded)

    return assistant_row


def run_agent_turn(
    *,
    db: Session,
    role: Role,
    user: User,
    organization: Organization,
    conversation: AgentConversation,
    user_message: str,
    accepted_role_version: int | None = None,
) -> list[AgentConversationMessage]:
    """Persist the user message then run the response in one synchronous call —
    returns the new VISIBLE messages (user + final assistant). Used by the bulk
    fan-out (already per-role in a worker) and the route tests. The single-message
    web path splits these halves instead, to run the response asynchronously.
    """
    user_row = persist_user_message(
        db=db, conversation=conversation, user=user, user_message=user_message
    )
    assistant_row = run_agent_response(
        db=db,
        role=role,
        user=user,
        organization=organization,
        conversation=conversation,
        accepted_role_version=accepted_role_version,
    )
    return [user_row, assistant_row]


__all__ = [
    "MAX_TOOL_ROUNDS",
    "persist_user_message",
    "run_agent_response",
    "run_agent_turn",
]
