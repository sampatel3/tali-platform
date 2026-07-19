"""Mid-window assessment nudges (flag-gated, one per assessment)."""

from datetime import datetime, timedelta, timezone

import pytest

import app.tasks.assessment_tasks as tasks_mod
from app.models.assessment import Assessment, AssessmentStatus
from app.models.candidate import Candidate
from app.models.task import Task
from app.platform.config import settings
from app.tasks.assessment_tasks import send_assessment_nudges


class FakeEmailService:
    def __init__(self):
        self.sent = []

    def send_assessment_nudge(self, **kwargs):
        self.sent.append(kwargs)
        return {"success": True, "email_id": f"nudge-{len(self.sent)}"}


@pytest.fixture
def nudge_env(db, monkeypatch):
    monkeypatch.setattr(settings, "ASSESSMENT_NUDGES_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key", raising=False)
    fake = FakeEmailService()
    import app.domains.integrations_notifications.adapters as adapters_mod
    monkeypatch.setattr(adapters_mod, "build_email_adapter", lambda: fake)
    monkeypatch.setattr(tasks_mod, "SessionLocal", lambda: db, raising=False)
    # send_assessment_nudges imports SessionLocal from platform.database inside
    # the function body — patch it there.
    import app.platform.database as db_mod
    real_close = db.close
    db.close = lambda: None  # keep the fixture session usable after the task
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: db)
    yield fake
    db.close = real_close


def _mk_assessment(db, *, token, delivered_hours=None, opened_hours=None, preview_hours=None,
                   expires_hours=72, timeline=None):
    now = datetime.now(timezone.utc)
    task = Task(name="Nudge task", description="d", task_type="python", difficulty="medium",
                duration_minutes=30, task_key=f"nudge_{token}")
    db.add(task)
    db.flush()
    candidate = Candidate(email=f"{token}@example.com", full_name="Cand Idate")
    db.add(candidate)
    db.flush()
    a = Assessment(
        task_id=task.id,
        candidate_id=candidate.id,
        token=token,
        duration_minutes=30,
        status=AssessmentStatus.PENDING,
        invite_sent_at=now - timedelta(hours=72),
        expires_at=now + timedelta(hours=expires_hours),
        invite_delivered_at=(now - timedelta(hours=delivered_hours)) if delivered_hours else None,
        invite_opened_at=(now - timedelta(hours=opened_hours)) if opened_hours else None,
        preview_viewed_at=(now - timedelta(hours=preview_hours)) if preview_hours else None,
        timeline=timeline,
    )
    db.add(a)
    db.commit()
    return a


def test_flag_off_is_noop(db, monkeypatch):
    monkeypatch.setattr(settings, "ASSESSMENT_NUDGES_ENABLED", False, raising=False)
    assert send_assessment_nudges() == {"status": "skipped", "reason": "flag_off"}


def test_delivered_not_opened_gets_that_nudge(db, nudge_env):
    _mk_assessment(db, token="nudge-a", delivered_hours=50)
    result = send_assessment_nudges()
    assert result["sent"] == 1
    assert nudge_env.sent[0]["kind"] == "delivered_not_opened"


def test_opened_not_started_wins_over_delivered(db, nudge_env):
    _mk_assessment(db, token="nudge-b", delivered_hours=60, preview_hours=50)
    result = send_assessment_nudges()
    assert result["sent"] == 1
    assert nudge_env.sent[0]["kind"] == "opened_not_started"


def test_one_nudge_per_assessment_ever(db, nudge_env):
    a = _mk_assessment(db, token="nudge-c", delivered_hours=50)
    assert send_assessment_nudges()["sent"] == 1
    db.refresh(a)
    events = [e for e in (a.timeline or []) if e.get("event_type") == "nudge_sent"]
    assert len(events) == 1 and events[0]["kind"] == "delivered_not_opened"
    # Second sweep: already nudged → skipped, nothing sent.
    result = send_assessment_nudges()
    assert result["sent"] == 0
    assert len(nudge_env.sent) == 1


def test_near_expiry_left_to_expiry_reminder(db, nudge_env):
    _mk_assessment(db, token="nudge-d", delivered_hours=50, expires_hours=12)
    result = send_assessment_nudges()
    assert result["sent"] == 0
    assert nudge_env.sent == []


def test_recent_delivery_not_yet_nudged(db, nudge_env):
    _mk_assessment(db, token="nudge-e", delivered_hours=10)
    result = send_assessment_nudges()
    assert result["sent"] == 0
