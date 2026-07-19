"""``AgentSDKChatService`` — claude-agent-sdk wrapper for candidate chat.

This is leaf B of the claude-agent-sdk migration. Replaces the
hand-rolled ``messages.create`` tool-use loop
(``..claude.agentic_chat.AgenticChatService``) with Anthropic's official
``claude-agent-sdk``. The SDK spawns the bundled Claude Code CLI as a
Node subprocess that owns the inner tool-use loop, session resumption,
and the MCP tool transport.

What this module owns
---------------------

1. **Budget gates**: pre-spend bail-out when ``budget_remaining_usd``
   is below a safety floor; ``max_budget_usd`` passed through to the
   SDK as a stop threshold, with one model-specific internal-call
   overshoot included in the durable credit hold.
2. **Conversation-history seeding**: SDK's ``query()`` accepts a single
   ``prompt`` string per call (no message-list shape). We inject prior
   messages by prepending a ``<PRIOR_CONVERSATION>`` block into the
   system prompt and send only the latest user message as ``prompt=``.
   Documented trade-off: this kills cache-key alignment across turns;
   revisit in v2 with the stateful ``ClaudeSDKClient`` if cost shows it.
3. **Aggregated metering**: one synthetic ``UsageEvent`` per
   ``query()`` invocation via
   ``usage_reconciler.write_aggregated_usage_event``. The compromise the
   user signed off on — see that module's docstring.

What this module does NOT own
-----------------------------

- The MCP tool server (leaf A's
  ``..claude_agent.sandbox_tools.build_sandbox_mcp_server``). Imported
  lazily inside ``run()`` and wrapped in ``try/except ImportError`` so
  this module loads cleanly in branches that don't carry leaf A yet.
- The ``Anthropic`` SDK client (the gateway-side ``MeteredAnthropicClient``
  wrapper). The agent SDK calls Anthropic from inside the Node
  subprocess; there is no Python-side client to wrap. The CI
  architecture gate ``test_no_bare_anthropic_client_construction``
  doesn't fire here because we never construct ``Anthropic(...)``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from ....platform.config import settings
from ....services.provider_usage_admission import (
    mark_provider_attempt_started,
    mark_provider_usage_succeeded,
    release_provider_usage,
)
from ....services.provider_error_evidence import safe_provider_error_code
from ....services.usage_credit_reservations import (
    CreditReservation,
    InsufficientRoleBudgetError,
)
from ....services.usage_metering_service import InsufficientCreditsError
from .sdk_budget_admission import reserve_sdk_query_credits
from .sdk_request_identity import SDK_ALLOWED_TOOLS, sdk_provider_request_sha256
from .types import ChatTurn
from .usage_reconciler import (
    write_aggregated_usage_event,
    write_incomplete_call_evidence,
)

logger = logging.getLogger(__name__)


# Pre-spend gate: refuse to call the SDK when the budget is already
# below this floor. Picked to be comfortably above one minimum-size
# Haiku turn (~$0.005). Sonnet turns cost more; the SDK's own
# ``max_budget_usd`` stops the loop after the threshold is exceeded.
_PRE_SPEND_FLOOR_USD = 0.05

_BUDGET_EXHAUSTED_TEXT = (
    "You've used your Claude budget for this assessment. "
    "Submit when you're ready."
)

_EMPTY_REPLY_FALLBACK = (
    "I couldn't produce a response for that turn. Try rephrasing or "
    "submit when you're ready."
)

# Default model — Haiku 4.5. Swapped from Sonnet (#412) after #75:
# Sonnet took ~30s per response, Haiku lands in ~3-5s. Acceptable
# trade — the surface measures candidate steering, not model alone.
# ``CLAUDE_CHAT_MODEL`` env overrides.
_DEFAULT_AGENT_SDK_MODEL = "claude-haiku-4-5-20251001"

# Cap on prior turns replayed via system-prompt history (SDK ``query()``
# is stateless, so we resend). 20 msgs ≈ 10 exchanges — enough context
# without ballooning cost.
_HISTORY_MAX_MESSAGES = 20

# Cap on a single captured tool RESULT on the ai_prompts record. Bash
# stdout / file reads can be large; the grader + replay only need a
# bounded excerpt, and the full artifact survives in git_evidence /
# final_repo_state. Keeps the ai_prompts JSON column from ballooning.
_MAX_TOOL_RESULT_CHARS = 2000


def _stringify_tool_result(content: "str | list[dict] | None") -> str:
    """Flatten a ``ToolResultBlock.content`` to a bounded plain string.

    The SDK delivers a tool result either as a plain string or as a list
    of content dicts (e.g. ``[{"type": "text", "text": "..."}]``). The
    grader and the candidate-process replay only need a bounded textual
    excerpt of what the agent observed.
    """
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        text = "\n".join(p for p in parts if p)
    else:
        text = str(content)
    text = text.strip()
    if len(text) > _MAX_TOOL_RESULT_CHARS:
        text = text[:_MAX_TOOL_RESULT_CHARS] + "\n... (truncated)"
    return text


class AgentSDKChatService:
    """Wraps ``claude_agent_sdk.query()`` for the assessment chat path.

    Constructor binds the org + assessment context (for UsageEvent
    attribution) and the leaf-A executor (which the MCP server factory
    closes over). ``run()`` is async — call from a route handler that
    has an event loop.
    """

    def __init__(
        self,
        *,
        api_key: str,
        organization_id: int,
        assessment_id: int,
        executor: Any,
        role_id: Optional[int] = None,
        trace_id: Optional[str] = None,
        model: Optional[str] = None,
        feature: str = "assessment",
        sub_feature: str = "agent_sdk_chat",
        max_turns: Optional[int] = None,
        _mcp_server_factory: Optional[Callable[[Any], Any]] = None,
    ):
        """
        Args:
            api_key: Anthropic API key (per-workspace or shared). Threaded
                into the SDK options ``env`` so the spawned Node CLI
                authenticates against the right org.
            organization_id: Bills this org for every chat turn. Required —
                missing org context = invisible spend (the 2026-05-20
                reconciliation gap).
            assessment_id: For the ``UsageEvent.entity_id`` FK / metadata
                tag. Lets the settings → usage tab attribute chat spend
                back to a specific candidate session.
            executor: Leaf A's ``AssessmentToolExecutor`` instance.
                Passed verbatim to the MCP server factory; the SDK
                doesn't see it.
            role_id: Optional hiring-role attribution for the canonical usage
                event and role budget.
            trace_id: Stable logical-call identifier shared by the usage event
                and reconciliation evidence. Candidate routes derive this from
                ``assessment_id`` plus the client request id.
            model: Optional override. ``None`` → falls back to the
                explicitly-set ``CLAUDE_CHAT_MODEL`` env var if present,
                else ``claude-sonnet-4-5`` (see module docstring).
            feature: Pricing feature bucket (default ``"assessment"``).
            sub_feature: Stamped into ``UsageEvent.event_metadata`` for
                drill-down. Default ``"agent_sdk_chat"`` distinguishes
                this row from the hand-rolled ``agentic_chat`` rows once
                the route swap lands.
            max_turns: SDK ``max_turns`` cap. ``None`` →
                ``settings.CLAUDE_TOOL_MAX_TURNS``.
            _mcp_server_factory: Test seam — inject a factory that
                builds a fake MCP server from an executor. Production
                resolves to leaf A's ``build_sandbox_mcp_server`` lazily
                inside ``run()``.
        """
        if not api_key:
            raise ValueError("api_key is required")

        self._api_key = api_key
        self._organization_id = int(organization_id)
        self._assessment_id = int(assessment_id)
        self._role_id = int(role_id) if role_id is not None else None
        self._trace_id = (
            str(trace_id).strip()
            if trace_id is not None and str(trace_id).strip()
            else f"assessment:{self._assessment_id}:agent_sdk"
        )
        self._executor = executor
        self._model = (model or "").strip() or self._resolve_default_model()
        self._feature = feature
        self._sub_feature = sub_feature
        self._max_turns = int(
            max_turns if max_turns is not None else settings.CLAUDE_TOOL_MAX_TURNS
        )
        self._mcp_server_factory = _mcp_server_factory

        logger.info(
            "AgentSDKChatService init org=%s assessment=%s model=%s max_turns=%d",
            self._organization_id,
            self._assessment_id,
            self._model,
            self._max_turns,
        )

    @staticmethod
    def _resolve_default_model() -> str:
        """Pick the model when the caller didn't supply one.

        Priority: ``$CLAUDE_CHAT_MODEL`` env var → ``_DEFAULT_AGENT_SDK_MODEL``.
        Documented in module docstring; tested in ``test_default_model_resolution``.
        """
        env_value = os.environ.get("CLAUDE_CHAT_MODEL", "").strip()
        if env_value:
            return env_value
        return _DEFAULT_AGENT_SDK_MODEL

    def _resolve_mcp_factory(self) -> Callable[[Any], Any]:
        """Resolve the MCP-server factory.

        Production path: lazy import of leaf A's
        ``build_sandbox_mcp_server``. Tests can bypass this by passing
        ``_mcp_server_factory=...`` to the constructor so the import
        never fires.

        Wrapped so this module loads cleanly in branches that don't
        carry leaf A — the import error is deferred to the point of
        actual use, where it's actionable.
        """
        if self._mcp_server_factory is not None:
            return self._mcp_server_factory
        try:
            from .sandbox_tools import build_sandbox_mcp_server  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — exercised once leaf A lands
            raise RuntimeError(
                "AgentSDKChatService requires the leaf-A "
                "``sandbox_tools.build_sandbox_mcp_server`` to be available. "
                "Either merge leaf A first or pass _mcp_server_factory=... "
                "for tests."
            ) from exc
        return build_sandbox_mcp_server

    def _reserve_paid_sdk_call(
        self,
        *,
        capped_budget_usd: float,
        request_sha256: str,
    ) -> CreditReservation:
        """Hold the SDK query's conservative charged-cost upper bound.

        The provider-owned subprocess checks ``max_budget_usd`` only after an
        internal request, so the query can exceed the threshold by at most one
        request. The bound is the smaller of ``max_turns * one_call_bound`` and
        ``threshold + one_call_bound``. Role attribution adds the role rail;
        a nullable role still receives the mandatory organization hold.
        """
        return reserve_sdk_query_credits(
            organization_id=self._organization_id,
            assessment_id=self._assessment_id,
            role_id=self._role_id,
            feature=self._feature,
            sub_feature=self._sub_feature,
            trace_id=self._trace_id,
            model=self._model,
            max_turns=self._max_turns,
            stop_threshold_usd=capped_budget_usd,
            request_sha256=request_sha256,
        )

    async def run(
        self,
        *,
        messages: list[dict],
        system: str,
        budget_remaining_usd: float,
        max_budget_usd: float = 1.0,
    ) -> ChatTurn:
        """Drive one chat turn end-to-end.

        Steps: (1) pre-spend gate; (2) history seeding; (3) build
        locked-down options (``tools=[]``, ``setting_sources=[]``,
        ``permission_mode="bypassPermissions"``, ``max_budget_usd=min(
        remaining, ceiling)``); (4) drive ``query()`` collecting text
        + tool-use blocks; (5) write the aggregated UsageEvent — skip
        ONLY when no ``ResultMessage`` arrived; (6) return ``ChatTurn``.

        Args:
            messages: Prior conversation
                (``[{"role": ..., "content": ...}]``); last entry must
                be the new user message — what we send as ``prompt=``.
            system: Caller's system prompt; history block is prepended.
            budget_remaining_usd: Live budget remainder. Pre-spend gate
                checks this; SDK gets ``min(this, max_budget_usd)``.
            max_budget_usd: Per-turn SDK stop threshold (default 1.0). The
                durable hold also covers the model-specific final-call
                overshoot permitted by the SDK contract.

        Returns ``ChatTurn`` (``success=False`` on gate/SDK error/no
        ``ResultMessage``).
        """
        # 1. Pre-spend gate ----------------------------------------------------
        if float(budget_remaining_usd or 0.0) < _PRE_SPEND_FLOOR_USD:
            logger.info(
                "AgentSDKChatService pre-spend gate tripped org=%s assessment=%s "
                "(remaining=$%.4f floor=$%.4f) — skipping SDK call",
                self._organization_id,
                self._assessment_id,
                budget_remaining_usd,
                _PRE_SPEND_FLOOR_USD,
            )
            return ChatTurn(
                success=False,
                content=_BUDGET_EXHAUSTED_TEXT,
                tool_calls_made=[],
                input_tokens=0,
                output_tokens=0,
                total_cost_usd=0.0,
                num_turns=0,
                stop_reason="budget_exhausted",
            )

        # 2. History seeding ---------------------------------------------------
        latest_user_message, history_block = self._split_history(messages)
        full_system = self._compose_system_prompt(system=system, history_block=history_block)

        # 3. Build options -----------------------------------------------------
        # Imports kept local so the module loads in environments that
        # don't have the SDK installed (e.g. lint-only CI runs).
        from claude_agent_sdk import (  # noqa: WPS433
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
            query,
        )

        mcp_factory = self._resolve_mcp_factory()
        mcp_server = mcp_factory(self._executor)
        capped_budget = min(float(budget_remaining_usd), float(max_budget_usd))
        request_hash = sdk_provider_request_sha256(
            prompt=latest_user_message,
            model=self._model,
            system_prompt=full_system,
            max_turns=self._max_turns,
            max_budget_usd=capped_budget,
        )

        def _on_stderr(line: str) -> None:
            try:
                trimmed = (line or "").rstrip()
                if not trimmed:
                    return
                logger.warning(
                    "claude_agent_sdk CLI stderr org=%s assessment=%s chars=%d",
                    self._organization_id, self._assessment_id, len(trimmed),
                )
            except Exception:  # pragma: no cover
                pass
        # Skip SDK→CLI version check (transient network → 500 otherwise).
        os.environ.setdefault("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")

        options = ClaudeAgentOptions(
            model=self._model,
            system_prompt=full_system,
            mcp_servers={"sandbox": mcp_server},
            allowed_tools=SDK_ALLOWED_TOOLS,
            # Empty list disables the SDK's built-in tool preset; the
            # sandbox MCP server is the *only* tool surface the model sees.
            tools=[],
            # Block ``~/.claude/settings.json`` (and project / local
            # equivalents) from leaking into the spawned CLI process.
            # Without this, an operator's local CLAUDE.md or hooks could
            # silently alter candidate-facing behaviour.
            setting_sources=[],
            permission_mode="bypassPermissions",
            max_turns=self._max_turns,
            max_budget_usd=capped_budget,
            # IS_SANDBOX=1: Railway pods run as uid=0; the bundled CLI
            # refuses --dangerously-skip-permissions under root unless told
            # the surrounding env is sandboxed (assessment 72, 2026-05-26).
            # Our MCP tools touch only the candidate's separate E2B VM,
            # so the pod-level bypass is safe.
            env={"ANTHROPIC_API_KEY": self._api_key, "IS_SANDBOX": "1"},
            stderr=_on_stderr,
        )

        try:
            credit_reservation = self._reserve_paid_sdk_call(
                capped_budget_usd=capped_budget,
                request_sha256=request_hash,
            )
            if credit_reservation is not None and not mark_provider_attempt_started(
                credit_reservation,
                provider="claude_agent_sdk",
            ):
                release_provider_usage(
                    credit_reservation,
                    reason="claude_agent_sdk_attempt_marker_failed",
                )
                raise RuntimeError(
                    "could not durably mark Claude Agent SDK provider attempt"
                )
        except InsufficientCreditsError as exc:
            logger.info(
                "AgentSDKChatService budget admission blocked org=%s role=%s "
                "assessment=%s err=%s",
                self._organization_id,
                self._role_id,
                self._assessment_id,
                exc,
            )
            return ChatTurn(
                success=False,
                content=_BUDGET_EXHAUSTED_TEXT,
                tool_calls_made=[],
                input_tokens=0,
                output_tokens=0,
                total_cost_usd=0.0,
                num_turns=0,
                stop_reason="budget_exhausted",
            )
        except InsufficientRoleBudgetError as exc:
            logger.info(
                "AgentSDKChatService role budget admission blocked org=%s role=%s "
                "assessment=%s err=%s",
                self._organization_id,
                self._role_id,
                self._assessment_id,
                exc,
            )
            return ChatTurn(
                success=False,
                content=_BUDGET_EXHAUSTED_TEXT,
                tool_calls_made=[],
                input_tokens=0,
                output_tokens=0,
                total_cost_usd=0.0,
                num_turns=0,
                stop_reason="role_budget_exhausted",
            )
        except Exception:  # fail closed on an indeterminate meter rail
            logger.exception(
                "AgentSDKChatService metering admission failed org=%s role=%s "
                "assessment=%s",
                self._organization_id,
                self._role_id,
                self._assessment_id,
            )
            return ChatTurn(
                success=False,
                content=_BUDGET_EXHAUSTED_TEXT,
                tool_calls_made=[],
                input_tokens=0,
                output_tokens=0,
                total_cost_usd=0.0,
                num_turns=0,
                stop_reason="metering_admission_failed",
            )

        # 4. Drive the stream -------------------------------------------------
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        # Correlate tool RESULTS back onto their originating call by
        # tool_use_id so scoring can see what the agent actually observed
        # (process-visible grading), not just what it asked for. The SDK
        # emits each result as a follow-up UserMessage AFTER the
        # AssistantMessage that carried the tool-use block — so we mutate
        # the already-appended call dict in place, and every return path
        # below carries the merged result for free.
        calls_by_id: dict[str, dict] = {}
        final: Optional[ResultMessage] = None

        try:
            async for msg in query(prompt=latest_user_message, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            content_parts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            call = {"name": block.name, "input": block.input}
                            tool_calls.append(call)
                            if block.id:
                                calls_by_id[block.id] = call
                elif isinstance(msg, UserMessage):
                    blocks = msg.content if isinstance(msg.content, list) else []
                    for block in blocks:
                        if isinstance(block, ToolResultBlock):
                            call = calls_by_id.get(block.tool_use_id)
                            if call is not None:
                                call["result"] = _stringify_tool_result(block.content)
                                call["is_error"] = bool(block.is_error)
                elif isinstance(msg, ResultMessage):
                    final = msg
        except Exception as exc:
            # Classification + recovery rules in error_recovery.classify (#76).
            from .error_recovery import classify
            recovered = classify(str(exc), content_parts, tool_calls=tool_calls)
            error_code = safe_provider_error_code(exc, operation="claude_agent_sdk_stream")
            log = logger.info if recovered.success else logger.error
            log("AgentSDKChatService exception org=%s assessment=%s stop=%s error_code=%s", self._organization_id, self._assessment_id, recovered.stop_reason, error_code)
            # No ResultMessage means no trustworthy token totals.  Persist an
            # explicit reconciliation gap without inventing a zero-cost usage
            # event or debiting the customer.
            mark_provider_usage_succeeded(
                credit_reservation,
                deferred_usage_event=None,
                provider="claude_agent_sdk",
            )
            write_incomplete_call_evidence(
                organization_id=self._organization_id,
                assessment_id=self._assessment_id,
                feature=self._feature,
                sub_feature=self._sub_feature,
                model=self._model,
                status=(
                    "sdk_incomplete_recovered"
                    if recovered.success
                    else "sdk_error_no_usage"
                ),
                error_reason=f"sdk_stream_incomplete:{type(exc).__name__}",
                trace_id=self._trace_id,
                error_class="other",
            )
            return ChatTurn(
                success=recovered.success,
                content=recovered.content,
                tool_calls_made=tool_calls,
                input_tokens=0,
                output_tokens=0,
                total_cost_usd=0.0,
                num_turns=len(tool_calls) if recovered.success else 0,
                stop_reason=recovered.stop_reason,
            )

        # 5. Build ChatTurn ----------------------------------------------------
        if final is None:
            # SDK never emitted a ResultMessage — pathological state.
            # Don't write a UsageEvent; defensive return so the caller
            # has something to persist.
            logger.warning(
                "AgentSDKChatService query() closed without a ResultMessage "
                "org=%s assessment=%s tool_calls=%d text_parts=%d",
                self._organization_id,
                self._assessment_id,
                len(tool_calls),
                len(content_parts),
            )
            mark_provider_usage_succeeded(
                credit_reservation,
                deferred_usage_event=None,
                provider="claude_agent_sdk",
            )
            write_incomplete_call_evidence(
                organization_id=self._organization_id,
                assessment_id=self._assessment_id,
                feature=self._feature,
                sub_feature=self._sub_feature,
                model=self._model,
                status="no_result_message",
                error_reason=(
                    "query stream closed without ResultMessage; usage totals unavailable"
                ),
                trace_id=self._trace_id,
                error_class="validation",
            )
            return ChatTurn(
                success=False,
                content="\n".join(content_parts) or _EMPTY_REPLY_FALLBACK,
                tool_calls_made=tool_calls,
                input_tokens=0,
                output_tokens=0,
                total_cost_usd=0.0,
                num_turns=0,
                stop_reason="no_result_message",
            )

        usage = final.usage or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        total_cost = float(final.total_cost_usd or 0.0)
        num_turns = int(final.num_turns or 0)
        stop_reason = (
            getattr(final, "stop_reason", None)
            or getattr(final, "subtype", None)
        )

        success = not bool(getattr(final, "is_error", False))
        content = "\n".join(p for p in content_parts if p).strip() or _EMPTY_REPLY_FALLBACK

        # 6. Write the aggregated UsageEvent ----------------------------------
        # Write on BOTH success and SDK-error paths: an error mid-flight
        # still cost money (the CLI fired Anthropic calls before the
        # error surfaced). The only skip path is "no ResultMessage" above.
        try:
            write_aggregated_usage_event(
                db=None,  # ignored — writer opens its own SessionLocal
                organization_id=self._organization_id,
                assessment_id=self._assessment_id,
                feature=self._feature,
                sub_feature=self._sub_feature,
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_creation,
                total_cost_usd=total_cost,
                num_turns=num_turns,
                role_id=self._role_id,
                trace_id=self._trace_id,
                call_status=("sdk_result_error" if not success else "ok"),
                extra_metadata={
                    "stop_reason": stop_reason,
                    "is_error": bool(getattr(final, "is_error", False)),
                    "tool_calls": len(tool_calls),
                },
                credit_reservation=(
                    credit_reservation.as_metering_payload()
                    if credit_reservation is not None
                    else None
                ),
            )
        except Exception:
            # ``write_aggregated_usage_event`` already swallows + logs,
            # but belt-and-braces this so a metering failure never breaks
            # the chat turn.
            logger.exception("AgentSDKChatService meter write failed")

        return ChatTurn(
            success=success,
            content=content,
            tool_calls_made=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
            total_cost_usd=total_cost,
            num_turns=num_turns,
            stop_reason=stop_reason,
            model=str(self._model or ""),
        )

    # ----- helpers --------------------------------------------------------

    @staticmethod
    def _split_history(messages: list[dict]) -> tuple[str, str]:
        """Split ``messages`` into ``(latest_user_message, history_block)``.

        ``latest_user_message`` is the LAST user-role message (what we
        send as ``prompt=``). ``history_block`` is the
        ``<PRIOR_CONVERSATION>`` text for the system prompt, or empty
        when there's no prior turn.

        Defensive: if ``messages`` is empty or the last entry isn't a
        user message, returns ``("", "")`` so the SDK gets a no-op
        prompt and the caller's earlier validation kicks in. We never
        crash on bad shapes — the route layer is the right place to
        reject empty inputs.
        """
        if not messages:
            return ("", "")

        # The latest user message is the prompt. We scan from the end
        # so a (rare) trailing assistant message doesn't break us.
        latest_user_idx: Optional[int] = None
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                latest_user_idx = idx
                break

        if latest_user_idx is None:
            return ("", "")

        latest_user_message = str(messages[latest_user_idx].get("content") or "").strip()

        prior = messages[:latest_user_idx]
        # Cap at the most recent N messages.
        if len(prior) > _HISTORY_MAX_MESSAGES:
            prior = prior[-_HISTORY_MAX_MESSAGES:]

        if not prior:
            return (latest_user_message, "")

        lines = ["<PRIOR_CONVERSATION>"]
        for m in prior:
            role = str(m.get("role") or "user")
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        lines.append("</PRIOR_CONVERSATION>")
        history_block = "\n".join(lines)
        return (latest_user_message, history_block)

    @staticmethod
    def _compose_system_prompt(*, system: str, history_block: str) -> str:
        """Prepend the history block above the caller's system prompt.

        Trade-off documented in the module docstring: this means the
        prior turns are NOT prompt-cache aligned with previous chat
        calls (the system prompt changes every turn as new messages
        accrue). v2 plan: switch to the stateful ``ClaudeSDKClient``
        and use ``resume=session_id`` so the SDK handles continuity
        natively.
        """
        sys = (system or "").strip()
        if not history_block:
            return sys
        if not sys:
            return history_block
        return f"{history_block}\n\n{sys}"
