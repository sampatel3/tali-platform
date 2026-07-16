from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.components.integrations.bullhorn import sync_jobs as bullhorn_sync
from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.services.role_change_audit import (
    ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED,
    ROLE_CHANGE_ACTION_RESTORED,
)


def _organization(db, *, suffix: str) -> Organization:
    org = Organization(
        name=f"ATS collaboration {suffix}",
        slug=f"ats-collaboration-{suffix}",
    )
    db.add(org)
    db.commit()
    return org


def test_workable_sync_versions_and_audits_material_change_but_not_noop(
    db, monkeypatch
):
    org = _organization(db, suffix="workable")
    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="WORK-42",
        name="Old Workable title",
        description="Old Workable specification",
        job_spec_text="Old Workable specification",
        workable_job_data={
            "shortcode": "WORK-42",
            "title": "Old Workable title",
            "details": {"description": "Old Workable specification"},
        },
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.commit()
    assert role.version == 1

    from app.platform.config import settings

    monkeypatch.setattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False)
    service = WorkableSyncService(
        WorkableService(access_token="test-token", subdomain="test")
    )
    job = {
        "shortcode": "WORK-42",
        "title": "New Workable title",
        "state": "draft",
    }
    new_spec = "New Workable specification with materially changed requirements."

    def _sync() -> Role:
        with patch.object(
            service,
            "_job_details_for_role",
            return_value={"description": new_spec},
        ), patch.object(service, "_refresh_role_stages"), patch(
            "app.components.integrations.workable.sync_service._format_job_spec_from_api",
            return_value=new_spec,
        ), patch(
            "app.components.integrations.workable.sync_service.upload_bytes_to_s3",
            return_value=None,
        ), patch(
            "app.services.role_criteria_service.sync_derived_criteria"
        ):
            synced, created = service._upsert_role(db, org, job)
        assert created is False
        db.commit()
        db.refresh(synced)
        return synced

    synced = _sync()
    assert synced.version == 2
    assert synced.name == "New Workable title"
    assert synced.job_spec_text == new_spec

    events = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.action == ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED
    assert event.from_version == 1
    assert event.to_version == 2
    assert event.actor_user_id is None
    assert event.reason == "Workable pull sync"
    assert event.request_id == "workable-job:WORK-42"
    assert {"name", "description", "job_spec_text"}.issubset(event.changes)

    # The same remote snapshot is not a collaboration change. It must neither
    # advance the version nor append a misleading history row.
    synced = _sync()
    assert synced.version == 2
    assert (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .count()
        == 1
    )


def test_workable_restore_stays_off_paused_and_is_versioned(db, monkeypatch):
    org = _organization(db, suffix="workable-restore")
    spec = "Stable Workable specification for a restored engineering job."
    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="WORK-RESTORE",
        name="Restored Workable role",
        description=spec,
        job_spec_text=spec,
        workable_job_data={
            "shortcode": "WORK-RESTORE",
            "title": "Restored Workable role",
            "details": {"description": spec},
        },
        deleted_at=datetime.now(timezone.utc),
        agentic_mode_enabled=True,
        assessment_task_provisioning={
            "activation_intent": {
                "status": "pending",
                "request_id": "workable-restore-intent",
            }
        },
    )
    db.add(role)
    db.commit()

    from app.platform.config import settings

    monkeypatch.setattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False)
    service = WorkableSyncService(
        WorkableService(access_token="test-token", subdomain="test")
    )
    job = {
        "shortcode": "WORK-RESTORE",
        "title": "Restored Workable role",
        "state": "draft",
    }
    with patch.object(
        service,
        "_job_details_for_role",
        return_value={"description": spec},
    ), patch.object(service, "_refresh_role_stages"), patch(
        "app.components.integrations.workable.sync_service._format_job_spec_from_api",
        return_value=spec,
    ):
        restored, created = service._upsert_role(db, org, job)
    assert created is False
    db.commit()
    db.refresh(restored)

    assert restored.deleted_at is None
    assert restored.agentic_mode_enabled is False
    assert restored.agent_paused_at is not None
    assert "Workable job restored" in (restored.agent_paused_reason or "")
    assert restored.version == 2
    assert (
        restored.assessment_task_provisioning["activation_intent"]["status"]
        == "cancelled"
    )
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.action == ROLE_CHANGE_ACTION_RESTORED
    assert event.from_version == 1
    assert event.to_version == 2
    assert {"deleted_at", "agentic_mode_enabled", "agent_paused_at"}.issubset(
        event.changes
    )


