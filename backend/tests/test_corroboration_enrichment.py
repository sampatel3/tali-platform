"""Generation and delivery safety for asynchronous corroboration enrichment."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.components.scoring.freshness import ScoreGenerationToken
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import CvScoreJob, SCORE_JOB_DONE, SCORE_JOB_STALE
from app.models.organization import Organization
from app.models.role import Role
from app.platform.config import settings
from app.services.role_intent_fingerprint import role_intent_fingerprint


@pytest.fixture()
def corroboration_case(db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "GRAPH_CORROBORATION_ENABLED", False)
    monkeypatch.setattr(settings, "GITHUB_CORROBORATION_ENABLED", True)
    monkeypatch.setattr(settings, "GRAPH_OUTCOME_PRIOR_ENABLED", False)

    org = Organization(name="Corroboration Org", slug="corroboration-org")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Backend Engineer",
        job_spec_text="Build reliable Python services.",
    )
    db.add(role)
    db.flush()
    candidate = Candidate(
        organization_id=int(org.id),
        email="candidate@example.com",
        social_profiles=[{"url": "https://github.com/old-profile"}],
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(org.id),
        candidate_id=int(candidate.id),
        role_id=int(role.id),
        status="applied",
        cv_text="Python engineer at Acme.",
        cv_uploaded_at=datetime.now(timezone.utc),
        cv_sections={
            "skills": ["Python"],
            "experience": [{"company": "Acme"}],
            "links": ["https://github.com/old-profile"],
        },
        cv_match_score=82.0,
        cv_match_scored_at=datetime.now(timezone.utc),
        cv_match_details={
            "summary": "current score evidence",
            "integrity_signals": {
                "jd_shingle": {"triggered": True},
                "triangulation": {"verdict": "review"},
            },
        },
    )
    db.add(application)
    db.flush()
    fingerprint = role_intent_fingerprint(role, db=db)
    job = CvScoreJob(
        application_id=int(application.id),
        role_id=int(role.id),
        status=SCORE_JOB_DONE,
        cache_key=f"role-intent:{fingerprint}",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()

    token = ScoreGenerationToken(
        application_id=int(application.id),
        role_id=int(role.id),
        job_id=int(job.id),
        role_intent_fingerprint=fingerprint,
    )
    from app.services.corroboration_enrichment import (
        capture_corroboration_generation,
    )

    generation = capture_corroboration_generation(
        application=application,
        candidate=candidate,
        score_generation=token,
    )
    return org, role, candidate, application, job, generation


def test_candidate_change_during_fetch_discards_old_enrichment(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """An old GitHub result may not overwrite details after candidate drift."""
    _org, _role, candidate, application, job, generation = corroboration_case
    from app.platform.database import SessionLocal
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment

    def stale_provider_result(*_args, **_kwargs):
        concurrent = SessionLocal()
        try:
            current_candidate = concurrent.get(Candidate, int(candidate.id))
            current_candidate.social_profiles = [
                {"url": "https://github.com/new-profile"}
            ]
            concurrent.commit()
        finally:
            concurrent.close()
        return {
            "status": "corroborated",
            "username": "old-profile",
            "matched_skills": ["python"],
        }

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        stale_provider_result,
    )

    result = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    db.rollback()
    db.expire_all()
    current = db.get(CandidateApplication, int(application.id))
    latest_job = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(application.id))
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    assert result["status"] == "superseded"
    assert current.cv_match_details["summary"] == "current score evidence"
    assert "github" not in current.cv_match_details["integrity_signals"]
    assert latest_job.status == SCORE_JOB_STALE
    assert int(latest_job.id) != int(job.id)


def test_queued_generation_drift_skips_external_work(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """A stale queued message is rejected before Graph/GitHub cost is incurred."""
    _org, _role, candidate, application, _job, generation = corroboration_case
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment

    provider = MagicMock()
    monkeypatch.setattr(external_corroboration, "corroborate_github", provider)
    candidate.social_profiles = [{"url": "https://github.com/new-profile"}]
    db.commit()

    result = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    assert result["status"] == "superseded"
    provider.assert_not_called()


def test_same_generation_is_enriched_at_most_once(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """A duplicate delivery must not repeat the external provider work."""
    _org, _role, _candidate, application, _job, generation = corroboration_case
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment

    calls = 0

    def provider_result(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "status": "corroborated",
            "username": "old-profile",
            "matched_skills": ["python"],
        }

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        provider_result,
    )
    first = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )
    second = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    assert first["status"] == "ok"
    assert second["status"] == "already_complete"
    assert calls == 1


def test_malformed_generation_lease_attempt_fails_closed(
    corroboration_case,
) -> None:
    _org, _role, _candidate, application, _job, generation = corroboration_case
    from app.services.corroboration_generation import (
        MARKER_KEY,
        claim_generation_lease,
    )

    details = dict(application.cv_match_details or {})
    details[MARKER_KEY] = {
        "generation": generation.digest(),
        "status": "retry_wait",
        "attempt": "not-an-integer",
    }
    application.cv_match_details = details

    claim = claim_generation_lease(application, generation=generation)

    assert claim.status == "retry_exhausted"
    assert application.cv_match_details[MARKER_KEY]["status"] == "failed"


def test_malformed_generation_lease_is_never_current(
    corroboration_case,
) -> None:
    _org, _role, _candidate, application, _job, generation = corroboration_case
    from app.services.corroboration_generation import (
        MARKER_KEY,
        CorroborationLease,
        lease_is_current,
    )

    now = datetime.now(timezone.utc)
    lease = CorroborationLease(
        generation_digest=generation.digest(),
        lease_id="lease-id",
        attempt=1,
        claimed_at=now,
    )
    details = dict(application.cv_match_details or {})
    details[MARKER_KEY] = {
        "generation": lease.generation_digest,
        "status": "running",
        "lease_id": lease.lease_id,
        "attempt": {"corrupt": True},
        "claimed_at": now.isoformat(),
    }
    application.cv_match_details = details

    assert not lease_is_current(application, lease=lease)


def test_legacy_single_argument_task_captures_current_generation(
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """Messages queued before the payload rollout remain safe and useful."""
    from inspect import signature

    _org, _role, _candidate, application, _job, _generation = corroboration_case
    from app.services import external_corroboration
    from app.tasks.corroboration_tasks import enrich_corroboration_job

    assert tuple(signature(enrich_corroboration_job.run).parameters) == (
        "application_id",
    )

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        lambda *_args, **_kwargs: {
            "status": "corroborated",
            "username": "old-profile",
            "matched_skills": ["python"],
        },
    )

    result = enrich_corroboration_job.run(int(application.id))

    assert result["status"] == "ok"
    assert result["verdict"] == "review"


def test_simultaneous_delivery_observes_generation_lease(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """Only the lease owner reaches GitHub when two workers overlap."""
    _org, _role, _candidate, application, _job, generation = corroboration_case
    from app.platform.database import SessionLocal
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment

    provider_calls = 0
    duplicate_result = None

    def provider_result(*_args, **_kwargs):
        nonlocal provider_calls, duplicate_result
        provider_calls += 1
        duplicate = SessionLocal()
        try:
            duplicate_result = run_corroboration_enrichment(
                duplicate,
                application_id=int(application.id),
                expected_generation=generation.as_payload(),
            )
        finally:
            duplicate.close()
        return {
            "status": "corroborated",
            "username": "old-profile",
            "matched_skills": ["python"],
        }

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        provider_result,
    )
    owner_result = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    assert owner_result["status"] == "ok"
    assert duplicate_result["status"] == "leased"
    assert provider_calls == 1


def test_newer_completed_score_is_never_overwritten_or_invalidated(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """Generation A finishing late cannot touch a completed generation B."""
    _org, role, _candidate, application, old_job, generation = corroboration_case
    from app.platform.database import SessionLocal
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment

    new_job_id = None

    def old_provider_result(*_args, **_kwargs):
        nonlocal new_job_id
        concurrent = SessionLocal()
        try:
            current_app = concurrent.get(CandidateApplication, int(application.id))
            current_app.cv_match_score = 94.0
            current_app.cv_match_scored_at = datetime.now(timezone.utc)
            current_app.cv_match_details = {
                "summary": "new score generation B",
                "integrity_signals": {
                    "jd_shingle": {"triggered": True},
                    "triangulation": {"verdict": "review"},
                },
            }
            fingerprint = role_intent_fingerprint(
                concurrent.get(Role, int(role.id)),
                db=concurrent,
            )
            new_job = CvScoreJob(
                application_id=int(application.id),
                role_id=int(role.id),
                status=SCORE_JOB_DONE,
                cache_key=f"role-intent:{fingerprint}",
                finished_at=datetime.now(timezone.utc),
            )
            concurrent.add(new_job)
            concurrent.commit()
            new_job_id = int(new_job.id)
        finally:
            concurrent.close()
        return {
            "status": "not_found",
            "username": "old-profile",
        }

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        old_provider_result,
    )
    result = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    db.rollback()
    db.expire_all()
    current = db.get(CandidateApplication, int(application.id))
    latest_job = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == int(application.id))
        .order_by(CvScoreJob.id.desc())
        .first()
    )
    assert result["status"] == "superseded"
    assert current.cv_match_score == 94.0
    assert current.cv_match_details["summary"] == "new score generation B"
    assert "github" not in current.cv_match_details["integrity_signals"]
    assert int(latest_job.id) == new_job_id
    assert int(latest_job.id) != int(old_job.id)
    assert latest_job.status == SCORE_JOB_DONE


def test_provider_failure_recovery_is_bounded_to_two_attempts(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    _org, _role, _candidate, application, _job, generation = corroboration_case
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment
    from app.services.corroboration_generation import MARKER_KEY

    calls = 0

    def provider_failure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary provider failure")

    def make_retry_due() -> None:
        db.rollback()
        current = db.get(CandidateApplication, int(application.id))
        details = dict(current.cv_match_details)
        marker = dict(details[MARKER_KEY])
        marker["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
        details[MARKER_KEY] = marker
        current.cv_match_details = details
        db.commit()

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        provider_failure,
    )
    first = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )
    make_retry_due()
    second = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )
    make_retry_due()
    exhausted = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    assert first["status"] == "retry_wait"
    assert second["status"] == "retry_wait"
    assert exhausted["status"] == "retry_exhausted"
    assert calls == 2


def test_role_change_during_fetch_discards_old_enrichment(
    db,
    monkeypatch: pytest.MonkeyPatch,
    corroboration_case,
) -> None:
    """A provider result remains tied to the exact role-intent generation."""
    _org, role, _candidate, application, _job, generation = corroboration_case
    from app.platform.database import SessionLocal
    from app.services import external_corroboration
    from app.services.corroboration_enrichment import run_corroboration_enrichment

    def stale_provider_result(*_args, **_kwargs):
        concurrent = SessionLocal()
        try:
            current_role = concurrent.get(Role, int(role.id))
            current_role.job_spec_text = "Build safe Rust systems instead."
            concurrent.commit()
        finally:
            concurrent.close()
        return {
            "status": "not_found",
            "username": "old-profile",
        }

    monkeypatch.setattr(
        external_corroboration,
        "corroborate_github",
        stale_provider_result,
    )
    result = run_corroboration_enrichment(
        db,
        application_id=int(application.id),
        expected_generation=generation.as_payload(),
    )

    db.rollback()
    db.expire_all()
    current = db.get(CandidateApplication, int(application.id))
    latest_status = (
        db.query(CvScoreJob.status)
        .filter(CvScoreJob.application_id == int(application.id))
        .order_by(CvScoreJob.id.desc())
        .limit(1)
        .scalar()
    )
    assert result["status"] == "superseded"
    assert "github" not in current.cv_match_details["integrity_signals"]
    assert latest_status == SCORE_JOB_STALE
