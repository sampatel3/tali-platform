"""Focused tests for the per-job authorization policy."""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.deps import get_current_user, require_org_owner
from app.domains.assessments_runtime import applications_routes
from app.domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from app.models.candidate_application import CandidateApplication
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.schemas.role import ApplicationCreate


def _user(db, org_id: int, email: str, *, org_role: str = "member") -> User:
    user = User(
        email=email,
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        organization_id=org_id,
        role=org_role,
    )
    db.add(user)
    db.flush()
    return user


def _team_member(db, role: Role, user: User, team_role: str) -> JobHiringTeam:
    membership = JobHiringTeam(
        organization_id=role.organization_id,
        role_id=role.id,
        user_id=user.id,
        team_role=team_role,
    )
    db.add(membership)
    db.flush()
    return membership


@pytest.fixture
def job_policy_subjects(db):
    org = Organization(name="Job Authz", slug="job-authz")
    other_org = Organization(name="Other Job Authz", slug="other-job-authz")
    db.add_all([org, other_org])
    db.flush()

    role = Role(organization_id=org.id, name="Platform Engineer", source="manual")
    other_role = Role(
        organization_id=other_org.id, name="Secret Other Job", source="manual"
    )
    db.add_all([role, other_role])
    db.flush()

    subjects = {
        "org": org,
        "role": role,
        "other_role": other_role,
        "owner": _user(db, org.id, "owner@job-authz.test", org_role="owner"),
        "recruiter": _user(db, org.id, "recruiter@job-authz.test"),
        "manager": _user(db, org.id, "manager@job-authz.test"),
        "interviewer": _user(db, org.id, "interviewer@job-authz.test"),
        "coordinator": _user(db, org.id, "coordinator@job-authz.test"),
        "unassigned": _user(db, org.id, "unassigned@job-authz.test"),
    }
    db.commit()
    return subjects


def _require(db, subjects, actor: str, permission: JobPermission) -> Role:
    return require_job_permission(
        db,
        current_user=subjects[actor],
        role_id=subjects["role"].id,
        permission=permission,
    )


def _assert_forbidden(call) -> None:
    with pytest.raises(HTTPException) as exc_info:
        call()
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


@pytest.mark.parametrize("permission", list(JobPermission))
def test_owner_is_allowed_every_job_permission(db, job_policy_subjects, permission):
    assert _require(db, job_policy_subjects, "owner", permission) is job_policy_subjects[
        "role"
    ]


def test_cross_org_and_unknown_roles_have_same_forbidden_response(
    db, job_policy_subjects
):
    owner = job_policy_subjects["owner"]

    for role_id in (job_policy_subjects["other_role"].id, 999_999_999):
        _assert_forbidden(
            lambda role_id=role_id: require_job_permission(
                db,
                current_user=owner,
                role_id=role_id,
                permission=JobPermission.VIEW,
            )
        )


def test_assigned_hiring_team_roles_follow_permission_matrix(db, job_policy_subjects):
    role = job_policy_subjects["role"]
    _team_member(db, role, job_policy_subjects["recruiter"], "recruiter")
    _team_member(db, role, job_policy_subjects["manager"], "hiring_manager")
    _team_member(db, role, job_policy_subjects["interviewer"], "interviewer")
    _team_member(db, role, job_policy_subjects["coordinator"], "coordinator")
    db.commit()

    for actor in ("recruiter", "manager", "interviewer", "coordinator", "unassigned"):
        assert _require(db, job_policy_subjects, actor, JobPermission.VIEW) is role

    for permission in (JobPermission.EDIT_ROLE, JobPermission.CONTROL_AGENT):
        assert _require(db, job_policy_subjects, "recruiter", permission) is role
        assert _require(db, job_policy_subjects, "manager", permission) is role
        for denied_actor in ("interviewer", "coordinator", "unassigned"):
            _assert_forbidden(
                lambda denied_actor=denied_actor, permission=permission: _require(
                    db, job_policy_subjects, denied_actor, permission
                )
            )

    assert (
        _require(
            db, job_policy_subjects, "manager", JobPermission.MANAGE_HIRING_TEAM
        )
        is role
    )
    for denied_actor in ("recruiter", "interviewer", "coordinator", "unassigned"):
        _assert_forbidden(
            lambda denied_actor=denied_actor: _require(
                db,
                job_policy_subjects,
                denied_actor,
                JobPermission.MANAGE_HIRING_TEAM,
            )
        )

    assert _require(
        db, job_policy_subjects, "manager", JobPermission.DELETE_ROLE
    ) is role
    for denied_actor in ("recruiter", "interviewer", "coordinator", "unassigned"):
        _assert_forbidden(
            lambda denied_actor=denied_actor: _require(
                db,
                job_policy_subjects,
                denied_actor,
                JobPermission.DELETE_ROLE,
            )
        )


def test_unassigned_role_fails_closed_for_member_edits_and_agent_control(
    db, job_policy_subjects
):
    _assert_forbidden(
        lambda: _require(
            db, job_policy_subjects, "unassigned", JobPermission.EDIT_ROLE
        )
    )
    _assert_forbidden(
        lambda: _require(
            db, job_policy_subjects, "unassigned", JobPermission.CONTROL_AGENT
        )
    )


