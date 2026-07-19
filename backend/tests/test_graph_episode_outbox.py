"""Durability of the realised-outcome (and decision) training signal.

A realised hiring outcome — what actually happened to a candidate after an
approved agent decision — cannot be reconstructed months later. The old
path emitted it to Graphiti fire-and-forget, so a graph outage silently
dropped it. These tests cover the durable outbox that replaced that path:

- The outcome write lands in ``graph_episode_outbox`` even when the graph
  client is unconfigured AND any direct graph call would raise.
- Enqueue is idempotent (deterministic dedup key).
- The drain sends pending rows, marks them ``sent``, and is idempotent.
- A send that doesn't land (returns 0 OR raises) leaves the row ``pending``
  — the irreplaceable signal is never lost.
- Transient provider/budget/graph failures stay retryable beyond the old cap
  and recover automatically after the cooldown.
- Only structurally invalid payloads become terminal ``failed``.
- The drain is a no-op while Graphiti is unconfigured (rows untouched).
- The Celery drain task ships pending rows and is idempotent across runs.

Mirrors the existing graph-test pattern: mock the graph client / dispatch
rather than standing up Neo4j.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.agent_runtime import outcome_learning
from app.actions._decision_side_effects import (
    _organization_resolution_guard_statement,
    apply_decision_side_effects,
)
from app.actions.types import ACTOR_RECRUITER, Actor
from app.candidate_graph import client as graph_client
from app.candidate_graph import episode_outbox
from app.candidate_graph import episode_outbox_query
from app.candidate_graph import episodes as episode_module
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.graph_episode_outbox import (
    EPISODE_KIND_DECISION,
    EPISODE_KIND_HIRING_OUTCOME,
    EPISODE_KIND_RECRUITER_ACTION,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SENT,
    GraphEpisodeOutbox,
)
from app.models.organization import Organization
from app.models.role import Role
from app.tasks.graph_outbox_tasks import drain_graph_episode_outbox


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_advance(db, *, label="default"):
    """Org + role + application + an already-approved advance decision."""
    org = Organization(
        name=f"Outbox Org {label}", slug=f"outbox-{id(db)}-{label}"
    )
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        agentic_mode_enabled=True,
    )
    db.add(role)
    db.flush()
    cand = Candidate(
        organization_id=org.id,
        email=f"c-{id(db)}-{label}@x.test",
        full_name="Outcome Cand",
    )
    db.add(cand)
    db.flush()
    app = CandidateApplication(
        organization_id=org.id,
        candidate_id=cand.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(app)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=app.id,
        agent_run_id=None,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="approved",
        reasoning="strong CV",
        confidence=0.9,
        model_version="test-model",
        prompt_version="test-prompt",
        idempotency_key=f"outbox:{app.id}:advance",
        resolved_at=datetime.now(timezone.utc),
    )
    db.add(decision)
    db.flush()
    return org, role, app, decision


def _enqueue_pending(db, *, label="default"):
    """Record an advance outcome → one pending outbox row. Commits."""
    org, role, app, decision = _seed_advance(db, label=label)
    outcome_learning.record_advance_outcome_on_stage(
        db, application=app, new_stage="advanced"
    )
    db.commit()
    return org, role, app, decision


# ---------------------------------------------------------------------------
# Enqueue: lands even when the graph is dead
# ---------------------------------------------------------------------------


def test_outbox_model_matches_live_role_ownership_schema():
    role_id = GraphEpisodeOutbox.__table__.c.role_id
    foreign_key = next(iter(role_id.foreign_keys))

    assert role_id.nullable is True
    assert foreign_key.target_fullname == "roles.id"
    assert foreign_key.ondelete == "SET NULL"
    assert foreign_key.constraint.name == "fk_graph_episode_outbox_role_id_roles"
    assert "ix_graph_episode_outbox_role_id" in {
        index.name for index in GraphEpisodeOutbox.__table__.indexes
    }


def test_outcome_lands_in_outbox_when_graph_unconfigured_and_raising(db):
    org, role, app, decision = _seed_advance(db)

    # Graph completely unusable: unconfigured AND any direct use raises.
    with patch.object(graph_client, "is_configured", return_value=False), patch.object(
        graph_client, "get_graphiti", side_effect=RuntimeError("graph down")
    ), patch.object(
        episode_module, "dispatch", side_effect=RuntimeError("graph down")
    ):
        outcome_learning.record_advance_outcome_on_stage(
            db, application=app, new_stage="advanced"
        )
        db.commit()

    rows = db.query(GraphEpisodeOutbox).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.episode_kind == EPISODE_KIND_HIRING_OUTCOME
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0
    assert row.organization_id == int(org.id)
    assert row.role_id == int(role.id)
    assert row.payload["decision_id"] == int(decision.id)
    assert row.payload["role_id"] == int(role.id)
    # v1 "interviewed" maps to the v2 outcome_type vocabulary.
    assert row.payload["outcome_type"] == "reached_interview"

    # Dual-write preserved: the calibration FIFO still records the outcome.
    db.refresh(role)
    outcomes = (role.agent_calibration or {}).get("outcomes") or []
    assert len(outcomes) == 1
    assert outcomes[0]["outcome"] == "interviewed"


def test_enqueue_is_idempotent_on_dedup_key(db):
    """Re-firing the same transition must not create a duplicate outbox row."""
    org, role, app, decision = _seed_advance(db)

    outcome_learning.record_advance_outcome_on_stage(
        db, application=app, new_stage="advanced"
    )
    outcome_learning.record_advance_outcome_on_stage(
        db, application=app, new_stage="advanced"
    )
    db.commit()

    assert db.query(GraphEpisodeOutbox).count() == 1


def test_decision_enqueue_persists_normalized_role_ownership(db):
    org, role, app, decision = _seed_advance(db)

    row = episode_outbox.enqueue_decision(
        db,
        organization_id=int(org.id),
        candidate_full_name="Outcome Cand",
        candidate_taali_id=int(app.candidate_id),
        application_id=int(app.id),
        role_id=int(role.id),
        decision_id=int(decision.id),
        recommended_action="advance_to_interview",
        confidence=0.9,
        policy_revision_id=None,
        reasoning="strong CV",
        created_at=decision.created_at,
    )

    assert row is not None
    assert row.episode_kind == EPISODE_KIND_DECISION
    assert row.role_id == int(role.id)
    assert row.payload["role_id"] == int(role.id)
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 1
    episode = list(dispatch.call_args.args[0])[0]
    assert episode.name == f"agent-decision-{int(decision.id)}"
    assert episode.group_id == graph_client.group_id_for_org(int(org.id))
    assert dispatch.call_args.kwargs["bill_organization_id"] == int(org.id)
    assert dispatch.call_args.kwargs["bill_role_id"] == int(role.id)


def test_recruiter_action_is_queued_without_contacting_graphiti(db):
    """Approval commits a durable graph intent; Graphiti runs after commit."""
    org, role, app, decision = _seed_advance(db)
    actor = Actor(type=ACTOR_RECRUITER, user_id=17)
    happened_at = decision.resolved_at.isoformat()

    with patch(
        "app.candidate_graph.agent_episodes.emit_recruiter_action_event",
        side_effect=AssertionError("Graphiti must not run inside approval"),
    ) as direct_emit:
        apply_decision_side_effects(
            db,
            actor,
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
            note="Strong evidence",
        )
        db.commit()

    direct_emit.assert_not_called()
    row = db.query(GraphEpisodeOutbox).one()
    assert row.episode_kind == EPISODE_KIND_RECRUITER_ACTION
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.role_id == int(role.id)
    assert row.payload == {
        "organization_id": int(org.id),
        "role_id": int(role.id),
        "decision_id": int(decision.id),
        "recruiter_id": 17,
        "action": "approve",
        "reason": "Strong evidence",
        "happened_at": happened_at,
    }


def test_resolution_guard_uses_postgres_key_share_lock():
    sql = str(
        _organization_resolution_guard_statement(7).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR KEY SHARE" in sql


def test_recruiter_action_enqueue_failure_does_not_poison_approval_transaction(db):
    org, role, app, decision = _seed_advance(db)

    def fail_with_constraint_error(outbox_db, **_kwargs):
        for _ in range(2):
            outbox_db.add(
                GraphEpisodeOutbox(
                    organization_id=int(org.id),
                    episode_kind=EPISODE_KIND_RECRUITER_ACTION,
                    dedup_key="forced-savepoint-conflict",
                    payload={},
                    status=OUTBOX_STATUS_PENDING,
                    attempts=0,
                )
            )
            outbox_db.flush()

    with patch.object(
        episode_outbox,
        "enqueue_recruiter_action",
        side_effect=fail_with_constraint_error,
    ):
        apply_decision_side_effects(
            db,
            Actor(type=ACTOR_RECRUITER, user_id=17),
            decision=decision,
            app=app,
            org=org,
            role=role,
            disposition="approved",
        )

    role.name = "Approval still committable"
    db.commit()

    assert db.get(Role, int(role.id)).name == "Approval still committable"
    assert db.query(GraphEpisodeOutbox).count() == 0


# ---------------------------------------------------------------------------
# Drain: sends pending rows, idempotent
# ---------------------------------------------------------------------------


def test_drain_sends_pending_and_is_idempotent(db):
    org, role, app, decision = _enqueue_pending(db)
    assert (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.status == OUTBOX_STATUS_PENDING)
        .count()
        == 1
    )

    dispatched = []
    dispatch_kwargs = []

    def fake_dispatch(eps, **kwargs):
        eps = list(eps)
        dispatched.append(eps)
        dispatch_kwargs.append(kwargs)
        return len(eps)

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", side_effect=fake_dispatch
    ):
        summary1 = episode_outbox.drain(db)

    assert summary1["sent"] == 1
    assert summary1["scanned"] == 1
    assert len(dispatched) == 1
    # The rebuilt episode carries the canonical HiringOutcome body.
    assert "HiringOutcome".lower() in dispatched[0][0].body.lower() or (
        f"D-" in dispatched[0][0].body
    )
    # Regression: the drain must attribute the spend to the row's org (+ pass
    # db) so the metered async wrapper writes a per-org graph_sync usage_event
    # instead of an unattributed (org=NULL) call_log row. Was dropped pre-fix.
    kw = dispatch_kwargs[0]
    assert kw.get("bill_organization_id") == int(org.id)
    assert kw.get("bill_role_id") == int(role.id)
    assert kw.get("require_hard_admission") is True
    assert kw.get("require_role_admission") is True
    assert kw.get("raise_on_error") is True
    assert str(kw.get("bill_trace_id")).startswith("graph-outbox:")
    assert kw.get("db") is db

    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_SENT
    assert row.sent_at is not None

    # Idempotent: nothing pending → no second dispatch.
    dispatched.clear()
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", side_effect=fake_dispatch
    ):
        summary2 = episode_outbox.drain(db)

    assert summary2["scanned"] == 0
    assert summary2["sent"] == 0
    assert dispatched == []


def test_normalized_active_row_uses_one_fresh_role_query(db):
    _enqueue_pending(db, label="single-role-query")
    statements: list[str] = []

    def capture_statement(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        statements.append(statement)

    engine = db.get_bind()
    sa.event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with patch.object(
            graph_client, "is_configured", return_value=True
        ), patch.object(episode_module, "dispatch", return_value=1):
            summary = episode_outbox.drain(db)
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture_statement)

    assert summary["sent"] == 1
    select_statements = [
        statement.lower()
        for statement in statements
        if statement.lstrip().lower().startswith("select")
    ]
    assert len(select_statements) == 2
    assert sum("from graph_episode_outbox" in sql for sql in select_statements) == 1
    assert sum("from roles join organizations" in sql for sql in select_statements) == 1


def test_drain_skips_unknown_future_kind_without_consuming_batch(db):
    """An older worker must leave newer episode kinds for a newer deploy."""
    org, _role, app, _decision = _seed_advance(db)
    future_row = GraphEpisodeOutbox(
        organization_id=int(org.id),
        episode_kind="future_episode_kind",
        dedup_key=f"future-episode-{int(app.id)}",
        payload={"introduced_by": "newer_worker"},
        status=OUTBOX_STATUS_PENDING,
        attempts=0,
    )
    db.add(future_row)
    db.flush()
    outcome_learning.record_advance_outcome_on_stage(
        db, application=app, new_stage="advanced"
    )
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as dispatch:
        summary = episode_outbox.drain(db, batch_size=1)

    assert summary["scanned"] == 1
    assert summary["sent"] == 1
    dispatch.assert_called_once()

    db.refresh(future_row)
    assert future_row.status == OUTBOX_STATUS_PENDING
    assert future_row.attempts == 0
    assert future_row.last_error is None


def test_drain_sends_recruiter_action_with_user_attribution(db):
    org, role, _app, decision = _seed_advance(db)
    episode_outbox.enqueue_recruiter_action(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        decision_id=int(decision.id),
        recruiter_id=23,
        action="approve",
        reason="Strong evidence",
        happened_at=decision.resolved_at,
    )
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 1
    episodes = list(dispatch.call_args.args[0])
    assert episodes[0].name == f"recruiter-action-approve-{int(decision.id)}"
    assert dispatch.call_args.kwargs["bill_organization_id"] == int(org.id)
    assert dispatch.call_args.kwargs["bill_role_id"] == int(role.id)
    assert dispatch.call_args.kwargs["bill_user_id"] == 23


def test_drain_preserves_system_recruiter_sentinel_without_user_billing(db):
    org, role, _app, decision = _seed_advance(db, label="system-actor")
    episode_outbox.enqueue_recruiter_action(
        db,
        organization_id=int(org.id),
        role_id=int(role.id),
        decision_id=int(decision.id),
        recruiter_id=0,
        action="approve",
        reason=None,
        happened_at=decision.resolved_at,
    )
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 1
    assert dispatch.call_args.kwargs["bill_user_id"] is None
    assert "Recruiter id=0" in list(dispatch.call_args.args[0])[0].body


def test_drain_defers_paused_role_without_attempt_or_provider_call(db):
    _, role, _, _ = _enqueue_pending(db)
    role.agent_paused_at = datetime.now(timezone.utc)
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    row = db.query(GraphEpisodeOutbox).one()
    assert summary["sent"] == 0
    assert summary["scanned"] == 0
    assert summary["deferred"] == 0
    assert summary["role_deferred"] == 0
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0
    dispatch.assert_not_called()

    role.agent_paused_at = None
    db.commit()
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as resumed_dispatch:
        resumed = episode_outbox.drain(db)

    assert resumed["sent"] == 1
    resumed_dispatch.assert_called_once()


def test_drain_defers_workspace_paused_role_without_provider_call(db):
    org, _role, _, _ = _enqueue_pending(db)
    org.agent_workspace_paused_at = datetime.now(timezone.utc)
    org.agent_workspace_paused_reason = "workspace paused by recruiter"
    org.agent_workspace_control_version = 2
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    row = db.query(GraphEpisodeOutbox).one()
    assert summary["sent"] == 0
    assert summary["scanned"] == 0
    assert summary["role_deferred"] == 0
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0
    dispatch.assert_not_called()


def test_drain_defers_turned_off_role_without_attempt_or_provider_call(db):
    _, role, _, _ = _enqueue_pending(db)
    role.agentic_mode_enabled = False
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    row = db.query(GraphEpisodeOutbox).one()
    assert summary["scanned"] == 0
    assert summary["role_deferred"] == 0
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "held_state",
    ("role_paused", "role_off", "workspace_paused"),
)
def test_drain_prioritizes_healthy_org_over_held_row_with_same_timestamp(
    db, held_state
):
    """A held row cannot consume the batch ahead of another org's work."""
    held_org, held_role, _, _ = _enqueue_pending(
        db, label=f"held-{held_state}"
    )
    healthy_org, _, _, _ = _enqueue_pending(db, label=f"healthy-{held_state}")
    held_row = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.organization_id == int(held_org.id))
        .one()
    )
    healthy_row = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.organization_id == int(healthy_org.id))
        .one()
    )
    same_time = datetime.now(timezone.utc) - timedelta(hours=2)
    held_row.updated_at = same_time
    healthy_row.updated_at = same_time
    if held_state == "role_paused":
        held_role.agent_paused_at = datetime.now(timezone.utc)
    elif held_state == "role_off":
        held_role.agentic_mode_enabled = False
    else:
        held_org.agent_workspace_paused_at = datetime.now(timezone.utc)
        held_org.agent_workspace_paused_reason = "recruiter hold"
        held_org.agent_workspace_control_version = 1
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as dispatch:
        summary = episode_outbox.drain(db, batch_size=1)

    assert summary["sent"] == 1
    assert dispatch.call_args.kwargs["bill_organization_id"] == int(healthy_org.id)
    db.refresh(held_row)
    db.refresh(healthy_row)
    assert held_row.status == OUTBOX_STATUS_PENDING
    assert held_row.attempts == 0
    assert healthy_row.status == OUTBOX_STATUS_SENT


