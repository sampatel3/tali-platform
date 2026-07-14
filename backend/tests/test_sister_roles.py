from types import SimpleNamespace
from unittest.mock import patch

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from tests.conftest import auth_headers


def _source_role_with_candidates(db, *, organization_id: int) -> tuple[Role, list[CandidateApplication]]:
    role = Role(
        organization_id=organization_id,
        name="AI Engineer",
        source="workable",
        workable_job_id="AI-ENG",
        workable_job_data={"state": "published"},
        job_spec_text="Original AI engineer role specification with Python and production ML systems.",
    )
    db.add(role)
    db.flush()
    applications = []
    for index, (has_cv, outcome, score) in enumerate(
        [(True, "open", 72.0), (False, "rejected", 41.0)]
    ):
        candidate = Candidate(
            organization_id=organization_id,
            email=f"candidate-{index}@example.com",
            full_name=f"Candidate {index}",
            cv_text="Python ML engineer with deployed LLM systems." if has_cv else None,
        )
        db.add(candidate)
        db.flush()
        application = CandidateApplication(
            organization_id=organization_id,
            candidate_id=candidate.id,
            role_id=role.id,
            source="workable",
            workable_candidate_id=f"workable-{index}",
            workable_stage="Sourced",
            application_outcome=outcome,
            cv_text=candidate.cv_text,
            taali_score_cache_100=score,
            role_fit_score_cache_100=score,
            score_mode_cache="role_fit_only",
        )
        db.add(application)
        applications.append(application)
    db.commit()
    return role, applications


def test_create_sister_role_persists_separate_scores_and_projects_source_roster(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(
        db, organization_id=user.organization_id
    )
    updated_spec = (
        "Updated AI engineer role requiring production RAG, evaluation design, "
        "Python, distributed systems, and ownership of model observability."
    )

    with patch(
        "app.domains.assessments_runtime.sister_role_routes.score_sister_role.apply_async"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{source.id}/sisters",
            json={"name": "AI Engineer · RAG", "job_spec_text": updated_spec},
            headers=headers,
        )

    assert response.status_code == 201, response.text
    body = response.json()
    sister = body["role"]
    assert sister["role_kind"] == ROLE_KIND_SISTER
    assert sister["ats_owner_role_id"] == source.id
    assert sister["ats_owner_role_name"] == source.name
    assert sister["workable_job_id"] is None
    assert sister["effective_workable_job_id"] == source.workable_job_id
    assert sister["applications_count"] == 2
    assert body["evaluation_counts"] == {"total": 2, "pending": 1, "unscorable": 1}
    dispatch.assert_called_once()

    source_detail = client.get(f"/api/v1/roles/{source.id}", headers=headers)
    assert source_detail.status_code == 200
    assert source_detail.json()["sister_role_count"] == 1

    evaluations = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == sister["id"])
        .order_by(SisterRoleEvaluation.source_application_id)
        .all()
    )
    assert [item.status for item in evaluations] == ["pending", "unscorable"]

    open_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "open"},
        headers=headers,
    )
    assert open_rows.status_code == 200, open_rows.text
    row = open_rows.json()[0]
    assert row["id"] == applications[0].id
    assert row["role_id"] == sister["id"]
    assert row["operational_role_id"] == source.id
    assert row["source_role_score"] == 72.0
    assert row["taali_score"] is None
    assert row["score_status"] == "pending"

    rejected_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "rejected"},
        headers=headers,
    )
    assert rejected_rows.status_code == 200
    assert rejected_rows.json()[0]["id"] == applications[1].id
    assert rejected_rows.json()[0]["score_status"] == "unscorable"


