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