def test_drain_prioritizes_due_retry_over_older_cooldown_row(db):
    """A cooling row cannot consume the batch ahead of a due retry."""
    old_org, _, _, _ = _enqueue_pending(db, label="cooldown-old")
    due_org, _, _, _ = _enqueue_pending(db, label="cooldown-due")
    old_row = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.organization_id == int(old_org.id))
        .one()
    )
    due_row = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.organization_id == int(due_org.id))
        .one()
    )
    fixed_now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    old_row.attempts = 5
    old_row.updated_at = fixed_now - timedelta(minutes=30)
    due_row.attempts = 1
    due_row.updated_at = fixed_now - timedelta(minutes=10)
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_outbox, "_now", return_value=fixed_now
    ), patch.object(episode_module, "dispatch", return_value=1) as dispatch:
        summary = episode_outbox.drain(db, batch_size=1)

    assert summary["sent"] == 1
    assert dispatch.call_args.kwargs["bill_organization_id"] == int(due_org.id)
    db.refresh(old_row)
    db.refresh(due_row)
    assert old_row.status == OUTBOX_STATUS_PENDING
    assert old_row.attempts == 5
    assert due_row.status == OUTBOX_STATUS_SENT


@pytest.mark.parametrize(
    ("attempts", "delay_minutes"),
    ((1, 5), (2, 10), (3, 20), (4, 40), (5, 60), (12, 60)),
)
def test_retry_buckets_match_sql_and_python_due_boundaries(
    db, attempts, delay_minutes
):
    _enqueue_pending(db, label=f"retry-boundary-{attempts}")
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    row = db.query(GraphEpisodeOutbox).one()
    row.attempts = attempts
    row.updated_at = now - timedelta(minutes=delay_minutes)
    db.commit()

    assert episode_outbox._retry_delay(attempts) == timedelta(
        minutes=delay_minutes
    )
    assert episode_outbox._retry_is_due(row, now=now) is True
    selected = episode_outbox_query.pending_outbox_query(
        db, now=now, batch_size=1
    ).all()
    assert [selected_row.id for selected_row, _payload_text in selected] == [row.id]

    row.updated_at = now - timedelta(minutes=delay_minutes) + timedelta(
        microseconds=1
    )
    db.commit()
    assert episode_outbox._retry_is_due(row, now=now) is False
    assert (
        episode_outbox_query.pending_outbox_query(
            db, now=now, batch_size=1
        ).all()
        == []
    )


