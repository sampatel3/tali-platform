"""ensure_deterministic_decision — a scored candidate ALWAYS carries its
deterministic verdict as a pending HITL decision the moment it's scored,
decoupled from the agent cohort tick (so paused roles never strand it)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from app.decision_policy.bootstrap import bootstrap_org
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_policy import DecisionPolicy
from app.models.organization import Organization
from app.models.role import Role
from app.models.rubric_revision import RubricRevision
from app.models.task import Task
from app.models.usage_event import UsageEvent
from app.services import bulk_decision_service as bds

# SQLite BigInteger-PK workaround (same as test_bulk_decision_service).
_PK: dict[str, int] = {}


def _assign_pk(mapper, connection, target):  # pragma: no cover — SQLA hook
    t = target.__table__.name
    if getattr(target, "id", None) is None:
        _PK[t] = _PK.get(t, 0) + 1
        target.id = _PK[t]


for _m in (AgentRun, AgentDecision, DecisionPolicy, RubricRevision, AgentNeedsInput):
    event.listen(_m, "before_insert", _assign_pk)


def _seed_role(db, *, score_threshold=50, with_task=False, paused=False):
    org = Organization(name="O", slug=f"o-{id(db)}-{score_threshold}-{with_task}-{paused}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="R", source="manual",
        score_threshold=score_threshold, auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True,
    )
    if paused:
        role.agent_paused_at = datetime.now(timezone.utc)
    db.add(role)
    db.flush()
    if with_task:
        task = Task(name="Take-home")
        db.add(task)
        db.flush()
        role.tasks.append(task)
        db.flush()
    bootstrap_org(db, organization_id=int(org.id))
    db.commit()
    return org, role


def _add_app(db, org, role, *, role_fit, pre_screen=70.0, stage="applied", outcome="open"):
    cand = Candidate(
        organization_id=org.id,
        email=f"c{role_fit}-{id(db)}-{id(role)}@x.test",
        full_name="C",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage=stage, pipeline_stage_source="recruiter",
        application_outcome=outcome, source="manual", cv_text="cv text",
        cv_match_score=role_fit, pre_screen_score_100=pre_screen,
    )
    db.add(app)
    db.commit()
    return app


def _pending(db, role):
    return (
        db.query(AgentDecision)
        .filter(AgentDecision.role_id == role.id, AgentDecision.status == "pending")
        .all()
    )


def test_queues_reject_below_bar_when_no_decision(db):
    org, role = _seed_role(db, score_threshold=50)
    app = _add_app(db, org, role, role_fit=30.0)  # < 50 → reject
    out = bds.ensure_deterministic_decision(db, app=app, role=role)
    db.commit()
    assert out == "reject"
    decs = _pending(db, role)
    assert len(decs) == 1
    assert decs[0].decision_type == "reject"
    assert decs[0].model_version == "bulk-deterministic"
    assert (decs[0].evidence or {}).get("source") == "score_time_decision"
    assert db.query(UsageEvent).count() == 0  # no LLM


def test_queues_advance_above_bar_no_task(db):
    org, role = _seed_role(db, score_threshold=50, with_task=False)
    app = _add_app(db, org, role, role_fit=80.0)
    assert bds.ensure_deterministic_decision(db, app=app, role=role) == "advance_to_interview"


def test_queues_send_above_bar_with_task(db):
    org, role = _seed_role(db, score_threshold=50, with_task=True)
    app = _add_app(db, org, role, role_fit=80.0)
    assert bds.ensure_deterministic_decision(db, app=app, role=role) == "send_assessment"


def test_noop_when_pending_already_exists(db):
    org, role = _seed_role(db, score_threshold=50)
    app = _add_app(db, org, role, role_fit=30.0)
    assert bds.ensure_deterministic_decision(db, app=app, role=role) == "reject"
    db.commit()
    # auto_correct owns an existing card — generator must not double-queue.
    assert bds.ensure_deterministic_decision(db, app=app, role=role) is None
    assert len(_pending(db, role)) == 1


def test_surfaces_post_handover_as_decision(db):
    """Post-handover candidates are no longer withheld — Taali's read is surfaced
    as a HITL card via the second opinion (a below-bar verdict → reject:
    "you're interviewing someone I'd have passed on"), instead of stranding them
    as 'not yet decided'."""
    org, role = _seed_role(db, score_threshold=50)
    app = _add_app(db, org, role, role_fit=30.0)
    app.workable_stage = "Final Interview"
    db.commit()
    out = bds.ensure_deterministic_decision(db, app=app, role=role)
    assert out == "reject"
    decs = _pending(db, role)
    assert len(decs) == 1
    assert decs[0].decision_type == "reject"
    assert db.query(UsageEvent).count() == 0  # still zero-LLM


def test_skips_non_open_candidate(db):
    org, role = _seed_role(db, score_threshold=50)
    app = _add_app(db, org, role, role_fit=30.0, outcome="rejected")
    assert bds.ensure_deterministic_decision(db, app=app, role=role) is None
    assert _pending(db, role) == []


def test_never_opens_a_needs_input_card(db):
    """Critical: the generator must NOT call _maybe_raise_volume_guard — else
    every score during a backlog drain spawns a threshold card."""
    org, role = _seed_role(db, score_threshold=50)
    app = _add_app(db, org, role, role_fit=30.0)
    bds.ensure_deterministic_decision(db, app=app, role=role)
    db.commit()
    assert db.query(AgentNeedsInput).count() == 0


def test_paused_role_still_queues_and_leaves_state_untouched(db):
    org, role = _seed_role(db, score_threshold=50, paused=True)
    app = _add_app(db, org, role, role_fit=30.0)
    paused_before = role.agent_paused_at
    out = bds.ensure_deterministic_decision(db, app=app, role=role)
    db.commit()
    assert out == "reject"  # generated even though the role is paused
    db.refresh(role)
    assert role.agent_paused_at == paused_before  # pause untouched — no resume
    assert role.agentic_mode_enabled is True
    assert db.query(UsageEvent).count() == 0


def test_awaiting_you_counts_all_pending_decisions(db):
    """Every scored candidate carries a pending verdict (active OR paused), and
    'awaiting you' is ONE honest number — all pending decisions org-wide — so it
    reconciles with the funnel and the Pending list (an earlier active-only
    scoping disagreed with the funnel and confused the count; it was reverted)."""
    from app.domains.agentic.hub_routes import _compute_kpis

    org = Organization(name="O", slug=f"o-kpi-{id(db)}")
    db.add(org)
    db.flush()
    active = Role(
        organization_id=org.id, name="Active", source="manual",
        score_threshold=50, auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True,
    )
    paused = Role(
        organization_id=org.id, name="Paused", source="manual",
        score_threshold=50, auto_reject_threshold_mode="manual",
        agentic_mode_enabled=True, agent_paused_at=datetime.now(timezone.utc),
    )
    db.add_all([active, paused])
    db.flush()
    bootstrap_org(db, organization_id=int(org.id))
    db.commit()

    a1 = _add_app(db, org, active, role_fit=30.0)
    a2 = _add_app(db, org, paused, role_fit=30.0)
    assert bds.ensure_deterministic_decision(db, app=a1, role=active) == "reject"
    assert bds.ensure_deterministic_decision(db, app=a2, role=paused) == "reject"
    db.commit()

    assert db.query(AgentDecision).filter(AgentDecision.status == "pending").count() == 2
    # Both count toward 'awaiting you' — consistent with the funnel.
    kpi = _compute_kpis(db, organization_id=int(org.id))
    assert kpi.pending == 2


def test_role_pipeline_counts_not_yet_decided(db):
    """'not_yet_decided' = scored candidates with NO decision (pending or
    resolved) — the TRUE funnel count, replacing the FE's scored-minus-pending
    over-count."""
    from app.domains.assessments_runtime.pipeline_service import role_pipeline_counts

    org, role = _seed_role(db, score_threshold=50)
    a_undecided = _add_app(db, org, role, role_fit=30.0)  # scored, no card
    a_decided = _add_app(db, org, role, role_fit=80.0)  # scored → will get a card
    # scored, no card, BUT already interviewing in Workable → NOT "not yet decided"
    a_interviewing = _add_app(db, org, role, role_fit=80.0)
    a_interviewing.workable_stage = "Technical Interview"
    assert bds.ensure_deterministic_decision(db, app=a_decided, role=role)
    db.commit()

    counts = role_pipeline_counts(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    # only a_undecided counts: a_decided has a card; a_interviewing is post-handover
    assert counts["not_yet_decided"] == 1
    # a_interviewing displays as 'advanced' (Workable hand-off), NOT in its
    # normal bucket, even though its Tali pipeline_stage is still 'applied'.
    assert counts["advanced"] == 1
    # Post-#653 the funnel buckets by the REAL cv_match_score, so these two
    # scored candidates sit in 'scored', leaving 'applied' empty.
    assert counts["scored"] == 2  # a_undecided + a_decided
    assert counts["applied"] == 0
    assert a_undecided.id is not None and a_interviewing.id is not None


def test_not_yet_decided_excludes_agent_off_role(db):
    """'not yet decided BY THE AGENT' must not count roles where the agent is
    OFF — there's no agent to rule, the recruiter decides manually. (All 9 org-2
    residuals were on one agent-off role; the count should be 0, not 9.)"""
    from app.domains.assessments_runtime.pipeline_service import (
        role_pipeline_counts,
        role_pipeline_counts_bulk,
    )

    org, role = _seed_role(db, score_threshold=50)
    role.agentic_mode_enabled = False  # agent OFF — recruiter runs it manually
    db.commit()
    _add_app(db, org, role, role_fit=30.0)  # scored, open, no decision

    counts = role_pipeline_counts(
        db, organization_id=int(org.id), role_id=int(role.id)
    )
    assert counts["not_yet_decided"] == 0
    bulk = role_pipeline_counts_bulk(
        db, organization_id=int(org.id), role_ids=[int(role.id)]
    )
    assert bulk[int(role.id)]["not_yet_decided"] == 0
