"""Unit tests for the role-agent chat impact engine + conversation service.

Pure DB, no LLM: covers the math the conversational agent runs (threshold
simulation / recommendation, the real retract+reconcile commit, constraint
edits that re-screen) and the sidebar/unread bookkeeping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.agent_chat import constraints as cc
from app.agent_chat import impact, service, tools
from app.models.agent_conversation import AgentConversation, AgentConversationMessage
from app.models.agent_decision import AgentDecision
from app.models.agent_needs_input import AgentNeedsInput
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import RoleCriterion
from app.models.user import User


# SQLite BigInteger PK workaround for the agent tables.
_BIG_PK_COUNTERS: dict[str, int] = {"agent_decisions": 0, "agent_needs_input": 0}


def _assign_big_pk(mapper, connection, target):  # pragma: no cover
    table = target.__table__.name
    if target.id is None and table in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[table] += 1
        target.id = _BIG_PK_COUNTERS[table]


event.listen(AgentDecision, "before_insert", _assign_big_pk)
event.listen(AgentNeedsInput, "before_insert", _assign_big_pk)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _org(db, name="Impact Org") -> Organization:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _user(db, org) -> User:
    u = User(
        email=f"u-{id(db)}-{org.id}@x.test",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(u)
    db.flush()
    return u


def _role(db, org, *, name="Backend", threshold=70, agentic=True) -> Role:
    role = Role(
        organization_id=org.id,
        name=name,
        source="manual",
        score_threshold=threshold,
        # Impact reporting is computed against a KNOWN pinned threshold. The
        # product default is now ``auto`` (dynamic); pin manual so these tests
        # exercise the recruiter-set-threshold path they were written for.
        auto_reject_threshold_mode="manual",
        agentic_mode_enabled=agentic,
    )
    db.add(role)
    db.flush()
    return role


def _scored_app(
    db, org, role, *, score, name="Cand", stage="applied", outcome="open"
) -> CandidateApplication:
    cand = Candidate(
        organization_id=org.id, email=f"{name}-{id(db)}-{score}@x.test", full_name=name
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage=stage,
        pipeline_stage_source="recruiter",
        application_outcome=outcome,
        source="manual",
        pre_screen_score_100=score,
        # A genuinely pre-screened candidate has this stamp; queue_pre_screen_reject
        # requires it (it refuses to card a candidate that was never pre-screened),
        # so the apply_threshold reconcile only emits cards when it's set.
        pre_screen_run_at=datetime.now(timezone.utc),
    )
    db.add(app)
    db.flush()
    return app


def _decision(db, org, role, app, *, decision_type="advance_to_interview", status="pending", key=None):
    d = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        decision_type=decision_type,
        recommendation=decision_type,
        status=status,
        reasoning="r",
        model_version="m",
        prompt_version="p",
        idempotency_key=key or f"k:{app.id}:{decision_type}:{status}",
    )
    db.add(d)
    db.flush()
    return d


# ---------------------------------------------------------------------------
# simulate_threshold (read-only projection)
# ---------------------------------------------------------------------------


def test_simulate_threshold_projects_above_below_and_newly_cleared(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _scored_app(db, org, role, score=80, name="Ada")
    _scored_app(db, org, role, score=65, name="Bo")
    _scored_app(db, org, role, score=50, name="Cy")
    db.commit()

    out = impact.simulate_threshold(db, role, 60)
    assert out["type"] == "threshold_simulation"
    assert out["current_threshold"] == 70
    assert out["simulated_threshold"] == 60
    # At 70: above {80}, below {65,50}. At 60: above {80,65}, below {50}.
    assert out["current_above"] == 1
    assert out["simulated_above"] == 2
    assert out["delta_above"] == 1
    assert out["newly_cleared_count"] == 1
    assert "Bo" in out["newly_cleared_sample"]
    # One open candidate below 60 with no pending decision → would be carded.
    assert out["would_reject_count"] == 1
    assert out["would_retract_count"] == 0


# ---------------------------------------------------------------------------
# recommend_threshold
# ---------------------------------------------------------------------------


def test_recommend_threshold_hits_target_additional(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _scored_app(db, org, role, score=85, name="Above")  # already clears
    _scored_app(db, org, role, score=68, name="Near1")
    _scored_app(db, org, role, score=65, name="Near2")
    _scored_app(db, org, role, score=50, name="Far")
    db.commit()

    out = impact.recommend_threshold(db, role, target_additional=2)
    assert out["type"] == "threshold_recommendation"
    # Boundary is the 2nd-best recoverable (65); cutoff drops to 65.
    assert out["recommended_threshold"] == 65
    assert out["projected_additional"] == 2
    assert out["current_above"] == 1
    assert out["projected_above"] == 3
    assert {"Near1", "Near2"} <= set(out["added_sample"])


def test_recommend_threshold_no_recoverable_returns_current(db):
    org = _org(db)
    role = _role(db, org, threshold=40)
    _scored_app(db, org, role, score=90, name="High")
    db.commit()

    out = impact.recommend_threshold(db, role, target_additional=5)
    assert out["recommended_threshold"] == role.score_threshold
    assert out["projected_additional"] == 0


# ---------------------------------------------------------------------------
# apply_threshold — the real commit: retract advances + reconcile rejects
# ---------------------------------------------------------------------------


def test_apply_threshold_retracts_stale_advance_and_cards_rejects(db):
    org = _org(db)
    role = _role(db, org, threshold=50, agentic=True)
    app_high = _scored_app(db, org, role, score=80, name="High")
    app_mid = _scored_app(db, org, role, score=55, name="Mid")
    app_low = _scored_app(db, org, role, score=40, name="Low")
    # A pending advance for Mid — valid at 50, stale once we raise to 60.
    adv = _decision(db, org, role, app_mid, decision_type="advance_to_interview", status="pending")
    db.commit()

    out = impact.apply_threshold(db, role, 60, organization_id=org.id)

    assert role.score_threshold == 60
    assert out["after_threshold"] == 60
    # Mid's stale advance is retracted…
    assert out["discarded_advances"] == 1
    db.refresh(adv)
    assert adv.status == "discarded"
    # …and reject cards are emitted for the two now-below-cutoff opens (Mid, Low).
    assert out["created_rejects"] == 2
    assert out["above_after"] == 1  # only High clears 60
    assert out["below_after"] == 2

    rejects = (
        db.query(AgentDecision)
        .filter(
            AgentDecision.role_id == role.id,
            AgentDecision.decision_type == "skip_assessment_reject",
            AgentDecision.status == "pending",
        )
        .all()
    )
    assert {r.application_id for r in rejects} == {app_mid.id, app_low.id}


def test_apply_threshold_clear_to_none_sets_org_default(db):
    org = _org(db)
    role = _role(db, org, threshold=70, agentic=True)
    _scored_app(db, org, role, score=60, name="Mid")
    db.commit()

    out = impact.apply_threshold(db, role, None, organization_id=org.id)
    assert role.score_threshold is None
    assert out["before_threshold"] == 70


# ---------------------------------------------------------------------------
# Constraint edits — create chip + trigger re-screen
# ---------------------------------------------------------------------------


def test_add_constraint_creates_chip_and_triggers_rescreen(db):
    org = _org(db)
    role = _role(db, org, agentic=True)
    _scored_app(db, org, role, score=72, name="C1")
    db.commit()

    with patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=4
    ) as mock_stale, patch("app.tasks.scoring_tasks.sweep_stale_scores") as mock_sweep:
        out = cc.add_or_update_constraint(
            db, role, text="Salary expectation <= 25,000", bucket="constraint"
        )

    assert out["type"] == "constraint_change"
    assert out["action"] == "added"
    assert out["criterion"]["bucket"] == "constraint"
    assert out["rescreening_count"] == 4
    mock_stale.assert_called_once()
    mock_sweep.apply_async.assert_called_once()

    chip = (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == role.id, RoleCriterion.deleted_at.is_(None))
        .one()
    )
    assert chip.text == "Salary expectation <= 25,000"
    assert chip.bucket == "constraint"
    assert chip.must_have is False


def test_preferred_constraint_does_not_trigger_rescreen(db):
    """A nice-to-have doesn't change the pre-screen prompt → no re-score."""
    org = _org(db)
    role = _role(db, org, agentic=True)
    db.commit()

    with patch(
        "app.services.cv_score_orchestrator.mark_role_scores_stale", return_value=9
    ) as mock_stale, patch("app.tasks.scoring_tasks.sweep_stale_scores") as mock_sweep:
        out = cc.add_or_update_constraint(
            db, role, text="Bonus: open-source contributions", bucket="preferred"
        )

    assert out["rescreening_count"] == 0
    mock_stale.assert_not_called()
    mock_sweep.apply_async.assert_not_called()


