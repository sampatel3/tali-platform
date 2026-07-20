"""GET /agent-decisions?status=resolved — the History view.

History is the inverse of the live queue: it returns every decision that has
left the recruiter's queue (approved / overridden / discarded / expired) and
excludes all live queue states (pending, reverted-for-feedback, processing).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _app(db, org_id, role_id, email):
    cand = Candidate(organization_id=org_id, email=email, full_name=email.split("@")[0])
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org_id,
        candidate_id=cand.id,
        role_id=role_id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org_id, role_id, app_id, *, status):
    d = AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app_id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status=status,
        reasoning="seed",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
        idempotency_key=f"resolved-test:{app_id}:{status}",
    )
    db.add(d)
    db.flush()
    return d


def test_resolved_status_is_inverse_of_queue(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    ids = {}
    for status in ("pending", "processing", "approved", "overridden", "reverted_for_feedback"):
        app = _app(db, org_id, role.id, f"{status}@x.test")
        ids[status] = _decision(db, org_id, role.id, app.id, status=status).id
    db.commit()

    resolved = client.get("/api/v1/agent-decisions?status=resolved", headers=headers)
    assert resolved.status_code == 200, resolved.text
    resolved_ids = {row["id"] for row in resolved.json()}
    assert resolved_ids == {ids["approved"], ids["overridden"]}
    # The live queue states must never leak into history.
    assert ids["pending"] not in resolved_ids
    assert ids["reverted_for_feedback"] not in resolved_ids
    assert ids["processing"] not in resolved_ids

    # No status parameter: this is the exact default route used by Home.
    queue = client.get("/api/v1/agent-decisions", headers=headers)
    assert queue.status_code == 200, queue.text
    assert [row["id"] for row in queue.json()] == [
        ids["pending"],
        ids["reverted_for_feedback"],
        ids["processing"],
    ]


def test_pending_queue_limits_each_live_lane_separately(client, db):
    """A deep lane cannot hide taught work or bounded in-flight receipts."""
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id,
        name="Queue ordering",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()

    old_pending_app = _app(db, org_id, role.id, "queue-pending-old@x.test")
    old_pending = _decision(
        db, org_id, role.id, old_pending_app.id, status="pending"
    )
    pending_app = _app(db, org_id, role.id, "queue-pending-new@x.test")
    pending = _decision(db, org_id, role.id, pending_app.id, status="pending")
    old_reverted_app = _app(db, org_id, role.id, "queue-reverted-old@x.test")
    old_reverted = _decision(
        db,
        org_id,
        role.id,
        old_reverted_app.id,
        status="reverted_for_feedback",
    )
    reverted_app = _app(db, org_id, role.id, "queue-reverted-new@x.test")
    reverted = _decision(
        db,
        org_id,
        role.id,
        reverted_app.id,
        status="reverted_for_feedback",
    )
    old_processing_app = _app(db, org_id, role.id, "queue-processing-old@x.test")
    old_processing = _decision(
        db, org_id, role.id, old_processing_app.id, status="processing"
    )
    processing_app = _app(db, org_id, role.id, "queue-processing-new@x.test")
    processing = _decision(db, org_id, role.id, processing_app.id, status="processing")
    db.commit()

    queue = client.get(
        "/api/v1/agent-decisions?status=pending&limit=1", headers=headers
    )

    assert queue.status_code == 200, queue.text
    assert [row["id"] for row in queue.json()] == [
        pending.id,
        reverted.id,
        processing.id,
    ]
    assert old_pending.id not in {row["id"] for row in queue.json()}
    assert old_reverted.id not in {row["id"] for row in queue.json()}
    assert old_processing.id not in {row["id"] for row in queue.json()}


def test_pending_queue_reads_both_lanes_in_one_database_snapshot(client, db):
    """The pending and processing lanes must share one SQL statement.

    Under PostgreSQL READ COMMITTED, separate statements can observe a decision
    before and after a pending-to-processing transition. SQLAlchemy's identity
    map can then return that decision twice with its first, stale status. A
    single statement gives both lane limits the same database snapshot.
    """
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id,
        name="Queue snapshot",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()

    pending_app = _app(db, org_id, role.id, "snapshot-pending@x.test")
    pending = _decision(db, org_id, role.id, pending_app.id, status="pending")
    processing_app = _app(db, org_id, role.id, "snapshot-processing@x.test")
    processing = _decision(
        db, org_id, role.id, processing_app.id, status="processing"
    )
    db.commit()

    lane_selects: list[str] = []

    def capture_lane_select(
        _conn, _cursor, statement, _parameters, _context, _many
    ):
        normalized = " ".join(statement.lower().split())
        if (
            normalized.startswith("select")
            and "from agent_decisions" in normalized
            and "agent_decisions.status" in normalized
            and "candidate_applications" in normalized
        ):
            lane_selects.append(normalized)

    event.listen(db.get_bind(), "before_cursor_execute", capture_lane_select)
    try:
        queue = client.get(
            "/api/v1/agent-decisions?status=pending&limit=1", headers=headers
        )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture_lane_select)

    assert queue.status_code == 200, queue.text
    assert [row["id"] for row in queue.json()] == [pending.id, processing.id]
    assert len(lane_selects) == 1, (
        "pending and processing lanes were read by separate SQL statements"
    )


def test_default_queue_snoozes_pending_and_reverted_but_not_processing(client, db):
    """Snooze hides actionable work, never an accepted in-flight receipt."""
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id,
        name="Snoozed receipt",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()

    pending_app = _app(db, org_id, role.id, "snoozed-pending@x.test")
    pending = _decision(db, org_id, role.id, pending_app.id, status="pending")
    reverted_app = _app(db, org_id, role.id, "snoozed-reverted@x.test")
    reverted = _decision(
        db,
        org_id,
        role.id,
        reverted_app.id,
        status="reverted_for_feedback",
    )
    expired_reverted_app = _app(db, org_id, role.id, "expired-reverted@x.test")
    expired_reverted = _decision(
        db,
        org_id,
        role.id,
        expired_reverted_app.id,
        status="reverted_for_feedback",
    )
    processing_app = _app(db, org_id, role.id, "snoozed-processing@x.test")
    processing = _decision(
        db, org_id, role.id, processing_app.id, status="processing"
    )
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    pending.snoozed_until = future
    reverted.snoozed_until = future
    processing.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=1)
    expired_reverted.snoozed_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    queue = client.get("/api/v1/agent-decisions", headers=headers)

    assert queue.status_code == 200, queue.text
    queue_ids = {row["id"] for row in queue.json()}
    assert pending.id not in queue_ids
    assert reverted.id not in queue_ids
    assert expired_reverted.id in queue_ids
    assert processing.id in queue_ids

    reverted_only = client.get(
        "/api/v1/agent-decisions?status=reverted_for_feedback", headers=headers
    )
    assert reverted_only.status_code == 200, reverted_only.text
    assert {row["id"] for row in reverted_only.json()} == {expired_reverted.id}


def test_decided_status_is_human_calls_only(client, db):
    """``status=decided`` (the Hub's "Recent decisions" panel) returns only the
    calls a human made — approved / overridden — and excludes the purge states
    (discarded / expired) and the taught-but-unresolved state, so a bulk purge
    can't crowd genuine decisions out of the panel's row limit."""
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Platform", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    ids = {}
    for status in ("pending", "approved", "overridden", "reverted_for_feedback", "discarded", "expired"):
        app = _app(db, org_id, role.id, f"decided-{status}@x.test")
        ids[status] = _decision(db, org_id, role.id, app.id, status=status).id
    db.commit()

    decided = client.get("/api/v1/agent-decisions?status=decided", headers=headers)
    assert decided.status_code == 200, decided.text
    decided_ids = {row["id"] for row in decided.json()}
    assert decided_ids == {ids["approved"], ids["overridden"]}
    for excluded in ("pending", "reverted_for_feedback", "discarded", "expired"):
        assert ids[excluded] not in decided_ids


def test_current_status_prefers_live_then_last_human_call(client, db):
    """Candidate reports must not show a newer purge artefact, and an older
    actionable card still wins over resolved history."""
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(
        organization_id=org_id,
        name="Current decision lens",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()

    live_app = _app(db, org_id, role.id, "current-live@x.test")
    approved = _decision(db, org_id, role.id, live_app.id, status="approved")
    pending = _decision(db, org_id, role.id, live_app.id, status="pending")
    _decision(db, org_id, role.id, live_app.id, status="discarded")

    resolved_app = _app(db, org_id, role.id, "current-resolved@x.test")
    last_call = _decision(db, org_id, role.id, resolved_app.id, status="overridden")
    _decision(db, org_id, role.id, resolved_app.id, status="expired")
    db.commit()

    live = client.get(
        f"/api/v1/agent-decisions?application_id={live_app.id}&status=current&limit=1",
        headers=headers,
    )
    assert live.status_code == 200, live.text
    assert [row["id"] for row in live.json()] == [pending.id]
    assert pending.id != approved.id

    resolved = client.get(
        f"/api/v1/agent-decisions?application_id={resolved_app.id}&status=current&limit=1",
        headers=headers,
    )
    assert resolved.status_code == 200, resolved.text
    assert [row["id"] for row in resolved.json()] == [last_call.id]
