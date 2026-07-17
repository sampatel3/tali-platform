"""Ordering contracts for ``GET /roles``."""

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
    full list — the Jobs hub paints this page first, then re-fetches the full
    list in the background. Without ``limit`` the response stays unbounded."""
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

    # No limit → all five (the background full fetch).
    full = client.get("/api/v1/roles", headers=headers)
    assert full.status_code == 200, full.text
    assert len(full.json()) == 5


def test_list_roles_name_order_is_case_insensitive_stable_and_prefix_page(db, client):
    headers, _ = auth_headers(client, organization_name="Name Sort Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    roles_by_name: dict[str, Role] = {}
    for name in ("Zulu", "beta", "ALPHA", "alpha", "Beta"):
        role = Role(
            organization_id=org_id,
            name=name,
            source="manual",
        )
        db.add(role)
        db.flush()
        roles_by_name[name] = role
    db.commit()

    full = client.get("/api/v1/roles?sort_by=name", headers=headers)
    assert full.status_code == 200, full.text
    full_ids = [row["id"] for row in full.json()]
    assert full_ids == [
        roles_by_name["ALPHA"].id,
        roles_by_name["alpha"].id,
        roles_by_name["beta"].id,
        roles_by_name["Beta"].id,
        roles_by_name["Zulu"].id,
    ]

    paged = client.get("/api/v1/roles?sort_by=name&limit=3", headers=headers)
    assert paged.status_code == 200, paged.text
    assert [row["id"] for row in paged.json()] == full_ids[:3]


def test_list_roles_agent_on_name_orders_agent_on_roles_first_then_by_name(db, client):
    headers, _ = auth_headers(client, organization_name="Agent On Sort Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    rows = [
        ("Aardvark Off", False, None, True),
        ("Zulu Managed", True, None, False),
        ("beta Managed", True, None, False),
        ("alpha Paused", True, datetime.now(timezone.utc), False),
        ("Beta Off", False, datetime.now(timezone.utc), False),
    ]
    roles_by_name: dict[str, Role] = {}
    for name, enabled, paused_at, starred in rows:
        role = Role(
            organization_id=org_id,
            name=name,
            source="manual",
            agentic_mode_enabled=enabled,
            agent_paused_at=paused_at,
            starred_for_auto_sync=starred,
        )
        db.add(role)
        db.flush()
        roles_by_name[name] = role
    db.commit()

    full = client.get("/api/v1/roles?sort_by=agent_on_name", headers=headers)
    assert full.status_code == 200, full.text
    full_ids = [row["id"] for row in full.json()]
    assert full_ids == [
        roles_by_name["beta Managed"].id,
        roles_by_name["Zulu Managed"].id,
        roles_by_name["Aardvark Off"].id,
        roles_by_name["alpha Paused"].id,
        roles_by_name["Beta Off"].id,
    ]

    # The Jobs page's first batch must already contain the agent-on roles; the
    # unlimited background response should only append the alphabetical tail.
    paged = client.get("/api/v1/roles?sort_by=agent_on_name&limit=2", headers=headers)
    assert paged.status_code == 200, paged.text
    assert [row["id"] for row in paged.json()] == full_ids[:2]


def test_list_roles_agent_on_name_promotes_complete_agent_on_related_family(db, client):
    headers, _ = auth_headers(client, organization_name="Agent On Family Sort Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    owner = Role(organization_id=org_id, name="Zulu Platform", source="workable")
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org_id,
        name="API Alternative",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner.id,
        agentic_mode_enabled=True,
    )
    standalone = Role(
        organization_id=org_id,
        name="Alpha Standalone",
        source="manual",
    )
    deleted_owner = Role(
        organization_id=org_id,
        name="Deleted Owner",
        source="workable",
        deleted_at=datetime.now(timezone.utc),
    )
    db.add(deleted_owner)
    db.flush()
    orphan_a = Role(
        organization_id=org_id,
        name="Beta Orphan",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=deleted_owner.id,
        agentic_mode_enabled=True,
    )
    orphan_b = Role(
        organization_id=org_id,
        name="Theta Orphan",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=deleted_owner.id,
        agentic_mode_enabled=True,
    )
    db.add_all([related, standalone, orphan_a, orphan_b])
    db.commit()

    full = client.get("/api/v1/roles?sort_by=agent_on_name", headers=headers)
    assert full.status_code == 200, full.text
    full_ids = [row["id"] for row in full.json()]
    assert full_ids == [
        orphan_a.id,
        orphan_b.id,
        owner.id,
        related.id,
        standalone.id,
    ]

    # Orphans are standalone in the sort, so ending on one remains a strict
    # prefix instead of appending its later sibling out of order.
    orphan_page = client.get(
        "/api/v1/roles?sort_by=agent_on_name&limit=1",
        headers=headers,
    )
    assert orphan_page.status_code == 200, orphan_page.text
    assert [row["id"] for row in orphan_page.json()] == full_ids[:1]

    # The nominal cutoff now lands on the live owner. Extend through its
    # enabled related role so that valid family never splits across pages.
    paged = client.get("/api/v1/roles?sort_by=agent_on_name&limit=3", headers=headers)
    assert paged.status_code == 200, paged.text
    assert [row["id"] for row in paged.json()] == full_ids[:4]


def test_list_roles_name_order_keeps_role_families_adjacent_before_pagination(db, client):
    headers, _ = auth_headers(client, organization_name="Family Sort Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    owner = Role(organization_id=org_id, name="Alpha Platform", source="workable")
    db.add(owner)
    db.flush()
    related = Role(
        organization_id=org_id,
        name="Zulu Alternative",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner.id,
    )
    standalone = Role(organization_id=org_id, name="Beta Standalone", source="manual")
    db.add_all([related, standalone])
    db.commit()

    full = client.get("/api/v1/roles?sort_by=name", headers=headers)
    assert full.status_code == 200, full.text
    full_ids = [row["id"] for row in full.json()]
    assert full_ids == [owner.id, related.id, standalone.id]

    paged = client.get("/api/v1/roles?sort_by=name&limit=2", headers=headers)
    assert paged.status_code == 200, paged.text
    assert [row["id"] for row in paged.json()] == full_ids[:2]


def test_list_roles_name_page_extends_through_boundary_family(db, client):
    headers, _ = auth_headers(client, organization_name="Boundary Family Sort Org")
    me = db.query(User).order_by(User.id.desc()).first()
    org_id = me.organization_id

    owner = Role(organization_id=org_id, name="Alpha Platform", source="workable")
    db.add(owner)
    db.flush()
    related_a = Role(
        organization_id=org_id,
        name="API Alternative",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner.id,
    )
    related_z = Role(
        organization_id=org_id,
        name="Zero Trust Alternative",
        source="sister",
        role_kind="sister",
        ats_owner_role_id=owner.id,
    )
    standalone = Role(
        organization_id=org_id,
        name="Beta Standalone",
        source="manual",
    )
    db.add_all([related_a, related_z, standalone])
    db.commit()

    full = client.get("/api/v1/roles?sort_by=name", headers=headers)
    assert full.status_code == 200, full.text
    full_ids = [row["id"] for row in full.json()]
    assert full_ids == [owner.id, related_a.id, related_z.id, standalone.id]

    # The nominal cutoff lands inside the family. The first page grows just
    # enough to keep that complete family together and remains a full-list prefix.
    paged = client.get("/api/v1/roles?sort_by=name&limit=2", headers=headers)
    assert paged.status_code == 200, paged.text
    paged_ids = [row["id"] for row in paged.json()]
    assert paged_ids == full_ids[:3]
