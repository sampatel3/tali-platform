"""P1: read the per-criterion assessments the scorer already stored in
cv_match_details — no LLM, no re-score. The basis for reasoning over a change."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.agent_chat import assessments, tools
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User


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


def test_related_assessments_use_membership_and_role_owned_details(db):
    org = _org(db)
    owner = _role(db, org)
    related = Role(
        organization_id=int(org.id),
        name="Independent Related Role",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=int(owner.id),
        score_threshold=70,
    )
    db.add(related)
    db.flush()
    member = _app(
        db,
        org,
        owner,
        name="Member",
        crit_id=42,
        status="met",
        reasoning="Owner-role assessment must not leak",
    )
    _app(
        db,
        org,
        owner,
        name="Owner Only",
        crit_id=42,
        status="met",
        reasoning="Not in related pool",
    )
    membership = SisterRoleEvaluation(
        organization_id=int(org.id),
        role_id=int(related.id),
        candidate_id=int(member.candidate_id),
        source_application_id=int(member.id),
        ats_application_id=int(member.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint="related-assessment-spec",
        role_fit_score=74,
        details={
            "requirements_assessment": [
                {
                    "requirement_id": "crit_42",
                    "requirement": "PySpark",
                    "status": "missing",
                    "reasoning": "No PySpark evidence for this role",
                    "evidence_quotes": [],
                }
            ]
        },
    )
    db.add(membership)
    member.deleted_at = datetime.now(timezone.utc)
    db.flush()

    breakdown = assessments.criterion_breakdown(db, related, 42)
    affected = assessments.affected_applications(
        db,
        related,
        42,
        statuses=("missing",),
    )

    assert breakdown["total"] == 1
    assert breakdown["counts"] == {
        "met": 0,
        "missing": 1,
        "unknown": 0,
        "not_assessed": 0,
    }
    assert affected == [
        {
            "application_id": int(member.id),
            "candidate_name": "Member",
            "status": "missing",
            "reasoning": "No PySpark evidence for this role",
            "evidence_quotes": [],
        }
    ]

    membership.deleted_at = datetime.now(timezone.utc)
    db.flush()
    assert assessments.criterion_breakdown(db, related, 42)["total"] == 0


def test_rescreen_scoped_only_marks_the_affected_subset(db):
    """P3: a scoped re-screen invalidates ONLY the affected candidates (the
    missing, for a widening) — not the whole pool."""
    org = _org(db)
    role = _role(db, org)
    user = User(
        email=f"assess-owner-{id(db)}@x.test",
        hashed_password="x",
        organization_id=org.id,
        role="owner",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    _app(db, org, role, name="Ada", crit_id=42, status="met", reasoning="UK")
    bo = _app(db, org, role, name="Bo", crit_id=42, status="missing", reasoning="Saudi")
    cy = _app(db, org, role, name="Cy", crit_id=42, status="missing", reasoning="India")

    with patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=2
    ) as stale, patch("app.tasks.scoring_tasks.sweep_stale_scores"):
        result = tools.dispatch_tool(
            "rescreen_scoped", {"criterion_id": 42, "statuses": ["missing"]},
            db=db, role=role, user=user,
        )

    assert result["type"] == "rescreen_started" and result["scoped"] is True
    _, kwargs = stale.call_args
    assert set(kwargs["application_ids"]) == {bo.id, cy.id}   # only the missing, not Ada


def test_rescreen_scoped_empty_match_never_expands_to_whole_role(db):
    org = _org(db)
    role = _role(db, org)
    user = User(
        email=f"empty-scope-owner-{id(db)}@x.test",
        hashed_password="x",
        organization_id=org.id,
        role="owner",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    _app(db, org, role, name="Ada", crit_id=42, status="met", reasoning="UK")

    with patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=0
    ) as stale, patch("app.tasks.scoring_tasks.sweep_stale_scores") as sweep:
        result = tools.dispatch_tool(
            "rescreen_scoped",
            {"criterion_id": 42, "statuses": ["missing"]},
            db=db,
            role=role,
            user=user,
        )

    assert result["type"] == "rescreen_started"
    assert result["rescreening_count"] == 0
    assert result["scoped"] is True
    # Empty scopes short-circuit before invalidation. This preserves the
    # semantic distinction from ``None`` (whole role) without creating a
    # redundant stale-score job or dispatching paid work.
    stale.assert_not_called()
    sweep.apply_async.assert_not_called()


def test_search_candidates_reuses_the_search_handler(db):
    """P4: the role-agent gets the Search page's candidate search."""
    org = _org(db)
    role = _role(db, org)
    with patch(
        "app.mcp.handlers.nl_search_candidates", return_value={"results": ["x"]}
    ) as srch:
        out = tools.dispatch_tool(
            "search_candidates", {"query": "based in MENA"}, db=db, role=role, user=None
        )
    assert out == {"results": ["x"]}
    _, kwargs = srch.call_args
    assert kwargs["query"] == "based in MENA" and kwargs["role_id"] == role.id


def test_find_top_candidates_reuses_shared_handler_and_preserves_report_link(db):
    org = _org(db)
    role = _role(db, org)
    payload = {
        "candidates": [],
        "deep_checked": 0,
        "report_token": "rpt_secure",
        "report_url": "https://taali.test/report/rpt_secure",
    }
    with patch("app.mcp.handlers.find_top_candidates", return_value=payload) as find:
        out = tools.dispatch_tool(
            "find_top_candidates",
            {"query": "candidates", "limit": 10},
            db=db,
            role=role,
            user=None,
        )

    assert out == {"type": "candidate_evidence", **payload}
    _, kwargs = find.call_args
    assert kwargs["query"] == "candidates"
    assert kwargs["limit"] == 10
    assert kwargs["role_id"] == role.id
