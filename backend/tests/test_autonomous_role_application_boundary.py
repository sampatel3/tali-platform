"""Ground-truth matrix for autonomous logical-role read/score authority."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent_runtime import tool_registry
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.logical_role_application_authority import (
    LogicalRoleApplicationAuthorizationError,
    authorize_logical_role_application,
)
from app.services.related_role_rescreen_service import (
    RelatedRoleRescreenUnavailableError,
    rescreen_related_role_candidates,
)


def _application(
    db,
    *,
    org: Organization,
    role: Role,
    candidate: Candidate,
    score: float,
) -> CandidateApplication:
    application = CandidateApplication(
        organization_id=int(org.id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source="manual",
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        cv_text=candidate.cv_text,
        cv_match_score=score / 100,
        cv_match_details={"summary": f"{role.name} owner-only score"},
        taali_score_cache_100=score,
        role_fit_score_cache_100=score,
    )
    db.add(application)
    db.flush()
    return application


def _evaluation(
    db,
    *,
    role: Role,
    candidate: Candidate,
    source: CandidateApplication,
    score: float,
) -> SisterRoleEvaluation:
    evaluation = SisterRoleEvaluation(
        organization_id=int(role.organization_id),
        role_id=int(role.id),
        candidate_id=int(candidate.id),
        source_application_id=int(source.id),
        ats_application_id=int(source.id),
        status="done",
        pipeline_stage="review",
        pipeline_stage_source="system",
        application_outcome="open",
        membership_source="initial_snapshot",
        spec_fingerprint=f"related-spec-{role.id}",
        cv_fingerprint=f"candidate-cv-{candidate.id}",
        role_fit_score=score,
        summary=f"Role-local score {score}",
        details={"summary": f"Role-local score {score}"},
        model_version="related-test-model",
        prompt_version="related-test-prompt",
    )
    db.add(evaluation)
    db.flush()
    return evaluation


def _world(db):
    org = Organization(
        name="Autonomous role boundary org",
        slug=f"autonomous-role-boundary-{id(db)}",
    )
    db.add(org)
    db.flush()
    owner = Role(
        organization_id=int(org.id),
        name="Owner A",
        source="manual",
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Owner-only backend infrastructure specification.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    unrelated = Role(
        organization_id=int(org.id),
        name="Unrelated C",
        source="manual",
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Unrelated finance systems specification.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add_all([owner, unrelated])
    db.flush()
    related = Role(
        organization_id=int(org.id),
        name="Related B",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=int(owner.id),
        related_source_role_id=int(owner.id),
        job_status=JOB_STATUS_OPEN,
        job_spec_text="Independent production AI and agent engineering specification.",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(related)
    db.flush()

    shared = Candidate(
        organization_id=int(org.id),
        email=f"shared-{org.id}@example.test",
        full_name="Shared Candidate",
        position="AI Engineer",
        cv_text="Python, agent platforms, distributed systems, and production AI.",
        skills=["Python", "Agentforce"],
    )
    second = Candidate(
        organization_id=int(org.id),
        email=f"second-{org.id}@example.test",
        full_name="Second Related Candidate",
        position="ML Engineer",
        cv_text="Python, model serving, and production ML systems.",
    )
    owner_only = Candidate(
        organization_id=int(org.id),
        email=f"owner-only-{org.id}@example.test",
        full_name="Owner Only",
        cv_text="Owner role evidence.",
    )
    db.add_all([shared, second, owner_only])
    db.flush()

    owner_app = _application(
        db, org=org, role=owner, candidate=shared, score=91
    )
    second_owner_app = _application(
        db, org=org, role=owner, candidate=second, score=73
    )
    owner_only_app = _application(
        db, org=org, role=owner, candidate=owner_only, score=66
    )
    unrelated_app = _application(
        db, org=org, role=unrelated, candidate=shared, score=17
    )
    related_eval = _evaluation(
        db,
        role=related,
        candidate=shared,
        source=owner_app,
        score=42,
    )
    second_related_eval = _evaluation(
        db,
        role=related,
        candidate=second,
        source=second_owner_app,
        score=58,
    )
    db.commit()
    return SimpleNamespace(
        org=org,
        owner=owner,
        related=related,
        unrelated=unrelated,
        shared=shared,
        owner_app=owner_app,
        second_owner_app=second_owner_app,
        owner_only_app=owner_only_app,
        unrelated_app=unrelated_app,
        related_eval=related_eval,
        second_related_eval=second_related_eval,
    )


def _run(role: Role) -> SimpleNamespace:
    return SimpleNamespace(
        id=9001,
        role_id=int(role.id),
        organization_id=int(role.organization_id),
        decisions_emitted=0,
    )


def test_boundary_uses_logical_membership_and_bound_read_hides_other_roles(db):
    world = _world(db)

    owner_context = authorize_logical_role_application(
        db, role=world.owner, application_id=int(world.owner_app.id)
    )
    related_context = authorize_logical_role_application(
        db, role=world.related, application_id=int(world.owner_app.id)
    )
    assert owner_context.is_related is False
    assert related_context.is_related is True
    assert int(related_context.related_evaluation.id) == int(world.related_eval.id)
    assert int(related_context.ats_application.id) == int(world.owner_app.id)

    for foreign_application in (world.owner_only_app, world.unrelated_app):
        with pytest.raises(LogicalRoleApplicationAuthorizationError):
            authorize_logical_role_application(
                db,
                role=world.related,
                application_id=int(foreign_application.id),
            )

    payload = tool_registry.dispatch(
        "get_candidate",
        {"candidate_id": int(world.shared.id)},
        db=db,
        agent_run=_run(world.related),
        role=world.related,
    )
    assert payload["candidate_id"] == int(world.shared.id)
    assert len(payload["applications"]) == 1
    logical_application = payload["applications"][0]
    assert logical_application["application_id"] == int(world.owner_app.id)
    assert logical_application["role_id"] == int(world.related.id)
    assert logical_application["role_name"] == "Related B"
    assert logical_application["taali_score"] == 42
    serialized = repr(payload)
    assert "Owner A" not in serialized
    assert "Unrelated C" not in serialized
    assert all(
        application["taali_score"] not in {91, 17}
        for application in payload["applications"]
    )


def test_related_score_uses_local_evaluation_and_preserves_owner_score(db):
    world = _world(db)

    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ) as dispatch, patch(
        "app.actions.score_cv.run"
    ) as owner_score_action:
        result = tool_registry.dispatch(
            "score_cv",
            {"application_id": int(world.owner_app.id), "force": True},
            db=db,
            agent_run=_run(world.related),
            role=world.related,
        )

    assert result["status"] == "queued"
    assert result["role_id"] == int(world.related.id)
    assert result["scoring_scope"] == "related_role_evaluation"
    assert result["evaluation_ids"] == [int(world.related_eval.id)]
    owner_score_action.assert_not_called()
    dispatch.assert_called_once_with(
        db,
        evaluation_id=int(world.related_eval.id),
    )

    db.refresh(world.related_eval)
    db.refresh(world.second_related_eval)
    db.refresh(world.owner_app)
    db.refresh(world.unrelated_app)
    assert world.related_eval.status == "pending"
    assert world.related_eval.role_fit_score is None
    assert world.related_eval.history[-1]["role_fit_score"] == 42
    assert world.second_related_eval.role_fit_score == 58
    assert world.owner_app.taali_score_cache_100 == 91
    assert world.owner_app.cv_match_score == pytest.approx(0.91)
    assert world.unrelated_app.taali_score_cache_100 == 17


@pytest.mark.parametrize("acting_role", ["owner", "related"])
def test_mixed_batch_is_rejected_before_any_score_or_rescreen(acting_role, db):
    world = _world(db)
    role = getattr(world, acting_role)
    valid_id = int(world.owner_app.id)
    invalid_id = int(world.unrelated_app.id)

    with patch("app.actions.score_cv.run") as ordinary_score, patch(
        "app.services.related_role_rescreen_service.rescreen_related_role_candidates"
    ) as related_rescreen:
        result = tool_registry.dispatch(
            "batch_score_cv",
            {"application_ids": [valid_id, invalid_id], "force": True},
            db=db,
            agent_run=_run(role),
            role=role,
        )

    assert result["status"] == "wrong_role"
    assert result["invalid_application_ids"] == [invalid_id]
    assert result["results"] == []
    ordinary_score.assert_not_called()
    related_rescreen.assert_not_called()
    db.refresh(world.related_eval)
    db.refresh(world.owner_app)
    assert world.related_eval.role_fit_score == 42
    assert world.owner_app.taali_score_cache_100 == 91


def test_related_rescreen_rechecks_batch_atomically_under_lock(db):
    world = _world(db)
    with patch("app.tasks.sister_role_tasks.dispatch_sister_evaluation") as dispatch:
        with pytest.raises(RelatedRoleRescreenUnavailableError):
            rescreen_related_role_candidates(
                db,
                world.related,
                reason="autonomous_atomic_batch_test",
                application_ids=[
                    int(world.owner_app.id),
                    int(world.unrelated_app.id),
                ],
                require_all_memberships=True,
            )

    dispatch.assert_not_called()
    db.refresh(world.related_eval)
    assert world.related_eval.status == "done"
    assert world.related_eval.role_fit_score == 42


def test_standard_score_passes_bound_role_and_rejects_other_role(db):
    world = _world(db)
    fake_job = SimpleNamespace(id=77, status="pending")
    with patch("app.actions.score_cv.run", return_value=fake_job) as score_action:
        valid = tool_registry.dispatch(
            "score_cv",
            {"application_id": int(world.owner_app.id)},
            db=db,
            agent_run=_run(world.owner),
            role=world.owner,
        )
        invalid = tool_registry.dispatch(
            "score_cv",
            {"application_id": int(world.unrelated_app.id)},
            db=db,
            agent_run=_run(world.owner),
            role=world.owner,
        )

    assert valid == {"job_id": 77, "status": "pending"}
    assert invalid["status"] == "wrong_role"
    score_action.assert_called_once()
    assert score_action.call_args.kwargs["role_id"] == int(world.owner.id)
    assert score_action.call_args.kwargs["application_id"] == int(world.owner_app.id)


def test_forced_policy_refresh_routes_related_role_to_local_evaluation(db):
    world = _world(db)
    with patch(
        "app.tasks.sister_role_tasks.dispatch_sister_evaluation",
        return_value={"status": "queued"},
    ) as dispatch, patch(
        "app.services.cv_score_orchestrator.enqueue_score"
    ) as owner_enqueue:
        result = tool_registry._queue_forced_policy_score_refresh(
            db,
            role=world.related,
            application_id=int(world.owner_app.id),
        )

    assert result["decision_type"] == "score_refresh_queued"
    assert result["status"] == "queued"
    assert result["scoring_scope"] == "related_role_evaluation"
    assert result["evaluation_ids"] == [int(world.related_eval.id)]
    owner_enqueue.assert_not_called()
    dispatch.assert_called_once_with(
        db,
        evaluation_id=int(world.related_eval.id),
    )
    db.refresh(world.related_eval)
    db.refresh(world.owner_app)
    assert world.related_eval.role_fit_score is None
    assert world.owner_app.taali_score_cache_100 == 91


def test_existing_related_score_attempt_is_reused_without_duplicate_dispatch(db):
    world = _world(db)
    world.related_eval.status = "pending"
    world.related_eval.dispatch_attempted_at = None
    db.commit()

    with patch(
        "app.services.related_role_rescreen_service.rescreen_related_role_candidates"
    ) as rescreen, patch(
        "app.services.cv_score_orchestrator.enqueue_score"
    ) as owner_enqueue:
        direct = tool_registry.dispatch(
            "score_cv",
            {"application_id": int(world.owner_app.id)},
            db=db,
            agent_run=_run(world.related),
            role=world.related,
        )
        forced = tool_registry._queue_forced_policy_score_refresh(
            db,
            role=world.related,
            application_id=int(world.owner_app.id),
        )

    assert direct["status"] == "pending"
    assert forced["decision_type"] == "score_refresh_pending"
    assert forced["status"] == "pending"
    rescreen.assert_not_called()
    owner_enqueue.assert_not_called()


def test_completed_related_score_is_idempotent_and_preserves_live_decision(db):
    world = _world(db)
    decision = AgentDecision(
        organization_id=int(world.org.id),
        role_id=int(world.related.id),
        application_id=int(world.owner_app.id),
        decision_type="advance_to_interview",
        recommendation="advance",
        status="pending",
        reasoning="Grounded role-local recommendation.",
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key=f"fresh-related-score-{world.related.id}",
    )
    db.add(decision)
    db.commit()
    score_before = world.related_eval.role_fit_score
    history_before = world.related_eval.history

    with patch(
        "app.services.related_role_rescreen_service.rescreen_related_role_candidates"
    ) as rescreen:
        result = tool_registry.dispatch(
            "score_cv",
            {"application_id": int(world.owner_app.id), "force": False},
            db=db,
            agent_run=_run(world.related),
            role=world.related,
        )

    assert result["status"] == "done"
    assert result["reason"] == "A current role-local score already exists."
    rescreen.assert_not_called()
    db.refresh(world.related_eval)
    db.refresh(decision)
    assert world.related_eval.role_fit_score == score_before
    assert world.related_eval.history == history_before
    assert decision.status == "pending"


def test_completed_related_batch_scores_are_not_reset_without_force(db):
    world = _world(db)
    before = {
        int(world.related_eval.id): world.related_eval.role_fit_score,
        int(world.second_related_eval.id): world.second_related_eval.role_fit_score,
    }

    with patch(
        "app.services.related_role_rescreen_service.rescreen_related_role_candidates"
    ) as rescreen:
        result = tool_registry.dispatch(
            "batch_score_cv",
            {
                "application_ids": [
                    int(world.owner_app.id),
                    int(world.second_owner_app.id),
                ],
                "force": False,
            },
            db=db,
            agent_run=_run(world.related),
            role=world.related,
        )

    assert result["status"] == "completed"
    assert [item["status"] for item in result["results"]] == ["done", "done"]
    rescreen.assert_not_called()
    db.refresh(world.related_eval)
    db.refresh(world.second_related_eval)
    assert world.related_eval.role_fit_score == before[int(world.related_eval.id)]
    assert (
        world.second_related_eval.role_fit_score
        == before[int(world.second_related_eval.id)]
    )
