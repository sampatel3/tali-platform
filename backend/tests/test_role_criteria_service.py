"""Tests for the chip-based role criteria service.

After alembic 068 dropped ``Role.additional_requirements``, the old
text → chips parser path is gone. The remaining service surface is:

- ``snapshot_workspace_criteria`` — copy active ``OrganizationCriterion``
  rows into ``role_criteria`` with provenance (``org_criterion_id``).
- ``sync_role_with_workspace`` — re-apply workspace text + bucket on
  non-customized chips, add new workspace chips, drop the workspace
  link on chips whose workspace counterpart is gone.
- ``reset_role_to_workspace`` — hard-delete recruiter chips + clear
  suppressions + re-snapshot workspace.
- ``sync_derived_criteria`` — parse the job spec's Requirements section
  into ``derived_from_spec`` chips.
- ``sync_all_criteria`` — snapshot workspace + sync derived (used at
  role create + Workable import time).
- ``render_role_intent_block`` / ``render_role_intent_lines`` —
  read-only views of recruiter chips for downstream consumers.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Organization,
    OrganizationCriterion,
    Role,
    RoleCriterion,
)
from app.platform.database import Base
from app.models.role_criterion import (
    CRITERION_SOURCE_DERIVED,
    CRITERION_SOURCE_RECRUITER,
)
from app.services.role_criteria_service import (
    render_role_intent_block,
    render_role_intent_lines,
    reset_role_to_workspace,
    snapshot_workspace_criteria,
    sync_all_criteria,
    sync_derived_criteria,
    sync_role_with_workspace,
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


def _add_org_chip(db, org, *, text, bucket="preferred", ordering=0):
    chip = OrganizationCriterion(
        organization_id=org.id, ordering=ordering, weight=1.0, bucket=bucket, text=text
    )
    db.add(chip)
    db.flush()
    return chip


def _make_role(db, org, **kwargs):
    role = Role(organization_id=org.id, name=kwargs.pop("name", "Backend Engineer"), **kwargs)
    db.add(role)
    db.flush()
    return role


def _recruiter_chips(role):
    return sorted(
        [c for c in role.criteria if c.source == CRITERION_SOURCE_RECRUITER and c.deleted_at is None],
        key=lambda c: c.ordering,
    )


# ---------------------------------------------------------------------------
# Snapshot workspace → role
# ---------------------------------------------------------------------------


def test_snapshot_copies_each_workspace_chip_with_provenance(session):
    db, org = session
    a = _add_org_chip(db, org, text="Python", bucket="must", ordering=0)
    b = _add_org_chip(db, org, text="LLMs", bucket="preferred", ordering=1)

    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.commit()
    db.refresh(role)

    chips = _recruiter_chips(role)
    assert {c.text for c in chips} == {"Python", "LLMs"}
    by_text = {c.text: c for c in chips}
    assert by_text["Python"].org_criterion_id == a.id
    assert by_text["Python"].bucket == "must"
    assert by_text["Python"].must_have is True
    assert by_text["LLMs"].org_criterion_id == b.id
    assert by_text["LLMs"].bucket == "preferred"


def test_snapshot_is_idempotent_on_unchanged_workspace(session):
    db, org = session
    _add_org_chip(db, org, text="Python", bucket="must")
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.commit()
    snapshot_workspace_criteria(db, role)
    db.commit()
    db.refresh(role)
    assert len(_recruiter_chips(role)) == 1


# ---------------------------------------------------------------------------
# Sync workspace
# ---------------------------------------------------------------------------


def test_sync_pulls_in_new_workspace_chips_without_disturbing_role_only(session):
    db, org = session
    _workspace = _add_org_chip(db, org, text="Python", bucket="must", ordering=0)
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    # Recruiter adds a role-only chip.
    db.add(
        RoleCriterion(
            role_id=role.id,
            source=CRITERION_SOURCE_RECRUITER,
            ordering=99,
            weight=1.0,
            must_have=False,
            bucket="preferred",
            text="role-only",
            org_criterion_id=None,
        )
    )
    db.commit()
    db.refresh(role)

    # Workspace adds a new chip.
    _add_org_chip(db, org, text="Postgres", bucket="must", ordering=1)

    sync_role_with_workspace(db, role)
    db.commit()
    db.refresh(role)
    chips = _recruiter_chips(role)
    texts = {c.text for c in chips}
    assert {"Python", "Postgres", "role-only"}.issubset(texts)


def test_sync_skips_customized_workspace_chips(session):
    db, org = session
    workspace = _add_org_chip(db, org, text="Python", bucket="must")
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.commit()
    db.refresh(role)

    # Recruiter customizes the chip on the role.
    role_chip = next(c for c in _recruiter_chips(role) if c.org_criterion_id == workspace.id)
    role_chip.text = "Python 3.11+"
    from datetime import datetime, timezone
    role_chip.customized_at = datetime.now(timezone.utc)

    # Workspace edits the same chip.
    workspace.text = "Python (any version)"

    sync_role_with_workspace(db, role)
    db.commit()
    db.refresh(role)
    same = next(c for c in _recruiter_chips(role) if c.org_criterion_id == workspace.id)
    assert same.text == "Python 3.11+"


def test_sync_drops_workspace_link_when_workspace_chip_is_deleted(session):
    db, org = session
    workspace = _add_org_chip(db, org, text="Python", bucket="must")
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.commit()
    db.refresh(role)

    # Workspace soft-deletes the chip.
    from datetime import datetime, timezone
    workspace.deleted_at = datetime.now(timezone.utc)
    db.flush()

    sync_role_with_workspace(db, role)
    db.commit()
    db.refresh(role)
    chips = _recruiter_chips(role)
    assert len(chips) == 1
    assert chips[0].text == "Python"  # role keeps its copy
    assert chips[0].org_criterion_id is None  # provenance dropped


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_drops_role_only_chips_and_clears_suppressions(session):
    db, org = session
    workspace = _add_org_chip(db, org, text="Python", bucket="must")
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.add(
        RoleCriterion(
            role_id=role.id,
            source=CRITERION_SOURCE_RECRUITER,
            ordering=99,
            weight=1.0,
            must_have=False,
            bucket="preferred",
            text="role-only",
            org_criterion_id=None,
        )
    )
    role.suppressed_org_criterion_ids = [workspace.id]
    db.commit()
    db.refresh(role)

    reset_role_to_workspace(db, role)
    db.commit()
    db.refresh(role)
    chips = _recruiter_chips(role)
    assert len(chips) == 1
    assert chips[0].text == "Python"
    assert chips[0].org_criterion_id == workspace.id
    assert role.suppressed_org_criterion_ids == []


# ---------------------------------------------------------------------------
# Spec-derived sync
# ---------------------------------------------------------------------------


def test_sync_derived_pulls_from_requirements_section(session):
    db, org = session
    role = _make_role(
        db,
        org,
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

    sync_derived_criteria(db, role)
    db.commit()
    db.refresh(role)

    derived = sorted(
        [c for c in role.criteria if c.source == CRITERION_SOURCE_DERIVED],
        key=lambda c: c.ordering,
    )
    texts = [c.text for c in derived]
    assert "5+ years Python" in texts
    assert "Postgres at scale" in texts
    assert not any("Health insurance" in t for t in texts)


def test_sync_derived_yields_nothing_when_no_requirements_heading(session):
    db, org = session
    role = _make_role(
        db, org, job_spec_text="A great opportunity for a backend engineer who loves Python."
    )
    sync_derived_criteria(db, role)
    db.commit()
    db.refresh(role)
    derived = [c for c in role.criteria if c.source == CRITERION_SOURCE_DERIVED]
    assert derived == []


def test_sync_all_snapshots_workspace_and_syncs_derived(session):
    db, org = session
    _add_org_chip(db, org, text="Recruiter must-have", bucket="must")
    role = _make_role(
        db, org, job_spec_text="Requirements\n- Spec-derived item\n"
    )

    sync_all_criteria(db, role)
    db.commit()
    db.refresh(role)

    recruiter = _recruiter_chips(role)
    derived = sorted(
        [c for c in role.criteria if c.source == CRITERION_SOURCE_DERIVED],
        key=lambda c: c.ordering,
    )
    assert [c.text for c in recruiter] == ["Recruiter must-have"]
    assert [c.text for c in derived] == ["Spec-derived item"]


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def test_render_role_intent_block_groups_by_bucket(session):
    db, org = session
    _add_org_chip(db, org, text="Python", bucket="must")
    _add_org_chip(db, org, text="LLMs", bucket="preferred", ordering=1)
    _add_org_chip(db, org, text="EU TZ", bucket="constraint", ordering=2)
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.commit()
    db.refresh(role)

    block = render_role_intent_block(role)
    assert "MUST HAVE" in block and "Python" in block
    assert "PREFERRED" in block and "LLMs" in block
    assert "CONSTRAINTS" in block and "EU TZ" in block


def test_render_role_intent_lines_returns_flat_chip_text(session):
    db, org = session
    _add_org_chip(db, org, text="Python", bucket="must")
    _add_org_chip(db, org, text="LLMs", bucket="preferred", ordering=1)
    role = _make_role(db, org)
    snapshot_workspace_criteria(db, role)
    db.commit()
    db.refresh(role)

    lines = render_role_intent_lines(role)
    assert lines == ["Python", "LLMs"]


def test_render_helpers_skip_derived_chips(session):
    """Derived-from-spec chips are produced by the parser and are NOT
    recruiter intent — they shouldn't bleed into the prompt sections."""
    db, org = session
    role = _make_role(db, org, job_spec_text="Requirements\n- Spec item\n")
    sync_derived_criteria(db, role)
    db.commit()
    db.refresh(role)

    assert render_role_intent_block(role) == ""
    assert render_role_intent_lines(role) == []