def test_pending_query_uses_fair_postgres_lock_contract(db):
    sql = str(
        episode_outbox_query.pending_outbox_query(
            db,
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
            batch_size=17,
        ).statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "LEFT OUTER JOIN roles ON roles.id = graph_episode_outbox.role_id" in sql
    assert "roles.organization_id = graph_episode_outbox.organization_id" in sql
    assert "roles.deleted_at IS NULL" in sql
    assert "LEFT OUTER JOIN organizations" in sql
    assert "roles.id IS NULL" in sql
    assert "coalesce(graph_episode_outbox.attempts, 0)" in sql
    assert "graph_episode_outbox.updated_at ASC" in sql
    assert "graph_episode_outbox.id ASC" in sql
    assert "LIMIT 17" in sql
    assert "OFFSET" not in sql
    assert "FOR UPDATE OF graph_episode_outbox SKIP LOCKED" in sql
    where_clause = sql.partition("WHERE")[2].partition("ORDER BY")[0]
    assert "attempts" in where_clause
    assert "roles.id IS NULL" in where_clause
    assert "roles.agentic_mode_enabled IS true" in where_clause
    assert "roles.agent_paused_at IS NULL" in where_clause
    assert "organizations.agent_workspace_paused_at IS NULL" in where_clause

    nonpositive_sql = str(
        episode_outbox_query.pending_outbox_query(
            db,
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
            batch_size=-1,
        ).statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LIMIT 0" in nonpositive_sql


# ---------------------------------------------------------------------------
# Drain: a failing send leaves the row pending (not lost)
# ---------------------------------------------------------------------------


def test_failing_send_returns_zero_leaves_row_pending(db):
    _enqueue_pending(db)

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=0
    ):
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 0
    assert summary["pending"] == 1
    assert summary["failed"] == 0

    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 1
    assert row.last_error
    assert row.sent_at is None


def test_failing_send_raises_leaves_row_pending_with_error(db):
    _enqueue_pending(db)

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", side_effect=RuntimeError("neo4j unreachable")
    ):
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 0
    assert summary["pending"] == 1

    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 1
    assert "neo4j unreachable" in (row.last_error or "")


