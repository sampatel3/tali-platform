"""Transactional ATS application-created dispatch for Workable + Bullhorn."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.components.integrations.bullhorn import sync_candidates
from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService
from app.models.application_created_outbox import (
    APPLICATION_CREATED_COMPLETE,
    APPLICATION_CREATED_PENDING,
    ApplicationCreatedOutbox,
)
from app.models.candidate_application import CandidateApplication
from app.models.cv_score_job import SCORE_JOB_PENDING, CvScoreJob
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.services.ats_application_ingest_outbox import dispatch_one


class _WorkableClient(WorkableService):
    def __init__(self):
        super().__init__(access_token="test", subdomain="test")

    def get_candidate(self, candidate_id):
        return {
            "id": candidate_id,
            "email": f"{candidate_id.lower()}@example.com",
            "name": f"Workable {candidate_id}",
            "stage": "Applied",
        }

    def get_candidate_activities(self, _candidate_id):
        return []

    def download_candidate_resume(self, _payload):
        return None

    def extract_workable_score(self, *, candidate_payload, ratings_payload=None):
        return None, None, None


class _BullhornClient:
    def list_file_attachments_strict(self, *, candidate_id, fields):
        return []


def _seed_role(db, provider: str) -> tuple[Organization, Role]:
    org = Organization(
        name=f"{provider.title()} outbox org",
        slug=f"{provider}-application-outbox",
        credits_balance=1_000_000_000,
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name=f"{provider.title()} Platform Engineer",
        source=provider,
        job_spec_text=(
            "Hire a senior platform engineer with Python, distributed systems, "
            "production reliability, and observability experience."
        ),
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        workable_job_id="WORK-1" if provider == "workable" else None,
        workable_job_data={"state": "published"} if provider == "workable" else None,
        bullhorn_job_order_id="9001" if provider == "bullhorn" else None,
        bullhorn_job_data={"id": 9001, "isOpen": True} if provider == "bullhorn" else None,
    )
    db.add(role)
    db.commit()
    return org, role


def _import_application(db, provider: str) -> CandidateApplication:
    org = db.query(Organization).filter(Organization.slug == f"{provider}-application-outbox").one()
    role = db.query(Role).filter(
        Role.organization_id == org.id,
        Role.ats_owner_role_id.is_(None),
    ).one()
    now = datetime.now(timezone.utc)
    if provider == "workable":
        service = WorkableSyncService(_WorkableClient())
        service._sync_candidate_for_role(
            db=db,
            org=org,
            role=role,
            job={"id": "WORK-1", "shortcode": "WORK-1"},
            candidate_ref={
                "id": "WC-1",
                "email": "workable-outbox@example.com",
                "name": "Workable Candidate",
                "stage": "Applied",
            },
            now=now,
            mode="full",
        )
    else:
        sync_candidates.sync_submission(
            db=db,
            org=org,
            role=role,
            submission={
                "id": "7001",
                "candidate": {"id": "8001"},
                "jobOrder": {"id": "9001"},
                "status": "New Lead",
            },
            candidate_payload={
                "id": "8001",
                "firstName": "Bullhorn",
                "lastName": "Candidate",
                "email": "bullhorn-outbox@example.com",
            },
            client=_BullhornClient(),
            now=now,
        )
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.organization_id == org.id)
        .one()
    )
    # Keep the importer real while avoiding object-storage coupling in this
    # transaction test. The CV text still lands before the same outer commit,
    # exactly as a successful ATS resume extraction does in production.
    app.cv_text = "Senior Python platform engineer with distributed systems experience."
    db.flush()
    return app


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_real_ats_import_dispatches_only_after_commit_and_scores(
    db, monkeypatch, provider
):
    from app.platform.config import settings
    from app.tasks import application_ingest_tasks, automation_tasks, scoring_tasks

    _seed_role(db, provider)
    kicks: list[int] = []
    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda outbox_id: kicks.append(int(outbox_id)),
    )

    app = _import_application(db, provider)
    row = (
        db.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == app.id)
        .one()
    )
    outbox_id = int(row.id)
    application_id = int(app.id)

    # The intent exists atomically, but neither broker nor score-job dispatch
    # can happen while the application is still invisible to another session.
    assert row.status == APPLICATION_CREATED_PENDING
    assert row.score_requested is True
    assert kicks == []
    assert db.query(CvScoreJob).filter(CvScoreJob.application_id == app.id).count() == 0

    db.commit()
    assert kicks == [outbox_id]

    auto_rejects: list[int] = []
    parses: list[tuple] = []
    scores: list[tuple[int, int]] = []
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False, raising=False)
    monkeypatch.setattr(
        automation_tasks.run_application_auto_reject,
        "delay",
        lambda app_id: auto_rejects.append(int(app_id)),
    )
    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        lambda args, **kwargs: parses.append((args, kwargs)),
    )
    monkeypatch.setattr(
        scoring_tasks.score_application_job,
        "delay",
        lambda app_id, **kwargs: (
            scores.append((int(app_id), int(kwargs["job_id"])))
            or SimpleNamespace(id=f"score-{kwargs['job_id']}")
        ),
    )

    result = dispatch_one(db, outbox_id=outbox_id)
    assert result["status"] == "complete"
    assert auto_rejects == [application_id]
    assert parses == [
        (
            (application_id,),
            {"kwargs": {"origin": "ats_ingest", "outbox_id": outbox_id}},
        )
    ]
    score_job = (
        db.query(CvScoreJob)
        .filter(CvScoreJob.application_id == application_id)
        .one()
    )
    assert score_job.status == SCORE_JOB_PENDING
    assert score_job.requires_active_agent is True
    assert scores == [(application_id, score_job.id)]
    db.refresh(row)
    assert row.status == APPLICATION_CREATED_COMPLETE
    assert row.cv_parse_dispatch_status == "enqueued"
    assert row.score_dispatch_status == "enqueued"
    assert row.score_job_id == score_job.id


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_related_role_fanout_is_committed_and_recovers_lost_broker_kick(
    db, monkeypatch, provider
):
    from app.platform.config import settings
    from app.platform.database import SessionLocal
    from app.tasks import (
        application_ingest_tasks,
        automation_tasks,
        scoring_tasks,
        sister_role_tasks,
    )
    from app.models.sister_role_evaluation import SisterRoleEvaluation

    org, source = _seed_role(db, provider)
    related = Role(
        organization_id=org.id,
        name=f"{provider.title()} Platform Engineer · Data",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=source.id,
        job_spec_text=(
            "A complete related role specification requiring Python data "
            "platforms, distributed systems, reliability, and observability."
        ),
    )
    db.add(related)
    db.commit()

    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda _outbox_id: None,
    )
    published: list[int] = []
    monkeypatch.setattr(
        sister_role_tasks.score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: published.append(int(args[0])),
    )
    app = _import_application(db, provider)
    row = (
        db.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == app.id)
        .one()
    )
    outbox_id = int(row.id)
    application_id = int(app.id)

    # Both importers stop at the transactional application outbox. No related
    # evaluation or task is allowed to escape the uncommitted transaction.
    assert db.query(SisterRoleEvaluation).filter(
        SisterRoleEvaluation.source_application_id == application_id
    ).count() == 0
    assert published == []
    db.commit()

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False, raising=False)
    monkeypatch.setattr(
        automation_tasks.run_application_auto_reject,
        "delay",
        lambda _app_id: None,
    )
    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scoring_tasks.score_application_job,
        "delay",
        lambda _app_id, **kwargs: SimpleNamespace(id=f"score-{kwargs['job_id']}"),
    )

    committed_before_publish: list[int] = []
    secret = "redis://:SECRET@host"

    def _lost_related_kick(*, args, queue):
        assert queue == "scoring"
        with SessionLocal() as check:
            visible = check.get(SisterRoleEvaluation, int(args[0]))
            assert visible is not None
            assert visible.source_application_id == application_id
            committed_before_publish.append(int(visible.id))
        raise RuntimeError(secret)

    # Authority can change after the importer commits. The cheap evaluation
    # receipt must still be created; its worker will hold paid work until the
    # source-role agent is turned back on.
    source = db.get(Role, source.id)
    source.agent_paused_at = datetime.now(timezone.utc)
    db.commit()
    monkeypatch.setattr(
        sister_role_tasks.score_sister_evaluation,
        "apply_async",
        _lost_related_kick,
    )
    assert dispatch_one(db, outbox_id=outbox_id)["status"] == "complete"

    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.source_application_id == application_id)
        .one()
    )
    assert committed_before_publish == [evaluation.id]
    assert evaluation.status == "retry_wait"
    assert secret not in str(evaluation.error_message)

    source.agent_paused_at = None
    evaluation.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    recovered: list[int] = []
    monkeypatch.setattr(
        sister_role_tasks.score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: recovered.append(int(args[0])),
    )
    summary = sister_role_tasks.recover_sister_role_evaluations.run(limit=10)
    assert summary["queued"] == 1
    assert recovered == [evaluation.id]


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
@pytest.mark.parametrize("owner_state", ["paused", "off"])
def test_related_roster_materializes_when_owner_is_already_held_at_ingest(
    db, monkeypatch, provider, owner_state
):
    from app.models.sister_role_evaluation import SisterRoleEvaluation
    from app.platform.config import settings
    from app.tasks import (
        application_ingest_tasks,
        automation_tasks,
        scoring_tasks,
        sister_role_tasks,
    )

    org, source = _seed_role(db, provider)
    if owner_state == "paused":
        source.agent_paused_at = datetime.now(timezone.utc)
    else:
        source.agentic_mode_enabled = False
    related = Role(
        organization_id=org.id,
        name=f"{provider.title()} Platform Engineer · Related Held Owner",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=source.id,
        job_spec_text=(
            "A complete related role specification requiring Python data "
            "platforms, distributed systems, reliability, and observability."
        ),
        # Related-role scoring owns this independent authority check. The
        # application outbox only materializes and publishes its cheap receipt.
        agentic_mode_enabled=False,
    )
    db.add(related)
    db.commit()

    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda _outbox_id: None,
    )
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "USAGE_METER_LIVE", False, raising=False)
    monkeypatch.setattr(
        automation_tasks.run_application_auto_reject,
        "delay",
        lambda _app_id: None,
    )
    owner_parses: list[tuple] = []
    owner_scores: list[int] = []
    published_related: list[int] = []
    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        lambda *args, **kwargs: owner_parses.append((args, kwargs)),
    )
    monkeypatch.setattr(
        scoring_tasks.score_application_job,
        "delay",
        lambda app_id, **_kwargs: owner_scores.append(int(app_id)),
    )
    monkeypatch.setattr(
        sister_role_tasks.score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: published_related.append(int(args[0])),
    )

    app = _import_application(db, provider)
    row = (
        db.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == app.id)
        .one()
    )
    outbox_id = int(row.id)
    application_id = int(app.id)
    assert row.paid_work_requested is False
    db.commit()

    assert dispatch_one(db, outbox_id=outbox_id)["status"] == "complete"
    evaluation = (
        db.query(SisterRoleEvaluation)
        .filter(
            SisterRoleEvaluation.source_application_id == application_id,
            SisterRoleEvaluation.role_id == related.id,
        )
        .one()
    )
    first_evaluation_id = int(evaluation.id)
    assert published_related == [first_evaluation_id]
    assert owner_parses == []
    assert owner_scores == []
    assert (
        db.query(CvScoreJob).filter(CvScoreJob.application_id == application_id).count()
        == 0
    )

    # An existing pending evaluation is already a durable receipt owned by the
    # recovery sweep. A routine resync must not reopen the application outbox
    # and republish held related-role work for the whole roster.
    _import_application(db, provider)
    db.refresh(row)
    assert row.status == APPLICATION_CREATED_COMPLETE
    assert published_related == [first_evaluation_id]
    db.commit()


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_real_ats_import_rollback_emits_nothing(db, monkeypatch, provider):
    from app.tasks import application_ingest_tasks

    _seed_role(db, provider)
    kicks: list[int] = []
    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda outbox_id: kicks.append(int(outbox_id)),
    )

    app = _import_application(db, provider)
    application_id = int(app.id)
    assert db.query(ApplicationCreatedOutbox).filter(
        ApplicationCreatedOutbox.application_id == application_id
    ).one_or_none() is not None
    db.rollback()

    assert kicks == []
    assert db.query(ApplicationCreatedOutbox).filter(
        ApplicationCreatedOutbox.application_id == application_id
    ).one_or_none() is None
    assert db.query(CandidateApplication).filter(
        CandidateApplication.id == application_id
    ).one_or_none() is None


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_dispatch_rechecks_live_role_authority_for_each_ats(
    db, monkeypatch, provider
):
    from app.tasks import application_ingest_tasks, automation_tasks

    _seed_role(db, provider)
    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda _outbox_id: None,
    )
    app = _import_application(db, provider)
    application_id = int(app.id)
    row = db.query(ApplicationCreatedOutbox).filter(
        ApplicationCreatedOutbox.application_id == application_id
    ).one()
    outbox_id = int(row.id)
    db.commit()

    # Pause after ingest commits but before the dispatcher runs. The persisted
    # request bit is not authority: only the fresh role state may permit spend.
    role = db.query(Role).filter(Role.id == app.role_id).one()
    role.agent_paused_at = datetime.now(timezone.utc)
    role.agent_paused_reason = "recruiter pause"
    db.commit()

    auto_rejects: list[int] = []
    parses: list[int] = []
    monkeypatch.setattr(
        automation_tasks.run_application_auto_reject,
        "delay",
        lambda app_id: auto_rejects.append(int(app_id)),
    )
    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        lambda args, **_kwargs: parses.append(int(args[0])),
    )

    result = dispatch_one(db, outbox_id=outbox_id)
    assert result["status"] == "complete"
    assert auto_rejects == [application_id]
    assert parses == []
    assert db.query(CvScoreJob).filter(
        CvScoreJob.application_id == application_id
    ).count() == 0
    db.refresh(row)
    assert row.cv_parse_dispatch_status == "authority_blocked"
    assert row.score_dispatch_status == "authority_blocked"


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_lost_post_commit_kick_is_recovered_for_each_ats(
    db, monkeypatch, provider
):
    from app.tasks import application_ingest_tasks

    _seed_role(db, provider)

    def _lost_kick(_outbox_id):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        _lost_kick,
    )
    app = _import_application(db, provider)
    application_id = int(app.id)
    db.commit()  # hook swallows the broker error; durable row must survive

    row = (
        db.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == application_id)
        .one()
    )
    assert row.status == APPLICATION_CREATED_PENDING
    recovered: list[int] = []
    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda outbox_id: recovered.append(int(outbox_id)),
    )
    db.rollback()  # release the test session before the sweep's own session

    summary = application_ingest_tasks.sweep_application_created_outbox.run(limit=10)
    assert summary == {"status": "ok", "scanned": 1, "dispatched": 1, "errors": 0}
    assert recovered == [row.id]


def test_ats_outbox_exception_details_never_leak_credentials(
    db, monkeypatch, caplog
):
    from app.platform.config import settings
    from app.services import ats_application_ingest_outbox as ingest_outbox
    from app.services.ats_cv_parse_outbox import dispatch_initial_cv_parse
    from app.tasks import application_ingest_tasks, automation_tasks

    secret = "redis://:SECRET@host"

    def _broker_failure(*_args, **_kwargs):
        raise RuntimeError(secret)

    caplog.set_level(logging.ERROR)
    _seed_role(db, "workable")
    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        _broker_failure,
    )
    app = _import_application(db, "workable")
    application_id = int(app.id)
    db.commit()  # Exercises the post-commit kick's safe logging path.

    row = (
        db.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == application_id)
        .one()
    )
    outbox_id = int(row.id)

    monkeypatch.setattr(
        automation_tasks.run_application_auto_reject,
        "delay",
        _broker_failure,
    )
    ingest_result = dispatch_one(db, outbox_id=outbox_id)
    db.refresh(row)
    assert row.last_error == "dispatch_failed:RuntimeError"
    assert ingest_result == {
        "status": "retry",
        "outbox_id": outbox_id,
        "error_code": "dispatch_failed",
        "error_type": "RuntimeError",
    }

    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        _broker_failure,
    )
    parse_result = dispatch_initial_cv_parse(
        db,
        row=row,
        app=app,
        live_authority=True,
    )
    db.refresh(row)
    assert row.cv_parse_last_error == "queue_unavailable:RuntimeError"
    assert parse_result == {
        "status": "retry_wait",
        "outbox_id": outbox_id,
        "error_code": "queue_unavailable",
        "error_type": "RuntimeError",
    }

    monkeypatch.setattr(ingest_outbox, "dispatch_one", _broker_failure)
    task_result = application_ingest_tasks.dispatch_application_created_outbox.run(
        outbox_id
    )
    assert task_result == {
        "status": "error",
        "outbox_id": outbox_id,
        "error_code": "dispatch_failed",
        "error_type": "RuntimeError",
    }

    exposed = repr(
        {
            "ingest_result": ingest_result,
            "parse_result": parse_result,
            "task_result": task_result,
            "last_error": row.last_error,
            "cv_parse_last_error": row.cv_parse_last_error,
        }
    )
    assert secret not in exposed
    assert secret not in caplog.text


@pytest.mark.parametrize("provider", ["workable", "bullhorn"])
def test_cv_parse_transient_failures_retry_past_fast_budget_then_succeed(
    db, monkeypatch, provider
):
    from app.platform.config import settings
    from app.services.ats_cv_parse_outbox import (
        claim_cv_parse_attempt,
        record_cv_parse_failure,
        record_cv_parse_success,
        recoverable_cv_parse_ids,
        redispatch_cv_parse,
    )
    from app.tasks import application_ingest_tasks, automation_tasks

    _seed_role(db, provider)
    monkeypatch.setattr(
        application_ingest_tasks.dispatch_application_created_outbox,
        "delay",
        lambda _outbox_id: None,
    )
    app = _import_application(db, provider)
    db.commit()
    row = (
        db.query(ApplicationCreatedOutbox)
        .filter(ApplicationCreatedOutbox.application_id == app.id)
        .one()
    )

    # The first three failures use short backoff. Later transient failures
    # move to a six-hour cadence but never become a manual/terminal state.
    for attempt in range(1, 6):
        row.cv_parse_attempts = attempt
        row.cv_parse_dispatch_status = "running"
        db.commit()
        assert (
            record_cv_parse_failure(
                db,
                outbox_id=row.id,
                error="claude_call_failed: temporary provider outage",
            )
            == "retry_wait"
        )
        db.refresh(row)
        assert row.cv_parse_dispatch_status == "retry_wait"
        assert row.cv_parse_last_error == "claude_call_failed"

    row.cv_parse_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert row.id in recoverable_cv_parse_ids(db, limit=10)

    published: list[int] = []
    monkeypatch.setattr(settings, "CV_PARSE_BATCH_ENABLED", False, raising=False)
    monkeypatch.setattr(
        automation_tasks.parse_application_cv_sections,
        "apply_async",
        lambda args, **_kwargs: published.append(int(args[0])),
    )
    assert redispatch_cv_parse(db, outbox_id=row.id)["status"] == "enqueued"
    assert published == [app.id]
    claim = claim_cv_parse_attempt(
        db,
        application_id=app.id,
        outbox_id=row.id,
    )
    assert claim["claimed"] is True
    assert record_cv_parse_success(db, outbox_id=row.id) == "succeeded"
    db.refresh(row)
    assert row.cv_parse_dispatch_status == "succeeded"
    assert row.cv_parse_attempts == 6