def test_only_owner_can_make_first_hiring_team_assignment(db, job_policy_subjects):
    # An empty team is owner-only, preventing member self-elevation.
    _assert_forbidden(
        lambda: _require(
            db,
            job_policy_subjects,
            "unassigned",
            JobPermission.MANAGE_HIRING_TEAM,
        )
    )
    assert (
        _require(
            db, job_policy_subjects, "owner", JobPermission.MANAGE_HIRING_TEAM
        )
        is job_policy_subjects["role"]
    )

    # Once the owner assigns a hiring manager, that manager can administer the
    # configured team; ordinary members still cannot.
    _team_member(
        db,
        job_policy_subjects["role"],
        job_policy_subjects["manager"],
        "hiring_manager",
    )
    db.commit()
    assert (
        _require(
            db, job_policy_subjects, "manager", JobPermission.MANAGE_HIRING_TEAM
        )
        is job_policy_subjects["role"]
    )
    _assert_forbidden(
        lambda: _require(
            db,
            job_policy_subjects,
            "unassigned",
            JobPermission.MANAGE_HIRING_TEAM,
        )
    )


def test_deactivated_user_is_denied_even_with_owner_or_team_authority(
    db, job_policy_subjects
):
    owner = job_policy_subjects["owner"]
    owner.is_active = False
    db.commit()

    for permission in JobPermission:
        _assert_forbidden(
            lambda permission=permission: _require(
                db, job_policy_subjects, "owner", permission
            )
        )


@pytest.mark.parametrize("actor_name", ["interviewer", "unassigned"])
def test_application_create_route_denies_non_editors(
    db, job_policy_subjects, actor_name
):
    role = job_policy_subjects["role"]
    if actor_name == "interviewer":
        _team_member(db, role, job_policy_subjects[actor_name], "interviewer")
    db.commit()

    _assert_forbidden(
        lambda: applications_routes.create_application(
            role_id=int(role.id),
            data=ApplicationCreate(
                candidate_email=f"application-authz-{actor_name}@example.com",
                candidate_name=actor_name.title(),
            ),
            db=db,
            current_user=job_policy_subjects[actor_name],
        )
    )
    assert (
        db.query(CandidateApplication)
        .filter(CandidateApplication.role_id == role.id)
        .count()
        == 0
    )


def test_assigned_recruiter_can_create_application(db, job_policy_subjects):
    role = job_policy_subjects["role"]
    recruiter = job_policy_subjects["recruiter"]
    role.job_spec_text = "A complete role specification for authorization testing."
    _team_member(db, role, recruiter, "recruiter")
    db.commit()

    result = applications_routes.create_application(
        role_id=int(role.id),
        data=ApplicationCreate(
            candidate_email="allowed-recruiter-application-authz@example.com",
            candidate_name="Allowed Recruiter Candidate",
        ),
        db=db,
        current_user=recruiter,
    )

    assert int(result.role_id) == int(role.id)
    assert result.candidate_email == "allowed-recruiter-application-authz@example.com"


@pytest.mark.parametrize("actor_name", ["interviewer", "unassigned"])
def test_paid_batch_score_route_denies_non_controllers(
    db, job_policy_subjects, actor_name
):
    role = job_policy_subjects["role"]
    role.job_spec_text = "A complete role specification for authorization testing."
    if actor_name == "interviewer":
        _team_member(db, role, job_policy_subjects[actor_name], "interviewer")
    db.commit()

    _assert_forbidden(
        lambda: applications_routes.batch_score_role(
            role_id=int(role.id),
            include_scored=False,
            applied_after=None,
            dry_run=True,
            db=db,
            current_user=job_policy_subjects[actor_name],
        )
    )


def test_assigned_recruiter_can_preview_paid_batch_score(
    db, job_policy_subjects
):
    role = job_policy_subjects["role"]
    recruiter = job_policy_subjects["recruiter"]
    role.job_spec_text = "A complete role specification for authorization testing."
    _team_member(db, role, recruiter, "recruiter")
    db.commit()

    result = applications_routes.batch_score_role(
        role_id=int(role.id),
        include_scored=False,
        applied_after=None,
        dry_run=True,
        db=db,
        current_user=recruiter,
    )

    assert result == {
        "will_fetch_cv": 0,
        "will_pre_screen": 0,
        "will_score": 0,
        "total": 0,
        "include_scored": False,
    }


def test_process_worker_rechecks_carried_user_authorization(
    db, job_policy_subjects
):
    role = job_policy_subjects["role"]
    recruiter = job_policy_subjects["recruiter"]
    membership = _team_member(db, role, recruiter, "recruiter")
    db.commit()
    db.delete(membership)
    db.commit()

    applications_routes._process_progress[int(role.id)] = (
        applications_routes._empty_process_progress()
    )
    applications_routes._run_process(
        int(role.id),
        int(role.organization_id),
        fetch_cvs=False,
        refresh_cvs=False,
        pre_screen=False,
        refresh_pre_screen=False,
        score_mode="none",
        sync_graph=False,
        user_id=int(recruiter.id),
    )

    progress = applications_routes._process_progress[int(role.id)]
    assert progress["status"] == "failed"
    assert progress["error"] == "authorization_revoked"


def _dependency_for(callable_, parameter_name: str):
    return inspect.signature(callable_).parameters[parameter_name].default.dependency


def test_org_wide_spend_mutations_are_owner_gated_but_status_reads_are_not():
    assert (
        _dependency_for(applications_routes.batch_score_all_roles, "current_user")
        is require_org_owner
    )
    assert (
        _dependency_for(applications_routes.sync_graph_org, "current_user")
        is require_org_owner
    )
    assert (
        _dependency_for(applications_routes.cancel_sync_graph, "current_user")
        is require_org_owner
    )

    assert (
        _dependency_for(applications_routes.batch_score_all_status, "current_user")
        is get_current_user
    )
    assert (
        _dependency_for(applications_routes.sync_graph_status, "current_user")
        is get_current_user
    )