# ---------------------------------------------------------------------------
# tools.dispatch_tool wiring + get_role_overview
# ---------------------------------------------------------------------------


def test_get_role_overview_reports_threshold_funnel_and_constraints(db):
    org = _org(db)
    role = _role(db, org, threshold=70, agentic=True)
    _scored_app(db, org, role, score=80, name="A", stage="applied")
    _scored_app(db, org, role, score=40, name="B", stage="applied")
    db.add(
        RoleCriterion(
            role_id=role.id, source="recruiter", bucket="constraint",
            must_have=False, text="Must be UK-based", ordering=0, weight=1.0,
        )
    )
    db.commit()

    user = _user(db, org)
    out = tools.dispatch_tool("get_role_overview", {}, db=db, role=role, user=user)
    assert out["threshold"]["effective"] == 70
    assert out["open_candidates"] == 2
    assert out["above_threshold"] == 1
    assert out["below_threshold"] == 1
    assert any(c["text"] == "Must be UK-based" for c in out["constraints"])
    assert out["funnel"].get("applied") == 2


def test_dispatch_simulate_threshold_returns_card(db):
    org = _org(db)
    role = _role(db, org, threshold=70)
    _scored_app(db, org, role, score=65, name="X")
    db.commit()
    user = _user(db, org)

    out = tools.dispatch_tool("simulate_threshold", {"threshold": 60}, db=db, role=role, user=user)
    assert out["type"] == "threshold_simulation"
    assert out["simulated_threshold"] == 60


