"""Grounded role-local labels for nightly policy fitting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.decision_policy import nightly_policy_fit
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation

from .conftest import make_org, make_role


def _decision(*, org_id: int, role_id: int, app: CandidateApplication, key: str):
    return AgentDecision(
        organization_id=org_id,
        role_id=role_id,
        application_id=app.id,
        candidate_id=app.candidate_id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="approved",
        reasoning="constructed policy-learning truth",
        evidence={"scores": {"cv_scoring": {"score": 0.75}}},
        confidence=0.75,
        model_version="offline-test",
        prompt_version="offline-test",
        idempotency_key=key,
    )


def test_nightly_labels_same_candidate_from_each_logical_roles_local_outcome(db):
    """ATS-owner outcomes cannot label an independent related decision."""

    org = make_org(db, name="Nightly Local Truth")
    owner = make_role(db, org=org, name="ATS owner")
    related = Role(
        organization_id=org.id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email="nightly-role-local@example.test",
        full_name="Role Local Candidate",
    )
    db.add(candidate)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="manual",
        status="applied",
        pipeline_stage="advanced",
        pipeline_stage_source="recruiter",
        application_outcome="hired",
        cv_match_score=92.0,
        role_fit_score_cache_100=92.0,
    )
    db.add(app)
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=org.id,
            role_id=related.id,
            candidate_id=candidate.id,
            source_application_id=app.id,
            ats_application_id=app.id,
            status="done",
            pipeline_stage="applied",
            application_outcome="rejected",
            membership_source="ground_truth_eval",
            spec_fingerprint="nightly-related-truth",
            role_fit_score=8.0,
        )
    )
    db.add_all(
        [
            _decision(
                org_id=org.id,
                role_id=owner.id,
                app=app,
                key="nightly-owner-decision",
            ),
            _decision(
                org_id=org.id,
                role_id=related.id,
                app=app,
                key="nightly-related-decision",
            ),
        ]
    )
    db.flush()

    with patch.object(nightly_policy_fit, "_collect_from_graphiti", return_value=[]):
        rows = nightly_policy_fit._collect_training_data(
            db,
            organization_id=org.id,
            since=datetime.now(timezone.utc) - timedelta(days=1),
        )

    by_role = {row.role_id: row for row in rows}
    assert by_role[owner.id].label == 1.0
    assert by_role[owner.id].weight == 1.0
    assert by_role[related.id].label == 0.0
    assert by_role[related.id].weight == 1.0


def test_nightly_labels_follow_role_candidate_after_membership_source_changes(db):
    """A replaced evidence row cannot disconnect a decision from its outcome."""

    org = make_org(db, name="Nightly Candidate Identity")
    owner = make_role(db, org=org, name="ATS owner")
    related = Role(
        organization_id=org.id,
        name="Independent related role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()
    candidate = Candidate(
        organization_id=org.id,
        email="nightly-source-change@example.test",
        full_name="Source Change Candidate",
    )
    db.add(candidate)
    db.flush()
    owner_application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=owner.id,
        source="manual",
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
    )
    direct_application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=related.id,
        source="manual",
        status="applied",
        pipeline_stage="applied",
        pipeline_stage_source="system",
        application_outcome="open",
    )
    db.add_all([owner_application, direct_application])
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=related.id,
                candidate_id=candidate.id,
                source_application_id=owner_application.id,
                ats_application_id=owner_application.id,
                status="done",
                pipeline_stage="review",
                application_outcome="open",
                membership_source="legacy_compat_shadow",
                spec_fingerprint="nightly-old-source",
                role_fit_score=70.0,
                deleted_at=now - timedelta(minutes=1),
            ),
            SisterRoleEvaluation(
                organization_id=org.id,
                role_id=related.id,
                candidate_id=candidate.id,
                source_application_id=direct_application.id,
                ats_application_id=owner_application.id,
                status="done",
                pipeline_stage="advanced",
                application_outcome="rejected",
                membership_source="direct",
                spec_fingerprint="nightly-current-source",
                role_fit_score=30.0,
            ),
            _decision(
                org_id=org.id,
                role_id=related.id,
                app=owner_application,
                key="nightly-prior-source-decision",
            ),
        ]
    )
    db.flush()

    with patch.object(nightly_policy_fit, "_collect_from_graphiti", return_value=[]):
        rows = nightly_policy_fit._collect_training_data(
            db,
            organization_id=org.id,
            since=now - timedelta(days=1),
        )

    assert len(rows) == 1
    assert rows[0].role_id == related.id
    assert rows[0].label == 0.0
    assert rows[0].weight == 1.0
