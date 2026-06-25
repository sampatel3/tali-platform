"""P1: role publishing (status + unique slug)."""
import pytest
from fastapi import HTTPException

from app.domains.assessments_runtime.role_publishing_service import (
    publish_role,
    set_role_status,
    slugify_role,
    unpublish_role,
)
from app.models import Organization, Role


def _org(db, slug="acme"):
    org = Organization(name=slug.title(), slug=slug)
    db.add(org)
    db.flush()
    return org


def _role(db, org, name="Senior Engineer"):
    role = Role(organization_id=org.id, name=name, source="manual")
    db.add(role)
    db.flush()
    return role


def test_new_role_defaults_to_draft(db):
    role = _role(db, _org(db))
    db.refresh(role)
    assert role.status == "draft"


def test_publish_sets_status_and_slug(db):
    role = _role(db, _org(db), "Senior Engineer")
    publish_role(db, role)
    assert role.status == "published"
    assert role.slug == "senior-engineer"


def test_publish_slug_is_unique_per_org(db):
    org = _org(db)
    r1 = _role(db, org, "Data Scientist")
    publish_role(db, r1)
    r2 = _role(db, org, "Data Scientist")
    publish_role(db, r2)
    assert r1.slug == "data-scientist"
    assert r2.slug == "data-scientist-2"


def test_publish_with_explicit_slug_is_slugified(db):
    role = _role(db, _org(db), "X")
    publish_role(db, role, slug="My Custom Slug!")
    assert role.slug == "my-custom-slug"


def test_unpublish_returns_to_draft_keeps_slug(db):
    role = _role(db, _org(db), "Eng")
    publish_role(db, role)
    slug = role.slug
    unpublish_role(db, role)
    assert role.status == "draft"
    assert role.slug == slug


def test_set_role_status_validates(db):
    role = _role(db, _org(db))
    set_role_status(db, role, "closed")
    assert role.status == "closed"
    with pytest.raises(HTTPException) as exc:
        set_role_status(db, role, "bogus")
    assert exc.value.status_code == 422


def test_slugify():
    assert slugify_role("  Senior  Engineer!! ") == "senior-engineer"
    assert slugify_role("") == ""
