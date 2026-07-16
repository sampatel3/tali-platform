"""ask_recruiter.open distinguishes by subject_id.

Codex #109: previously the idempotency key was ``(role_id, kind)`` so
multiple per-candidate questions overwrote each other in the same cycle.
The subject_id parameter scopes the key per subject (candidate /
assessment / etc.). Uses ``candidate_tie_break`` here as a per-candidate
kind that still lives in NEEDS_INPUT_KINDS — the original example used
the send_assessment_approval kind, which now flows through agent_decisions.
"""

from __future__ import annotations

from app.actions import ask_recruiter
from app.actions.types import Actor
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role


def _make_world(db):
    org = Organization(name=f"AR Org {id(db)}", slug=f"ar-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    return org, role


def test_different_subject_ids_create_separate_rows(db):
    org, role = _make_world(db)
    run = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    actor = Actor.agent(agent_run_id=int(run.id))
    row_a = ask_recruiter.open(
        db,
        actor,
        organization_id=org.id,
        role_id=role.id,
        kind="candidate_tie_break",
        prompt="Approve send for app 1?",
        subject_id=1,
    )
    row_b = ask_recruiter.open(
        db,
        actor,
        organization_id=org.id,
        role_id=role.id,
        kind="candidate_tie_break",
        prompt="Approve send for app 2?",
        subject_id=2,
    )
    assert row_a.id != row_b.id
    assert row_a.subject_id == 1
    assert row_b.subject_id == 2

    open_rows = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == "candidate_tie_break",
            AgentNeedsInput.resolved_at.is_(None),
            AgentNeedsInput.dismissed_at.is_(None),
        )
        .all()
    )
    assert len(open_rows) == 2


def test_same_subject_id_returns_existing_row_with_refreshed_prompt(db):
    org, role = _make_world(db)
    run = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    actor = Actor.agent(agent_run_id=int(run.id))
    row_a = ask_recruiter.open(
        db,
        actor,
        organization_id=org.id,
        role_id=role.id,
        kind="candidate_tie_break",
        prompt="First framing.",
        subject_id=42,
    )
    row_b = ask_recruiter.open(
        db,
        actor,
        organization_id=org.id,
        role_id=role.id,
        kind="candidate_tie_break",
        prompt="Refined framing.",
        subject_id=42,
    )
    assert row_a.id == row_b.id
    assert row_b.prompt == "Refined framing."


def test_null_subject_id_keeps_role_wide_semantics(db):
    """Role-wide kinds (like monthly_budget_missing) collapse onto one row."""
    org, role = _make_world(db)
    run = AgentRun(
        organization_id=org.id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    actor = Actor.agent(agent_run_id=int(run.id))
    row_a = ask_recruiter.open(
        db,
        actor,
        organization_id=org.id,
        role_id=role.id,
        kind="monthly_budget_missing",
        prompt="Pick a monthly cap.",
    )
    row_b = ask_recruiter.open(
        db,
        actor,
        organization_id=org.id,
        role_id=role.id,
        kind="monthly_budget_missing",
        prompt="Still need a monthly cap.",
    )
    assert row_a.id == row_b.id
    assert row_a.subject_id is None
