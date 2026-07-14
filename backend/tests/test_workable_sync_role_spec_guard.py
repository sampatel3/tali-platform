"""Sync must NOT re-derive criteria when the Workable job spec is unchanged.

`sync_derived_criteria` hard-deletes + re-inserts derived criteria with new row
IDs. The decision-staleness fingerprint includes those IDs, so re-deriving an
*unchanged* spec on every sync tick spuriously invalidates every pending
decision for the role. The role-import guard re-derives only on a real change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import (
    WorkableSyncService,
    _format_job_spec_from_api,
)
from app.models.organization import Organization
from app.models.role import JOB_STATUS_OPEN, Role
from app.models.role_criterion import CRITERION_SOURCE_DERIVED
from app.services.job_page_lifecycle import native_intake_state

_REQS = (
    "Description\nSenior backend role.\n"
    "Requirements\n- 5+ years Python\n- Postgres at scale\n"
    "Benefits\n- Health insurance\n"
)
_REQS_CHANGED = (
    "Description\nSenior backend role.\n"
    "Requirements\n- 5+ years Python\n- Postgres at scale\n- Kubernetes in prod\n"
    "Benefits\n- Health insurance\n"
)


def _derived_ids(role):
    return sorted(
        c.id for c in role.criteria
        if c.source == CRITERION_SOURCE_DERIVED and c.deleted_at is None
    )


def _sync_once(db, svc, org, job, spec):
    with patch(
        "app.components.integrations.workable.sync_service._format_job_spec_from_api",
        return_value=spec,
    ), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3",
        return_value="s3://fake/job-spec.txt",
    ), patch(
        "app.components.integrations.workable.sync_service.build_role_interview_pack_templates",
        return_value={"screening": {}, "tech_stage_2": {}},
    ), patch.object(
        svc,
        "_job_details_for_role",
        return_value={"description": "Fetched Workable job details"},
    ):
        role, created = svc._upsert_role(db, org, job)
    db.commit()
    db.refresh(role)
    return role, created


def test_unchanged_spec_does_not_rederive_criteria(db):
    org = Organization(name="O", slug="o-guard")
    db.add(org)
    db.flush()
    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    job = {"shortcode": "JOBGUARD", "title": "Senior Role"}

    role, created = _sync_once(db, svc, org, job, _REQS)
    assert created
    ids1 = _derived_ids(role)
    assert ids1, "first sync should derive criteria from the Requirements section"

    # Same spec again → must NOT churn (identical IDs, no hard-replace).
    role, created = _sync_once(db, svc, org, job, _REQS)
    assert created is False
    assert _derived_ids(role) == ids1, "unchanged spec must not re-derive (would churn staleness)"

    # Changed spec → must re-derive (new criteria).
    role, _ = _sync_once(db, svc, org, job, _REQS_CHANGED)
    ids3 = _derived_ids(role)
    assert ids3 != ids1, "changed spec must re-derive"
    assert any(
        c.text == "Kubernetes in prod"
        for c in role.criteria
        if c.source == CRITERION_SOURCE_DERIVED and c.deleted_at is None
    )


def test_empty_detail_fetch_merges_cached_rich_job_data(db):
    org = Organization(name="O", slug="o-cached-workable-detail")
    db.add(org)
    db.flush()

    cached_job_data = {
        "shortcode": "JOBCACHED",
        "title": "Senior Role",
        "state": "published",
        "details": {
            "description": "<p>Last known rich role narrative.</p>",
            "requirements": "<ul><li>Python at scale.</li></ul>",
        },
    }
    previous_spec = _format_job_spec_from_api(cached_job_data)
    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="JOBCACHED",
        name="Senior Role",
        description=previous_spec,
        job_spec_text=previous_spec,
        workable_job_data=cached_job_data,
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.flush()

    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    list_job = {
        "shortcode": "JOBCACHED",
        "title": "Senior Role",
        "state": "closed",
    }
    with patch.object(svc, "_job_details_for_role", return_value={}), patch.object(
        svc, "_refresh_role_stages"
    ), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3",
    ) as upload_mock, patch(
        "app.services.role_criteria_service.sync_derived_criteria"
    ) as derive_mock:
        synced_role, created = svc._upsert_role(db, org, list_job)

    assert created is False
    assert synced_role.workable_job_data["state"] == "closed"
    assert native_intake_state(synced_role)["reason"] == "ats_job_not_live"
    assert synced_role.workable_job_data["details"] == cached_job_data["details"]
    assert synced_role.job_spec_text == previous_spec
    assert synced_role.description == previous_spec
    assert "Last known rich role narrative." in previous_spec
    assert "Python at scale." in previous_spec
    upload_mock.assert_not_called()
    derive_mock.assert_not_called()


def test_empty_detail_fetch_does_not_replace_rich_spec_with_metadata_only(db):
    org = Organization(name="O", slug="o-preserve-workable-spec")
    db.add(org)
    db.flush()

    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="JOBPRESERVE",
        name="Senior Role",
        description=_REQS,
        job_spec_text=_REQS,
        # Older rows may have retained the text spec without a rich raw payload.
        workable_job_data={
            "shortcode": "JOBPRESERVE",
            "title": "Senior Role",
            "state": "published",
        },
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.flush()

    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    list_job = {
        "shortcode": "JOBPRESERVE",
        "title": "Renamed Senior Role",
        "state": "closed",
    }
    with patch.object(svc, "_job_details_for_role", return_value={}), patch.object(
        svc, "_refresh_role_stages"
    ), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3"
    ) as upload_mock, patch(
        "app.services.role_criteria_service.sync_derived_criteria"
    ) as derive_mock:
        synced_role, created = svc._upsert_role(db, org, list_job)

    assert created is False
    assert synced_role.name == "Renamed Senior Role"
    assert synced_role.workable_job_data["state"] == "closed"
    assert synced_role.job_spec_text == _REQS
    assert synced_role.description == _REQS
    upload_mock.assert_not_called()
    derive_mock.assert_not_called()


def test_empty_detail_fetch_still_accepts_description_from_list_payload(db):
    org = Organization(name="O", slug="o-list-workable-spec")
    db.add(org)
    db.flush()

    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="JOBLISTSPEC",
        name="Senior Role",
        description=_REQS,
        job_spec_text=_REQS,
        workable_job_data={
            "shortcode": "JOBLISTSPEC",
            "title": "Senior Role",
            "details": {
                "description": "<p>Stale cached description.</p>",
                "requirements": "<ul><li>Python at scale.</li></ul>",
                "benefits": "<p>Hybrid working.</p>",
            },
        },
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.flush()

    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    list_job = {
        "shortcode": "JOBLISTSPEC",
        "title": "Senior Role",
        "description": "<p>Fresh description supplied by the expanded jobs list.</p>",
    }
    with patch.object(svc, "_job_details_for_role", return_value={}), patch.object(
        svc, "_refresh_role_stages"
    ), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3",
        return_value="s3://fake/job-spec.txt",
    ) as upload_mock, patch(
        "app.services.role_criteria_service.sync_derived_criteria"
    ) as derive_mock:
        synced_role, created = svc._upsert_role(db, org, list_job)

    assert created is False
    assert "Fresh description supplied by the expanded jobs list." in synced_role.job_spec_text
    assert "Stale cached description." not in synced_role.job_spec_text
    assert "Python at scale." in synced_role.job_spec_text
    assert "Hybrid working." in synced_role.job_spec_text
    assert synced_role.workable_job_data["description"] == list_job["description"]
    upload_mock.assert_called_once()
    derive_mock.assert_called_once()


def test_manual_spec_override_survives_workable_sync_while_metadata_refreshes(db):
    org = Organization(name="O", slug="o-manual-workable-spec")
    db.add(org)
    db.flush()
    edited_at = datetime.now(timezone.utc)
    manual_spec = "Recruiter-authored role specification that must remain authoritative."
    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="JOBMANUAL",
        name="Old ATS title",
        description=manual_spec,
        job_spec_text=manual_spec,
        job_spec_manually_edited_at=edited_at,
        workable_job_data={"shortcode": "JOBMANUAL", "state": "published"},
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.flush()

    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    job = {
        "shortcode": "JOBMANUAL",
        "title": "Fresh ATS title",
        "state": "closed",
        "description": "Fresh external description that must not replace the override.",
    }
    with patch.object(
        svc,
        "_job_details_for_role",
        return_value={"description": job["description"]},
    ), patch.object(svc, "_refresh_role_stages"), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3"
    ) as upload_mock, patch(
        "app.services.role_criteria_service.sync_derived_criteria"
    ) as derive_mock:
        synced_role, created = svc._upsert_role(db, org, job)

    assert created is False
    assert synced_role.name == "Fresh ATS title"
    assert synced_role.workable_job_data["state"] == "closed"
    assert synced_role.job_spec_text == manual_spec
    assert synced_role.description == manual_spec
    assert synced_role.job_spec_manually_edited_at == edited_at
    upload_mock.assert_not_called()
    derive_mock.assert_not_called()


def test_legacy_taali_override_is_inferred_before_workable_payload_replacement(db):
    org = Organization(name="O", slug="o-legacy-manual-workable-spec")
    db.add(org)
    db.flush()
    cached_job_data = {
        "shortcode": "JOBLEGACYMANUAL",
        "title": "Old ATS title",
        "state": "published",
        "details": {"description": "The previous ATS-owned description."},
    }
    previous_ats_spec = _format_job_spec_from_api(cached_job_data)
    manual_spec = (
        "Legacy recruiter-authored specification with authoritative requirements "
        "that differ from the cached Workable description."
    )
    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="JOBLEGACYMANUAL",
        name="Old ATS title",
        # Before migration 161 uploads/agent edits changed job_spec_text but
        # left description at its last ATS-synced value.
        description=previous_ats_spec,
        job_spec_text=manual_spec,
        job_spec_uploaded_at=datetime.now(timezone.utc),
        workable_job_data=cached_job_data,
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.flush()

    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    job = {
        "shortcode": "JOBLEGACYMANUAL",
        "title": "Fresh ATS title",
        "state": "closed",
        "description": "Fresh external text that must not overwrite the legacy edit.",
    }
    with patch.object(
        svc,
        "_job_details_for_role",
        return_value={"description": job["description"]},
    ), patch.object(svc, "_refresh_role_stages"), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3"
    ) as upload_mock, patch(
        "app.services.role_criteria_service.sync_derived_criteria"
    ) as derive_mock:
        synced_role, created = svc._upsert_role(db, org, job)

    assert created is False
    assert synced_role.name == "Fresh ATS title"
    assert synced_role.workable_job_data["state"] == "closed"
    assert synced_role.job_spec_text == manual_spec
    assert synced_role.description == manual_spec
    assert synced_role.job_spec_manually_edited_at is not None
    upload_mock.assert_not_called()
    derive_mock.assert_not_called()


def test_legacy_unmarked_workable_owned_spec_still_refreshes(db):
    org = Organization(name="O", slug="o-legacy-workable-owned-spec")
    db.add(org)
    db.flush()
    cached_job_data = {
        "shortcode": "JOBLEGACYATS",
        "title": "ATS-owned role",
        "state": "published",
        "details": {"description": "The previous ATS-owned description."},
    }
    previous_ats_spec = _format_job_spec_from_api(cached_job_data)
    role = Role(
        organization_id=org.id,
        source="workable",
        workable_job_id="JOBLEGACYATS",
        name="ATS-owned role",
        description=previous_ats_spec,
        job_spec_text=previous_ats_spec,
        workable_job_data=cached_job_data,
        screening_pack_template={},
        tech_interview_pack_template={},
    )
    db.add(role)
    db.flush()

    svc = WorkableSyncService(WorkableService(access_token="x", subdomain="t"))
    job = {
        "shortcode": "JOBLEGACYATS",
        "title": "Renamed ATS-owned role",
        "state": "closed",
        "description": "A fresh ATS-owned description.",
    }
    with patch.object(
        svc,
        "_job_details_for_role",
        return_value={"description": job["description"]},
    ), patch.object(svc, "_refresh_role_stages"), patch(
        "app.components.integrations.workable.sync_service.upload_bytes_to_s3",
        return_value="s3://fake/job-spec.txt",
    ), patch(
        "app.services.role_criteria_service.sync_derived_criteria"
    ):
        synced_role, created = svc._upsert_role(db, org, job)

    assert created is False
    assert synced_role.name == "Renamed ATS-owned role"
    assert "A fresh ATS-owned description." in synced_role.job_spec_text
    assert synced_role.job_spec_text != previous_ats_spec
    assert synced_role.description == synced_role.job_spec_text
    assert synced_role.job_spec_manually_edited_at is None
