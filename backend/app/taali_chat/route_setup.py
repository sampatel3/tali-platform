"""Task-contract setup for one routed Taali Chat turn."""

from __future__ import annotations

from typing import Any

from ..components.ai_routing import (
    RouteExecution,
    RoutingAttribution,
    TaskKey,
    estimate_anthropic_messages,
    prepare_route,
)
from .stream_round import MAX_TOKENS_PER_TURN
from .tool_registry import TAALI_CHAT_TOOLS


def prepare_chat_route(
    *,
    system_blocks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    organization_id: int,
    user_id: int,
    role_id: int | None,
    conversation_id: int,
    tool_choice: dict[str, Any] | None = None,
) -> RouteExecution:
    """Plan and durably start the task route before transport resolution."""

    return prepare_route(
        TaskKey.GENERAL_CHAT_ORCHESTRATION,
        request_estimate=estimate_anthropic_messages(
            system=system_blocks,
            messages=messages,
            tools=TAALI_CHAT_TOOLS,
            tool_choice=tool_choice,
            max_tokens=MAX_TOKENS_PER_TURN,
        ),
        attribution=RoutingAttribution(
            organization_id=organization_id,
            user_id=user_id,
            role_id=role_id,
            entity_id=str(conversation_id),
        ),
        operation="taali_chat.turn",
    )


__all__ = ["prepare_chat_route"]
