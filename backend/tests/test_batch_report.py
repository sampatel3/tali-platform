"""Manual-batch → role-chat status + completion messages.

Covers the deterministic message composition, the started/completion posting
into a role conversation, and the Celery backstop that confirms a batch-score
finished from the job rows before reporting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.agent_chat import batch_report, service
from app.models.agent_conversation import AgentConversationMessage
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob, SCORE_JOB_DONE, SCORE_JOB_ERROR
from app.models.organization import Organization
from app.models.role import Role


# ---------------------------------------------------------------------------
# Factories (kept local; mirrors the agent-chat impact tests)
# ---------------------------------------------------------------------------


def _org(db, name="Batch Org") -> Organization:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _role(db, org, *, name="Backend") -> Role:
    role = Role(organization_id=org.id, name=name, source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    return role


def _app(db, org, role, *, name="Cand") -> CandidateApplication:
    cand = Candidate(organization_id=org.id, email=f"{name}-{id(db)}@x.test", full_name=name)
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
    )
    db.add(app)
    db.flush()
    return app


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_compose_completion_process_lists_each_step():
    counts = {
        "fetch": {"total": 120, "fetched": 118, "errors": 2},
        "pre_screen": {"total": 2000, "processed": 2000, "errors": 1},
        "score": {"total": 1850, "scored": 1840, "filtered": 150, "errors": 10},
    }
    text = batch_report.compose_completion("Sales Lead", batch_report.KIND_PROCESS, counts)
    assert text.startswith("Finished processing Sales Lead —")
    assert "fetched 118 of 120 CVs (2 failed)" in text
    assert "pre-screened 2,000 of 2,000 (1 errors)" in text
    assert "scored 1,840 of 1,850 (150 below cut-off, 10 errors)" in text


def test_compose_completion_score_and_pre_screen():
    s = batch_report.compose_completion(
        "Backend", batch_report.KIND_BATCH_SCORE,
        {"total": 500, "scored": 480, "errors": 5, "pre_screened_out": 15},
    )
    assert "scored 480 of 500 candidate(s) (15 below cut-off, 5 errors)" in s

    p = batch_report.compose_completion(
        "Backend", batch_report.KIND_BATCH_PRE_SCREEN, {"total": 90, "processed": 90}
    )
    assert "pre-screened 90 of 90 candidate(s)." in p


def test_compose_completion_cancelled_verb():
    s = batch_report.compose_completion(
        "Backend", batch_report.KIND_BATCH_SCORE, {"total": 10, "scored": 4}, status="cancelled"
    )
    assert s.startswith("Cancelled scoring Backend")


def test_compose_started_mentions_steps_and_total():
    s = batch_report.compose_started("Backend", batch_report.KIND_PROCESS, 2000, ["fetch CVs", "pre-screen", "score"])
    assert "2,000 candidate(s)" in s
    assert "fetch CVs → pre-screen → score" in s


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def test_post_started_writes_message_with_running_card(db):
    org = _org(db)
    role = _role(db, org)
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    db.commit()

    msg = batch_report.post_started(
        db, conversation=convo, role=role, kind=batch_report.KIND_PROCESS,
        total=42, steps=["fetch CVs", "score"], token="tok-1",
    )
    db.commit()
    assert msg.kind == "chat"
    assert msg.actions and msg.actions[0]["type"] == batch_report.CARD_BATCH_RUNNING
    assert msg.actions[0]["status"] == "running"
    assert msg.actions[0]["total"] == 42
    assert convo.last_message_at is not None


def test_post_completion_writes_summary(db):
    org = _org(db)
    role = _role(db, org)
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    db.commit()

    msg = batch_report.post_completion(
        db, conversation=convo, role=role, kind=batch_report.KIND_BATCH_SCORE,
        counts={"total": 3, "scored": 3}, token="tok-2",
    )
    db.commit()
    assert msg is not None
    assert "Finished scoring" in msg.text
    assert msg.actions[0]["type"] == batch_report.CARD_BATCH_DONE


def test_backstop_task_posts_when_jobs_settled(db, monkeypatch):
    """All targeted jobs terminal → the backstop posts the completion message."""
    from app.tasks.agent_chat_tasks import report_batch_score_complete

    # Force the Redis claim to succeed so the test doesn't depend on Redis.
    monkeypatch.setattr(batch_report, "claim_completion", lambda *a, **k: True)

    org = _org(db)
    role = _role(db, org)
    a = _app(db, org, role, name="A")
    b = _app(db, org, role, name="B")
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)

    started = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)
    fin = started + timedelta(minutes=1)
    db.add(CvScoreJob(application_id=a.id, role_id=role.id, status=SCORE_JOB_DONE, cache_hit="miss", queued_at=started, finished_at=fin))
    db.add(CvScoreJob(application_id=b.id, role_id=role.id, status=SCORE_JOB_ERROR, queued_at=started, finished_at=fin))
    db.commit()  # the task opens its own session

    out = report_batch_score_complete(
        role_id=role.id, organization_id=org.id, conversation_id=convo.id,
        token="tok-3", started_at_iso=started.isoformat(), total=2,
    )
    assert out["status"] == "posted"

    posted = (
        db.query(AgentConversationMessage)
        .filter(AgentConversationMessage.conversation_id == convo.id)
        .all()
    )
    assert any("Finished scoring" in (m.text or "") for m in posted)


def test_backstop_task_waits_when_jobs_incomplete(db, monkeypatch):
    """Fewer terminal jobs than the target → reschedule, don't post yet."""
    from app.tasks.agent_chat_tasks import report_batch_score_complete

    scheduled = {}
    monkeypatch.setattr(
        report_batch_score_complete, "apply_async",
        lambda **kw: scheduled.update(kw) or None,
    )

    org = _org(db)
    role = _role(db, org)
    a = _app(db, org, role, name="A")
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    started = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)
    db.add(CvScoreJob(application_id=a.id, role_id=role.id, status=SCORE_JOB_DONE, cache_hit="miss", queued_at=started, finished_at=started + timedelta(minutes=1)))
    db.commit()

    out = report_batch_score_complete(
        role_id=role.id, organization_id=org.id, conversation_id=convo.id,
        token="tok-4", started_at_iso=started.isoformat(), total=5,
    )
    assert out["status"] == "waiting"
    assert scheduled.get("kwargs", {}).get("attempt") == 1