def test_bullhorn_sync_versions_and_audits_material_change_but_not_noop(
    db, monkeypatch
):
    org = _organization(db, suffix="bullhorn")
    previous_job = {
        "id": 707,
        "title": "Old Bullhorn title",
        "description": "Old Bullhorn specification.",
    }
    previous_spec = bullhorn_sync.format_job_spec_from_job_order(previous_job)
    role = Role(
        organization_id=org.id,
        source="bullhorn",
        bullhorn_job_order_id="707",
        bullhorn_job_data=previous_job,
        name="Old Bullhorn title",
        description=previous_spec,
        job_spec_text=previous_spec,
    )
    db.add(role)
    db.commit()
    assert role.version == 1

    from app.platform.config import settings

    monkeypatch.setattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False)
    job = {
        "id": 707,
        "title": "New Bullhorn title",
        "description": "New Bullhorn specification with changed requirements.",
    }

    def _sync() -> Role:
        with patch.object(bullhorn_sync, "_store_job_spec_attachment"), patch.object(
            bullhorn_sync, "_sync_role_criteria"
        ):
            synced, created = bullhorn_sync.upsert_role_from_job_order(db, org, job)
        assert synced is not None
        assert created is False
        db.commit()
        db.refresh(synced)
        return synced

    synced = _sync()
    assert synced.version == 2
    assert synced.name == "New Bullhorn title"
    assert "New Bullhorn specification" in synced.job_spec_text

    events = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .all()
    )
    assert len(events) == 1
    event = events[0]
    assert event.action == ROLE_CHANGE_ACTION_JOB_SPEC_UPDATED
    assert event.from_version == 1
    assert event.to_version == 2
    assert event.actor_user_id is None
    assert event.reason == "Bullhorn pull sync"
    assert event.request_id == "bullhorn-job:707"
    assert {"name", "description", "job_spec_text"}.issubset(event.changes)

    synced = _sync()
    assert synced.version == 2
    assert (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .count()
        == 1
    )


def test_bullhorn_sync_refreshes_stale_identity_map_before_version_bump(
    db, monkeypatch
):
    """The locked read must use the committed version, not a cached ORM row."""

    org = _organization(db, suffix="bullhorn-stale-session")
    previous_job = {
        "id": 808,
        "title": "Cached title",
        "description": "Cached specification.",
    }
    previous_spec = bullhorn_sync.format_job_spec_from_job_order(previous_job)
    role = Role(
        organization_id=org.id,
        source="bullhorn",
        bullhorn_job_order_id="808",
        bullhorn_job_data=previous_job,
        name="Cached title",
        description=previous_spec,
        job_spec_text=previous_spec,
    )
    db.add(role)
    db.commit()
    assert role.version == 1  # intentionally leave v1 in this session's cache

    concurrent_db = Session(bind=db.get_bind())
    try:
        concurrently_updated = concurrent_db.get(Role, role.id)
        assert concurrently_updated is not None
        concurrently_updated.version = 2
        concurrent_db.commit()
    finally:
        concurrent_db.close()

    # The original session still holds v1. populate_existing() on the locked
    # query must refresh it to v2 before this sync advances it to v3.
    assert role.version == 1
    from app.platform.config import settings

    monkeypatch.setattr(settings, "AUTO_GENERATE_ASSESSMENT_TASKS", False)
    with patch.object(bullhorn_sync, "_store_job_spec_attachment"), patch.object(
        bullhorn_sync, "_sync_role_criteria"
    ):
        synced, created = bullhorn_sync.upsert_role_from_job_order(
            db,
            org,
            {
                "id": 808,
                "title": "Fresh title",
                "description": "Fresh specification after concurrent update.",
            },
        )
    assert synced is not None and created is False
    db.commit()
    db.refresh(synced)

    assert synced.version == 3
    event = (
        db.query(RoleChangeEvent)
        .filter(RoleChangeEvent.role_id == role.id)
        .one()
    )
    assert event.from_version == 2
    assert event.to_version == 3
