import app.components.assessments.claude_budget as claude_budget


def test_resolve_effective_budget_limit_caps_demo_and_candidate_defaults(monkeypatch):
    monkeypatch.setattr(claude_budget.settings, "DEMO_CLAUDE_BUDGET_LIMIT_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "ASSESSMENT_CLAUDE_BUDGET_DEFAULT_USD", 5.0)

    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=True, task_budget_limit_usd=None) == 1.0
    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=True, task_budget_limit_usd=2.5) == 1.0
    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=True, task_budget_limit_usd=0.4) == 0.4

    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=False, task_budget_limit_usd=None) == 5.0
    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=False, task_budget_limit_usd=7.0) == 5.0
    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=False, task_budget_limit_usd=2.0) == 2.0


def test_resolve_effective_budget_limit_allows_task_limit_when_default_disabled(monkeypatch):
    monkeypatch.setattr(claude_budget.settings, "ASSESSMENT_CLAUDE_BUDGET_DEFAULT_USD", None)

    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=False, task_budget_limit_usd=None) is None
    assert claude_budget.resolve_effective_budget_limit_usd(is_demo=False, task_budget_limit_usd=3.0) == 3.0


def test_compute_claude_cost_usd_includes_cache_tokens():
    """Anthropic prompt-cache tokens MUST be priced into the candidate
    budget. Pre-#416 the candidate UI under-counted by ~2x because the
    SDK loop streams 50k+ cache_read tokens per turn at $0.10/M — a
    real cost that wasn't reflected on the $5.00-of-$5.00 badge.

    Regression for assessment 77 (2026-05-26): real spend was $0.149
    across 8 turns, badge said $0.075. Two cents off per cent.
    """
    # 1M input @ $1, 1M output @ $5, 1M cache-read @ $0.10, 1M cache-write @ $1.25
    # → $7.35
    cost = claude_budget.compute_claude_cost_usd(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )
    assert abs(cost - 7.35) < 1e-6

    # Backwards-compatible: old callers with no cache args still work.
    cost_no_cache = claude_budget.compute_claude_cost_usd(input_tokens=1000, output_tokens=500)
    assert abs(cost_no_cache - (1000 / 1_000_000.0 + 500 * 5 / 1_000_000.0)) < 1e-9


def test_summarize_prompt_usage_aggregates_cache_token_fields():
    """``ai_prompts`` records written from #416 onward carry
    ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
    The aggregator must sum them and feed the cost calculation."""
    prompts = [
        {
            "input_tokens": 3298,
            "output_tokens": 7667,
            "cache_read_input_tokens": 54496,
            "cache_creation_input_tokens": 11966,
        },
        {
            # An older record from before #416 — should pass through cleanly.
            "input_tokens": 4104,
            "output_tokens": 316,
        },
    ]
    out = claude_budget.summarize_prompt_usage(prompts)

    assert out["input_tokens"] == 3298 + 4104
    assert out["output_tokens"] == 7667 + 316
    assert out["cache_read_tokens"] == 54496
    assert out["cache_creation_tokens"] == 11966
    # Cost routes through the canonical model-aware pricing
    # (compute_claude_cost_usd -> raw_cost_usd_micro), NOT the legacy flat
    # CLAUDE_*_COST_PER_MILLION_USD env vars. Assert summarize feeds the
    # aggregated tokens (INCLUDING cache) into the canonical cost fn, and that
    # the cache tokens materially increase the cost.
    expected = claude_budget.compute_claude_cost_usd(
        input_tokens=7402,
        output_tokens=7983,
        cache_read_tokens=54496,
        cache_creation_tokens=11966,
    )
    assert out["cost_usd"] == expected
    assert out["cost_usd"] > claude_budget.compute_claude_cost_usd(
        input_tokens=7402, output_tokens=7983,
    )
