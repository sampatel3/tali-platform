"""Pre-screen calibration data collection — shadow-scores a random sample of
pre-screen rejects without surfacing the result to the recruiter."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.cv_matching.runner import MODEL_VERSION, PROMPT_VERSION
from app.cv_matching.schemas import CVMatchOutput, ScoringStatus
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.prescreen_calibration_sample import PrescreenCalibrationSample
from app.models.role import Role
from app.services.prescreen_calibration import sample_and_shadow_score_rejects


def _stub_output(score: float) -> CVMatchOutput:
    return CVMatchOutput(
        prompt_version=PROMPT_VERSION,
        skills_match_score=score,
        experience_relevance_score=score,
        matching_skills=["Python"],
        experience_highlights=["6 years"],
        summary="stub",
        requirements_match_score=score,
        cv_fit_score=score,
        role_fit_score=score,
        scoring_status=ScoringStatus.OK,
        error_reason="",
        model_version=MODEL_VERSION,
        trace_id="t",
    )


def _reject_app(db, org, role, *, ps_score=18.0, cv=None, cv_match=None):
    cand = Candidate(organization_id=org.id, email=f"c{id(object())}@x.test", full_name="C")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review", pipeline_stage_source="recruiter",
        application_outcome="open", source="manual",
        cv_text=cv or "candidate cv text here",
        cv_match_score=cv_match,
        pre_screen_run_at=datetime.now(timezone.utc),
        pre_screen_score_100=ps_score,
        pre_screen_evidence={"llm_score_100": ps_score, "decision": "no"},
    )
    db.add(app); db.flush()
    return app


def test_sample_shadow_scores_rejects_without_surfacing(db):
    org = Organization(name="O", slug=f"o-{id(db)}cal"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", job_spec_text="JD requirements")
    db.add(role); db.flush()
    app = _reject_app(db, org, role, ps_score=18.0)  # below threshold 30 → reject

    with patch("app.cv_matching.runner.run_cv_match", return_value=_stub_output(72.0)) as m:
        res = sample_and_shadow_score_rejects(db, organization_id=int(org.id), limit=10)

    assert res == {"sampled": 1, "scored": 1, "failed": 0}
    m.assert_called_once()
    db.refresh(app)
    # Shadow: the application's surfaced score is untouched.
    assert app.cv_match_score is None
    sample = db.query(PrescreenCalibrationSample).filter_by(application_id=app.id).one()
    assert sample.pre_screen_score == 18.0
    assert sample.full_cv_match_score == 72.0
    assert sample.scoring_status == "ok"


def test_sample_skips_full_scored_and_above_threshold(db):
    org = Organization(name="O", slug=f"o-{id(db)}cal2"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", job_spec_text="JD")
    db.add(role); db.flush()
    _reject_app(db, org, role, ps_score=70.0)            # above threshold → not a reject
    _reject_app(db, org, role, ps_score=18.0, cv_match=55.0)  # already full-scored

    with patch("app.cv_matching.runner.run_cv_match", return_value=_stub_output(72.0)) as m:
        res = sample_and_shadow_score_rejects(db, organization_id=int(org.id), limit=10)

    assert res["sampled"] == 0
    m.assert_not_called()


def test_sample_skips_already_sampled(db):
    org = Organization(name="O", slug=f"o-{id(db)}cal3"); db.add(org); db.flush()
    role = Role(organization_id=org.id, name="R", source="manual", job_spec_text="JD")
    db.add(role); db.flush()
    app = _reject_app(db, org, role, ps_score=18.0)
    db.add(PrescreenCalibrationSample(
        organization_id=int(org.id), role_id=int(role.id), application_id=int(app.id),
        pre_screen_score=18.0, full_cv_match_score=60.0, scoring_status="ok",
    ))
    db.flush()

    with patch("app.cv_matching.runner.run_cv_match", return_value=_stub_output(72.0)) as m:
        res = sample_and_shadow_score_rejects(db, organization_id=int(org.id), limit=10)

    assert res["sampled"] == 0  # already has a sample row
    m.assert_not_called()
