"""Dataclass exports for the ``claude_agent_sdk`` chat wrapper.

Kept in a separate module so callers (route handlers, tests, persistence
layer) can import the shape without dragging in the SDK or the metering
helpers.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatTurn:
    """One end-to-end candidate→assistant exchange driven by
    ``claude_agent_sdk.query()``.

    Mirrors the legacy ``..claude.agentic_chat.ChatTurn`` so the route
    layer can swap implementations without touching its persistence
    contract — but extends it with the richer fields the SDK reports
    (cache tokens, cost, num_turns, stop_reason).

    Notes
    -----
    - ``success`` is False whenever the SDK reported an error OR the
      pre-spend budget gate fired (in which case no SDK call was made
      and no ``UsageEvent`` was written).
    - ``tool_calls_made`` is never shown to the candidate. Each entry is
      ``{"name", "input", "result", "is_error"}`` — the SDK's tool-use
      block plus the correlated tool RESULT (bounded), so scoring can see
      what the agent actually did AND observed, not just what it asked
      for. ``result``/``is_error`` are absent for a call whose result
      never arrived (e.g. the turn truncated at ``max_turns``).
    - ``total_cost_usd`` and the token counts here aggregate every
      internal Anthropic call the SDK made for this turn. They match
      the single ``UsageEvent`` row the service writes (tagged
      ``source=claude_agent_sdk_aggregated``).
    - ``stop_reason`` falls back to ``ResultMessage.subtype`` when the
      SDK doesn't populate the primary field (e.g. ``"error_max_turns"``).
    """

    success: bool
    content: str
    tool_calls_made: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_cost_usd: float = 0.0
    num_turns: int = 0
    stop_reason: str | None = None
    # Model alias used for this chat turn. Persisted onto the ai_prompts
    # record so candidate-budget pricing stays model-aware after future
    # model swaps (claude_budget.summarize_prompt_usage walks records
    # and prices each turn against its model). Defaults to empty string
    # when the SDK doesn't report one — the budget code falls back to
    # the chat-path default (Haiku 4.5).
    model: str = ""
