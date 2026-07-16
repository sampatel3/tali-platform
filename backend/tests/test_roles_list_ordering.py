"""/roles ordering: starred first, then most-recently-updated."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.role import Role
from app.models.user import User

from .conftest import auth_headers


def test_list_roles_orders_starred_first_then_by_updated_at(db, client):
    headers, _ = auth_headers(client, organization_name="Sort Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    now = datetime.now(timezone.utc)
    rows = [
        # name, starred, updated_at
        ("alpha-old-unstarred", False, now - timedelta(days=10)),
        ("beta-new-unstarred", False, now - timedelta(hours=1)),
        ("gamma-starred-old", True, now - timedelta(days=30)),
        ("delta-starred-new", True, now - timedelta(minutes=5)),
        ("epsilon-mid-unstarred", False, now - timedelta(days=2)),
    ]
    created: list[Role] = []
    for name, starred, updated_at in rows:
        role = Role(
            organization_id=org_id,
            name=name,
            source="manual",
            starred_for_auto_sync=starred,
            updated_at=updated_at,
        )
        db.add(role)
        db.flush()
        created.append(role)
    db.commit()

    resp = client.get("/api/v1/roles", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    names = [r["name"] for r in payload]
    # Expected order:
    #   starred bucket (newer first): delta, gamma
    #   unstarred bucket (newer first): beta, epsilon, alpha
    assert names == [
        "delta-starred-new",
        "gamma-starred-old",
        "beta-new-unstarred",
        "epsilon-mid-unstarred",
        "alpha-old-unstarred",
    ]


def test_list_roles_limit_returns_first_page_in_sort_order(db, client):
    """``?limit=N`` returns the first N roles in the SAME sort order as the
    full list, and ``offset`` advances through that stable ordering."""
    headers, _ = auth_headers(client, organization_name="Page Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    now = datetime.now(timezone.utc)
    rows = [
        ("alpha-old-unstarred", False, now - timedelta(days=10)),
        ("beta-new-unstarred", False, now - timedelta(hours=1)),
        ("gamma-starred-old", True, now - timedelta(days=30)),
        ("delta-starred-new", True, now - timedelta(minutes=5)),
        ("epsilon-mid-unstarred", False, now - timedelta(days=2)),
    ]
    for name, starred, updated_at in rows:
        db.add(Role(
            organization_id=org_id,
            name=name,
            source="manual",
            starred_for_auto_sync=starred,
            updated_at=updated_at,
        ))
    db.commit()

    # First page: the two starred-then-newest roles, in full-list order.
    paged = client.get("/api/v1/roles?limit=2", headers=headers)
    assert paged.status_code == 200, paged.text
    assert [r["name"] for r in paged.json()] == ["delta-starred-new", "gamma-starred-old"]

    second_page = client.get("/api/v1/roles?limit=2&offset=2", headers=headers)
    assert second_page.status_code == 200, second_page.text
    assert [r["name"] for r in second_page.json()] == [
        "beta-new-unstarred",
        "epsilon-mid-unstarred",
    ]

    # The default bounded page preserves the same ordering.
    full = client.get("/api/v1/roles", headers=headers)
    assert full.status_code == 200, full.text
    assert len(full.json()) == 5
