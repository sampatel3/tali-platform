"""Shared helpers for decision_policy tests."""

from __future__ import annotations

from sqlalchemy import event

from app.decision_policy.bootstrap import bootstrap_org
from app.models.decision_policy import DecisionPolicy
from app.models.organization import Organization
from app.models.role import Role
from app.models.rubric_revision import RubricRevision


# SQLite-with-BigInteger PK workaround.
# Several models in this suite use ``BigInteger`` primary keys; SQLite's
# autoincrement only works on plain ``INTEGER`` columns, so we hand out
# monotonically increasing ids per-table via a before_insert listener.
_BIG_PK_COUNTERS: dict[str, int] = {
    "rubric_revisions": 0,
    "decision_policies": 0,
}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


for _model in (RubricRevision, DecisionPolicy):
    event.listen(_model, "before_insert", _assign_big_pk)


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
