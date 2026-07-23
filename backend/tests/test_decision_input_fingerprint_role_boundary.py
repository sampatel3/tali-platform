"""Ground-truth tests for decision snapshots at the logical-role boundary."""

from __future__ import annotations

import hashlib

from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.decision_input_fingerprint import capture_input_fingerprint
from app.services.decision_role_context import related_decision_staleness


def _candidate_application(db, *, role: Role, suffix: str) -> CandidateApplication:
    candidate = Candidate(
        organization_id=int(role.organization_id),
        email=f"{suffix}@example.test",
        full_name=f"Fingerprint {suffix}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(role.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        pipeline_stage="review",
        application_outcome="open",
        cv_text=f"Current CV for {suffix}",
        pre_screen_score_100=11,
        assessment_score_cache_100=22,
        taali_score_cache_100=33,
        cv_match_score=44,
    )
    db.add(application)
    db.flush()
    return application


def test_direct_related_application_snapshots_membership_owned_inputs_and_staleness(db):
    organization = Organization(
        name="Direct related fingerprint",
        slug="direct-related-fingerprint",
    )
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=int(organization.id),
        name="Direct related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        score_threshold=70,
    )
    db.add(role)
    db.flush()
    application = _candidate_application(
        db,
        role=role,
        suffix="direct-related",
    )
    scored_cv_fingerprint = hashlib.sha256(
        b"CV used by the related-role score"
    ).hexdigest()
    evaluation = SisterRoleEvaluation(
        organization_id=int(organization.id),
        role_id=int(role.id),
        candidate_id=int(application.candidate_id),
        source_application_id=int(application.id),
        status="done",
        pipeline_stage="review",
        application_outcome="open",
        membership_source="direct",
        spec_fingerprint="direct-related-spec",
        cv_fingerprint=scored_cv_fingerprint,
        role_fit_score=91,
        details={"engine_version": "2.1.0"},
    )
    db.add(evaluation)
    db.flush()

    fingerprint, criteria_fingerprint, cv_fingerprint = capture_input_fingerprint(
        db,
        application_id=int(application.id),
        role_id=int(role.id),
    )

    assert fingerprint["pre_screen_score_at_emit"] is None
    assert fingerprint["assessment_score_at_emit"] is None
    assert fingerprint["cv_match_score_at_emit"] == 91
    assert fingerprint["taali_score_at_emit"] == 91
    assert fingerprint["pre_screen_cutoff_at_emit"] is None
    assert fingerprint["cv_fingerprint"] == scored_cv_fingerprint
    assert cv_fingerprint == scored_cv_fingerprint

    decision = AgentDecision(
        organization_id=int(organization.id),
        role_id=int(role.id),
        application_id=int(application.id),
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="Direct related-role fit is strong.",
        evidence={},
        model_version="test",
        prompt_version="test",
        input_fingerprint=fingerprint,
        criteria_fingerprint=criteria_fingerprint,
        cv_fingerprint=cv_fingerprint,
        idempotency_key="direct-related-fingerprint",
    )
    db.add(decision)
    db.commit()

    report = related_decision_staleness(
        db,
        decision,
        evaluation,
        application=application,
        role=role,
    )

    assert report.is_stale is True
    assert "cv_replaced" in report.reasons


def test_ordinary_application_snapshots_application_owned_inputs(db):
    organization = Organization(
        name="Ordinary fingerprint",
        slug="ordinary-fingerprint",
    )
    db.add(organization)
    db.flush()
    role = Role(
        organization_id=int(organization.id),
        name="Ordinary role",
        source="manual",
    )
    db.add(role)
    db.flush()
    application = _candidate_application(
        db,
        role=role,
        suffix="ordinary",
    )
    expected_cv_fingerprint = hashlib.sha256(
        application.cv_text.encode("utf-8")
    ).hexdigest()

    fingerprint, _criteria_fingerprint, cv_fingerprint = capture_input_fingerprint(
        db,
        application_id=int(application.id),
        role_id=int(role.id),
    )

    assert fingerprint["pre_screen_score_at_emit"] == 11
    assert fingerprint["assessment_score_at_emit"] == 22
    assert fingerprint["cv_match_score_at_emit"] == 44
    assert fingerprint["taali_score_at_emit"] == 33
    assert fingerprint["cv_fingerprint"] == expected_cv_fingerprint
    assert cv_fingerprint == expected_cv_fingerprint
