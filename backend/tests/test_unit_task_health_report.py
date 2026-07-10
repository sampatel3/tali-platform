"""analyse_task — the per-task health stats + difficulty flags."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.task_health_report import analyse_task


def _a(status="completed", started=True, score=None, minutes=20, voided=False, demo=False):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        is_voided=voided,
        is_demo=demo,
        status=status,
        started_at=(now - timedelta(minutes=minutes)) if started else None,
        completed_at=now if status.startswith("completed") else None,
        taali_score=score,
        assessment_score=None,
        final_score=None,
        score=None,
    )


def test_flags_insufficient_n_and_low_completion():
    rows = [_a(score=50)] + [_a(status="expired", started=True, minutes=5)] * 4
    stats = analyse_task(rows)
    assert stats["completed"] == 1
    assert stats["completion_rate"] == 20.0
    assert "insufficient_n" in stats["flags"]
    assert "low_completion" in stats["flags"]


def test_flags_too_easy_and_no_discrimination():
    rows = [_a(score=s) for s in (88, 89, 90, 91, 92)]
    stats = analyse_task(rows)
    assert "too_easy" in stats["flags"]
    assert "no_discrimination" in stats["flags"]
    assert "insufficient_n" not in stats["flags"]


def test_healthy_task_has_no_flags():
    rows = [_a(score=s) for s in (35, 48, 62, 71, 83)]
    stats = analyse_task(rows)
    assert stats["flags"] == []
    assert stats["score_mean"] is not None


def test_voided_and_demo_excluded():
    rows = [_a(score=90, voided=True), _a(score=90, demo=True), _a(score=55)]
    stats = analyse_task(rows)
    assert stats["sent"] == 1
    assert stats["completed"] == 1
