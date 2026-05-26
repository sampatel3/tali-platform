"""Agentic multi-turn chat service for candidate assessments.

This is leaf B of the terminal-removal refactor. The old assessment runtime
delegated to the Claude CLI ("claude_cli_terminal"); the new runtime is a
plain Anthropic ``messages.create`` loop with native tool-use. Tool
schemas + dispatch live in leaf A (``tool_definitions.py`` +
``claude_tool_executor.py``); this file owns the conversation loop, the
metering contract, and the budget guard.

Invariants
----------
1. Every Anthropic call flows through ``MeteredAnthropicClient`` so a
   ``UsageEvent`` lands per turn. The CI architecture gate
   (``test_no_bare_anthropic_client_construction``) requires the literal
   ``Anthropic(api_key=...)`` and ``MeteredAnthropicClient(inner=...)``
   to appear in the same file — both do, in ``__init__``.
2. The metering kwarg is passed on every ``messages.create`` call (not
   just the first), so a chat that does 4 tool turns writes 4 usage_events.
3. Budget is checked between turns. If ``budget_remaining_usd`` minus the
   estimated next-call cost falls below ``_BUDGET_SAFETY_MARGIN_USD``, we
   stop and return a partial response — better than hard-stopping mid-tool
   and leaving the candidate without context.
4. Tool-loop iterations are capped by ``settings.CLAUDE_TOOL_MAX_TURNS``.
   If we hit the cap, append a brief "I couldn't complete that" text turn
   so the caller has *something* to persist.

Caller persists the returned ``ChatTurn`` to ``ai_prompts``; this service
does no DB I/O.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from anthropic import Anthropic

from ....platform.config import settings
from ....services.metered_anthropic_client import MeteredAnthropicClient
from ....components.assessments.claude_budget import compute_claude_cost_usd
from .model_fallback import candidate_models_for, is_model_not_found_error

logger = logging.getLogger(__name__)

# Tool definitions live in leaf A. The import is wrapped so this module
# loads cleanly in branches/CI before leaf A's PR merges; in production
# both PRs land together and ``TOOLS`` resolves to the real list. The
# constructor also accepts a ``tools`` override so unit tests inject mocks
# without depending on leaf A.
try:  # pragma: no cover — exercised in integration once leaf A lands
    from .tool_definitions import TOOLS as _DEFAULT_TOOLS  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _DEFAULT_TOOLS: list[dict] = []

# Safety margin between turns. If we have less than this much budget
# headroom left after subtracting the estimated next-call cost, we bail
# out rather than risk going negative. 0.10 USD covers ~2-3 conservative
# Haiku turns; tighter than this and the partial response gets clipped.
_BUDGET_SAFETY_MARGIN_USD = 0.10

# Conservative floor for "estimated next-call cost" when we have no
# history yet. ~5k input tokens + 1k output @ Haiku rates ≈ $0.01.
_MIN_NEXT_CALL_ESTIMATE_USD = 0.01

# Brief filler text appended when we hit the tool-turn cap. Surfaces to
# the candidate as the final assistant message so they aren't left with
# an empty response.
_MAX_TURNS_FALLBACK_TEXT = (
    "I couldn't complete that within the tool-use budget for this turn. "
    "Try narrowing your request or breaking it into smaller steps."
)

_BUDGET_EXHAUSTED_TEXT = "Budget exhausted — partial response above."


@dataclass
class ChatTurn:
    """One end-to-end candidate→assistant exchange.

    ``content`` is the rendered assistant text the candidate sees and the
    caller persists to ``ai_prompts.response``. ``tool_calls_made`` is
    analytics-only (tool name + input + ok flag) — not shown to the
    candidate, but useful for reconstructing what Claude did.

    Token counts are cumulative across every ``messages.create`` call in
    the loop, so ``ai_prompts.input_tokens`` / ``output_tokens`` reflect
    the full assistant turn's spend (not just the last sub-call).
    """

    role: str  # "assistant" — always, for now (the user turn lives in the input)
    content: str
    tool_calls_made: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class AgenticChatService:
    """Multi-turn ``messages.create`` loop with tool-use + metering + budget.

    Construction binds an org-scoped api_key, the organization_id (for
    UsageEvent attribution), and the executor that dispatches tool calls.
    The executor interface is intentionally narrow: ``dispatch(name, input)
    -> {"ok": bool, "result": ..., "error": ...?}``. Don't construct the
    executor here — caller injects it so the same chat service can be
    reused across assessment sessions with different sandbox contexts.
    """

    def __init__(
        self,
        api_key: str,
        *,
        organization_id: int,
        executor: Any,
        tools: Optional[list[dict]] = None,
        max_turns: Optional[int] = None,
    ):
        """
        Args:
            api_key: Anthropic API key (per-workspace or shared).
            organization_id: Bills this org for every turn. Required —
                we'd rather crash here than ship an unattributed call to
                prod (the 2026-05-20 reconciliation gap).
            executor: Object exposing ``dispatch(tool_name, tool_input)
                -> dict``. Leaf A's ``ClaudeToolExecutor`` is the
                production impl; tests can inject a mock.
            tools: Optional override for the tool-schema list. Defaults
                to leaf A's ``TOOLS`` constant. Tests pass a small list
                so they don't depend on leaf A merging first.
            max_turns: Optional override for the tool-loop cap. Defaults
                to ``settings.CLAUDE_TOOL_MAX_TURNS``.
        """
        # NOTE: both ``Anthropic(api_key=`` and ``MeteredAnthropicClient(inner=``
        # must appear in the same source file to satisfy the architecture
        # gate ``test_no_bare_anthropic_client_construction``. Do not
        # split this construction across helpers.
        self._client = MeteredAnthropicClient(
            inner=Anthropic(api_key=api_key),
            organization_id=organization_id,
        )
        self._organization_id = organization_id
        self._executor = executor
        self._tools = tools if tools is not None else _DEFAULT_TOOLS
        self._model = settings.resolved_claude_model
        self._max_turns = int(max_turns if max_turns is not None else settings.CLAUDE_TOOL_MAX_TURNS)
        logger.info(
            "AgenticChatService initialised model=%s org=%s max_turns=%d tool_count=%d",
            self._model,
            organization_id,
            self._max_turns,
            len(self._tools),
        )

    # ----- public API -----------------------------------------------------

    def run(
        self,
        *,
        messages: list[dict],
        system: str,
        budget_remaining_usd: Optional[float],
    ) -> ChatTurn:
        """Drive the tool-use loop and return a single ``ChatTurn``.

        Args:
            messages: Prior conversation already flattened to Anthropic's
                ``[{"role": ..., "content": ...}, ...]`` shape. The
                latest user message must be the last entry. We append
                assistant + tool_result turns to a *local* copy so we
                don't mutate the caller's list.
            system: System prompt — task scenario + safety instructions.
            budget_remaining_usd: How much candidate budget is left. None
                = unbounded (e.g. demo without a cap). When set, we
                track per-turn cost and bail out before going negative.

        Returns:
            A single ``ChatTurn`` aggregating every ``messages.create``
            call in the loop. Content is whatever assistant text Claude
            produced (joined across multi-block responses if any). Tokens
            sum across all sub-calls so persistence reflects true spend.
        """
        # Work on a local copy — never mutate the caller's list.
        working_messages: list[dict] = list(messages)

        cumulative_input = 0
        cumulative_output = 0
        tool_calls_made: list[dict] = []
        final_text_blocks: list[str] = []
        stopped_for_budget = False
        stopped_for_max_turns = False

        for turn_idx in range(self._max_turns):
            # Budget pre-check: estimate next call's cost from the most
            # expensive turn so far (context grows, so prior peak is a
            # reasonable lower bound on the next call) and bail if we'd
            # go under the safety margin.
            if budget_remaining_usd is not None:
                spent_so_far = compute_claude_cost_usd(
                    input_tokens=cumulative_input,
                    output_tokens=cumulative_output,
                )
                remaining = budget_remaining_usd - spent_so_far
                if turn_idx > 0:
                    # Use this turn's *running* cost as the estimate for
                    # the next one — tools only add context, so next
                    # turn is at least as expensive.
                    est_next = max(_MIN_NEXT_CALL_ESTIMATE_USD, spent_so_far / max(1, turn_idx))
                else:
                    est_next = _MIN_NEXT_CALL_ESTIMATE_USD
                if remaining - est_next < _BUDGET_SAFETY_MARGIN_USD:
                    logger.info(
                        "AgenticChatService budget guard tripped at turn=%d "
                        "(remaining=%.4f est_next=%.4f margin=%.4f) — stopping",
                        turn_idx,
                        remaining,
                        est_next,
                        _BUDGET_SAFETY_MARGIN_USD,
                    )
                    stopped_for_budget = True
                    break

            try:
                response = self._create_with_fallback(
                    system=system,
                    messages=working_messages,
                )
            except Exception:
                logger.exception(
                    "AgenticChatService messages.create failed at turn=%d", turn_idx
                )
                raise

            usage = getattr(response, "usage", None)
            cumulative_input += int(getattr(usage, "input_tokens", 0) or 0)
            cumulative_output += int(getattr(usage, "output_tokens", 0) or 0)

            content_blocks = list(getattr(response, "content", []) or [])
            stop_reason = getattr(response, "stop_reason", None)

            # Pull any plain-text blocks Claude produced *this* turn —
            # the candidate-visible content is the union of text blocks
            # across every turn the loop executed (Anthropic emits
            # interleaved text + tool_use when explaining what it's
            # about to do).
            this_turn_text = self._extract_text(content_blocks)
            if this_turn_text:
                final_text_blocks.append(this_turn_text)

            if stop_reason != "tool_use":
                # end_turn / max_tokens / stop_sequence — natural stop.
                break

            # tool_use path: append assistant turn (raw content blocks),
            # dispatch each tool, then append a user turn with tool_result
            # blocks, and loop.
            tool_uses = [b for b in content_blocks if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                # Defensive: stop_reason said tool_use but nothing parseable.
                break

            working_messages.append(
                {
                    "role": "assistant",
                    "content": self._serialize_content_blocks(content_blocks),
                }
            )

            tool_results: list[dict] = []
            for tool_use in tool_uses:
                name = getattr(tool_use, "name", "") or ""
                tool_input = getattr(tool_use, "input", {}) or {}
                tool_use_id = getattr(tool_use, "id", "") or ""

                try:
                    dispatch_result = self._executor.dispatch(name, tool_input)
                except Exception as exc:  # noqa: BLE001 — never let tool errors break the loop
                    logger.warning(
                        "AgenticChatService executor.dispatch raised for tool=%s: %s",
                        name,
                        exc,
                    )
                    dispatch_result = {"ok": False, "error": f"executor_exception: {exc!s}"[:500]}

                ok = bool(dispatch_result.get("ok", False))
                tool_calls_made.append(
                    {
                        "name": name,
                        "input": tool_input,
                        "result_ok": ok,
                    }
                )

                # Whether ok or not, surface the result back to Claude so
                # it can self-correct. An ok=False result becomes a tool
                # error block; Claude almost always recovers on the next
                # turn (e.g. by re-reading the directory).
                tool_results.append(
                    self._format_tool_result_block(
                        tool_use_id=tool_use_id,
                        dispatch_result=dispatch_result,
                    )
                )

            working_messages.append({"role": "user", "content": tool_results})

        else:
            # The for-loop's else fires only when we exhausted ``max_turns``
            # without a break. That means we kept seeing stop_reason="tool_use"
            # right up to the cap.
            stopped_for_max_turns = True

        # Assemble the final assistant content.
        content_parts: list[str] = [t for t in final_text_blocks if t]
        if stopped_for_max_turns:
            content_parts.append(_MAX_TURNS_FALLBACK_TEXT)
            logger.warning(
                "AgenticChatService hit max_turns=%d cap (org=%s tool_calls=%d)",
                self._max_turns,
                self._organization_id,
                len(tool_calls_made),
            )
        if stopped_for_budget:
            content_parts.append(_BUDGET_EXHAUSTED_TEXT)

        # An empty content string would crash downstream persistence
        # (ai_prompts.response is NOT NULL in some envs). Fall back to a
        # neutral message — should be rare; means Claude returned a
        # tool_use-only first turn and then bailed for budget.
        content = "\n\n".join(p for p in content_parts if p).strip()
        if not content:
            content = "(no response)"

        return ChatTurn(
            role="assistant",
            content=content,
            tool_calls_made=tool_calls_made,
            input_tokens=cumulative_input,
            output_tokens=cumulative_output,
        )

    # ----- internals ------------------------------------------------------

    def _create_with_fallback(self, *, system: str, messages: list[dict]) -> Any:
        """Mirror of ``ClaudeService._create_with_model_fallback``: try the
        configured model, fall back to known snapshot/legacy aliases if
        the API returns a model-not-found error. Same metering kwarg on
        every attempt (we want a UsageEvent for each *successful* call
        and a claude_call_log row for each attempt).
        """
        last_model_error: Exception | None = None
        for candidate_model in candidate_models_for(self._model):
            try:
                response = self._client.messages.create(
                    model=candidate_model,
                    system=system,
                    messages=messages,
                    tools=self._tools,
                    max_tokens=4096,
                    metering={
                        "organization_id": self._organization_id,
                        "feature": "assessment",
                        "sub_feature": "candidate_chat",
                    },
                )
                if candidate_model != self._model:
                    logger.warning(
                        "AgenticChatService fell back to model=%s (primary=%s unavailable)",
                        candidate_model,
                        self._model,
                    )
                return response
            except Exception as exc:
                if is_model_not_found_error(exc):
                    last_model_error = exc
                    logger.warning(
                        "AgenticChatService model unavailable (model=%s): %s",
                        candidate_model,
                        exc,
                    )
                    continue
                raise
        if last_model_error is not None:
            raise last_model_error
        raise RuntimeError("AgenticChatService: no candidate Claude model succeeded")

    @staticmethod
    def _extract_text(content_blocks: list[Any]) -> str:
        """Join all text-type blocks in a response into one string. Anthropic
        emits multiple blocks per response (text + tool_use interleaved);
        we want every text fragment concatenated in order."""
        parts: list[str] = []
        for block in content_blocks:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "") or ""
                if text:
                    parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _serialize_content_blocks(content_blocks: list[Any]) -> list[dict]:
        """Convert SDK response blocks back to the dict shape that
        ``messages.create`` accepts on the next turn. Anthropic requires
        the assistant turn to be replayed verbatim — including the
        ``tool_use`` blocks — so the API can match ``tool_result.tool_use_id``."""
        out: list[dict] = []
        for block in content_blocks:
            btype = getattr(block, "type", None)
            if btype == "text":
                out.append({"type": "text", "text": getattr(block, "text", "") or ""})
            elif btype == "tool_use":
                out.append(
                    {
                        "type": "tool_use",
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}) or {},
                    }
                )
            else:
                # Unknown block types (e.g. future tool variants) — pass a
                # best-effort dict so the loop doesn't crash. Anthropic
                # will reject if the shape is wrong; that's a louder
                # failure than silently dropping the block.
                fallback = {"type": btype or "text"}
                text = getattr(block, "text", None)
                if text is not None:
                    fallback["text"] = text
                out.append(fallback)
        return out

    @staticmethod
    def _format_tool_result_block(*, tool_use_id: str, dispatch_result: dict) -> dict:
        """Render an executor result as a ``tool_result`` content block.

        Anthropic's expected shape:
        ``{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}``

        On ok=True we serialize ``dispatch_result["result"]`` (could be a
        string or structured data — Claude tolerates both). On ok=False
        we set ``is_error=True`` and surface the error string so Claude
        can self-correct.
        """
        if dispatch_result.get("ok", False):
            result_payload = dispatch_result.get("result", "")
            content = (
                result_payload
                if isinstance(result_payload, (str, list))
                else str(result_payload)
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
            }
        error_msg = str(dispatch_result.get("error", "tool error"))
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": error_msg,
            "is_error": True,
        }