def test_transient_failure_remains_pending_beyond_old_cap_and_recovers(db):
    _enqueue_pending(db)
    # Push attempts to one below the former terminal cap and make the row due.
    row = db.query(GraphEpisodeOutbox).one()
    row.attempts = episode_outbox._MAX_ATTEMPTS - 1
    row.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=0
    ):
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 0
    assert summary["pending"] == 1
    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == episode_outbox._MAX_ATTEMPTS

    # While the cooldown is active, repeated beat/manual drains do not hammer
    # the provider or burn another attempt.
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as mock_dispatch:
        deferred = episode_outbox.drain(db)
    assert deferred["scanned"] == 0
    assert deferred["deferred"] == 0
    mock_dispatch.assert_not_called()

    # Once the bounded cooldown elapses, the same durable row is retried and
    # sent — no human requeue or status repair required.
    row.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ):
        recovered = episode_outbox.drain(db)
    assert recovered["sent"] == 1
    assert db.query(GraphEpisodeOutbox).one().status == OUTBOX_STATUS_SENT


def test_admission_or_metering_error_stays_retryable_with_reason(db):
    _enqueue_pending(db)

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module,
        "dispatch",
        side_effect=RuntimeError("usage settlement unavailable"),
    ):
        summary = episode_outbox.drain(db)

    assert summary["pending"] == 1
    assert summary["failed"] == 0
    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 1
    assert "usage settlement unavailable" in (row.last_error or "")


