"""Claude budget accounting helpers for assessment runtime."""

from __future__ import annotations

from typing import Any, Dict, List

from ...platform.config import settings

TOKENS_PER_MILLION = 1_000_000.0
EPSILON = 1e-9


def _input_cost_per_token_usd() -> float:
    return float(settings.CLAUDE_INPUT_COST_PER_MILLION_USD) / TOKENS_PER_MILLION


def _output_cost_per_token_usd() -> float:
    return float(settings.CLAUDE_OUTPUT_COST_PER_MILLION_USD) / TOKENS_PER_MILLION


def _cache_read_cost_per_token_usd() -> float:
    return float(settings.CLAUDE_CACHE_READ_COST_PER_MILLION_USD) / TOKENS_PER_MILLION


def _cache_creation_cost_per_token_usd() -> float:
    return float(settings.CLAUDE_CACHE_CREATION_COST_PER_MILLION_USD) / TOKENS_PER_MILLION


def compute_claude_cost_usd(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Compute estimated Claude USD cost from token counts.

    Now includes prompt-cache tokens. Without them the candidate budget
    UI undercounted by ~2x on agentic chat (the SDK loop drives heavy
    cache hits). Pre-#416 callers passing only ``input``/``output``
    still work — extras default to zero.
    """
    safe_input = max(0, int(input_tokens or 0))
    safe_output = max(0, int(output_tokens or 0))
    safe_cache_read = max(0, int(cache_read_tokens or 0))
    safe_cache_creation = max(0, int(cache_creation_tokens or 0))
    return (
        (safe_input * _input_cost_per_token_usd())
        + (safe_output * _output_cost_per_token_usd())
        + (safe_cache_read * _cache_read_cost_per_token_usd())
        + (safe_cache_creation * _cache_creation_cost_per_token_usd())
    )


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
    for prompt in prompts or []:
        if not isinstance(prompt, dict):
            continue
        input_tokens += max(0, int(prompt.get("input_tokens") or 0))
        output_tokens += max(0, int(prompt.get("output_tokens") or 0))
        cache_read_tokens += max(0, int(prompt.get("cache_read_input_tokens") or 0))
        cache_creation_tokens += max(0, int(prompt.get("cache_creation_input_tokens") or 0))
    tokens_used = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
    cost_usd = compute_claude_cost_usd(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
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
    input_per_token = _input_cost_per_token_usd()
    output_per_token = _output_cost_per_token_usd()
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
