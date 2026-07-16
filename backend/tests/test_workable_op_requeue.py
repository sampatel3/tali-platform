"""(a) A failed approve batch (e.g. Workable lock timeout) must return every
decision to the Hub queue (status processing → pending), not strand them.
"""
from __future__ import annotations

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.services.workable_actions_service import WorkableWritebackError
from app.services.workable_op_runner import OP_APPROVE_DECISIONS, surface_op_failure

def _seed_processing_decisions(db, n=2):
    org = Organization(name="O", slug=f"o-req-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="R", source="manual")
    db.add(role)
    db.flush()
    ids = []
    for i in range(n):
        cand = Candidate(organization_id=org.id, email=f"c{i}-{id(db)}@x.test", full_name=f"C{i}")
        db.add(cand)
        db.flush()
        app = CandidateApplication(
            organization_id=org.id, candidate_id=cand.id, role_id=role.id,
            status="applied", pipeline_stage="applied", pipeline_stage_source="recruiter",
            application_outcome="open", source="manual",
        )
        db.add(app)
        db.flush()
        d = AgentDecision(
            organization_id=org.id, role_id=role.id, application_id=app.id,
            decision_type="advance_to_interview", recommendation="advance_to_interview",
            status="processing", reasoning="in flight", model_version="x", prompt_version="x",
            idempotency_key=f"k{i}-{id(db)}",
        )
        db.add(d)
        db.flush()
        ids.append(int(d.id))
    db.commit()
    return org, ids


def test_lock_timeout_requeues_whole_approve_batch(db):
    org, ids = _seed_processing_decisions(db, n=3)
    err = WorkableWritebackError(
        action="approve_decisions", code="lock_timeout", message="Workable was busy", retriable=True
    )

    surface_op_failure(
        db, organization_id=int(org.id), op_type=OP_APPROVE_DECISIONS,
        payload={"decision_ids": ids}, error=err,
    )

    rows = db.query(AgentDecision).filter(AgentDecision.id.in_(ids)).all()
    assert {r.status for r in rows} == {"pending"}, "all stranded decisions return to pending"