def test_invalid_episode_payload_is_terminal(db):
    _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload.pop("candidate_taali_id")
    row.payload = payload
    db.commit()

    mock_dispatch = MagicMock()
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", mock_dispatch
    ):
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_FAILED
    assert "invalid episode payload" in (row.last_error or "")
    mock_dispatch.assert_not_called()


@pytest.mark.parametrize(
    "raw_payload",
    ("9" * 5_000, "[" * 10_000 + "0" + "]" * 10_000),
    ids=("oversized_number", "excessive_nesting"),
)
def test_pathological_legacy_json_becomes_failed_without_crashing_drain(
    db, raw_payload
):
    _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    row_id = int(row.id)
    db.execute(
        sa.text(
            "UPDATE graph_episode_outbox SET payload = :payload WHERE id = :row_id"
        ),
        {"payload": raw_payload, "row_id": row_id},
    )
    db.commit()
    stored_before = (
        db.query(sa.cast(GraphEpisodeOutbox.payload, sa.Text))
        .filter(GraphEpisodeOutbox.id == row_id)
        .scalar()
    )

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    dispatch.assert_not_called()
    status, last_error = (
        db.query(GraphEpisodeOutbox.status, GraphEpisodeOutbox.last_error)
        .filter(GraphEpisodeOutbox.id == row_id)
        .one()
    )
    assert status == OUTBOX_STATUS_FAILED
    assert "invalid episode payload" in (last_error or "")
    stored_payload = (
        db.query(sa.cast(GraphEpisodeOutbox.payload, sa.Text))
        .filter(GraphEpisodeOutbox.id == row_id)
        .scalar()
    )
    assert stored_payload == stored_before


