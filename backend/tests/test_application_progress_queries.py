from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event

from app.domains.assessments_runtime.application_progress_queries import (
    batch_score_role_name,
    batch_score_terminal_counts,
)
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import (
    CvScoreJob,
    SCORE_JOB_DONE,
    SCORE_JOB_ERROR,
    SCORE_JOB_PENDING,
)
from app.models.organization import Organization
from app.models.role import Role


def _create_application(db, *, organization, role, suffix: str):
    candidate = Candidate(
        organization_id=organization.id,
        full_name=f"Progress Candidate {suffix}",
        email=f"progress-{organization.id}-{suffix}@example.test",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=organization.id,
        role_id=role.id,
        candidate_id=candidate.id,
        source="manual",
        status="applied",
    )
    db.add(application)
    db.flush()
    return application


def test_batch_score_terminal_counts_use_one_query_and_include_legacy_null_cache(
    db,
) -> None:
    organization = Organization(name="Progress org", slug=f"progress-{id(db)}")
    db.add(organization)
    db.flush()
    role = Role(organization_id=organization.id, name="Platform Engineer")
    db.add(role)
    db.flush()
    applications = [
        _create_application(
            db,
            organization=organization,
            role=role,
            suffix=str(index),
        )
        for index in range(5)
    ]
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    finished_at = datetime.now(timezone.utc)
    db.add_all(
        [
            CvScoreJob(
                application_id=applications[0].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit=None,
                queued_at=started_at + timedelta(seconds=1),
                finished_at=finished_at,
            ),
            CvScoreJob(
                application_id=applications[1].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="miss",
                queued_at=started_at + timedelta(seconds=1),
                finished_at=finished_at,
            ),
            CvScoreJob(
                application_id=applications[2].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="pre_screen_filtered",
                queued_at=started_at + timedelta(seconds=1),
                finished_at=finished_at,
            ),
            CvScoreJob(
                application_id=applications[3].id,
                role_id=role.id,
                status=SCORE_JOB_ERROR,
                queued_at=started_at + timedelta(seconds=1),
                finished_at=finished_at,
            ),
            CvScoreJob(
                application_id=applications[4].id,
                role_id=role.id,
                status=SCORE_JOB_ERROR,
                queued_at=started_at - timedelta(seconds=2),
                finished_at=started_at - timedelta(seconds=1),
            ),
        ]
    )
    db.commit()

    role_id = int(role.id)
    selects: list[str] = []

    def observe_query(_conn, _cursor, statement, *_args) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", observe_query)
    try:
        counts = batch_score_terminal_counts(
            db,
            role_id=role_id,
            started_at=started_at,
        )
    finally:
        event.remove(bind, "before_cursor_execute", observe_query)

    assert counts == (2, 1, 1)
    assert len(selects) == 1
    assert batch_score_terminal_counts(
        db,
        role_id=role_id,
        started_at=started_at,
        application_ids=[applications[0].id, applications[2].id],
    ) == (1, 0, 1)
    assert batch_score_terminal_counts(
        db,
        role_id=role_id,
        started_at=started_at,
        application_ids=[],
    ) == (0, 0, 0)


def test_batch_score_terminal_counts_use_only_latest_relevant_job_per_app(
    db,
) -> None:
    organization = Organization(name="Retry org", slug=f"retry-{id(db)}")
    db.add(organization)
    db.flush()
    role = Role(organization_id=organization.id, name="Data Engineer")
    db.add(role)
    db.flush()
    applications = [
        _create_application(
            db,
            organization=organization,
            role=role,
            suffix=f"retry-{index}",
        )
        for index in range(7)
    ]
    started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    first_attempt = started_at + timedelta(seconds=1)
    retry_attempt = started_at + timedelta(seconds=2)
    finished_at = datetime.now(timezone.utc)

    db.add_all(
        [
            # A historical terminal result from before this run is excluded.
            CvScoreJob(
                application_id=applications[0].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="miss",
                queued_at=started_at - timedelta(seconds=1),
                finished_at=started_at - timedelta(microseconds=1),
            ),
            # A successful retry replaces the earlier error contribution.
            CvScoreJob(
                application_id=applications[1].id,
                role_id=role.id,
                status=SCORE_JOB_ERROR,
                queued_at=first_attempt,
                finished_at=first_attempt,
            ),
            CvScoreJob(
                application_id=applications[1].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit=None,
                queued_at=retry_attempt,
                finished_at=retry_attempt,
            ),
            # A failed retry replaces the earlier success contribution.
            CvScoreJob(
                application_id=applications[2].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="miss",
                queued_at=first_attempt,
                finished_at=first_attempt,
            ),
            CvScoreJob(
                application_id=applications[2].id,
                role_id=role.id,
                status=SCORE_JOB_ERROR,
                queued_at=retry_attempt,
                finished_at=retry_attempt,
            ),
            # The latest pre-screen verdict is its own terminal category.
            CvScoreJob(
                application_id=applications[3].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="miss",
                queued_at=first_attempt,
                finished_at=first_attempt,
            ),
            CvScoreJob(
                application_id=applications[3].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="pre_screen_filtered",
                queued_at=retry_attempt,
                finished_at=retry_attempt,
            ),
            # A newer in-flight retry means the application is not terminal.
            CvScoreJob(
                application_id=applications[4].id,
                role_id=role.id,
                status=SCORE_JOB_ERROR,
                queued_at=first_attempt,
                finished_at=first_attempt,
            ),
            CvScoreJob(
                application_id=applications[4].id,
                role_id=role.id,
                status=SCORE_JOB_PENDING,
                queued_at=retry_attempt,
            ),
            # enqueue_score may reuse an attempt that was already pending
            # when the batch began. Its post-start completion still belongs
            # in this batch's progress.
            CvScoreJob(
                application_id=applications[6].id,
                role_id=role.id,
                status=SCORE_JOB_DONE,
                cache_hit="miss",
                queued_at=started_at - timedelta(seconds=1),
                finished_at=finished_at,
            ),
        ]
    )
    db.flush()

    # IDs deterministically break an otherwise equal enqueue timestamp.
    db.add(
        CvScoreJob(
            application_id=applications[5].id,
            role_id=role.id,
            status=SCORE_JOB_DONE,
            cache_hit="miss",
            queued_at=retry_attempt,
            finished_at=retry_attempt,
        )
    )
    db.flush()
    db.add(
        CvScoreJob(
            application_id=applications[5].id,
            role_id=role.id,
            status=SCORE_JOB_ERROR,
            queued_at=retry_attempt,
            finished_at=retry_attempt,
        )
    )
    db.commit()

    assert batch_score_terminal_counts(
        db,
        role_id=int(role.id),
        started_at=started_at,
    ) == (2, 2, 1)


def test_batch_score_role_name_uses_cached_value_without_query() -> None:
    class _NoQueryDb:
        def query(self, *_args, **_kwargs):
            raise AssertionError("cached role name performed a database query")

    assert batch_score_role_name(
        _NoQueryDb(),
        progress={"role_name": "Platform Engineer"},
        role_id=9,
        organization_id=4,
    ) == "Platform Engineer"
