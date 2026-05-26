import app.components.assessments.claude_budget as claude_budget
import app.components.assessments.terminal_runtime as terminal_runtime


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


def test_compute_claude_cost_usd_includes_cache_tokens(monkeypatch):
    """Anthropic prompt-cache tokens MUST be priced into the candidate
    budget. Pre-#416 the candidate UI under-counted by ~2x because the
    SDK loop streams 50k+ cache_read tokens per turn at $0.10/M — a
    real cost that wasn't reflected on the $5.00-of-$5.00 badge.

    Regression for assessment 77 (2026-05-26): real spend was $0.149
    across 8 turns, badge said $0.075. Two cents off per cent.
    """
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_INPUT_COST_PER_MILLION_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_OUTPUT_COST_PER_MILLION_USD", 5.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_CACHE_READ_COST_PER_MILLION_USD", 0.10)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_CACHE_CREATION_COST_PER_MILLION_USD", 1.25)

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


def test_summarize_prompt_usage_aggregates_cache_token_fields(monkeypatch):
    """``ai_prompts`` records written from #416 onward carry
    ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
    The aggregator must sum them and feed the cost calculation."""
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_INPUT_COST_PER_MILLION_USD", 1.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_OUTPUT_COST_PER_MILLION_USD", 5.0)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_CACHE_READ_COST_PER_MILLION_USD", 0.10)
    monkeypatch.setattr(claude_budget.settings, "CLAUDE_CACHE_CREATION_COST_PER_MILLION_USD", 1.25)

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
    # 7402 in @ $1/M + 7983 out @ $5/M + 54496 cache-read @ $0.10/M + 11966 cache-write @ $1.25/M
    expected = (
        7402 / 1_000_000.0
        + 7983 * 5 / 1_000_000.0
        + 54496 * 0.10 / 1_000_000.0
        + 11966 * 1.25 / 1_000_000.0
    )
    assert abs(out["cost_usd"] - expected) < 1e-9


def test_terminal_command_scopes_repo_and_disables_bash(monkeypatch):
    monkeypatch.setattr(terminal_runtime.settings, "CLAUDE_CLI_COMMAND", "claude")
    monkeypatch.setattr(terminal_runtime.settings, "CLAUDE_CLI_PERMISSION_MODE_DEFAULT", "acceptEdits")
    monkeypatch.setattr(terminal_runtime.settings, "CLAUDE_CLI_DISALLOWED_TOOLS", "Bash")

    command = terminal_runtime._build_claude_cli_command(repo_root="/workspace/example-task")

    assert "--permission-mode" in command
    assert "acceptEdits" in command
    assert "--add-dir" in command
    assert "/workspace/example-task" in command
    assert "--append-system-prompt" in command
    assert "--disallowedTools" in command
    assert "Bash" in command


def test_terminal_bootstrap_script_exposes_safe_prompt_helpers(monkeypatch):
    monkeypatch.setattr(terminal_runtime.settings, "CLAUDE_CLI_COMMAND", "claude")
    monkeypatch.setattr(terminal_runtime.settings, "CLAUDE_CLI_PERMISSION_MODE_DEFAULT", "acceptEdits")
    monkeypatch.setattr(terminal_runtime.settings, "CLAUDE_CLI_DISALLOWED_TOOLS", "Bash")

    command = terminal_runtime._build_claude_cli_command(repo_root="/workspace/example-task")
    script = terminal_runtime._build_terminal_bootstrap_script(
        repo_root="/workspace/example-task",
        cli_cmd=command,
    )

    assert "taali_claude() {" in script
    assert 'if [ "$#" -gt 1 ]; then' in script
    assert '-p "$*"' in script
    assert '-p "$1"' in script
    assert "taali_ask() {" in script
    assert "/send" in script
    assert "alias claude='taali_claude'" in script
    assert "alias ask='taali_ask'" in script
    assert "use Ask Claude (Cursor-style) in UI" in script


def test_detects_legacy_prompt_wrapper_from_terminal_output():
    assessment = type(
        "AssessmentLike",
        (),
        {
            "cli_transcript": [
                {
                    "event_type": "terminal_output",
                    "data": 'Claude Code CLI ready. Type: claude "<your prompt>"',
                }
            ]
        },
    )()
    assert terminal_runtime._has_legacy_prompt_wrapper(assessment) is True
