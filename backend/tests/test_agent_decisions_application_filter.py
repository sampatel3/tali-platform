"""GET /agent-decisions?application_id=X — the single-candidate lens.

The candidate standing report fetches just this application's pending
decision(s) to surface the agent's recommendation in its header strip,
so the filter must return ONLY that application's decisions (and never
another application's, even within the same org / role).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
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


def _decision(db, org_id, role_id, app_id, *, status="pending"):
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
        idempotency_key=f"appfilter-test:{app_id}:{status}",
    )
    db.add(d)
    db.flush()
    return d


def test_application_id_filters_to_one_candidate(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()

    # Two applications on the same role, each with its own pending decision.
    app_a = _app(db, org_id, role.id, "a@x.test")
    app_b = _app(db, org_id, role.id, "b@x.test")
    decision_a = _decision(db, org_id, role.id, app_a.id)
    decision_b = _decision(db, org_id, role.id, app_b.id)
    db.commit()

    # Filtering by app_a returns ONLY app_a's decision.
    res = client.get(
        f"/api/v1/agent-decisions?application_id={app_a.id}", headers=headers
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert {row["id"] for row in rows} == {decision_a.id}
    assert all(row["application_id"] == app_a.id for row in rows)
    assert decision_b.id not in {row["id"] for row in rows}

    # And by app_b returns ONLY app_b's decision — proving the filter scopes
    # per-application, not just "any decision on the role".
    res_b = client.get(
        f"/api/v1/agent-decisions?application_id={app_b.id}", headers=headers
    )
    assert res_b.status_code == 200, res_b.text
    assert {row["id"] for row in res_b.json()} == {decision_b.id}

    # Sanity: without the filter, both decisions are visible in the queue.
    res_all = client.get("/api/v1/agent-decisions?status=pending", headers=headers)
    assert res_all.status_code == 200, res_all.text
    all_ids = {row["id"] for row in res_all.json()}
    assert {decision_a.id, decision_b.id} <= all_ids


def test_application_id_with_no_decisions_returns_empty(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    role = Role(organization_id=org_id, name="Backend", source="manual", agentic_mode_enabled=True)
    db.add(role)
    db.flush()
    app = _app(db, org_id, role.id, "nodecision@x.test")
    db.commit()

    res = client.get(
        f"/api/v1/agent-decisions?application_id={app.id}", headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


def test_related_role_decision_includes_complete_named_role_family(client, db):
    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    owner = Role(
        organization_id=org_id,
        name="AI Engineer",
        source="workable",
        workable_job_id="AI-ENGINEER",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org_id,
        name="AI Engineer · Evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    app = _app(db, org_id, owner.id, "related-decision@x.test")
    db.add(
        SisterRoleEvaluation(
            organization_id=org_id,
            role_id=related.id,
            candidate_id=app.candidate_id,
            source_application_id=app.id,
            ats_application_id=app.id,
            status="done",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            application_outcome_source="recruiter",
            membership_source="test",
            spec_fingerprint="related-decision-family",
        )
    )
    db.flush()
    decision = _decision(db, org_id, related.id, app.id)
    db.commit()

    response = client.get(
        f"/api/v1/agent-decisions?application_id={app.id}", headers=headers
    )

    assert response.status_code == 200, response.text
    payload = next(row for row in response.json() if row["id"] == decision.id)
    assert payload["role_name"] == related.name
    assert payload["role_family"] == {
        "owner": {"id": owner.id, "name": owner.name},
        "related": [{"id": related.id, "name": related.name}],
    }


def test_related_role_decision_uses_only_related_evaluation_presentation_fields(
    client, db, monkeypatch
):
    """A shared candidate pool must never become shared scoring context."""

    headers, email = auth_headers(client)
    org_id = db.query(User).filter(User.email == email).first().organization_id
    owner = Role(
        organization_id=org_id,
        name="AI Engineer",
        source="workable",
        workable_job_id="AI-ENGINEER-OWNER",
        agentic_mode_enabled=True,
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org_id,
        name="AI Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        agentic_mode_enabled=True,
    )
    db.add(related)
    db.flush()

    app = _app(db, org_id, owner.id, "related-presentation@x.test")
    app.cv_match_score = 25.0
    app.role_fit_score_cache_100 = 25.0
    app.taali_score_cache_100 = 25.0
    app.cv_match_scored_at = datetime(2026, 7, 16, 3, 41, tzinfo=timezone.utc)
    app.cv_match_details = {
        "summary": "OWNER ROLE ONLY: Pre-screen filtered at 25/100.",
        "engine_version": "1.9.0",
        "requirements_assessment": [
            {
                "criterion_text": "Owner-only requirement",
                "match_score": 25,
                "status": "missing",
            }
        ],
        "integrity_signals": {
            "document_hygiene": {"injection_detected": True}
        },
    }
    evaluation = SisterRoleEvaluation(
        organization_id=org_id,
        role_id=related.id,
        source_application_id=app.id,
        status="done",
        pipeline_stage="review",
        spec_fingerprint="related-spec",
        cv_fingerprint="shared-cv",
        role_fit_score=72.0,
        summary="RELATED ROLE ONLY: Strong fit for this role.",
        details={
            "summary": "RELATED ROLE ONLY: Strong fit for this role.",
            "engine_version": "2.1.0",
            "requirements_assessment": [
                {
                    "criterion_text": "Related-role requirement",
                    "match_score": 90,
                    "status": "met",
                }
            ],
            "integrity_signals": {
                "github": {
                    "status": "corroborated",
                    "username": "related-role-proof",
                    "matched_skills": ["Python"],
                }
            },
        },
        model_version="claude-sonnet-4-6",
        prompt_version="holistic_v2_1",
        scored_at=datetime(2026, 7, 17, 9, 45, tzinfo=timezone.utc),
    )
    db.add(evaluation)
    db.flush()
    decision = _decision(db, org_id, related.id, app.id)
    # Deliberately omit candidate_summary: production decision 204836 has this
    # legacy shape, so the read path must repair existing rows too.
    decision.evidence = {
        "source": "related_role_runtime",
        "sister_evaluation_id": evaluation.id,
        "role_fit_score": 72.0,
    }
    # An owner-role rescore must not freeze this related-role card.
    db.add(CvScoreJob(application_id=app.id, role_id=owner.id, status="pending"))
    db.commit()

    # Make owner engine staleness deterministic. Related-role staleness must not
    # call this owner-application path at all.
    monkeypatch.setattr(
        "app.services.decision_staleness._engine_outdated", lambda _app: True
    )
    monkeypatch.setattr(
        "app.services.cv_score_orchestrator.score_is_outdated", lambda _app: True
    )

    response = client.get(
        f"/api/v1/agent-decisions?application_id={app.id}&status=pending",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = next(row for row in response.json() if row["id"] == decision.id)
    assert payload["role_id"] == related.id
    assert payload["application_id"] == app.id
    assert payload["workable_job_id"] == owner.workable_job_id
    assert payload["taali_score"] == 72.0
    assert payload["candidate_summary"] == "RELATED ROLE ONLY: Strong fit for this role."
    assert payload["requirements"] == [
        {"label": "Related-role requirement", "score": 90, "status": "met"}
    ]
    assert payload["score_summary"]["score_provenance"] == {
        "source": "sister_role_evaluation",
        "label": "Related role fit",
        "engine_version": "2.1.0",
        "scored_at": "2026-07-17T09:45:00+00:00",
        "model": "claude-sonnet-4-6",
    }
    assert payload["score_summary"]["integrity"]["warnings"] == []
    assert payload["score_summary"]["integrity"]["corroborations"] == [
        "GitHub profile (github.com/related-role-proof) backs up the CV — "
        "public repositories use Python."
    ]
    assert payload["is_stale"] is False
    assert payload["rescore_in_flight"] is False
    serialized = str(payload)
    assert "OWNER ROLE ONLY" not in serialized
    assert "Owner-only requirement" not in serialized
    assert "1.9.0" not in serialized
    assert "Hidden prompt-injection" not in serialized

    reeval_count = client.get(
        f"/api/v1/agent-decisions/needs-reeval-count?role_id={related.id}",
        headers=headers,
    )
    assert reeval_count.status_code == 200, reeval_count.text
    assert reeval_count.json() == {"count": 0}
