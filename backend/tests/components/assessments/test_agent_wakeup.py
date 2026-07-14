"""Event-driven role-agent wake-up after assessment scoring."""

from __future__ import annotations

from types import SimpleNamespace

from app.components.assessments import service as assessments_svc
from app.tasks import agent_tasks


def _stub_scoring(monkeypatch, events: list[object], result: dict | None = None) -> dict:
    expected = result or {"success": True, "score": 8.2}

    def fake_submit_impl(*_args, **_kwargs):
        # Returning from the implementation represents its completed DB commit;
        # the wake must be observed only after this marker.
        events.append("score_committed")
        return expected

    monkeypatch.setattr(assessments_svc, "submit_assessment_impl", fake_submit_impl)
    return expected


def test_successful_scoring_wakes_enabled_role_after_commit(monkeypatch):
    events: list[object] = []
    expected = _stub_scoring(monkeypatch, events)
    assessment = SimpleNamespace(
        id=701,
        role_id=91,
        role=SimpleNamespace(agentic_mode_enabled=True),
    )

    monkeypatch.setattr(
        agent_tasks.agent_cohort_tick_role,
        "delay",
        lambda role_id, *, activation: events.append(("agent_wake", role_id, activation)),
    )

    result = assessments_svc.submit_assessment(assessment, "code", 0, object())

    assert result == expected
    assert events == ["score_committed", ("agent_wake", 91, False)]


def test_broker_failure_does_not_break_successful_submission(monkeypatch):
    events: list[object] = []
    expected = _stub_scoring(monkeypatch, events)
    assessment = SimpleNamespace(id=702, role_id=92)

    def broker_down(*_args, **_kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(agent_tasks.agent_cohort_tick_role, "delay", broker_down)

    result = assessments_svc.submit_assessment(assessment, "code", 0, object())

    assert result == expected
    assert events == ["score_committed"]


def test_submission_without_role_does_not_enqueue_wake(monkeypatch):
    events: list[object] = []
    expected = _stub_scoring(monkeypatch, events)
    assessment = SimpleNamespace(id=703, role_id=None)
    wake_calls: list[object] = []

    monkeypatch.setattr(
        agent_tasks.agent_cohort_tick_role,
        "delay",
        lambda *args, **kwargs: wake_calls.append((args, kwargs)),
    )

    result = assessments_svc.submit_assessment(assessment, "code", 0, object())

    assert result == expected
    assert events == ["score_committed"]
    assert wake_calls == []


def test_incomplete_rubric_does_not_wake_agent(monkeypatch):
    events: list[object] = []
    expected = _stub_scoring(
        monkeypatch,
        events,
        {"success": True, "score": None, "grading_status": "pending"},
    )
    assessment = SimpleNamespace(id=704, role_id=94)
    monkeypatch.setattr(
        agent_tasks.agent_cohort_tick_role,
        "delay",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    result = assessments_svc.submit_assessment(assessment, "code", 0, object())

    assert result == expected
    assert events == ["score_committed"]
