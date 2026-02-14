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


def compute_claude_cost_usd(input_tokens: int = 0, output_tokens: int = 0) -> float:
    """Compute estimated Claude USD cost from token counts."""
    safe_input = max(0, int(input_tokens or 0))
    safe_output = max(0, int(output_tokens or 0))
    return (safe_input * _input_cost_per_token_usd()) + (safe_output * _output_cost_per_token_usd())


def summarize_prompt_usage(prompts: List[Dict[str, Any]] | None) -> Dict[str, Any]:
    """Aggregate prompt token usage and estimated cost."""
    input_tokens = 0
    output_tokens = 0
    for prompt in prompts or []:
        if not isinstance(prompt, dict):
            continue
        input_tokens += max(0, int(prompt.get("input_tokens") or 0))
        output_tokens += max(0, int(prompt.get("output_tokens") or 0))
    tokens_used = input_tokens + output_tokens
    cost_usd = compute_claude_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tokens_used": tokens_used,
        "cost_usd": cost_usd,
    }


def _token_estimate_for_budget(remaining_usd: float, per_token_cost: float) -> int | None:
    if per_token_cost <= 0:
        return None
    return max(0, int(remaining_usd / per_token_cost))


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
