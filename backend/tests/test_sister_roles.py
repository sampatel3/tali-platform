from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.job_hiring_team import TEAM_ROLE_HIRING_MANAGER, JobHiringTeam
from app.models.job_page import JobPage
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.role_brief import BRIEF_STATUS_APPLIED, RoleBrief
from app.models.role_criterion import RoleCriterion
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


def _scorable_evaluation(db, *, organization_id: int):
    source, applications = _source_role_with_candidates(
        db, organization_id=organization_id
    )
    source.agentic_mode_enabled = True
    source.monthly_usd_budget_cents = 5000
    sister = Role(
        organization_id=organization_id,
        name="AI Engineer · Durable evaluation",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=source.id,
        job_spec_text=(
            "A detailed related-role specification requiring production Python, "
            "RAG evaluation, distributed systems, and operational reliability."
        ),
    )
    db.add(sister)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=organization_id,
        role_id=sister.id,
        source_application_id=applications[0].id,
        status="pending",
        spec_fingerprint="initial",
    )
    db.add(evaluation)
    db.commit()
    return source, evaluation


def _match_output(*, ok: bool):
    return SimpleNamespace(
        scoring_status=SimpleNamespace(value="ok" if ok else "failed"),
        role_fit_score=88.0 if ok else None,
        summary="Strong production evidence." if ok else None,
        error_reason=None if ok else "claude_call_failed: temporary upstream outage",
        model_version="test-model",
        prompt_version="test-prompt",
        trace_id="trace-durable",
        cache_hit=False,
        model_dump=lambda **_: {"role_fit_score": 88.0},
    )


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
        "app.services.related_role_service.score_sister_role.apply_async"
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
    fallback_membership = (
        db.query(JobHiringTeam)
        .filter(
            JobHiringTeam.role_id == sister["id"],
            JobHiringTeam.user_id == user.id,
        )
        .one()
    )
    assert fallback_membership.team_role == TEAM_ROLE_HIRING_MANAGER

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

    evaluations[0].status = "retry_wait"
    evaluations[0].last_error_code = "authority_blocked"
    db.commit()
    waiting_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "open"},
        headers=headers,
    )
    assert waiting_rows.status_code == 200, waiting_rows.text
    assert waiting_rows.json()[0]["score_status"] == "retry_wait"

    rejected_rows = client.get(
        f"/api/v1/roles/{sister['id']}/applications",
        params={"application_outcome": "rejected"},
        headers=headers,
    )
    assert rejected_rows.status_code == 200
    assert rejected_rows.json()[0]["id"] == applications[1].id
    assert rejected_rows.json()[0]["score_status"] == "unscorable"


def test_related_role_uses_cloned_requisition_chat_then_creates_coupled_scoring_role(
    client, db
):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, _ = _source_role_with_candidates(
        db, organization_id=user.organization_id
    )

    created_draft = client.post(
        "/api/v1/requisitions",
        json={"source_role_id": source.id},
        headers=headers,
    )
    assert created_draft.status_code == 201, created_draft.text
    draft = created_draft.json()
    assert draft["brief_kind"] == "related_role"
    assert draft["source_role_id"] == source.id
    assert draft["source_role"] == {
        "role_id": source.id,
        "name": source.name,
        "ats_provider": "workable",
        "version": int(source.version or 1),
    }
    assert draft["jd_override"] == source.job_spec_text
    assert draft["related_role_preview"]["candidates_total"] == 2
    assert draft["related_role_preview"]["candidates_with_cv"] == 1
    assert "Tell me what should change" in draft["messages"][0]["content"]

    completed = client.patch(
        f"/api/v1/requisitions/{draft['id']}",
        json={
            "title": "AI Engineer · Evaluation Platform",
            "seniority": "senior",
            "summary": "Own reliable evaluation systems for production AI products.",
            "workplace_type": "remote",
            "employment_type": "full_time",
            "openings": 1,
            "must_haves": ["Python", "Production AI evaluation"],
            "success_profile": "Ships measurable model-quality improvements end-to-end.",
            "custom_fields": {
                "domain": "Artificial intelligence",
                "urgency": "high",
                "responsibilities": [
                    "Design production evaluation systems",
                    "Own reliability and observability",
                ],
            },
        },
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["gaps"] == []

    updated_spec = (
        "Senior AI engineer responsible for production evaluation platforms, "
        "Python services, RAG quality measurement, model observability, and "
        "reliable delivery across distributed systems."
    )
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        published = client.post(
            f"/api/v1/requisitions/{draft['id']}/publish",
            json={"jd_markdown": updated_spec},
            headers=headers,
        )

    assert published.status_code == 200, published.text
    receipt = published.json()
    assert receipt["related_role"] is True
    assert receipt["source_role_id"] == source.id
    assert receipt["status"] == BRIEF_STATUS_APPLIED
    assert receipt["evaluation_counts"] == {
        "total": 2,
        "pending": 1,
        "unscorable": 1,
    }
    dispatch.assert_called_once()

    related = db.get(Role, receipt["role_id"])
    brief = db.get(RoleBrief, draft["id"])
    assert related.role_kind == ROLE_KIND_SISTER
    assert related.ats_owner_role_id == source.id
    assert related.job_spec_text == updated_spec
    assert related.name == "AI Engineer · Evaluation Platform"
    assert brief.role_id == related.id
    assert brief.status == BRIEF_STATUS_APPLIED
    assert {
        criterion.text
        for criterion in db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == related.id)
        .all()
    }.issuperset({"Python", "Production AI evaluation"})
    assert db.query(JobPage).filter(JobPage.brief_id == brief.id).count() == 0