@pytest.mark.parametrize("shape", ("array_of_pairs", "nonfinite_number"))
def test_valid_json_with_invalid_payload_shape_fails_only_that_row(db, shape):
    bad_org, _bad_role, _bad_app, _bad_decision = _enqueue_pending(
        db, label=f"invalid-json-shape-{shape}"
    )
    healthy_org, _role, _app, _decision = _enqueue_pending(
        db, label=f"healthy-json-shape-{shape}"
    )
    row = (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.organization_id == int(bad_org.id))
        .one()
    )
    row_id = int(row.id)
    payload = dict(row.payload or {})
    if shape == "array_of_pairs":
        raw_payload = json.dumps(list(payload.items()))
    else:
        payload["candidate_taali_id"] = "__NONFINITE__"
        raw_payload = json.dumps(payload).replace('"__NONFINITE__"', "1e400")
    db.execute(
        sa.text(
            "UPDATE graph_episode_outbox SET payload = :payload WHERE id = :row_id"
        ),
        {"payload": raw_payload, "row_id": row_id},
    )
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    assert summary["sent"] == 1
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["bill_organization_id"] == int(healthy_org.id)
    status, last_error = (
        db.query(GraphEpisodeOutbox.status, GraphEpisodeOutbox.last_error)
        .filter(GraphEpisodeOutbox.id == row_id)
        .one()
    )
    assert status == OUTBOX_STATUS_FAILED
    assert "invalid episode payload" in (last_error or "")


