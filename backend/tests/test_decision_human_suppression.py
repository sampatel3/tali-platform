"""BUG-1: a recruiter's discard/override is an explicit "no".

The agent must not re-queue the same verdict next cycle (silently overriding
the human signal). The suppression holds until the candidate's cited inputs
materially change — a new score / CV / criteria edit / recruiter note —
at which point the agent is free to re-decide on fresh information.

Two guards are exercised:
  * ``queue_decision.run`` — the authoritative re-emit guard.
  * ``cohort_tools.find_apps_in_state`` — keeps suppressed apps out of the
    triage cohort so the agent doesn't even spend a cycle on them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.actions import queue_decision
from app.actions.types import Actor
from app.agent_runtime.cohort_tools import find_apps_in_state
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role


def _seed(db, *, pre_screen=60.0, cv_match=70.0, stage="review"):
    org = Organization(name="O", slug=f"o-{uuid4().hex}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_reject=False,
    )
    db.add(role)
    db.flush()
    cand = Candidate(organization_id=org.id, email=f"c-{id(db)}@x.test", full_name="C")
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text="some cv text",
        pre_screen_score_100=pre_screen,
        cv_match_score=cv_match,
    )
    db.add(app)
    db.flush()
    return org, role, app


def _agent_run(db, role: Role) -> AgentRun:
    run = AgentRun(
        organization_id=role.organization_id,
        role_id=role.id,
        trigger="cron",
        status="running",
        model_version="m",
        prompt_version="p",
    )
    db.add(run)
    db.flush()
    return run


def _queue(db, org, role, app, run, decision_type="send_assessment"):
    return queue_decision.run(
        db,
        Actor.agent(int(run.id)),
        organization_id=int(org.id),
        role_id=int(role.id),
        application_id=int(app.id),
        decision_type=decision_type,
        reasoning="Looks like a fit.",
        confidence=0.9,
        model_version="m",
        prompt_version="p",
    )


def _discard(db, decision: AgentDecision, *, by_user: bool = True) -> None:
    """Discard a decision. ``by_user`` mirrors a recruiter's explicit "no"
    (sets ``resolved_by_user_id`` — the only kind that suppresses re-emit);
    ``by_user=False`` mirrors a SYSTEM discard (re-score supersede / reconcile),
    which leaves the field NULL and must NOT suppress re-decision."""
    from app.models.user import User

    decision.status = "discarded"
    decision.resolved_at = datetime.now(timezone.utc)
    if by_user:
        decision.resolution_note = "Recruiter said no."
        u = User(
            email=f"u-{id(decision)}-{decision.id}@x.test",
            hashed_password="x",
            full_name="U",
            organization_id=decision.organization_id,
            is_active=True,
            is_verified=True,
        )
        db.add(u)
        db.flush()
        decision.resolved_by_user_id = u.id
    else:
        decision.resolution_note = (
            "superseded: candidate_data_changed; agent will re-decide once the new score lands"
        )
    db.flush()


# ---------------------------------------------------------------------------
# queue_decision.run
# ---------------------------------------------------------------------------


def test_discard_suppresses_reemit_until_inputs_change(db):
    org, role, app = _seed(db)
    run1 = _agent_run(db, role)
    db.commit()

    first = _queue(db, org, role, app, run1)
    db.commit()
    assert getattr(first, "_just_created", None) is True
    _discard(db, first)
    db.commit()

    # Next cycle: same verdict, inputs unchanged → suppressed (returns the
    # discarded row, no new pending decision).
    run2 = _agent_run(db, role)
    second = _queue(db, org, role, app, run2)
    assert int(second.id) == int(first.id)
    assert second.status == "discarded"
    assert getattr(second, "_just_created", None) is False
    pending = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.application_id == app.id,
            AgentDecision.status == "pending",
        )
        .count()
    )
    assert pending == 0


def test_system_discard_does_not_suppress_reemit(db):
    """A SYSTEM discard (re-score supersede / reconcile — no resolved_by_user_id)
    must NOT suppress re-decision, even when the verdict holds (so verdict-aware
    staleness reports 'not stale'). Otherwise a re-score discards the card and it
    can never be re-created — the stranding bug."""
    org, role, app = _seed(db)
    run1 = _agent_run(db, role)
    db.commit()

    first = _queue(db, org, role, app, run1)
    db.commit()
    _discard(db, first, by_user=False)  # system discard — inputs UNCHANGED
    db.commit()

    # Same verdict, inputs unchanged → a HUMAN discard would suppress, but a
    # system discard must let the agent re-create the card.
    run2 = _agent_run(db, role)
    second = _queue(db, org, role, app, run2)
    db.commit()
    assert int(second.id) != int(first.id)
    assert second.status == "pending"
    assert getattr(second, "_just_created", None) is True


def test_score_change_releases_suppression(db):
    org, role, app = _seed(db, pre_screen=60.0)
    run1 = _agent_run(db, role)
    db.commit()

    first = _queue(db, org, role, app, run1)
    db.commit()
    _discard(db, first)
    db.commit()

    # Pre-screen score drifts > the 5pt band → material change → release.
    app.pre_screen_score_100 = 80.0
    db.commit()

    run2 = _agent_run(db, role)
    second = _queue(db, org, role, app, run2)
    db.commit()
    assert int(second.id) != int(first.id)
    assert second.status == "pending"
    assert getattr(second, "_just_created", None) is True


def test_override_also_suppresses_reemit(db):
    """An override (recruiter took the opposite action) is just as much a
    human "no" to the agent's verdict as a discard."""
    org, role, app = _seed(db)
    run1 = _agent_run(db, role)
    db.commit()

    first = _queue(db, org, role, app, run1)
    db.commit()
    from app.models.user import User

    overrider = User(
        email=f"ovr-{id(db)}@x.test", hashed_password="x", full_name="O",
        organization_id=org.id, is_active=True, is_verified=True,
    )
    db.add(overrider)
    db.flush()
    first.status = "overridden"
    first.resolved_at = datetime.now(timezone.utc)
    first.human_disposition = "overridden"
    first.resolved_by_user_id = overrider.id  # an override is a human action
    db.commit()

    run2 = _agent_run(db, role)
    second = _queue(db, org, role, app, run2)
    assert int(second.id) == int(first.id)
    assert second.status == "overridden"