def test_create_related_role_from_bullhorn_uses_same_shared_roster(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source = Role(
        organization_id=user.organization_id,
        name="Bullhorn Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="BH-9001",
        bullhorn_job_data={"id": 9001, "isOpen": True},
        job_spec_text=(
            "Original platform engineer role requiring Python, distributed "
            "systems, production ownership, and observability."
        ),
    )
    db.add(source)
    db.flush()
    candidate = Candidate(
        organization_id=user.organization_id,
        email="bullhorn-related@example.com",
        full_name="Bullhorn Candidate",
        cv_text="Python platform engineer with production distributed systems experience.",
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=user.organization_id,
            candidate_id=candidate.id,
            role_id=source.id,
            source="bullhorn",
            bullhorn_job_submission_id="BH-SUB-1",
            cv_text=candidate.cv_text,
            application_outcome="open",
        )
    )
    db.commit()

    preview = client.get(
        f"/api/v1/roles/{source.id}/sisters/preview",
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["source_ats_provider"] == "bullhorn"

    updated_spec = (
        "Updated platform role requiring Python, distributed systems, reliable "
        "data services, production observability, and technical leadership."
    )
    with patch(
        "app.services.related_role_service.score_sister_role.apply_async"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{source.id}/sisters",
            json={"name": "Platform Engineer · Data", "job_spec_text": updated_spec},
            headers=headers,
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"]["ats_provider"] == "bullhorn"
    assert body["role"]["external_job_id"] == "BH-9001"
    assert body["role"]["applications_count"] == 1
    assert body["evaluation_counts"] == {"total": 1, "pending": 1, "unscorable": 0}
    dispatch.assert_called_once()


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
        json={"expected_version": int(sister.version or 1), "agentic_mode_enabled": True, "monthly_usd_budget_cents": 5000},
        headers=headers,
    )
    assert response.status_code == 409
    assert "score-only" in response.json()["detail"]


def test_preview_rejects_non_ats_source(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    role = Role(organization_id=user.organization_id, name="Native role")
    db.add(role)
    db.commit()

    response = client.get(
        f"/api/v1/roles/{role.id}/sisters/preview", headers=headers
    )
    assert response.status_code == 409
    assert "Workable- or Bullhorn-linked" in response.json()["detail"]


def test_sister_evaluation_task_persists_holistic_score(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, applications = _source_role_with_candidates(db, organization_id=user.organization_id)
    source.agentic_mode_enabled = True
    source.monthly_usd_budget_cents = 5000
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
        patch(
            "app.cv_matching.holistic.run_holistic_match", return_value=output
        ) as holistic_match,
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
    metering = holistic_match.call_args.kwargs["metering_context"]
    assert metering["organization_id"] == user.organization_id
    assert metering["role_id"] == source.id
    assert metering["entity_id"] == f"sister_evaluation:{evaluation.id}"
    assert metering["role_id"] != sister.id


def test_related_role_transient_failures_continue_after_fast_retry_budget(
    client, db
):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_evaluation,
    )

    outputs = [_match_output(ok=False) for _ in range(4)] + [
        _match_output(ok=True)
    ]
    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=outputs,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        first = score_sister_evaluation.run(evaluation.id)
        assert first["status"] == "retry_wait"
        for _ in range(4):
            db.expire_all()
            saved = db.get(SisterRoleEvaluation, evaluation.id)
            assert saved.status == "retry_wait"
            saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            recover_sister_role_evaluations.run()

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "done"
    assert saved.attempts == 5
    assert saved.role_fit_score == 88.0


def test_related_role_hard_admission_uses_source_role_budget(
    client, db, monkeypatch
):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    source, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    # One cent is deliberately below the provider hold used by the fake
    # scoring boundary. The score-only projection has no cap, so this test
    # fails if metering ever regresses back to evaluation.role_id.
    source.monthly_usd_budget_cents = 1
    db.commit()

    from app.services.pricing_service import Feature
    from app.services.provider_usage_admission import reserve_provider_usage
    from app.tasks.sister_role_tasks import score_sister_evaluation

    monkeypatch.setattr(
        "app.platform.config.settings.USAGE_METER_LIVE", False
    )

    def _admitted_match(*_args, metering_context, **_kwargs):
        reserve_provider_usage(
            organization_id=int(metering_context["organization_id"]),
            role_id=int(metering_context["role_id"]),
            feature=Feature.SCORE,
            trace_id="related-role-source-budget-test",
            entity_id=str(metering_context["entity_id"]),
            amount=20_000,
        )
        return _match_output(ok=True)

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            side_effect=_admitted_match,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = score_sister_evaluation.run(evaluation.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["status"] == "retry_wait"
    assert result["error_code"] == "provider_exception"
    assert saved.status == "retry_wait"
    assert saved.attempts == 1
    assert saved.role_fit_score is None


def test_related_role_pause_holds_then_recovers_without_paid_work(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).first()
    source, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    source.agent_paused_at = datetime.now(timezone.utc)
    db.commit()

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_evaluation,
    )

    with patch("app.cv_matching.holistic.run_holistic_match") as paid_call:
        held = score_sister_evaluation.run(evaluation.id)
    assert held["status"] == "authority_blocked"
    paid_call.assert_not_called()
    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "retry_wait"
    assert saved.attempts == 0

    status_response = client.get(
        f"/api/v1/roles/{evaluation.role_id}/sister-scoring-status",
        headers=headers,
    )
    assert status_response.status_code == 200, status_response.text
    status_body = status_response.json()
    assert status_body["status"] == "waiting"
    assert status_body["waiting_reason"] == "agent_paused"
    assert status_body["scoreable_total"] == 1
    assert status_body["scored"] == 0
    assert status_body["progress_percent"] == 0

    source = db.get(Role, source.id)
    source.agent_paused_at = None
    saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            return_value=_match_output(ok=True),
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        recover_sister_role_evaluations.run()

    db.expire_all()
    assert db.get(SisterRoleEvaluation, evaluation.id).status == "done"


def test_related_role_stale_worker_and_secret_broker_error_self_recover(
    client, db, caplog
):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    evaluation.status = "running"
    evaluation.attempts = 1
    evaluation.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()

    from app.tasks.sister_role_tasks import (
        recover_sister_role_evaluations,
        score_sister_evaluation,
    )

    secret = "redis://:SECRET@host"
    with (
        caplog.at_level("ERROR", logger="taali.tasks.sister_roles"),
        patch.object(
            score_sister_evaluation,
            "apply_async",
            side_effect=RuntimeError(secret),
        ),
    ):
        result = recover_sister_role_evaluations.run()

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert saved.status == "retry_wait"
    assert saved.last_error_code == "queue_unavailable_runtimeerror"
    assert secret not in str(result)
    assert secret not in str(saved.error_message)
    assert secret not in caplog.text

    saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    published: list[int] = []
    with patch.object(
        score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: published.append(args[0]),
    ):
        recover_sister_role_evaluations.run()
    assert published == [evaluation.id]


def test_related_role_duplicate_delivery_does_not_repeat_paid_call(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            return_value=_match_output(ok=True),
        ) as paid_call,
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        assert score_sister_evaluation.run(evaluation.id)["status"] == "done"
        assert score_sister_evaluation.run(evaluation.id)["status"] == "skipped"
    paid_call.assert_called_once()


def test_related_role_only_deterministic_provider_failure_is_terminal(client, db):
    headers, email = auth_headers(client)
    del headers
    user = db.query(User).filter(User.email == email).first()
    _, evaluation = _scorable_evaluation(
        db, organization_id=user.organization_id
    )
    failed = _match_output(ok=False)
    failed.error_reason = (
        "holistic_score_failed: validation_failed_after_retry: redis://:SECRET@host"
    )

    from app.tasks.sister_role_tasks import score_sister_evaluation

    with (
        patch(
            "app.cv_matching.holistic.run_holistic_match",
            return_value=failed,
        ),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value="",
        ),
    ):
        result = score_sister_evaluation.run(evaluation.id)

    db.expire_all()
    saved = db.get(SisterRoleEvaluation, evaluation.id)
    assert result["status"] == "error"
    assert saved.status == "error"
    assert saved.last_error_code == "validation_failed"
    assert "SECRET" not in repr(result)
    assert "SECRET" not in str(saved.error_message)


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