@pytest.mark.parametrize("mismatch", ("organization_id", "role_id"))
def test_normalized_row_rejects_cross_tenant_payload_metadata(db, mismatch):
    own_org, own_role, _app, _decision = _enqueue_pending(
        db, label=f"payload-mismatch-{mismatch}"
    )
    other_org, other_role, _other_app, _other_decision = _seed_advance(
        db, label=f"payload-mismatch-other-{mismatch}"
    )
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload[mismatch] = (
        int(other_org.id) if mismatch == "organization_id" else int(other_role.id)
    )
    row.payload = payload
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    assert summary["sent"] == 0
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.organization_id == int(own_org.id)
    assert row.role_id == int(own_role.id)
    assert row.status == OUTBOX_STATUS_FAILED
    assert f"payload {mismatch} does not match outbox row" in (row.last_error or "")


@pytest.mark.parametrize("timestamp_value", (None, "not-a-timestamp"))
def test_missing_or_invalid_timestamp_is_terminal_not_rewritten_to_now(
    db, timestamp_value
):
    _enqueue_pending(db, label=f"invalid-time-{timestamp_value}")
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload["observed_at"] = timestamp_value
    row.payload = payload
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.status == OUTBOX_STATUS_FAILED
    assert "episode timestamp" in (row.last_error or "")


def test_legacy_outcome_payload_resolves_role_from_decision(db):
    org, role, app, decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload.pop("role_id")
    row.role_id = None
    row.payload = payload
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as mock_dispatch:
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 1
    assert mock_dispatch.call_args.kwargs["bill_role_id"] == int(role.id)
    db.refresh(row)
    assert row.role_id == int(role.id)


def test_legacy_payload_role_is_repaired_before_dispatch(db):
    _org, role, _app, _decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    row.role_id = None
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ):
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 1
    db.refresh(row)
    assert row.role_id == int(role.id)


