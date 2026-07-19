"""Talent-pool re-scoring releases SQL before model/cache execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.pool_rescore_job import PoolRescoreJob
from app.models.role import Role
from app.tasks.pool_rescore_tasks import rescore_pool_against_requirement


def test_holistic_match_runs_without_open_worker_transaction(db):
    organization = Organization(
        name="Pool boundary org",
        slug=f"pool-boundary-{id(db)}",
    )
    db.add(organization)
    db.flush()
    role = Role(organization_id=organization.id, name="Backend", source="manual")
    candidate = Candidate(
        organization_id=organization.id,
        email="pool-boundary@example.com",
        full_name="Pool Candidate",
        cv_text="Python platform engineer",
    )
    db.add_all((role, candidate))
    db.flush()
    application = CandidateApplication(
        organization_id=organization.id,
        role_id=role.id,
        candidate_id=candidate.id,
        source="manual",
        status="review",
        pipeline_stage="review",
        application_outcome="open",
        cv_text="Python platform engineer",
    )
    db.add(application)
    db.flush()
    job = PoolRescoreJob(
        organization_id=organization.id,
        requirement_text="Python",
        requirement_hash="pool-boundary",
        application_ids=[application.id],
        status="pending",
    )
    db.add(job)
    db.commit()
    worker_db = Session(bind=db.get_bind())

    def score(*args, **kwargs):
        assert worker_db.in_transaction() is False
        return SimpleNamespace(
            scoring_status=SimpleNamespace(value="ok"),
            role_fit_score=91,
            summary="Strong fit",
            cache_hit=False,
        )

    with (
        patch("app.platform.database.SessionLocal", return_value=worker_db),
        patch(
            "app.services.claude_client_resolver.get_metered_client",
            return_value=object(),
        ),
        patch(
            "app.services.workable_context_service.format_workable_context",
            return_value=None,
        ),
        patch("app.cv_matching.holistic.run_holistic_match", side_effect=score),
    ):
        result = rescore_pool_against_requirement.run(int(job.id))

    assert result == {"ok": True, "scored": 1, "cached": 0, "failed": 0}