def test_dispatch_unknown_tool_raises(db):
    org = _org(db)
    role = _role(db, org)
    user = _user(db, org)
    with pytest.raises(KeyError):
        tools.dispatch_tool("nope", {}, db=db, role=role, user=user)


# ---------------------------------------------------------------------------
# Conversation service — ensure / list / unread
# ---------------------------------------------------------------------------


def test_ensure_conversation_is_idempotent_per_role(db):
    org = _org(db)
    role = _role(db, org)
    db.commit()
    c1 = service.ensure_conversation(db, organization_id=org.id, role=role)
    c2 = service.ensure_conversation(db, organization_id=org.id, role=role)
    assert c1.id == c2.id


def test_list_agent_conversations_counts_attention(db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, name="Data Eng", agentic=True)
    app = _scored_app(db, org, role, score=40, name="Z")
    _decision(db, org, role, app, decision_type="skip_assessment_reject", status="pending", key="z1")
    # An open agent question on the role.
    db.add(
        AgentNeedsInput(
            organization_id=org.id, role_id=role.id, kind="threshold_ambiguous",
            prompt="What cutoff do you want?",
        )
    )
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    # An unread agent message in the thread.
    db.add(
        AgentConversationMessage(
            conversation_id=convo.id, organization_id=org.id, role_id=role.id,
            author_role="assistant", kind="chat", content=[{"type": "text", "text": "hi"}],
            text="I queued a reject for Z.",
        )
    )
    db.commit()

    items = service.list_agent_conversations(db, organization_id=org.id, user=user)
    assert len(items) == 1
    item = items[0]
    assert item["role_id"] == role.id
    assert item["pending_decisions"] == 1
    assert item["open_questions"] == 1
    assert item["unread_messages"] == 1
    assert item["attention"] == 3


