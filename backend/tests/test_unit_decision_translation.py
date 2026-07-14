"""Focused assessment-stage translation tests."""

from types import SimpleNamespace

from app.agent_runtime.decision_translation import role_has_assessment_stage


def _role(*tasks, auto_skip_assessment=False):
    return SimpleNamespace(
        tasks=list(tasks), auto_skip_assessment=auto_skip_assessment
    )


def test_inactive_draft_does_not_create_assessment_stage():
    assert role_has_assessment_stage(_role(SimpleNamespace(is_active=False))) is False


def test_at_least_one_active_task_creates_assessment_stage():
    assert role_has_assessment_stage(
        _role(
            SimpleNamespace(is_active=False),
            SimpleNamespace(is_active=True),
        )
    ) is True


def test_skip_toggle_wins_even_with_active_task():
    assert role_has_assessment_stage(
        _role(SimpleNamespace(is_active=True), auto_skip_assessment=True)
    ) is False