def test_sister_role_cannot_enable_candidate_automation(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, _ = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="Alternate AI Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A complete alternate specification for an AI engineer with strong platform depth.",
    )
    db.add(sister)
    db.commit()

    response = client.patch(
        f"/api/v1/roles/{sister.id}",
        json={"agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
        headers=headers,
    )
    assert response.status_code == 409
    assert "score-only" in response.json()["detail"]


def test_preview_rejects_non_workable_source(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    role = Role(organization_id=user.organization_id, name="Native role")
    db.add(role)
    db.commit()

    response = client.get(
        f"/api/v1/roles/{role.id}/sisters/preview", headers=headers
    )
    assert response.status_code == 409
    assert "Workable-linked" in response.json()["detail"]


def test_sister_evaluation_task_persists_holistic_score(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A detailed alternate AI engineer specification focused on RAG evaluation and reliability.",
    )
    db.add(sister)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=user.organization_id,
        role_id=sister.id,
        source_application_id=applications[0].id,
        status="pending",
        spec_fingerprint="spec",
    )
    db.add(evaluation)
    db.commit()

    output = SimpleNamespace(
        scoring_status=SimpleNamespace(value="ok"),
        role_fit_score=86.0,
        summary="Strong RAG evaluation evidence.",
        error_reason=None,
        model_version="test-model",
        prompt_version="test-prompt",
        trace_id="trace-1",
        cache_hit=False,
        model_dump=lambda **_: {"role_fit_score": 86.0, "summary": "Strong RAG evaluation evidence."},
    )
    with (
        patch("app.cv_matching.holistic.run_holistic_match", return_value=output),
        patch("app.services.claude_client_resolver.get_metered_client", return_value=object()),
        patch("app.services.workable_context_service.format_workable_context", return_value=""),
    ):
        from app.tasks.sister_role_tasks import score_sister_evaluation

        result = score_sister_evaluation.run(evaluation.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["status"] == "done"
    assert saved.status == "done"
    assert saved.role_fit_score == 86.0
    assert saved.details["role_fit_score"] == 86.0


def test_new_or_changed_workable_cv_queues_only_its_sister_evaluation(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Live sister",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A detailed sister specification requiring RAG, evaluation, and reliable Python services.",
    )
    db.add(sister)
    db.commit()

    from app.services.sister_role_service import ensure_application_sister_evaluations

    queued = ensure_application_sister_evaluations(
        db, applications[0], sister_roles=[sister]
    )
    db.commit()
    assert len(queued) == 1

    evaluation = db.get(SisterRoleEvaluation, queued[0])
    evaluation.status = "done"
    evaluation.role_fit_score = 84.0
    db.commit()
    assert ensure_application_sister_evaluations(
        db, applications[0], sister_roles=[sister]
    ) == []

    applications[0].cv_text = f"{applications[0].cv_text} Added agent evaluation ownership."
    changed = ensure_application_sister_evaluations(
        db, applications[0], sister_roles=[sister]
    )
    assert changed == [evaluation.id]
    assert evaluation.status == "pending"
    assert evaluation.role_fit_score is None
    assert evaluation.history[-1]["role_fit_score"] == 84.0


def test_workable_candidate_without_cv_becomes_scorable_when_cv_arrives(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Deferred CV",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A detailed sister specification requiring production AI systems and evaluation depth.",
    )
    db.add(sister)
    db.commit()

    from app.services.sister_role_service import ensure_application_sister_evaluations

    application = applications[1]
    assert ensure_application_sister_evaluations(
        db, application, sister_roles=[sister]
    ) == []
    db.commit()
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.role_id == sister.id,
            SisterRoleEvaluation.source_application_id == application.id,
        )
        .one()
    )
    assert evaluation.status == "unscorable"
    assert evaluation.error_message == "No CV text available"

    application.cv_text = "Python AI engineer with production RAG and model evaluation experience."
    queued = ensure_application_sister_evaluations(
        db, application, sister_roles=[sister]
    )
    assert queued == [evaluation.id]
    assert evaluation.status == "pending"
    assert evaluation.error_message is None


def test_sister_application_api_ranks_by_alternate_score(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    # Make both source applications active so one response can prove ranking.
    applications[1].application_outcome = "open"
    sister = Role(
        organization_id=user.organization_id,
        name="AI Engineer · Ranked sister",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text="A full alternate specification emphasizing AI reliability, RAG, and evaluation systems.",
    )
    db.add(sister)
    db.flush()
    db.add_all([
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=sister.id,
            source_application_id=applications[0].id,
            status="done",
            spec_fingerprint="spec",
            role_fit_score=55.0,
        ),
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=sister.id,
            source_application_id=applications[1].id,
            status="done",
            spec_fingerprint="spec",
            role_fit_score=93.0,
        ),
    ])
    db.commit()

    response = client.get(
        f"/api/v1/roles/{sister.id}/applications",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["id"] for row in rows] == [applications[1].id, applications[0].id]
    assert [row["taali_score"] for row in rows] == [93.0, 55.0]

    pipeline = client.get(
        f"/api/v1/roles/{sister.id}/pipeline",
        params={"sort_by": "taali_score", "sort_order": "desc"},
        headers=headers,
    )
    assert pipeline.status_code == 200, pipeline.text
    assert [row["id"] for row in pipeline.json()["items"]] == [
        applications[1].id, applications[0].id,
    ]

    detail = client.get(
        f"/api/v1/applications/{applications[1].id}",
        params={"view_role_id": sister.id},
        headers=headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["role_id"] == sister.id
    assert detail.json()["operational_role_id"] == source.id
    assert detail.json()["taali_score"] == 93.0
