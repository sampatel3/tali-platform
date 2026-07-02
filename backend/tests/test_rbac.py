"""P0.5: RBAC — require_role dependency + users.role default."""
import pytest
from fastapi import HTTPException

from app.deps import require_role
from app.models.user import (
    ROLE_ADMIN,
    ROLE_RECRUITER,
    ROLE_VIEWER,
    USER_ROLES,
    User,
)


class _FakeUser:
    def __init__(self, role):
        self.role = role


def test_require_role_allows_listed():
    dep = require_role(ROLE_ADMIN, ROLE_RECRUITER)
    assert dep(current_user=_FakeUser(ROLE_ADMIN)).role == ROLE_ADMIN
    assert dep(current_user=_FakeUser(ROLE_RECRUITER)).role == ROLE_RECRUITER


def test_require_role_denies_unlisted():
    dep = require_role(ROLE_ADMIN, ROLE_RECRUITER)
    with pytest.raises(HTTPException) as exc:
        dep(current_user=_FakeUser(ROLE_VIEWER))
    assert exc.value.status_code == 403


def test_require_role_no_roles_allows_any_authenticated():
    dep = require_role()
    assert dep(current_user=_FakeUser("anything")).role == "anything"


def test_user_role_constants():
    assert ROLE_ADMIN in USER_ROLES
    assert len(USER_ROLES) == 5


def test_user_defaults_to_admin_role(db):
    user = User(
        email="rbac@x.test",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=False,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    assert user.role == ROLE_ADMIN  # default preserves pre-RBAC (everyone admin)


def test_job_hiring_team_membership_unique_per_role_user(db):
    """Per-job hiring-team membership (P0.5): one row per (role, user), with a
    per-job team role distinct from the org-wide RBAC role."""
    from sqlalchemy.exc import IntegrityError

    from app.models import JobHiringTeam, TEAM_ROLES
    from app.models.job_hiring_team import TEAM_ROLE_HIRING_MANAGER
    from app.models.organization import Organization
    from app.models.role import Role

    assert TEAM_ROLE_HIRING_MANAGER in TEAM_ROLES

    org = Organization(name="Acme", slug="acme-ht")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend Engineer")
    member = User(
        email="hm@ht.test", hashed_password="x",
        is_active=True, is_superuser=False, is_verified=False,
    )
    db.add_all([role, member])
    db.flush()

    m = JobHiringTeam(
        organization_id=org.id, role_id=role.id, user_id=member.id,
        team_role=TEAM_ROLE_HIRING_MANAGER,
    )
    db.add(m)
    db.flush()
    assert m.id is not None and m.team_role == "hiring_manager"

    # (role, user) is unique — a second membership for the same pair is rejected.
    db.add(JobHiringTeam(
        organization_id=org.id, role_id=role.id, user_id=member.id,
        team_role="interviewer",
    ))
    with pytest.raises(IntegrityError):
        db.flush()
