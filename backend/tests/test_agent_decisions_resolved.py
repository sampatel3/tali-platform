"""GET /agent-decisions?status=resolved — the History view.

History is the inverse of the live queue: it returns every decision that has
left the recruiter's queue (approved / overridden / taught / discarded /
expired) and excludes the actionable queue states (pending, processing).
"""
from __future__ import annotations

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
    assert resolved_ids == {ids["approved"], ids["overridden"], ids["reverted_for_feedback"]}
    # The live queue states must never leak into history.
    assert ids["pending"] not in resolved_ids
    assert ids["processing"] not in resolved_ids

    queue = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert queue.status_code == 200, queue.text
    queue_ids = {row["id"] for row in queue.json()}
    assert queue_ids == {ids["pending"], ids["processing"]}


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