def test_legacy_held_role_is_repaired_without_provider_call(db):
    _org, role, _app, _decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    row.role_id = None
    role.agent_paused_at = datetime.now(timezone.utc)
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["role_deferred"] == 1
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.role_id == int(role.id)
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0


@pytest.mark.parametrize("invalid_role", ("cross_org", "deleted"))
def test_normalized_invalid_role_fails_without_payload_fallback(db, invalid_role):
    own_org, _own_role, _app, _decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    if invalid_role == "cross_org":
        _other_org, role, _other_app, _other_decision = _seed_advance(
            db, label="invalid-owner"
        )
    else:
        role = db.get(Role, int(row.role_id))
        role.deleted_at = datetime.now(timezone.utc)
    row.role_id = int(role.id)
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.organization_id == int(own_org.id)
    assert row.status == OUTBOX_STATUS_FAILED
    assert row.attempts == 0
    if invalid_role == "cross_org":
        assert "payload role_id does not match outbox row" in (row.last_error or "")
    else:
        assert row.last_error == (
            "valid role attribution unavailable for graph billing"
        )


def test_explicit_malformed_legacy_role_never_falls_back_to_decision(db):
    _org, _role, _app, _decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload["role_id"] = "9" * 5_000
    row.role_id = None
    row.payload = payload
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.status == OUTBOX_STATUS_FAILED
    assert row.attempts == 0


@pytest.mark.parametrize("invalid_fallback", ("cross_org", "deleted_role"))
def test_invalid_legacy_decision_role_fails_closed(db, invalid_fallback):
    _org, role, _app, _decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload.pop("role_id")
    if invalid_fallback == "cross_org":
        _other_org, _other_role, _other_app, other_decision = _seed_advance(
            db, label="cross-org-decision"
        )
        payload["decision_id"] = int(other_decision.id)
    else:
        role.deleted_at = datetime.now(timezone.utc)
    row.role_id = None
    row.payload = payload
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch"
    ) as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.status == OUTBOX_STATUS_FAILED
    assert row.attempts == 0
    assert row.role_id is None


def test_fresh_authority_change_after_claim_prevents_dispatch(db):
    _org, role, _app, _decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    build_episode = episode_outbox._build_episode

    def build_then_pause(claimed_row, **kwargs):
        episode = build_episode(claimed_row, **kwargs)
        role.agent_paused_at = datetime.now(timezone.utc)
        db.flush()
        return episode

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_outbox, "_build_episode", side_effect=build_then_pause
    ), patch.object(episode_module, "dispatch") as dispatch:
        summary = episode_outbox.drain(db)

    assert summary["deferred"] == 1
    assert summary["role_deferred"] == 1
    dispatch.assert_not_called()
    db.refresh(row)
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0


def test_drain_is_noop_when_graph_unconfigured(db):
    _enqueue_pending(db)

    mock_dispatch = MagicMock()
    with patch.object(graph_client, "is_configured", return_value=False), patch.object(
        episode_module, "dispatch", mock_dispatch
    ):
        summary = episode_outbox.drain(db)

    assert summary["status"] == "unconfigured"
    assert not mock_dispatch.called

    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0


# ---------------------------------------------------------------------------
# Celery task wrapper
# ---------------------------------------------------------------------------


def test_drain_task_sends_pending_and_is_idempotent(db):
    # The task opens its own SessionLocal — it only sees committed rows.
    _enqueue_pending(db)

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as mock_dispatch:
        out1 = drain_graph_episode_outbox.run(batch_size=50)
        # Second run: the first marked the row sent (committed in the shared
        # in-memory DB), so a fresh task session finds nothing pending.
        out2 = drain_graph_episode_outbox.run(batch_size=50)

    assert out1["status"] == "ok"
    assert out1["sent"] == 1
    assert out2["scanned"] == 0
    assert out2["sent"] == 0
    assert mock_dispatch.call_count == 1


def test_drain_task_noop_when_unconfigured(db):
    _enqueue_pending(db)

    with patch.object(graph_client, "is_configured", return_value=False):
        out = drain_graph_episode_outbox.run(batch_size=50)

    assert out["status"] == "unconfigured"
