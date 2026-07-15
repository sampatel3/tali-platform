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

from datetime import datetime, timedelta, timezone
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
    role = Role(
        organization_id=org.id,
        name="Backend",
        source="manual",
        agentic_mode_enabled=True,
    )
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
    assert summary["deferred"] == 1
    assert summary["role_deferred"] == 1
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
    assert summary["role_deferred"] == 1
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
    assert summary["role_deferred"] == 1
    assert row.status == OUTBOX_STATUS_PENDING
    assert row.attempts == 0
    dispatch.assert_not_called()


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
    assert deferred["deferred"] == 1
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


def test_invalid_payload_is_the_only_terminal_failure(db):
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


def test_legacy_outcome_payload_resolves_role_from_decision(db):
    org, role, app, decision = _enqueue_pending(db)
    row = db.query(GraphEpisodeOutbox).one()
    payload = dict(row.payload or {})
    payload.pop("role_id")
    row.payload = payload
    db.commit()

    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "dispatch", return_value=1
    ) as mock_dispatch:
        summary = episode_outbox.drain(db)

    assert summary["sent"] == 1
    assert mock_dispatch.call_args.kwargs["bill_role_id"] == int(role.id)


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
