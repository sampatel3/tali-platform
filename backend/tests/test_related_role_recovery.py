"""Targeted legacy-hold recovery never resumes unrelated paid work."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import app.services.related_role_scope_snapshot as scope_snapshot_service
from app.agent_runtime.budget_guard import BudgetCheck
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
from app.models.user import User
from app.services.related_role_scope_snapshot import related_role_scope_snapshot
from tests.conftest import auth_headers


def _seed_recovery_scope(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    paused_at = datetime.now(timezone.utc)
    organization = user.organization
    organization.agent_workspace_paused_at = paused_at
    organization.agent_workspace_paused_reason = "workspace paused by recruiter"
    organization.agent_workspace_paused_by_user_id = int(user.id)
    organization.agent_workspace_paused_by_name = str(user.full_name or user.email)
    organization.agent_workspace_control_version = 7
    source = Role(
        organization_id=int(user.organization_id),
        name="Original AI Engineer",
        source="workable",
        workable_job_id="AI-RECOVERY",
    )
    related = Role(
        organization_id=int(user.organization_id),
        name="Security scoring view",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=None,
        agentic_mode_enabled=True,
        agent_paused_at=paused_at,
        agent_paused_reason="paused by workspace control",
        monthly_usd_budget_cents=5000,
        job_spec_text="Security-focused related-role scoring specification.",
    )
    already_paused = Role(
        organization_id=int(user.organization_id),
        name="Unrelated paused role",
        source="manual",
        agentic_mode_enabled=True,
        agent_paused_at=paused_at,
        agent_paused_reason="paused by recruiter",
        monthly_usd_budget_cents=5000,
    )
    overlay_only = Role(
        organization_id=int(user.organization_id),
        name="Unrelated overlay-only role",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
    )
    db.add_all([source, related, already_paused, overlay_only])
    db.flush()
    related.ats_owner_role_id = int(source.id)
    candidate = Candidate(
        organization_id=int(user.organization_id),
        full_name="Recovery Candidate",
        email="related-recovery@example.com",
        cv_text="Production security and Python engineering experience.",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(user.organization_id),
        candidate_id=int(candidate.id),
        role_id=int(source.id),
        source="workable",
        application_outcome="open",
        cv_text=candidate.cv_text,
    )
    db.add(application)
    db.flush()
    db.add(
        SisterRoleEvaluation(
            organization_id=int(user.organization_id),
            role_id=int(related.id),
            source_application_id=int(application.id),
            status="retry_wait",
            spec_fingerprint="recovery-spec",
            last_error_code="authority_blocked",
        )
    )
    db.commit()
    return {
        "headers": headers,
        "organization": organization,
        "source": source,
        "related": related,
        "already_paused": already_paused,
        "overlay_only": overlay_only,
        "user": user,
    }


def _authority(db, seeded):
    related = seeded["related"]
    scope = related_role_scope_snapshot(db, related)
    return {
        "expected_version": int(related.version or 1),
        "expected_workspace_control_version": 7,
        "expected_role_family": {
            "owner": {
                "id": int(seeded["source"].id),
                "name": seeded["source"].name,
            },
            "related": [{"id": int(related.id), "name": related.name}],
        },
        "cohort_fingerprint": scope["cohort_fingerprint"],
        "approved_max_candidates_total": scope["total"],
        "approved_max_scoreable_count": scope["scoreable"],
    }


def _resume_target(_db, *, role, explicit):
    assert explicit is True
    role.agent_paused_at = None
    role.agent_paused_reason = None
    return True


def test_recovery_scope_hashes_the_exact_cohort_once_outside_progress_polling(
    client,
    db,
):
    seeded = _seed_recovery_scope(client, db)

    with patch(
        "app.services.related_role_scope_snapshot.text_fingerprint",
        wraps=scope_snapshot_service.text_fingerprint,
    ) as hash_cv:
        response = client.get(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "legacy-workspace-recovery-scope",
            headers=seeded["headers"],
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role_id"] == int(seeded["related"].id)
    assert body["workspace_paused"] is True
    assert body["workspace_control_version"] == 7
    assert body["cohort_total"] == 1
    assert body["cohort_scoreable"] == 1
    assert len(body["cohort_fingerprint"]) == 64
    assert body["role_family"] == {
        "owner": {
            "id": int(seeded["source"].id),
            "name": seeded["source"].name,
        },
        "related": [
            {
                "id": int(seeded["related"].id),
                "name": seeded["related"].name,
            }
        ],
    }
    assert hash_cv.call_count == 1


def test_recovery_scope_is_owner_only_and_does_not_hash_for_members(client, db):
    seeded = _seed_recovery_scope(client, db)
    seeded["user"].role = "member"
    db.commit()

    with patch(
        "app.services.related_role_scope_snapshot.text_fingerprint"
    ) as hash_cv:
        response = client.get(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "legacy-workspace-recovery-scope",
            headers=seeded["headers"],
        )

    assert response.status_code == 403, response.text
    hash_cv.assert_not_called()


def test_recovery_scope_rejects_an_orphaned_related_role_before_hashing(client, db):
    seeded = _seed_recovery_scope(client, db)
    seeded["source"].deleted_at = datetime.now(timezone.utc)
    db.commit()

    with patch(
        "app.services.related_role_scope_snapshot.text_fingerprint"
    ) as hash_cv:
        response = client.get(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "legacy-workspace-recovery-scope",
            headers=seeded["headers"],
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Role is not a coupled related role"
    hash_cv.assert_not_called()


def test_related_role_recovery_preserves_every_unrelated_role_pause(client, db):
    seeded = _seed_recovery_scope(client, db)
    authority = _authority(db, seeded)
    already_version = int(seeded["already_paused"].version or 1)
    overlay_version = int(seeded["overlay_only"].version or 1)

    with (
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "budget_guard.resume_if_under_budget",
            side_effect=_resume_target,
        ),
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "dispatch_role_agent_cycle"
        ) as dispatch,
    ):
        response = client.post(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 200, response.text
    assert response.json()["preserved_paused_count"] == 2
    db.expire_all()
    assert db.get(type(seeded["organization"]), seeded["organization"].id).agent_workspace_paused_at is None
    target = db.get(Role, seeded["related"].id)
    already = db.get(Role, seeded["already_paused"].id)
    overlay_only = db.get(Role, seeded["overlay_only"].id)
    assert target.agent_paused_at is None
    assert already.agent_paused_at is not None
    assert int(already.version or 1) == already_version
    assert overlay_only.agent_paused_at is not None
    assert int(overlay_only.version or 1) == overlay_version + 1
    dispatch.assert_called_once()
    assert int(dispatch.call_args.args[0].id) == int(target.id)


@pytest.mark.parametrize(
    "pause_reason",
    ["paused by recruiter", "agent recovery dispatch failed"],
)
def test_related_role_recovery_preserves_target_manual_and_system_pauses(
    client,
    db,
    pause_reason,
):
    seeded = _seed_recovery_scope(client, db)
    target = seeded["related"]
    target.agent_paused_reason = pause_reason
    db.commit()
    authority = _authority(db, seeded)
    target_version = int(target.version or 1)

    with (
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "budget_guard.resume_if_under_budget"
        ) as resume,
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "dispatch_role_agent_cycle"
        ) as dispatch,
    ):
        response = client.post(
            f"/api/v1/roles/{target.id}/agent/recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 200, response.text
    assert response.json()["resumed"] is False
    db.expire_all()
    current = db.get(Role, target.id)
    assert current.agent_paused_at is not None
    assert current.agent_paused_reason == pause_reason
    assert int(current.version or 1) == target_version
    assert seeded["organization"].agent_workspace_paused_at is None
    resume.assert_not_called()
    dispatch.assert_not_called()


def test_related_role_recovery_overlay_only_target_must_be_under_budget(client, db):
    seeded = _seed_recovery_scope(client, db)
    target = seeded["related"]
    target.agent_paused_at = None
    target.agent_paused_reason = None
    db.commit()
    authority = _authority(db, seeded)

    with (
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "budget_guard.check_monthly_usd",
            return_value=BudgetCheck(ok=False, reason="monthly cap reached"),
        ),
        patch(
            "app.services.agent_activation_readiness.activation_readiness"
        ) as readiness,
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "dispatch_role_agent_cycle"
        ) as dispatch,
    ):
        response = client.post(
            f"/api/v1/roles/{target.id}/agent/recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason"] == "related_role_not_ready"
    db.expire_all()
    assert seeded["organization"].agent_workspace_paused_at is not None
    assert seeded["overlay_only"].agent_paused_at is None
    readiness.assert_not_called()
    dispatch.assert_not_called()


def test_related_role_recovery_overlay_only_target_must_be_runtime_ready(client, db):
    seeded = _seed_recovery_scope(client, db)
    target = seeded["related"]
    target.agent_paused_at = None
    target.agent_paused_reason = None
    db.commit()
    authority = _authority(db, seeded)

    with (
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "budget_guard.check_monthly_usd",
            return_value=BudgetCheck(ok=True),
        ),
        patch(
            "app.services.agent_activation_readiness.activation_readiness",
            return_value={"ready": False, "reasons": ["worker unavailable"]},
        ),
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "dispatch_role_agent_cycle"
        ) as dispatch,
    ):
        response = client.post(
            f"/api/v1/roles/{target.id}/agent/recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["reason"] == "related_role_not_ready"
    db.expire_all()
    assert seeded["organization"].agent_workspace_paused_at is not None
    assert seeded["overlay_only"].agent_paused_at is None
    dispatch.assert_not_called()


def test_related_role_recovery_resumes_a_budget_healthy_ready_overlay_only_target(
    client,
    db,
):
    seeded = _seed_recovery_scope(client, db)
    target = seeded["related"]
    target.agent_paused_at = None
    target.agent_paused_reason = None
    db.commit()
    authority = _authority(db, seeded)

    with (
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "budget_guard.check_monthly_usd",
            return_value=BudgetCheck(ok=True),
        ),
        patch(
            "app.services.agent_activation_readiness.activation_readiness",
            return_value={"ready": True, "reasons": []},
        ) as readiness,
        patch(
            "app.domains.agentic.related_role_recovery_routes."
            "dispatch_role_agent_cycle"
        ) as dispatch,
    ):
        response = client.post(
            f"/api/v1/roles/{target.id}/agent/recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 200, response.text
    assert response.json()["resumed"] is True
    db.expire_all()
    assert seeded["organization"].agent_workspace_paused_at is None
    assert db.get(Role, target.id).agent_paused_at is None
    assert db.get(Role, seeded["overlay_only"].id).agent_paused_at is not None
    readiness.assert_called_once()
    dispatch.assert_called_once()


def test_related_role_recovery_rejects_a_changed_cohort_without_mutation(client, db):
    seeded = _seed_recovery_scope(client, db)
    authority = _authority(db, seeded)
    candidate = Candidate(
        organization_id=int(seeded["user"].organization_id),
        full_name="Late Candidate",
        email="late-related-recovery@example.com",
        cv_text="New production Python evidence.",
    )
    db.add(candidate)
    db.flush()
    db.add(
        CandidateApplication(
            organization_id=int(seeded["user"].organization_id),
            candidate_id=int(candidate.id),
            role_id=int(seeded["source"].id),
            source="workable",
            application_outcome="open",
            cv_text=candidate.cv_text,
        )
    )
    db.commit()

    with patch(
        "app.domains.agentic.related_role_recovery_routes.dispatch_role_agent_cycle"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "RELATED_ROLE_RECOVERY_SCOPE_CHANGED"
    assert response.json()["detail"]["reason"] == "candidate_cohort_changed"
    db.expire_all()
    assert seeded["organization"].agent_workspace_paused_at is not None
    assert seeded["related"].agent_paused_at is not None
    assert seeded["overlay_only"].agent_paused_at is None
    dispatch.assert_not_called()


def test_related_role_recovery_rejects_family_drift_without_mutation(client, db):
    seeded = _seed_recovery_scope(client, db)
    authority = _authority(db, seeded)
    db.add(
        Role(
            organization_id=int(seeded["user"].organization_id),
            name="Another related scoring view",
            source="sister",
            role_kind=ROLE_KIND_SISTER,
            ats_owner_role_id=int(seeded["source"].id),
            job_spec_text="Another complete related-role specification.",
        )
    )
    db.commit()

    with patch(
        "app.domains.agentic.related_role_recovery_routes.dispatch_role_agent_cycle"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "RELATED_ROLE_RECOVERY_SCOPE_CHANGED"
    assert detail["reason"] == "role_family_changed"
    db.expire_all()
    assert seeded["organization"].agent_workspace_paused_at is not None
    assert seeded["related"].agent_paused_at is not None
    assert seeded["overlay_only"].agent_paused_at is None
    dispatch.assert_not_called()


def test_related_role_recovery_rejects_version_drift_without_mutation(client, db):
    seeded = _seed_recovery_scope(client, db)
    authority = _authority(db, seeded)
    seeded["related"].version = int(seeded["related"].version or 1) + 1
    db.commit()

    with patch(
        "app.domains.agentic.related_role_recovery_routes.dispatch_role_agent_cycle"
    ) as dispatch:
        response = client.post(
            f"/api/v1/roles/{seeded['related'].id}/agent/"
            "recover-legacy-workspace-hold",
            json=authority,
            headers=seeded["headers"],
        )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "ROLE_VERSION_CONFLICT"
    db.expire_all()
    assert seeded["organization"].agent_workspace_paused_at is not None
    assert seeded["related"].agent_paused_at is not None
    assert seeded["overlay_only"].agent_paused_at is None
    dispatch.assert_not_called()