# ---------------------------------------------------------------------------
# Re-screen impact report ("feels instant" completion message)
# ---------------------------------------------------------------------------


def test_post_rescreen_impact_reports_shrink_and_recommends(db):
    from app.agent_chat.rescreen_report import post_rescreen_impact

    org = _org(db)
    role = _role(db, org, threshold=70, agentic=True)
    _scored_app(db, org, role, score=80, name="Keep")
    _scored_app(db, org, role, score=65, name="Near")
    _scored_app(db, org, role, score=50, name="Far")
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    db.commit()

    # Pretend 3 cleared the cut-off before the re-screen; now only Keep (80) does.
    msg = post_rescreen_impact(db, conversation=convo, role=role, baseline_qualified=3)
    db.commit()
    assert "from 3 to 1" in msg.text
    assert "65" in msg.text  # the recommended lower cut-off
    assert msg.actions and msg.actions[0]["type"] == "threshold_recommendation"
    assert msg.kind == "action"


def test_post_rescreen_impact_no_change_has_no_card(db):
    from app.agent_chat.rescreen_report import post_rescreen_impact

    org = _org(db)
    role = _role(db, org, threshold=70, agentic=True)
    _scored_app(db, org, role, score=80, name="Keep")
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    db.commit()

    msg = post_rescreen_impact(db, conversation=convo, role=role, baseline_qualified=1)
    db.commit()
    assert "No change" in msg.text
    assert not msg.actions


def test_count_inflight_score_jobs_uses_latest_job_per_app(db):
    from datetime import datetime, timezone

    from app.agent_chat.rescreen_report import count_inflight_score_jobs
    from app.models.cv_score_job import CvScoreJob

    org = _org(db)
    role = _role(db, org, threshold=70, agentic=True)
    a = _scored_app(db, org, role, score=80, name="A")
    b = _scored_app(db, org, role, score=60, name="B")
    # A: latest job is done (older stale superseded) → not in flight.
    db.add(CvScoreJob(application_id=a.id, status="stale", queued_at=datetime(2026, 6, 3, 8, tzinfo=timezone.utc)))
    db.add(CvScoreJob(application_id=a.id, status="done", queued_at=datetime(2026, 6, 3, 9, tzinfo=timezone.utc)))
    # B: latest job still stale → in flight.
    db.add(CvScoreJob(application_id=b.id, status="stale", queued_at=datetime(2026, 6, 3, 9, tzinfo=timezone.utc)))
    db.commit()

    assert count_inflight_score_jobs(db, role.id) == 1


def test_report_task_posts_when_rescore_settled(db):
    """No in-flight score jobs → the task posts the proactive impact message."""
    from app.models.agent_conversation import AgentConversationMessage
    from app.tasks.agent_chat_tasks import report_rescreen_impact

    org = _org(db)
    role = _role(db, org, threshold=70, agentic=True)
    _scored_app(db, org, role, score=80, name="Keep")
    _scored_app(db, org, role, score=65, name="Near")
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    db.commit()  # the task opens its own session — needs committed rows

    out = report_rescreen_impact(convo.id, role.id, baseline_qualified=5)
    assert out["status"] == "posted"

    posted = (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == convo.id,
            AgentConversationMessage.author_role == "assistant",
        )
        .all()
    )
    assert any("Re-screen complete" in (m.text or "") for m in posted)


def test_mark_read_clears_unread(db):
    org = _org(db)
    user = _user(db, org)
    role = _role(db, org, agentic=True)
    convo = service.ensure_conversation(db, organization_id=org.id, role=role)
    db.add(
        AgentConversationMessage(
            conversation_id=convo.id, organization_id=org.id, role_id=role.id,
            author_role="assistant", kind="chat", content=[{"type": "text", "text": "hi"}],
            text="ping",
        )
    )
    db.commit()

    before = service.list_agent_conversations(db, organization_id=org.id, user=user)
    assert before[0]["unread_messages"] == 1
    service.mark_read(db, conversation=convo, user=user)
    db.commit()
    after = service.list_agent_conversations(db, organization_id=org.id, user=user)
    assert after[0]["unread_messages"] == 0
