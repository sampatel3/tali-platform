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
