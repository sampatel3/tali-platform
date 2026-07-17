"""Claude budget accounting helpers for assessment runtime."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...models.assessment import Assessment
from ...platform.config import settings
from ...services.pricing_service import raw_cost_usd_micro

TOKENS_PER_MILLION = 1_000_000.0
EPSILON = 1e-9

# When a callsite knows the model the chat ran on (it always does — the
# SDK records it on every chat_turn), this is the canonical default to
# fall back to for ai_prompts records that pre-date the model tag. Set
# to Haiku 4.5 because that's what the chat path defaults to today
# (``AgentSDKChatService._DEFAULT_AGENT_SDK_MODEL``). Override per call.
_DEFAULT_CHAT_MODEL = "claude-haiku-4-5"


def terminal_usage_totals(assessment: Assessment) -> tuple[int, int]:
    """Aggregate provider usage emitted by the Claude CLI transcript."""

    input_tokens = 0
    output_tokens = 0
    for entry in list(getattr(assessment, "cli_transcript", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_usage":
            continue
        input_tokens += max(0, int(entry.get("input_tokens") or 0))
        output_tokens += max(0, int(entry.get("output_tokens") or 0))
    return input_tokens, output_tokens


def compute_claude_cost_usd(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    *,
    model: Optional[str] = None,
) -> float:
    """Compute estimated Claude USD cost from token counts.

    Routes through ``pricing_service.raw_cost_usd_micro`` (the canonical
    pricing source) so model-aware rates apply: Haiku at $1/$5 per MTok,
    Sonnet at $3/$15, Opus at $15/$75. Was previously env-var driven
    (``CLAUDE_INPUT_COST_PER_MILLION_USD`` etc.) which silently
    under-counted Sonnet chat by ~⅓ — same shape as the historical
    pricing bug fixed in ``raw_cost_usd_micro`` itself (2026-05-26).
    Today the chat runs on Haiku and the env-var defaults happen to
    match Haiku rates so the numbers were right by coincidence; this
    locks the math to the model so the next model swap doesn't drift.

    ``model`` defaults to ``_DEFAULT_CHAT_MODEL`` (Haiku 4.5) so legacy
    callers stay correct. Pass the actual model when known.
    """
    safe_input = max(0, int(input_tokens or 0))
    safe_output = max(0, int(output_tokens or 0))
    safe_cache_read = max(0, int(cache_read_tokens or 0))
    safe_cache_creation = max(0, int(cache_creation_tokens or 0))
    micro = raw_cost_usd_micro(
        input_tokens=safe_input,
        output_tokens=safe_output,
        cache_read_tokens=safe_cache_read,
        cache_creation_tokens=safe_cache_creation,
        cache_creation_1h_tokens=None,
        model=(model or _DEFAULT_CHAT_MODEL),
    )
    return float(micro) / TOKENS_PER_MILLION


def summarize_prompt_usage(prompts: List[Dict[str, Any]] | None) -> Dict[str, Any]:
    """Aggregate prompt token usage and estimated cost.

    Reads cache-token fields when present (added in #416); older
    records pass them through as zero — those are pre-cache rows so
    that's an accurate accounting.
    """
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    # When ai_prompts records carry an explicit ``model`` (added going
    # forward — older records don't), use the LATEST non-empty model
    # seen as the per-token rate. Defaults to the chat-path default
    # (Haiku 4.5) when no record carries one — accurate today because
    # AgentSDKChatService runs Haiku.
    record_model: Optional[str] = None
    for prompt in prompts or []:
        if not isinstance(prompt, dict):
            continue
        input_tokens += max(0, int(prompt.get("input_tokens") or 0))
        output_tokens += max(0, int(prompt.get("output_tokens") or 0))
        cache_read_tokens += max(0, int(prompt.get("cache_read_input_tokens") or 0))
        cache_creation_tokens += max(0, int(prompt.get("cache_creation_input_tokens") or 0))
        m = prompt.get("model")
        if isinstance(m, str) and m.strip():
            record_model = m.strip()
    tokens_used = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
    cost_usd = compute_claude_cost_usd(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        model=record_model,
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "tokens_used": tokens_used,
        "cost_usd": cost_usd,
    }


def _token_estimate_for_budget(remaining_usd: float, per_token_cost: float) -> int | None:
    if per_token_cost <= 0:
        return None
    return max(0, int(remaining_usd / per_token_cost))


def resolve_effective_budget_limit_usd(
    *,
    is_demo: bool,
    task_budget_limit_usd: float | None,
) -> float | None:
    """Resolve effective Claude budget cap for an assessment session."""
    task_limit = None if task_budget_limit_usd is None else max(0.0, float(task_budget_limit_usd))

    if is_demo:
        demo_cap = max(0.0, float(settings.DEMO_CLAUDE_BUDGET_LIMIT_USD))
        if task_limit is None:
            return demo_cap
        return min(task_limit, demo_cap)

    assessment_default = settings.ASSESSMENT_CLAUDE_BUDGET_DEFAULT_USD
    if assessment_default is None:
        return task_limit
    assessment_cap = max(0.0, float(assessment_default))
    if task_limit is None:
        return assessment_cap
    return min(task_limit, assessment_cap)


def build_claude_budget_snapshot(
    budget_limit_usd: float | None,
    prompts: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    """Build candidate-safe Claude budget status payload."""
    usage = summarize_prompt_usage(prompts)
    enabled = budget_limit_usd is not None

    if not enabled:
        return {
            "enabled": False,
            "limit_usd": None,
            "used_usd": round(float(usage["cost_usd"]), 6),
            "remaining_usd": None,
            "input_tokens_used": usage["input_tokens"],
            "output_tokens_used": usage["output_tokens"],
            "tokens_used": usage["tokens_used"],
            "remaining_input_tokens_estimate": None,
            "remaining_output_tokens_estimate": None,
            "remaining_total_tokens_estimate": None,
            "is_exhausted": False,
        }

    limit = max(0.0, float(budget_limit_usd))
    used = float(usage["cost_usd"])
    remaining = max(0.0, limit - used)
    # Token-remaining estimates use the chat model's per-token rates so
    # the "tokens you have left" number scales with whichever model the
    # chat path actually runs on. Reads the canonical pricing table via
    # raw_cost_usd_micro(input_tokens=1, ...) which returns micro-USD
    # per token directly.
    chat_model = _DEFAULT_CHAT_MODEL
    input_per_token = float(raw_cost_usd_micro(input_tokens=1, output_tokens=0, model=chat_model)) / TOKENS_PER_MILLION
    output_per_token = float(raw_cost_usd_micro(input_tokens=0, output_tokens=1, model=chat_model)) / TOKENS_PER_MILLION
    blended_per_token = (input_per_token + output_per_token) / 2.0

    return {
        "enabled": True,
        "limit_usd": round(limit, 6),
        "used_usd": round(used, 6),
        "remaining_usd": round(remaining, 6),
        "input_tokens_used": usage["input_tokens"],
        "output_tokens_used": usage["output_tokens"],
        "tokens_used": usage["tokens_used"],
        "remaining_input_tokens_estimate": _token_estimate_for_budget(remaining, input_per_token),
        "remaining_output_tokens_estimate": _token_estimate_for_budget(remaining, output_per_token),
        "remaining_total_tokens_estimate": _token_estimate_for_budget(remaining, blended_per_token),
        "is_exhausted": remaining <= EPSILON,
    }
