"""Shared-family authority for deterministic auto-reject configuration."""

from __future__ import annotations

from app.models.role import ROLE_KIND_SISTER, Role
from app.models.user import User
from tests.conftest import auth_headers


def _seed_family(db, organization_id: int) -> tuple[Role, Role]:
    owner = Role(
        organization_id=organization_id,
        name="Platform Engineer",
        source="workable",
        workable_job_id="PLATFORM-ENGINEER",
        auto_reject=False,
        auto_reject_pre_screen=False,
    )
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=organization_id,
        name="AI Platform Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.commit()
    return owner, related


def _family(owner: Role, *related: Role) -> dict:
    return {
        "owner": {"id": owner.id, "name": owner.name},
        "related": [
            {"id": role.id, "name": role.name}
            for role in sorted(related, key=lambda row: (row.name.casefold(), row.id))
        ],
    }


def test_enabling_auto_reject_requires_and_accepts_exact_shared_family(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, related = _seed_family(db, int(user.organization_id))

    missing = client.patch(
        f"/api/v1/roles/{owner.id}",
        json={
            "expected_version": owner.version,
            "auto_reject": True,
            "auto_reject_pre_screen": True,
        },
        headers=headers,
    )
    assert missing.status_code == 409, missing.text
    assert missing.json()["detail"]["code"] == "ROLE_FAMILY_CHANGED"
    db.rollback()

    accepted = client.patch(
        f"/api/v1/roles/{owner.id}",
        json={
            "expected_version": owner.version,
            "expected_role_family": _family(owner, related),
            "auto_reject": True,
            "auto_reject_pre_screen": True,
        },
        headers=headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["auto_reject"] is True
    assert accepted.json()["auto_reject_pre_screen"] is True


def test_enabling_auto_reject_rejects_family_growth_after_confirmation(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, first_related = _seed_family(db, int(user.organization_id))
    displayed = _family(owner, first_related)
    second_related = Role(
        organization_id=int(user.organization_id),
        name="Data Platform Engineer",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(second_related)
    db.commit()

    changed = client.patch(
        f"/api/v1/roles/{owner.id}",
        json={
            "expected_version": owner.version,
            "expected_role_family": displayed,
            "auto_reject": True,
            "auto_reject_pre_screen": True,
        },
        headers=headers,
    )

    assert changed.status_code == 409, changed.text
    detail = changed.json()["detail"]
    assert detail["code"] == "ROLE_FAMILY_CHANGED"
    assert {row["id"] for row in detail["current_role_family"]["related"]} == {
        first_related.id,
        second_related.id,
    }
    db.rollback()
    db.refresh(owner)
    assert owner.auto_reject is False
    assert owner.auto_reject_pre_screen is False


def test_disabling_auto_reject_does_not_require_family_confirmation(client, db):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner, _related = _seed_family(db, int(user.organization_id))
    owner.auto_reject = True
    owner.auto_reject_pre_screen = True
    db.commit()

    disabled = client.patch(
        f"/api/v1/roles/{owner.id}",
        json={
            "expected_version": owner.version,
            "auto_reject": False,
            "auto_reject_pre_screen": False,
        },
        headers=headers,
    )

    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["auto_reject"] is False
    assert disabled.json()["auto_reject_pre_screen"] is False
