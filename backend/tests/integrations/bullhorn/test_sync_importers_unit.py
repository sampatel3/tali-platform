"""Unit-smoke tests for the Bullhorn full-sync importers (PR-5).

These exercise the importer modules directly against a real DB session with a
lightweight stub client (no socket) — the module-boundary behaviors that matter:
stage-map resolution + needs-mapping, JobOrder→Role upsert idempotency,
JobSubmission→Candidate/CandidateApplication dedup + funnel-top-on-unmapped, and
the append-only/idempotent history + notes importers.

The end-to-end walk against the LIVE fake server lives in
``test_sync_engine_e2e.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.components.integrations.bullhorn import event_handlers, note_reconciliation
from app.components.integrations.bullhorn import stage_map as sm
from app.components.integrations.bullhorn import sync_candidates, sync_events, sync_jobs
from app.models.ats_stage_map import AtsStageMap
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.job_page import JobPage
from app.models.organization import Organization
from app.models.role import JOB_STATUS_DRAFT, JOB_STATUS_OPEN, Role
from app.models.role_brief import RoleBrief
from app.models.role_change_event import RoleChangeEvent
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.services.auto_reject_operation_receipt import AUTO_REJECT_OPERATION_KEY
from app.services.ats_writeback_state import set_outcome_writeback_state


def _org(db) -> Organization:
    org = Organization(name="Bullhorn Org")
    db.add(org)
    db.commit()
    return org


def _candidate(db, org, *, email="c@example.com") -> Candidate:
    cand = Candidate(organization_id=org.id, email=email, full_name="Test Candidate")
    db.add(cand)
    db.flush()
    return cand


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _StubClient:
    """Records-only stand-in for BullhornService (event/notes importers)."""

    def __init__(self, *, history=None, notes=None, exact_notes=None):
        self._history = history or []
        self._notes = notes or []
        self._exact_notes = self._notes if exact_notes is None else exact_notes
        self.exact_note_reads: list[str] = []

    def get_job_submission_history(self, *, job_submission_id, fields):
        return list(self._history)

    def get_job_submission_history_complete(self, *, job_submission_id, fields):
        return list(self._history)

    def query_notes(self, *, candidate_id, fields):
        return list(self._notes)

    def query_notes_complete(self, *, candidate_id, fields):
        del fields
        return [
            {
                **note,
                "personReference": note.get("personReference")
                or {"id": int(candidate_id)},
            }
            for note in self._notes
        ]

    def get_note_exact(self, note_id, *, fields):
        del fields
        normalized = str(int(note_id))
        self.exact_note_reads.append(normalized)
        for note in self._exact_notes:
            if str(int(note.get("id"))) == normalized:
                return {
                    **note,
                    "personReference": note.get("personReference")
                    or {"id": 1001},
                }
        return None

    # Not used by these unit tests, but present so the CV path no-ops cleanly.
    def list_file_attachments(self, *, candidate_id, fields):
        return []

    def list_file_attachments_strict(self, *, candidate_id, fields):
        return []


@pytest.mark.parametrize(
    ("agentic", "paused", "is_open", "expected_paid"),
    [
        pytest.param(True, False, True, True, id="enabled"),
        pytest.param(True, True, True, False, id="paused"),
        pytest.param(False, False, True, False, id="off"),
        pytest.param(True, False, False, False, id="provider-closed"),
    ],
)
def test_new_bullhorn_application_paid_dispatch_requires_running_agent(
    db,
    monkeypatch,
    *,
    agentic,
    paused,
    is_open,
    expected_paid,
):
    """Bullhorn metadata imports even when a sticky-starred agent is held."""

    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="9001",
        bullhorn_job_data={"id": 9001, "isOpen": is_open},
        job_status=JOB_STATUS_OPEN,
        starred_for_auto_sync=True,
        agentic_mode_enabled=agentic,
        agent_paused_at=(_now() if paused else None),
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()

    calls: list[dict] = []

    def _capture_created(app, **kwargs):
        calls.append({"application_id": app.id, **kwargs})

    monkeypatch.setattr(sync_candidates, "on_application_created", _capture_created)
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
            "firstName": "Ada",
            "lastName": "Lovelace",
            "email": "ada@example.com",
        },
        client=_StubClient(),
        now=_now(),
    )

    assert len(calls) == 1
    assert calls[0]["score"] is expected_paid
    assert calls[0]["allow_paid_work"] is expected_paid
    assert calls[0]["parse_origin"] == "ats_ingest"
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == role.id)
        .one()
    )
    assert app.source == "bullhorn"
    assert app.bullhorn_job_submission_id == "7001"
    assert role.starred_for_auto_sync is True


def test_new_bullhorn_application_does_not_dispatch_related_role_before_commit(
    db, monkeypatch
):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="9001",
        bullhorn_job_data={"id": 9001, "isOpen": True},
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    related = Role(
        organization_id=org.id,
        name="Platform Engineer · Data",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=role.id,
        job_spec_text=(
            "A complete related role specification for reliable Python data "
            "platforms, distributed systems, and production observability."
        ),
    )
    db.add(related)
    db.flush()

    monkeypatch.setattr(sync_candidates, "on_application_created", lambda *args, **kwargs: None)

    def _store_cv(**kwargs):
        kwargs["app"].cv_text = (
            "Python platform engineer with distributed systems and data reliability experience."
        )
        kwargs["candidate"].cv_text = kwargs["app"].cv_text

    monkeypatch.setattr(sync_candidates, "_fetch_and_store_cv", _store_cv)
    dispatched: list[tuple[list[int], str]] = []

    from app.tasks import sister_role_tasks

    monkeypatch.setattr(
        sister_role_tasks.score_sister_evaluation,
        "apply_async",
        lambda *, args, queue: dispatched.append((args, queue)),
    )

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
            "firstName": "Ada",
            "lastName": "Lovelace",
            "email": "ada-related@example.com",
        },
        client=_StubClient(),
        now=_now(),
    )

    # Importers only persist the application + transactional ingest outbox.
    # Related-role rows and task publication happen from the post-commit
    # dispatcher, so a fast worker can never race an uncommitted evaluation.
    assert (
        db.query(SisterRoleEvaluation)
        .filter(SisterRoleEvaluation.role_id == related.id)
        .count()
        == 0
    )
    assert dispatched == []


def test_bullhorn_resync_preserves_pending_outcome_writeback_receipt(db, monkeypatch):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="9001",
        bullhorn_job_data={"id": 9001, "isOpen": True},
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    monkeypatch.setattr(
        sync_candidates, "on_application_created", lambda *args, **kwargs: None
    )
    submission = {
        "id": "7001",
        "candidate": {"id": "8001"},
        "jobOrder": {"id": "9001"},
        "status": "New Lead",
    }
    candidate_payload = {
        "id": "8001",
        "firstName": "Ada",
        "lastName": "Lovelace",
        "email": "ada-writeback@example.com",
    }

    first = sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission=submission,
        candidate_payload=candidate_payload,
        client=_StubClient(),
        now=_now(),
    )
    assert first["application_upserted"] == 1
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.bullhorn_job_submission_id == "7001")
        .one()
    )
    starting_version = int(app.version or 1)
    app.deleted_at = _now()
    app.integration_sync_state = {
        "last_sync_at": _now().isoformat(),
        "sync_status": "success",
        "source": "bullhorn",
        "outcome_writeback": {
            "provider": "bullhorn",
            "status": "queued",
            "target_outcome": "rejected",
            "provider_called": False,
            "requested_at": _now().isoformat(),
        },
        AUTO_REJECT_OPERATION_KEY: {
            "operation_id": f"auto-reject:{app.id}:previous-life",
            "status": "authorized",
            "provider_called": False,
        },
        "cv_gap_rejection_operation": {
            "operation_id": f"cv-gap-reject:{app.id}:audit",
            "status": "completed",
        },
    }
    db.commit()

    second = sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission=submission,
        candidate_payload=candidate_payload,
        client=_StubClient(),
        now=_now(),
    )

    assert second["application_upserted"] == 1
    synced = db.get(CandidateApplication, int(app.id))
    assert synced.deleted_at is None
    assert synced.version == starting_version + 1
    assert (
        synced.integration_sync_state["outcome_writeback"]["status"]
        == "superseded"
    )
    assert (
        synced.integration_sync_state["outcome_writeback"]["target_outcome"]
        == "rejected"
    )
    assert (
        synced.integration_sync_state[AUTO_REJECT_OPERATION_KEY]["status"]
        == "superseded"
    )
    assert (
        synced.integration_sync_state["cv_gap_rejection_operation"]["status"]
        == "completed"
    )


def test_bullhorn_reject_sync_defers_before_partial_application_mutation(
    db, monkeypatch
):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="bullhorn",
        bullhorn_job_order_id="9001",
        job_status=JOB_STATUS_OPEN,
    )
    db.add(role)
    db.add(
        AtsStageMap(
            org_id=org.id,
            ats="bullhorn",
            remote_status="Client Rejected",
            taali_stage="review",
            is_reject=True,
        )
    )
    db.flush()
    monkeypatch.setattr(
        sync_candidates, "on_application_created", lambda *args, **kwargs: None
    )
    submission = {
        "id": "7001",
        "candidate": {"id": "8001"},
        "jobOrder": {"id": "9001"},
        "status": "New Lead",
    }
    candidate_payload = {
        "id": "8001",
        "name": "Fenced Candidate",
        "email": "bullhorn-fenced@example.com",
    }
    sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission=submission,
        candidate_payload=candidate_payload,
        client=_StubClient(),
        now=_now(),
    )
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.bullhorn_job_submission_id == "7001")
        .one()
    )
    operation_id = f"manual-bullhorn:{app.id}:inflight"
    set_outcome_writeback_state(
        app,
        provider="bullhorn",
        status="provider_call_started",
        target_outcome="rejected",
        expected_application_version=int(app.version),
        expected_local_outcome="open",
        operation_id=operation_id,
        provider_target_id="7001",
    )
    db.commit()
    before = (
        app.pipeline_stage,
        app.application_outcome,
        int(app.version),
        app.bullhorn_status,
    )
    submission["status"] = "Client Rejected"

    with pytest.raises(HTTPException):
        sync_candidates.sync_submission(
            db=db,
            org=org,
            role=role,
            submission=submission,
            candidate_payload=candidate_payload,
            client=_StubClient(),
            now=_now(),
        )
    db.rollback()
    db.refresh(app)
    assert (
        app.pipeline_stage,
        app.application_outcome,
        int(app.version),
        app.bullhorn_status,
    ) == before
    receipt = app.integration_sync_state["outcome_writeback"]
    assert receipt["operation_id"] == operation_id
    assert receipt["status"] == "provider_call_started"


def test_bullhorn_restore_then_stage_and_outcome_preserves_all_versions_and_receipts(
    db, monkeypatch
):
    org = _org(db)
    role = Role(
        organization_id=org.id,
        name="Restored Engineer",
        source="bullhorn",
        bullhorn_job_order_id="9002",
        job_status=JOB_STATUS_OPEN,
    )
    db.add(role)
    db.flush()
    monkeypatch.setattr(
        sync_candidates, "on_application_created", lambda *args, **kwargs: None
    )
    submission = {
        "id": "7002",
        "candidate": {"id": "8002"},
        "jobOrder": {"id": "9002"},
        "status": "New Lead",
    }
    candidate_payload = {
        "id": "8002",
        "name": "Restored Candidate",
        "email": "bullhorn-restored@example.com",
    }
    sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission=submission,
        candidate_payload=candidate_payload,
        client=_StubClient(),
        now=_now(),
    )
    app = (
        db.query(CandidateApplication)
        .filter(CandidateApplication.bullhorn_job_submission_id == "7002")
        .one()
    )
    db.add(
        AtsStageMap(
            org_id=org.id,
            ats="bullhorn",
            remote_status="Client Rejected",
            taali_stage="review",
            is_reject=True,
        )
    )
    app.pipeline_stage = "in_assessment"
    app.status = "in_assessment"
    starting_version = int(app.version)
    app.deleted_at = _now()
    app.integration_sync_state = {
        "source": "bullhorn",
        "last_sync_at": "2026-01-01T00:00:00+00:00",
        AUTO_REJECT_OPERATION_KEY: {
            "operation_id": f"auto-reject:{app.id}:safe",
            "status": "authorized",
            "provider_called": False,
        },
    }
    db.commit()
    submission["status"] = "Client Rejected"

    sync_candidates.sync_submission(
        db=db,
        org=org,
        role=role,
        submission=submission,
        candidate_payload=candidate_payload,
        client=_StubClient(),
        now=_now(),
    )

    db.refresh(app)
    assert app.deleted_at is None
    assert app.pipeline_stage == "review"
    assert app.application_outcome == "rejected"
    assert app.version == starting_version + 3
    assert app.integration_sync_state["source"] == "bullhorn"
    assert app.integration_sync_state["last_sync_at"]
    assert (
        app.integration_sync_state[AUTO_REJECT_OPERATION_KEY]["status"]
        == "superseded"
    )
    events = (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == app.id)
        .all()
    )
    assert sum(e.event_type == "pipeline_stage_changed" for e in events) == 1
    assert sum(e.event_type == "application_outcome_changed" for e in events) == 1


# --- stage_map --------------------------------------------------------------


class TestStageMap:
    def test_unmapped_status_is_needs_mapping(self, db):
        org = _org(db)
        assert sm.resolve_stage(db, org, "Some Custom Status") is None
        assert sm.is_needs_mapping(db, org, "Some Custom Status") is True
        # blank is not "needs mapping" (nothing to map)
        assert sm.is_needs_mapping(db, org, "") is False

    def test_mapped_status_resolves(self, db):
        org = _org(db)
        db.add(
            AtsStageMap(
                org_id=org.id,
                ats="bullhorn",
                remote_status="Client Rejected",
                taali_stage="review",
                is_reject=True,
            )
        )
        db.commit()
        mapping = sm.resolve_stage(db, org, "Client Rejected")
        assert mapping is not None
        assert mapping.taali_stage == "review"
        assert mapping.is_reject is True

    def test_seed_from_categorization_is_idempotent(self, db):
        org = _org(db)
        categorization = {
            "interviewScheduledJobResponseStatus": "Interview Scheduled",
            "confirmedJobResponseStatus": "Placed",
            "rejectedJobResponseStatus": "Client Rejected",
        }
        created = sm.seed_stage_map_from_categorization(db, org, categorization=categorization)
        db.commit()
        assert created == 3
        # rejected setting → reject mapping
        rej = sm.resolve_stage(db, org, "Client Rejected")
        assert rej is not None and rej.is_reject is True
        assert sm.resolve_stage(db, org, "Placed").is_reject is False
        # re-seed: no duplicates, no overwrite
        again = sm.seed_stage_map_from_categorization(db, org, categorization=categorization)
        db.commit()
        assert again == 0
        assert (
            db.query(AtsStageMap).filter(AtsStageMap.org_id == org.id).count() == 3
        )

    def test_unmapped_statuses_lists_seen_but_unmapped(self, db):
        org = _org(db)
        role = Role(organization_id=org.id, name="R", source="bullhorn")
        db.add(role)
        db.flush()
        # two apps carrying raw bullhorn_status, one mapped one not
        db.add(AtsStageMap(org_id=org.id, ats="bullhorn", remote_status="Placed", taali_stage="advanced", is_reject=False))
        for i, status in enumerate(("Placed", "Weird Local Status")):
            cand = _candidate(db, org, email=f"c{i}@example.com")
            db.add(
                CandidateApplication(
                    organization_id=org.id,
                    candidate_id=cand.id,
                    role_id=role.id,
                    status="applied",
                    pipeline_stage="applied",
                    application_outcome="open",
                    bullhorn_status=status,
                    version=1,
                )
            )
        db.commit()
        assert sm.unmapped_statuses(db, org) == ["Weird Local Status"]


# --- sync_jobs --------------------------------------------------------------


class TestUpsertRoleFromJobOrder:
    def test_creates_role_with_blob_and_spec(self, db):
        org = _org(db)
        job_order = {
            "id": 501,
            "title": "Senior Platform Engineer",
            "isOpen": True,
            "employmentType": "Permanent",
            "address": {"city": "Dubai", "countryName": "UAE"},
            "description": "<p>Build the platform. Own reliability.</p>",
        }
        role, created = sync_jobs.upsert_role_from_job_order(db, org, job_order)
        db.commit()
        assert created is True
        assert role is not None
        assert role.bullhorn_job_order_id == "501"
        assert role.source == "bullhorn"
        assert role.name == "Senior Platform Engineer"
        # structural mapping + blob
        assert role.bullhorn_job_data["employmentType"] == "Permanent"
        assert "# Senior Platform Engineer" in role.job_spec_text
        assert "Build the platform" in role.job_spec_text
        assert "Dubai" in role.job_spec_text
        # no raw HTML leaked
        assert "<p>" not in role.job_spec_text

    @pytest.mark.parametrize("initial_job_status", [JOB_STATUS_DRAFT, JOB_STATUS_OPEN])
    def test_adopts_stamped_requisition_role_without_losing_page_or_settings(
        self, db, monkeypatch, initial_job_status
    ):
        org = _org(db)
        role = Role(
            organization_id=org.id,
            name="Existing requisition role",
            source="requisition",
            job_status=initial_job_status,
            job_spec_text="Taali-owned requisition specification",
            monthly_usd_budget_cents=12_345,
            auto_send_assessment=False,
            auto_advance=True,
            agent_action_allowlist=["send_assessment", "advance_stage"],
        )
        db.add(role)
        db.flush()
        brief = RoleBrief(
            organization_id=org.id,
            role_id=role.id,
            ref_code="TAL-7K2QF",
        )
        db.add(brief)
        db.flush()
        page = JobPage(
            organization_id=org.id,
            brief_id=brief.id,
            token="bullhorn-adoption-page",
            status="open",
        )
        db.add(page)
        db.flush()
        role_id = role.id
        page_id = page.id

        # Keep this test scoped to adoption; attachment/criteria behavior already
        # has dedicated importer tests.
        monkeypatch.setattr(sync_jobs, "_store_job_spec_attachment", lambda _role: None)
        monkeypatch.setattr(sync_jobs, "_sync_role_criteria", lambda *_args, **_kwargs: None)

        adopted, created = sync_jobs.upsert_role_from_job_order(
            db,
            org,
            {
                "id": 8801,
                "title": "Bullhorn Platform Engineer",
                "isOpen": True,
                "publicDescription": "<p>Build systems.</p><p>Taali ref: TAL-7K2QF</p>",
            },
        )
        db.flush()

        assert created is False
        assert adopted is not None and adopted.id == role_id
        assert adopted.source == "bullhorn"
        assert adopted.bullhorn_job_order_id == "8801"
        assert adopted.job_status == JOB_STATUS_OPEN
        assert adopted.monthly_usd_budget_cents == 12_345
        assert adopted.auto_send_assessment is False
        assert adopted.auto_advance is True
        assert adopted.agent_action_allowlist == ["send_assessment", "advance_stage"]
        assert db.query(Role).filter(Role.organization_id == org.id).count() == 1
        persisted_page = db.query(JobPage).filter(JobPage.id == page_id).one()
        assert persisted_page.brief_id == brief.id
        assert persisted_page.brief.role_id == role_id
        assert adopted.version == 2
        event = (
            db.query(RoleChangeEvent)
            .filter(RoleChangeEvent.role_id == role_id)
            .one()
        )
        assert event.from_version == 1
        assert event.to_version == 2
        assert event.reason == "Bullhorn requisition role linked"
        if initial_job_status == JOB_STATUS_DRAFT:
            assert event.changes["job_status"] == {
                "before": JOB_STATUS_DRAFT,
                "after": JOB_STATUS_OPEN,
            }

    def test_stamped_job_does_not_hijack_workable_linked_requisition(self, db):
        org = _org(db)
        role = Role(
            organization_id=org.id,
            name="Workable-owned requisition",
            source="workable",
            job_status=JOB_STATUS_OPEN,
            workable_job_id="WORK-7",
        )
        db.add(role)
        db.flush()
        db.add(
            RoleBrief(
                organization_id=org.id,
                role_id=role.id,
                ref_code="TAL-8M3RF",
            )
        )
        db.flush()

        imported, created = sync_jobs.upsert_role_from_job_order(
            db,
            org,
            {
                "id": 8802,
                "title": "Duplicate external job",
                "isOpen": True,
                "description": "Taali ref: TAL-8M3RF",
            },
        )

        assert created is True
        assert imported is not None and imported.id != role.id
        assert role.bullhorn_job_order_id is None
        assert role.workable_job_id == "WORK-7"

    def test_resync_updates_same_role_no_duplicate(self, db):
        org = _org(db)
        job_order = {"id": 777, "title": "Data Engineer", "description": "Own the pipeline."}
        role1, created1 = sync_jobs.upsert_role_from_job_order(db, org, job_order)
        db.commit()
        role_id = role1.id
        # re-sync with an updated title
        job_order2 = {"id": 777, "title": "Staff Data Engineer", "description": "Own the pipeline."}
        role2, created2 = sync_jobs.upsert_role_from_job_order(db, org, job_order2)
        db.commit()
        assert created2 is False
        assert role2.id == role_id
        assert role2.name == "Staff Data Engineer"
        assert "# Staff Data Engineer" in role2.job_spec_text
        assert role2.job_spec_manually_edited_at is None
        assert db.query(Role).filter(Role.organization_id == org.id).count() == 1

    def test_manual_spec_override_survives_resync_while_metadata_refreshes(self, db):
        org = _org(db)
        manual_spec = "Recruiter-authored Bullhorn role specification remains authoritative."
        role = Role(
            organization_id=org.id,
            source="bullhorn",
            bullhorn_job_order_id="778",
            name="Old Bullhorn title",
            description=manual_spec,
            job_spec_text=manual_spec,
            job_spec_manually_edited_at=_now(),
        )
        db.add(role)
        db.commit()

        synced, created = sync_jobs.upsert_role_from_job_order(
            db,
            org,
            {
                "id": 778,
                "title": "Fresh Bullhorn title",
                "status": "Accepting Candidates",
                "description": "Fresh ATS text must not replace the manual override.",
            },
        )
        db.commit()

        assert created is False
        assert synced.id == role.id
        assert synced.name == "Fresh Bullhorn title"
        assert synced.bullhorn_job_data["status"] == "Accepting Candidates"
        assert synced.job_spec_text == manual_spec
        assert synced.description == manual_spec

    def test_legacy_taali_override_is_inferred_before_bullhorn_payload_replacement(
        self, db
    ):
        org = _org(db)
        cached_job_order = {
            "id": 779,
            "title": "Old Bullhorn title",
            "status": "Accepting Candidates",
            "description": "The previous Bullhorn-owned description.",
        }
        previous_ats_spec = sync_jobs.format_job_spec_from_job_order(cached_job_order)
        manual_spec = (
            "Legacy recruiter-authored Bullhorn role specification with requirements "
            "that must remain authoritative."
        )
        role = Role(
            organization_id=org.id,
            source="bullhorn",
            bullhorn_job_order_id="779",
            name="Old Bullhorn title",
            description=previous_ats_spec,
            job_spec_text=manual_spec,
            job_spec_uploaded_at=_now(),
            bullhorn_job_data=cached_job_order,
        )
        db.add(role)
        db.commit()

        synced, created = sync_jobs.upsert_role_from_job_order(
            db,
            org,
            {
                "id": 779,
                "title": "Fresh Bullhorn title",
                "status": "Closed",
                "description": "Fresh Bullhorn text must not replace the legacy edit.",
            },
        )
        db.commit()

        assert created is False
        assert synced.name == "Fresh Bullhorn title"
        assert synced.bullhorn_job_data["status"] == "Closed"
        assert synced.job_spec_text == manual_spec
        assert synced.description == manual_spec
        assert synced.job_spec_manually_edited_at is not None

    def test_no_id_returns_none(self, db):
        org = _org(db)
        role, created = sync_jobs.upsert_role_from_job_order(db, org, {"title": "No id"})
        assert role is None and created is False


# --- sync_events ------------------------------------------------------------


def _seed_application(
    db,
    org,
    *,
    bullhorn_candidate_id: str = "1001",
    suffix: str = "",
) -> CandidateApplication:
    role = Role(
        organization_id=org.id,
        name=f"R{suffix}",
        source="bullhorn",
        bullhorn_job_order_id=f"9{suffix}",
    )
    db.add(role)
    db.flush()
    cand = _candidate(db, org, email=f"c{suffix}@example.com")
    cand.bullhorn_candidate_id = bullhorn_candidate_id
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        bullhorn_job_submission_id=f"42{suffix}",
        version=1,
    )
    db.add(app)
    db.commit()
    return app


class TestImportSubmissionHistory:
    def test_history_appends_events_and_is_idempotent(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        history = [
            {"id": 1, "status": "New Lead", "dateAdded": 100},
            {"id": 2, "status": "Submitted", "dateAdded": 200},
            {"id": 3, "status": "Interview Scheduled", "dateAdded": 300},
        ]
        client = _StubClient(history=history)
        added = sync_events.import_submission_history(
            db=db, app=app, submission_id="42", client=client
        )
        db.commit()
        assert added == 3
        rows = (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.event_type == sync_events.BULLHORN_STATUS_CHANGE_EVENT,
            )
            .all()
        )
        assert len(rows) == 3
        # chronological from_stage chaining
        by_key = {r.idempotency_key: r for r in rows}
        assert by_key["bullhorn_jsh:2"].from_stage == "New Lead"
        assert by_key["bullhorn_jsh:2"].to_stage == "Submitted"
        # re-run: no new rows
        again = sync_events.import_submission_history(
            db=db, app=app, submission_id="42", client=client
        )
        db.commit()
        assert again == 0
        assert (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.event_type == sync_events.BULLHORN_STATUS_CHANGE_EVENT,
            )
            .count()
            == 3
        )

    def test_duplicate_history_id_in_one_response_is_deduped_not_crash(self, db):
        # A JPQL /query over an association can fan out the SAME history row twice
        # in one response. With autoflush=False the in-transaction pre-check can't
        # see the still-pending row, so a naive importer double-inserts the same
        # idempotency key → IntegrityError at flush → the whole submission upsert
        # rolls back and the record is skipped indefinitely. The importer must
        # collapse duplicates and write the row exactly once.
        org = _org(db)
        app = _seed_application(db, org)
        history = [
            {"id": 7, "status": "Submitted", "dateAdded": 100},
            {"id": 7, "status": "Submitted", "dateAdded": 100},  # duplicate id
        ]
        client = _StubClient(history=history)
        added = sync_events.import_submission_history(
            db=db, app=app, submission_id="42", client=client
        )
        db.commit()  # would raise IntegrityError before the fix
        assert added == 1
        assert (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.idempotency_key == "bullhorn_jsh:7",
            )
            .count()
            == 1
        )


class TestImportNotes:
    def test_snapshot_missing_note_requires_exact_absence_before_revocation(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        live_note = {
            "id": "00061",
            "comments": "Still live despite an unstable snapshot page.",
            "personReference": {"id": "01001"},
        }
        sync_events.import_notes(
            db=db,
            app=app,
            bullhorn_candidate_id="1001",
            client=_StubClient(notes=[live_note]),
            now=_now(),
        )
        db.commit()

        unstable = _StubClient(notes=[], exact_notes=[live_note])
        assert (
            sync_events.import_notes(
                db=db,
                app=app,
                bullhorn_candidate_id="1001",
                client=unstable,
                now=_now(),
            )
            == 0
        )
        db.commit()

        row = (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.idempotency_key == "bullhorn_note:61")
            .one()
        )
        assert unstable.exact_note_reads == ["61"]
        assert row.event_metadata["bullhorn_note_id"] == "61"
        assert row.event_metadata["for_agent"] is True
        assert row.event_metadata.get("revoked") is not True
        assert (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.event_type == "bullhorn_note_revoked")
            .count()
            == 0
        )

    def test_exact_confirmation_failure_rechecks_provider_lease(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        sync_events.import_notes(
            db=db,
            app=app,
            bullhorn_candidate_id="1001",
            client=_StubClient(notes=[{"id": 62, "comments": "Keep visible."}]),
            now=_now(),
        )
        db.commit()

        class _BrokenExact(_StubClient):
            def get_note_exact(self, note_id, *, fields):
                del note_id, fields
                raise RuntimeError("exact read failed")

        checkpoints: list[int] = []
        with pytest.raises(RuntimeError, match="exact read failed"):
            sync_events.import_notes(
                db=db,
                app=app,
                bullhorn_candidate_id="1001",
                client=_BrokenExact(notes=[]),
                now=_now(),
                provider_guard=lambda: checkpoints.append(len(checkpoints) + 1),
            )
        db.rollback()
        # Before/after the complete snapshot, then before/after the failed exact
        # provider call. The exception-side checkpoint must never be omitted.
        assert checkpoints == [1, 2, 3, 4]
        row = (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.idempotency_key == "bullhorn_note:62")
            .one()
        )
        assert row.event_metadata["for_agent"] is True

    def test_exact_event_failure_rechecks_provider_lease(self):
        class _BrokenExact:
            def get_note_exact(self, note_id, *, fields):
                del note_id, fields
                raise RuntimeError("event exact read failed")

        checkpoints: list[int] = []
        with pytest.raises(RuntimeError, match="event exact read failed"):
            event_handlers._note_payload(
                _BrokenExact(),
                "63",
                provider_guard=lambda: checkpoints.append(len(checkpoints) + 1),
            )
        assert checkpoints == [1, 2]

    def test_notes_become_agent_visible_context_and_are_idempotent(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        notes = [
            {"id": 11, "comments": "Strong on backend; light on infra.", "commentingPerson": {"name": "Jo Recruiter"}},
            {"id": 12, "comments": "Interviewed elsewhere — still keen.", "commentingPerson": {"name": "Jo Recruiter"}},
            {"id": 13, "comments": "   ", "commentingPerson": {"name": "Jo"}},  # blank → skipped
        ]
        client = _StubClient(notes=notes)
        added = sync_events.import_notes(
            db=db, app=app, bullhorn_candidate_id="1001", client=client, now=_now()
        )
        db.commit()
        assert added == 2
        rows = (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.event_type == "recruiter_note",
            )
            .all()
        )
        assert len(rows) == 2
        # agent-visible flag + source stamp, so recruiter_notes_for_agent reads them
        assert all(r.event_metadata.get("for_agent") is True for r in rows)
        assert all(r.event_metadata.get("source") == "bullhorn" for r in rows)
        # re-run: idempotent on note id
        again = sync_events.import_notes(
            db=db, app=app, bullhorn_candidate_id="1001", client=client, now=_now()
        )
        db.commit()
        assert again == 0

    def test_duplicate_note_id_in_one_response_is_deduped_not_crash(self, db):
        # A Notes /query (personReference.id) can return the SAME note twice on a
        # join fan-out. autoflush=False hides the pending row from the pre-check,
        # so a naive importer db.add-s the same idempotency key twice → IntegrityError
        # at flush → the submission upsert rolls back. Must write the note once.
        org = _org(db)
        app = _seed_application(db, org)
        notes = [
            {"id": 30, "comments": "Client wants a call.", "commentingPerson": {"name": "Lead"}},
            {"id": 30, "comments": "Client wants a call.", "commentingPerson": {"name": "Lead"}},
        ]
        client = _StubClient(notes=notes)
        added = sync_events.import_notes(
            db=db, app=app, bullhorn_candidate_id="1001", client=client, now=_now()
        )
        db.commit()  # would raise IntegrityError before the fix
        assert added == 1
        assert (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.idempotency_key == "bullhorn_note:30",
            )
            .count()
            == 1
        )

    def test_imported_notes_ride_in_agent_payload(self, db):
        from app.services.application_notes import recruiter_notes_for_agent

        org = _org(db)
        app = _seed_application(db, org)
        client = _StubClient(notes=[{"id": 21, "comments": "Do not reject — client wants a call.", "commentingPerson": {"name": "Lead"}}])
        sync_events.import_notes(
            db=db, app=app, bullhorn_candidate_id="1001", client=client, now=_now()
        )
        db.commit()
        db.refresh(app)
        agent_notes = recruiter_notes_for_agent(app)
        assert any("client wants a call" in n["note"] for n in agent_notes)

    def test_failed_complete_snapshot_never_revokes_existing_context(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        sync_events.import_notes(
            db=db,
            app=app,
            bullhorn_candidate_id="1001",
            client=_StubClient(notes=[{"id": 41, "comments": "Keep visible."}]),
            now=_now(),
        )
        db.commit()

        class _FailedSnapshot:
            def query_notes_complete(self, **_kwargs):
                raise RuntimeError("partial final page")

        with pytest.raises(RuntimeError, match="partial final page"):
            sync_events.import_notes(
                db=db,
                app=app,
                bullhorn_candidate_id="1001",
                client=_FailedSnapshot(),
                now=_now(),
            )
        db.rollback()

        note = (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.idempotency_key == "bullhorn_note:41",
            )
            .one()
        )
        assert note.event_metadata["for_agent"] is True
        assert note.event_metadata.get("revoked") is not True

    def test_blank_remote_revision_revokes_prior_context_idempotently(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        sync_events.import_notes(
            db=db,
            app=app,
            bullhorn_candidate_id="1001",
            client=_StubClient(notes=[{"id": 45, "comments": "Do not reject."}]),
            now=_now(),
        )
        db.commit()

        blank = _StubClient(notes=[{"id": 45, "comments": "   "}])
        assert (
            sync_events.import_notes(
                db=db,
                app=app,
                bullhorn_candidate_id="1001",
                client=blank,
                now=_now(),
            )
            == 0
        )
        db.commit()
        original = (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.idempotency_key == "bullhorn_note:45")
            .one()
        )
        assert original.event_metadata["for_agent"] is False
        assert original.event_metadata["revoked"] is True
        assert original.event_metadata["revocation_source"] == "bullhorn_blank_note"

        sync_events.import_notes(
            db=db,
            app=app,
            bullhorn_candidate_id="1001",
            client=blank,
            now=_now(),
        )
        db.commit()
        assert (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.event_type == "bullhorn_note_revoked")
            .count()
            == 1
        )

    def test_negative_reconciliation_is_tenant_and_candidate_scoped(self, db):
        org = _org(db)
        target = _seed_application(db, org, suffix="target")
        other_candidate = _seed_application(
            db,
            org,
            bullhorn_candidate_id="2002",
            suffix="other",
        )
        other_org = _org(db)
        other_tenant = _seed_application(db, other_org, suffix="tenant")
        for app, candidate_id, note_id in (
            (target, "1001", 51),
            (other_candidate, "2002", 52),
            (other_tenant, "1001", 51),
        ):
            sync_events.import_notes(
                db=db,
                app=app,
                bullhorn_candidate_id=candidate_id,
                client=_StubClient(notes=[{"id": note_id, "comments": "Scoped."}]),
                now=_now(),
            )
            db.commit()

        sync_events.import_notes(
            db=db,
            app=target,
            bullhorn_candidate_id="1001",
            client=_StubClient(notes=[]),
            now=_now(),
        )
        db.commit()

        notes = {
            row.application_id: row
            for row in db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.event_type == "recruiter_note")
            .all()
        }
        assert notes[target.id].event_metadata["for_agent"] is False
        assert notes[target.id].event_metadata["revoked"] is True
        assert notes[other_candidate.id].event_metadata["for_agent"] is True
        assert notes[other_tenant.id].event_metadata["for_agent"] is True

    def test_reassignment_and_workable_authority_repair_every_placement(self, db):
        org = _org(db)
        source_app = _seed_application(
            db,
            org,
            bullhorn_candidate_id="1001",
            suffix="source",
        )
        target_app = _seed_application(
            db,
            org,
            bullhorn_candidate_id="2002",
            suffix="target",
        )
        target_candidate = db.get(Candidate, target_app.candidate_id)
        workable_role = Role(
            organization_id=org.id,
            name="Workable authority",
            source="workable",
            workable_job_id="workable-role",
        )
        db.add(workable_role)
        db.flush()
        workable_app = CandidateApplication(
            organization_id=org.id,
            candidate_id=target_candidate.id,
            role_id=workable_role.id,
            source="workable",
            workable_candidate_id="workable-candidate",
            bullhorn_job_submission_id="evidence-only",
            status="applied",
            pipeline_stage="applied",
            application_outcome="open",
            version=1,
        )
        db.add(workable_app)
        other_org = _org(db)
        other_tenant_app = _seed_application(db, other_org)

        original = {
            "id": 80,
            "comments": "Candidate A guidance.",
            "personReference": {"id": 1001},
        }
        sync_events.apply_exact_note(
            db=db,
            org_id=org.id,
            note=original,
            now=_now(),
        )
        sync_events.apply_exact_note(
            db=db,
            org_id=other_org.id,
            note=original,
            now=_now(),
        )
        # Seed a historical cross-provider placement so the new authority pass
        # proves it repairs existing contamination as well as preventing it.
        sync_events.upsert_note_revision(
            db=db,
            app=workable_app,
            note={**original, "personReference": {"id": 2002}},
            now=_now(),
        )
        db.commit()

        moved = {
            "id": "080",
            "comments": "Candidate B guidance.",
            "personReference": {"id": "02002"},
        }
        first = sync_events.apply_exact_note(
            db=db,
            org_id=org.id,
            note=moved,
            now=_now(),
        )
        db.commit()
        assert first == {"created": 1, "revoked": 2}

        def _active(app_id):
            rows = (
                db.query(CandidateApplicationEvent)
                .filter(
                    CandidateApplicationEvent.application_id == app_id,
                    CandidateApplicationEvent.event_type == "recruiter_note",
                )
                .all()
            )
            return [
                row
                for row in rows
                if row.event_metadata.get("for_agent") is not False
                and row.event_metadata.get("revoked") is not True
                and row.event_metadata.get("superseded") is not True
            ]

        assert _active(source_app.id) == []
        assert len(_active(target_app.id)) == 1
        assert _active(workable_app.id) == []
        assert len(_active(other_tenant_app.id)) == 1
        tombstones_before = (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.organization_id == org.id,
                CandidateApplicationEvent.event_type == "bullhorn_note_revoked",
            )
            .count()
        )
        assert sync_events.apply_exact_note(
            db=db,
            org_id=org.id,
            note=moved,
            now=_now(),
        ) == {"created": 0, "revoked": 0}
        db.commit()
        assert (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.organization_id == org.id,
                CandidateApplicationEvent.event_type == "bullhorn_note_revoked",
            )
            .count()
            == tombstones_before
        )

    def test_repeated_revocation_preserves_prior_tombstone_metadata(self, db):
        org = _org(db)
        app = _seed_application(db, org)
        note = {
            "id": 91,
            "comments": "Revocation history must be immutable.",
            "personReference": {"id": 1001},
        }
        sync_events.apply_exact_note(
            db=db,
            org_id=org.id,
            note=note,
            now=_now(),
        )
        first_at = _now()
        note_reconciliation.revoke_note_placements(
            db=db,
            org_id=org.id,
            note_id="91",
            now=first_at,
            source="first_revocation",
        )
        db.commit()
        first_revision = (
            db.query(CandidateApplicationEvent)
            .filter(CandidateApplicationEvent.idempotency_key == "bullhorn_note:91")
            .one()
        )
        first_metadata = dict(first_revision.event_metadata)

        sync_events.apply_exact_note(
            db=db,
            org_id=org.id,
            note=note,
            now=first_at + timedelta(minutes=1),
        )
        db.commit()
        note_reconciliation.revoke_note_placements(
            db=db,
            org_id=org.id,
            note_id="91",
            now=first_at + timedelta(minutes=2),
            source="second_revocation",
        )
        db.commit()
        db.refresh(first_revision)
        assert first_revision.event_metadata == first_metadata
        revisions = (
            db.query(CandidateApplicationEvent)
            .filter(
                CandidateApplicationEvent.application_id == app.id,
                CandidateApplicationEvent.event_type == "recruiter_note",
            )
            .order_by(CandidateApplicationEvent.id.asc())
            .all()
        )
        assert len(revisions) == 2
        assert revisions[1].event_metadata["revocation_source"] == "second_revocation"
