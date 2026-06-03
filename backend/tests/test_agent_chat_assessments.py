"""P1: read the per-criterion assessments the scorer already stored in
cv_match_details — no LLM, no re-score. The basis for reasoning over a change."""
from __future__ import annotations

from app.agent_chat import assessments
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _org(db):
    org = Organization(name="Assess Org", slug=f"assess-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _role(db, org):
    role = Role(organization_id=org.id, name="Role", source="manual", score_threshold=70)
    db.add(role)
    db.flush()
    return role


def _app(db, org, role, *, name, crit_id, status, reasoning, assessed=True):
    cand = Candidate(organization_id=org.id, email=f"{name}-{id(db)}@x.test", full_name=name)
    db.add(cand)
    db.flush()
    details = None
    if assessed:
        details = {
            "requirements_assessment": [
                {"requirement_id": f"crit_{crit_id}", "requirement": "loc",
                 "status": status, "reasoning": reasoning},
            ]
        }
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        cv_match_details=details,
    )
    db.add(app)
    db.flush()
    return app


def test_criterion_breakdown_groups_by_stored_status(db):
    org = _org(db)
    role = _role(db, org)
    _app(db, org, role, name="Ada", crit_id=42, status="met", reasoning="UK-based, in scope")
    _app(db, org, role, name="Bo", crit_id=42, status="missing", reasoning="based in India")
    _app(db, org, role, name="Cy", crit_id=42, status="missing", reasoning="based in Saudi")
    _app(db, org, role, name="Di", crit_id=42, status="x", reasoning="", assessed=False)

    out = assessments.criterion_breakdown(db, role, 42)
    assert out["total"] == 4
    assert out["counts"]["met"] == 1
    assert out["counts"]["missing"] == 2
    assert out["counts"]["not_assessed"] == 1
    reasons = " ".join(c["reasoning"] or "" for c in out["samples"]["missing"])
    assert "India" in reasons and "Saudi" in reasons      # reasoning carried for scoping


def test_affected_applications_scopes_by_status(db):
    org = _org(db)
    role = _role(db, org)
    _app(db, org, role, name="Ada", crit_id=42, status="met", reasoning="UK")
    bo = _app(db, org, role, name="Bo", crit_id=42, status="missing", reasoning="Saudi")

    aff = assessments.affected_applications(db, role, 42, statuses=("missing",))
    assert [a["candidate_name"] for a in aff] == ["Bo"]
    assert aff[0]["application_id"] == bo.id
    assert "Saudi" in (aff[0]["reasoning"] or "")
