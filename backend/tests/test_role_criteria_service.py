"""Tests for the role criteria sync service."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Organization, Role, RoleCriterion
from app.platform.database import Base
from app.models.role_criterion import (
    CRITERION_SOURCE_DERIVED,
    CRITERION_SOURCE_RECRUITER,
)
from app.services.role_criteria_service import (
    sync_all_criteria,
    sync_derived_criteria,
    sync_recruiter_criteria,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    org = Organization(name="Acme", slug="acme")
    db.add(org)
    db.commit()
    db.refresh(org)
    yield db, org
    db.close()


def _criteria_by_source(role: Role, source: str) -> list[RoleCriterion]:
    return sorted(
        [c for c in role.criteria if c.source == source],
        key=lambda c: c.ordering,
    )


def test_sync_recruiter_criteria_creates_one_row_per_bullet(session) -> None:
    db, org = session
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        additional_requirements="- 5+ years Python\n- AWS or GCP\n- Postgres",
    )
    db.add(role)
    db.flush()

    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)

    items = _criteria_by_source(role, CRITERION_SOURCE_RECRUITER)
    assert [c.text for c in items] == ["5+ years Python", "AWS or GCP", "Postgres"]
    assert [c.ordering for c in items] == [0, 1, 2]
    assert all(c.weight == 1.0 and c.must_have is False for c in items)


def test_sync_recruiter_criteria_replaces_old_rows_on_resync(session) -> None:
    db, org = session
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        additional_requirements="- TypeScript\n- React",
    )
    db.add(role)
    db.flush()
    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)
    first_ids = {c.id for c in role.criteria}

    role.additional_requirements = "- TypeScript\n- Node.js"
    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)

    items = _criteria_by_source(role, CRITERION_SOURCE_RECRUITER)
    assert [c.text for c in items] == ["TypeScript", "Node.js"]
    assert {c.id for c in items}.isdisjoint(first_ids), "old criteria rows should be hard-deleted"


def test_sync_derived_criteria_pulls_from_requirements_section(session) -> None:
    db, org = session
    role = Role(
        organization_id=org.id,
        name="Senior Engineer",
        job_spec_text=(
            "Description\n"
            "Senior backend role.\n"
            "Requirements\n"
            "- 5+ years Python\n"
            "- Postgres at scale\n"
            "Benefits\n"
            "- Health insurance\n"
        ),
    )
    db.add(role)
    db.flush()

    sync_derived_criteria(db, role)
    db.commit()
    db.refresh(role)

    items = _criteria_by_source(role, CRITERION_SOURCE_DERIVED)
    texts = [c.text for c in items]
    assert "5+ years Python" in texts
    assert "Postgres at scale" in texts
    # Benefits content must NOT leak into derived criteria.
    assert not any("Health insurance" in t for t in texts)


def test_sync_derived_criteria_yields_nothing_when_no_requirements_heading(session) -> None:
    db, org = session
    role = Role(
        organization_id=org.id,
        name="Senior Engineer",
        job_spec_text="A great opportunity for a backend engineer who loves Python.",
    )
    db.add(role)
    db.flush()

    sync_derived_criteria(db, role)
    db.commit()
    db.refresh(role)

    items = _criteria_by_source(role, CRITERION_SOURCE_DERIVED)
    assert items == [], "no Requirements heading → no derived criteria (recruiter must add must-haves)"


def test_sync_all_runs_both_sources_independently(session) -> None:
    db, org = session
    role = Role(
        organization_id=org.id,
        name="Senior Engineer",
        additional_requirements="- Recruiter must-have",
        job_spec_text="Requirements\n- Spec-derived item\n",
    )
    db.add(role)
    db.flush()

    sync_all_criteria(db, role)
    db.commit()
    db.refresh(role)

    recruiter = _criteria_by_source(role, CRITERION_SOURCE_RECRUITER)
    derived = _criteria_by_source(role, CRITERION_SOURCE_DERIVED)
    assert [c.text for c in recruiter] == ["Recruiter must-have"]
    assert [c.text for c in derived] == ["Spec-derived item"]


def test_sync_recruiter_criteria_clears_when_text_removed(session) -> None:
    db, org = session
    role = Role(
        organization_id=org.id,
        name="Backend Engineer",
        additional_requirements="- 5+ years Python",
    )
    db.add(role)
    db.flush()
    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)
    assert len(_criteria_by_source(role, CRITERION_SOURCE_RECRUITER)) == 1

    role.additional_requirements = None
    sync_recruiter_criteria(db, role)
    db.commit()
    db.refresh(role)
    assert _criteria_by_source(role, CRITERION_SOURCE_RECRUITER) == []
