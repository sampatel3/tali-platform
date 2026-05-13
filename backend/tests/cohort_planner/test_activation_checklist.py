"""Activation checklist surfaces every config gap as a NeedsInput row."""

from __future__ import annotations

from app.models.agent_needs_input import AgentNeedsInput
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.models.task import Task
from app.services.agent_activation_checklist import surface_activation_questions


def _make_org(db) -> Organization:
    org = Organization(name="Checklist Org", slug=f"checklist-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _make_role(db, org, **overrides) -> Role:
    defaults = dict(
        organization_id=org.id,
        name="Backend Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        score_threshold=None,
        auto_promote=False,
    )
    defaults.update(overrides)
    role = Role(**defaults)
    db.add(role)
    db.flush()
    return role


def _open_kinds(db, role) -> set[str]:
    rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .all()
    )
    return {r.kind for r in rows}


def test_surface_opens_all_gaps_on_activation(db):
    org = _make_org(db)
    # No threshold, no must-haves, no task linked. Budget IS set so that
    # gap doesn't fire (PATCH handler enforces it before activation).
    role = _make_role(db, org, score_threshold=None)
    surface_activation_questions(db, role=role)
    db.flush()
    kinds = _open_kinds(db, role)
    assert "threshold_ambiguous" in kinds
    assert "intent_slot_missing" in kinds
    assert "task_assignment_missing" in kinds
    # budget is set on the fixture, so no monthly_budget_missing
    assert "monthly_budget_missing" not in kinds


def test_surface_is_idempotent(db):
    """Re-running on activation toggles produces the same set of open rows,
    not duplicates."""
    org = _make_org(db)
    role = _make_role(db, org, score_threshold=None)
    surface_activation_questions(db, role=role)
    db.flush()
    surface_activation_questions(db, role=role)
    db.flush()
    rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .all()
    )
    # Three distinct kinds → three rows, not six.
    assert len(rows) == 3


def test_surface_skips_gaps_that_are_already_filled(db):
    """A role with threshold + must-have + task gets no questions."""
    org = _make_org(db)
    role = _make_role(db, org, score_threshold=70)
    # Recruiter-set must-have chip.
    must = RoleCriterion(
        role_id=role.id,
        source="recruiter",
        bucket="must",
        text="Python 5y",
        weight=1.0,
    )
    db.add(must)
    # Linked task.
    task = Task(
        organization_id=org.id,
        name="Build CRUD app",
        description="Spec",
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    db.flush()

    surface_activation_questions(db, role=role)
    db.flush()
    assert _open_kinds(db, role) == set()


def test_surface_intent_slot_carries_settings_link(db):
    """The intent_slot_missing canonical template embeds the settings-tab
    link via response_schema so the frontend can render a button."""
    org = _make_org(db)
    role = _make_role(db, org, score_threshold=70)  # only intent_slot gap
    surface_activation_questions(db, role=role)
    db.flush()
    intent_row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == "intent_slot_missing",
        )
        .one()
    )
    assert intent_row.response_schema is not None
    assert intent_row.response_schema.get("link_url") == f"/jobs/{int(role.id)}?tab=agent-settings"
    assert intent_row.response_schema.get("link_label") == "Open agent settings"