def test_different_decision_type_not_suppressed_by_discard(db):
    """The discard suppression is per decision_type — discarding a
    send_assessment must not block a (different) reject verdict."""
    org, role, app = _seed(db)
    run1 = _agent_run(db, role)
    db.commit()

    first = _queue(db, org, role, app, run1, decision_type="send_assessment")
    db.commit()
    _discard(db, first)
    db.commit()

    run2 = _agent_run(db, role)
    second = _queue(db, org, role, app, run2, decision_type="reject")
    db.commit()
    assert second.decision_type == "reject"
    assert int(second.id) != int(first.id)
    assert second.status == "pending"


# ---------------------------------------------------------------------------
# cohort_tools.find_apps_in_state
# ---------------------------------------------------------------------------


def test_cohort_excludes_app_with_live_discard(db):
    org, role, app = _seed(db, stage="review")  # ready_for_assessment_decision
    run = _agent_run(db, role)
    db.commit()

    # Before any discard the app is in the triage cohort.
    ids = find_apps_in_state(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        state="ready_for_assessment_decision",
    )
    assert int(app.id) in ids

    first = _queue(db, org, role, app, run)
    db.commit()
    _discard(db, first)
    db.commit()

    # With a live discard the app drops out of the cohort.
    ids = find_apps_in_state(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        state="ready_for_assessment_decision",
    )
    assert int(app.id) not in ids


def test_cohort_reincludes_app_after_inputs_change(db):
    org, role, app = _seed(db, pre_screen=60.0, stage="review")
    run = _agent_run(db, role)
    db.commit()

    first = _queue(db, org, role, app, run)
    db.commit()
    _discard(db, first)
    db.commit()

    ids = find_apps_in_state(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        state="ready_for_assessment_decision",
    )
    assert int(app.id) not in ids

    # Material input change releases the suppression.
    app.pre_screen_score_100 = 85.0
    db.commit()

    ids = find_apps_in_state(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        state="ready_for_assessment_decision",
    )
    assert int(app.id) in ids
