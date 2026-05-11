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
