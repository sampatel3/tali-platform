"""The role agent can apply a pasted job spec + re-derive its criteria (opt-in).

update_job_spec replaces role.job_spec_text, re-derives the spec criteria
(sync_derived_criteria — mocked here; its own derivation is tested separately),
returns the criteria diff + a re-screen cost estimate, and does NOT auto-spend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.agent_chat.constraints import rescreen_role, update_job_spec
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, ROLE_KIND_STANDARD, Role
from app.models.role_criterion import CRITERION_SOURCE_DERIVED, RoleCriterion
from app.models.sister_role_evaluation import SisterRoleEvaluation


def _org(db) -> Organization:
    org = Organization(name="JS Org", slug=f"js-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _role(db, org) -> Role:
    role = Role(organization_id=org.id, name="AI Engineer", source="manual",
                score_threshold=70, agentic_mode_enabled=True, job_spec_text="old spec")
    db.add(role)
    db.flush()
    return role


def _crit(db, role, text, *, ordering=0):
    c = RoleCriterion(
        role_id=role.id, text=text, bucket="must",
        source=CRITERION_SOURCE_DERIVED, ordering=ordering, weight=1.0,
    )
    db.add(c)
    db.flush()
    return c


def test_update_job_spec_applies_rederives_and_estimates(db):
    org = _org(db)
    role = _role(db, org)
    old = _crit(db, role, "Python", ordering=0)

    # Simulate the re-derive: drop the old derived chip, add a new one.
    def _sync(db_, role_):
        old.deleted_at = datetime.now(timezone.utc)
        _crit(db_, role_, "Kubernetes", ordering=1)

    new_jd = "Senior AI Engineer. Requirements: 5y Kubernetes, distributed systems, LLM serving." * 2
    with patch("app.services.role_criteria_service.sync_derived_criteria", side_effect=_sync):
        res = update_job_spec(db, role, job_spec_text=new_jd)

    assert res["type"] == "job_spec_change" and res["applied"] is True
    assert "Kubernetes" in res["added"]
    assert "Python" in res["removed"]
    assert "count" in res["would_rescreen"] and "est_cost_usd" in res["would_rescreen"]
    assert res["scores_invalidated"] == 0
    assert res["rescore_dispatch_approved"] is False
    db.refresh(role)
    assert role.job_spec_text.startswith("Senior AI Engineer")
    assert role.description == role.job_spec_text
    assert role.job_spec_manually_edited_at is not None


def test_update_job_spec_rejects_too_short(db):
    org = _org(db)
    role = _role(db, org)
    res = update_job_spec(db, role, job_spec_text="too short")
    assert res.get("ok") is False
    db.refresh(role)
    assert role.job_spec_text == "old spec"  # unchanged


def test_related_role_spec_edit_waits_for_separate_paid_work_approval(db):
    org = _org(db)
    owner = Role(
        organization_id=org.id,
        name="AI Engineer",
        source="workable",
        role_kind=ROLE_KIND_STANDARD,
        job_spec_text="Canonical owner specification.",
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org.id,
        name="AI Engineer · Reliability",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_spec_text="Old related-role specification with enough detail.",
    )
    candidate = Candidate(
        organization_id=org.id,
        full_name="Candidate",
        email=f"related-spec-{id(db)}@example.com",
        cv_text="Production Python, RAG evaluation, and reliability engineering.",
    )
    db.add_all([related, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="workable",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related.id,
        source_application_id=application.id,
        status="done",
        spec_fingerprint="old-fingerprint",
        cv_fingerprint="old-cv-fingerprint",
        role_fit_score=84.0,
        summary="Strong prior fit",
    )
    db.add(evaluation)
    db.commit()

    replacement = (
        "Own production AI reliability, distributed inference, incident response, "
        "RAG evaluation, observability, and high-quality Python services."
    )
    with (
        patch("app.services.role_criteria_service.sync_derived_criteria"),
        patch(
            "app.services.task_provisioning_service."
            "request_assessment_task_provisioning"
        ) as provision_assessment,
        patch("app.services.cv_score_orchestrator.mark_role_scores_stale") as generic_stale,
        patch("app.tasks.sister_role_tasks.score_sister_role.apply_async") as dispatch,
    ):
        result = update_job_spec(db, related, job_spec_text=replacement)
        db.flush()

    assert result["scores_invalidated"] == 1
    assert result["would_rescreen"] == {"count": 1, "est_cost_usd": 0.08}
    assert result["rescore_dispatch_approved"] is False
    assert evaluation.status == "stale"
    assert evaluation.role_fit_score == 84.0
    assert evaluation.history[-1]["role_fit_score"] == 84.0
    assert evaluation.last_error_code == "spec_changed_awaiting_rescore_approval"
    from app.domains.assessments_runtime.sister_role_routes import _scoring_status

    scoring_status = _scoring_status(db, related)
    assert scoring_status.status == "stale"
    assert scoring_status.waiting_reason == "rescore_approval_required"
    assert scoring_status.progress_percent == 0.0
    assert scoring_status.estimated_rescore_cost_usd == 0.08
    provision_assessment.assert_not_called()
    generic_stale.assert_not_called()
    dispatch.assert_not_called()


def test_related_role_confirmed_rescreen_dispatches_only_after_commit(db):
    org = _org(db)
    owner = Role(
        organization_id=org.id,
        name="Owner",
        source="workable",
        role_kind=ROLE_KIND_STANDARD,
    )
    related = Role(
        organization_id=org.id,
        name="Owner · Related",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role=owner,
        job_spec_text=(
            "Production AI reliability, distributed inference, RAG evaluation, "
            "observability, and Python services."
        ),
    )
    candidate = Candidate(
        organization_id=org.id,
        full_name="Candidate",
        email=f"related-rescore-{id(db)}@example.com",
        cv_text="Production AI and Python reliability experience.",
    )
    db.add_all([owner, related, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="workable",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()
    evaluation = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related.id,
        source_application_id=application.id,
        status="stale",
        spec_fingerprint="current-spec",
        cv_fingerprint="current-cv",
        last_error_code="spec_changed_awaiting_rescore_approval",
    )
    db.add(evaluation)
    # This represents a candidate scored after the edit preview. It must not be
    # swept into a broader paid run when the recruiter confirms the stale scope.
    second_candidate = Candidate(
        organization_id=org.id,
        full_name="New Candidate",
        email=f"related-current-{id(db)}@example.com",
        cv_text="Newly arrived production AI engineer.",
    )
    db.add(second_candidate)
    db.flush()
    second_application = CandidateApplication(
        organization_id=org.id,
        candidate_id=second_candidate.id,
        role_id=owner.id,
        source="workable",
        application_outcome="open",
        cv_text=second_candidate.cv_text,
    )
    db.add(second_application)
    db.flush()
    already_current = SisterRoleEvaluation(
        organization_id=org.id,
        role_id=related.id,
        source_application_id=second_application.id,
        status="done",
        spec_fingerprint="current-spec",
        cv_fingerprint="new-candidate-cv",
        role_fit_score=91.0,
    )
    db.add(already_current)
    db.commit()

    with patch(
        "app.tasks.sister_role_tasks.score_sister_role.apply_async"
    ) as dispatch:
        result = rescreen_role(db, related)
        assert result == {
            "type": "related_role_rescore_started",
            "rescreening_count": 1,
            "est_cost_usd": 0.08,
            "scoped": False,
        }
        assert evaluation.status == "pending"
        assert already_current.status == "done"
        assert already_current.role_fit_score == 91.0
        dispatch.assert_not_called()
        db.commit()
        dispatch.assert_called_once_with(args=[related.id], queue="scoring")
