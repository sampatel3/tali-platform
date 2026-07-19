"""Shared helpers for decision_policy tests."""

from __future__ import annotations

from app.decision_policy.bootstrap import bootstrap_org
from app.models.decision_policy import DecisionPolicy
from app.models.organization import Organization
from app.models.role import Role


def make_org(db, *, name: str = "Test Org", default_score_threshold: int | None = None) -> Organization:
    org = Organization(
        name=name,
        slug=f"{name.lower().replace(' ', '-')}-{id(db)}",
        default_score_threshold=default_score_threshold,
    )
    db.add(org)
    db.flush()
    return org


def make_role(
    db,
    *,
    org: Organization,
    name: str = "Backend",
    score_threshold: int | None = None,
) -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        score_threshold=score_threshold,
    )
    db.add(role)
    db.flush()
    return role


def bootstrap(db, org: Organization) -> DecisionPolicy:
    return bootstrap_org(db, organization_id=int(org.id))
