"""One server-side Taali Chat tool round with safe search-failure semantics."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..candidate_search.tool_failure_contract import (
    candidate_search_failure_result,
    candidate_search_result_failed,
    candidate_search_tools_first,
    is_candidate_search_tool,
    new_candidate_search_incident_id,
    skipped_after_search_failure_result,
    unexpected_tool_failure_result,
)
from ..models.taali_chat_conversation import TaaliChatConversation
from ..models.user import User
from .persistence import result_for_storage
from .tool_registry import dispatch_tool

logger = logging.getLogger("taali.taali_chat")

_ROLE_SCOPED_TOOLS = frozenset(
    {
        "search_applications",
        "find_top_candidates",
        "screen_pool_against_requirement",
        "nl_search_candidates",
        "graph_search_candidates",
        "list_recent_agent_decisions",
        "list_recent_agent_runs",
        "get_recruiting_overview",
        "list_assessments",
        "preview_related_role",
        "create_related_role",
    }
)


def _arguments_with_role_scope(
    name: str,
    arguments: dict[str, Any],
    *,
    conversation_role_id: int | None,
) -> dict[str, Any]:
    if (
        name in _ROLE_SCOPED_TOOLS
        and conversation_role_id is not None
        and arguments.get("role_id") is None
    ):
        return {**arguments, "role_id": int(conversation_role_id)}
    return arguments


@dataclass(frozen=True)
class ToolRoundResult:
    live_results: list[dict[str, Any]]
    stored_results: list[dict[str, Any]]
    error_count: int
    search_failure_incident: str | None


def execute_tool_round(
    *,
    db: Session,
    user: User,
    conversation: TaaliChatConversation,
    assistant_blocks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> ToolRoundResult:
    """Dispatch a round, buffering results until every search is verified."""

    tool_blocks = [
        block for block in assistant_blocks if block.get("type") == "tool_use"
    ]
    conversation_role_id = int(conversation.role_id) if conversation.role_id else None
    outcomes: dict[str, tuple[Any, bool, str]] = {}
    search_failure_incident: str | None = None

    for block in candidate_search_tools_first(assistant_blocks):
        tool_call_id = str(block.get("id") or "")
        name = str(block.get("name") or "")
        args = _arguments_with_role_scope(
            name,
            block.get("input") or {},
            conversation_role_id=conversation_role_id,
        )
        if search_failure_incident is not None:
            result = skipped_after_search_failure_result(
                tool=name,
                incident_id=search_failure_incident,
            )
            outcomes[tool_call_id] = (result, True, name)
            continue

        try:
            result = dispatch_tool(
                name,
                args,
                db=db,
                user=user,
                conversation=conversation,
                messages=messages,
            )
            is_error = False
            if candidate_search_result_failed(name, result):
                search_failure_incident = new_candidate_search_incident_id()
                logger.warning(
                    "Taali Chat candidate search returned an unavailable result "
                    "tool=%s incident_id=%s",
                    name,
                    search_failure_incident,
                )
                db.rollback()
                result = candidate_search_failure_result(
                    tool=name,
                    incident_id=search_failure_incident,
                )
                is_error = True
            else:
                # One tool is one durable transaction. Candidate reports can
                # contain bearer URLs, and no later independent tool failure
                # may roll back a result that has already been buffered.
                db.commit()
        except Exception:
            incident_id = new_candidate_search_incident_id()
            logger.exception(
                "Taali Chat tool failed tool=%s incident_id=%s",
                name,
                incident_id,
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
        outcomes[tool_call_id] = (result, is_error, name)

    if search_failure_incident is not None:
        # Discard every buffered result from a mixed/parallel round. The next
        # transcript replay must not see partial search evidence.
        outcomes = {
            str(block.get("id") or ""): (
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
                True,
                str(block.get("name") or ""),
            )
            for block in tool_blocks
        }

    live_results: list[dict[str, Any]] = []
    stored_results: list[dict[str, Any]] = []
    error_count = 0
    for block in tool_blocks:
        tool_call_id = str(block.get("id") or "")
        result, is_error, name = outcomes[tool_call_id]
        if is_error:
            error_count += 1
        live_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": json.dumps(result, default=str),
                "is_error": is_error,
            }
        )
        stored_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": json.dumps(
                    result if is_error else result_for_storage(name, result),
                    default=str,
                ),
                "is_error": is_error,
            }
        )

    return ToolRoundResult(
        live_results=live_results,
        stored_results=stored_results,
        error_count=error_count,
        search_failure_incident=search_failure_incident,
    )


__all__ = ["ToolRoundResult", "execute_tool_round"]
