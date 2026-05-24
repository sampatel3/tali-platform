"""Sync must NOT re-derive criteria when the Workable job spec is unchanged.

`sync_derived_criteria` hard-deletes + re-inserts derived criteria with new row
IDs. The decision-staleness fingerprint includes those IDs, so re-deriving an
*unchanged* spec on every sync tick spuriously invalidates every pending
decision for the role. The role-import guard re-derives only on a real change.
"""
from __future__ import annotations

from unittest.mock import patch

from app.components.integrations.workable.service import WorkableService
from app.components.integrations.workable.sync_service import WorkableSyncService
from app.models.organization import Organization
from app.models.role_criterion import CRITERION_SOURCE_DERIVED

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
    ), patch.object(svc, "_job_details_for_role", return_value={}):
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
