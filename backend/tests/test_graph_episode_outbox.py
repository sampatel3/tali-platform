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
- The retry budget is capped: a row that keeps failing is eventually
  marked ``failed`` rather than retried forever.
- The drain is a no-op while Graphiti is unconfigured (rows untouched).
- The Celery drain task ships pending rows and is idempotent across runs.

Mirrors the existing graph-test pattern: mock the graph client / dispatch
rather than standing up Neo4j.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.agent_runtime import outcome_learning
from app.candidate_graph import client as graph_client
from app.candidate_graph import episode_outbox
from app.candidate_graph import episodes as episode_module
from app.models.agent_decision import AgentDecision
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.graph_episode_outbox import (
    EPISODE_KIND_HIRING_OUTCOME,
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


def _seed_advance(db):
    """Org + role + application + an already-approved advance decision."""
    org = Organization(name="Outbox Org", slug=f"outbox-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=org.id, name="Backend", source="manual")
    db.add(role)
    db.flush()
    cand = Candidate(
        organization_id=org.id, email=f"c-{id(db)}@x.test", full_name="Outcome Cand"
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


def _enqueue_pending(db):
    """Record an advance outcome → one pending outbox row. Commits."""
    org, role, app, decision = _seed_advance(db)
    outcome_learning.record_advance_outcome_on_stage(
        db, application=app, new_stage="advanced"
    )
    db.commit()
    return org, role, app, decision


# ---------------------------------------------------------------------------
# Enqueue: lands even when the graph is dead
# ---------------------------------------------------------------------------


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
    assert row.payload["decision_id"] == int(decision.id)
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


# ---------------------------------------------------------------------------
# Drain: sends pending rows, idempotent
# ---------------------------------------------------------------------------


def test_drain_sends_pending_and_is_idempotent(db):
    _enqueue_pending(db)
    assert (
        db.query(GraphEpisodeOutbox)
        .filter(GraphEpisodeOutbox.status == OUTBOX_STATUS_PENDING)
        .count()
        == 1
    )

    dispatched = []

    def fake_dispatch(eps):
        eps = list(eps)
        dispatched.append(eps)
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


def test_retry_budget_capped_marks_failed(db):
    _enqueue_pending(db)
    # Push attempts to one below the cap so the next failure trips it.
    row = db.query(GraphEpisodeOutbox).one()
    row.attempts = episode_outbox._MAX_ATTEMPTS - 1
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=0
    ):
        summary = episode_outbox.drain(db)

    assert summary["failed"] == 1
    row = db.query(GraphEpisodeOutbox).one()
    assert row.status == OUTBOX_STATUS_FAILED
    assert row.attempts == episode_outbox._MAX_ATTEMPTS


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
