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
