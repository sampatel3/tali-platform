"""Durability and no-replay boundaries for listener-driven graph ingestion."""

from __future__ import annotations

import ast
import inspect
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.candidate_graph import episodes as episode_module
from app.candidate_graph import (
    ingest_manifest,
    ingest_outbox,
    ingest_reconciliation,
    listeners,
)
from app.candidate_graph import sync as sync_module
from app.models.application_interview import ApplicationInterview
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.graph_ingest_dispatch import (
    GRAPH_INGEST_CLAIMED,
    GRAPH_INGEST_COMPLETE,
    GRAPH_INGEST_DISPATCHING,
    GRAPH_INGEST_PENDING,
    GRAPH_INGEST_PROVIDER_STARTED,
    GRAPH_INGEST_QUEUED,
    GRAPH_INGEST_RECONCILIATION,
    GRAPH_INGEST_SKIPPED,
    GraphIngestDispatch,
)
from app.models.organization import Organization
from app.models.role import Role
from app.models.graph_sync_state import GraphSyncState
from app.platform.config import settings
from app.tasks import graph_ingest_tasks


def _operation(
    *,
    kind: str = "candidate",
    entity_id: int = 7,
    status: str = GRAPH_INGEST_PENDING,
    dispatch_nonce: str | None = None,
    claimed_at: datetime | None = None,
    provider_started_at: datetime | None = None,
) -> GraphIngestDispatch:
    return GraphIngestDispatch(
        operation_id=str(uuid.uuid4()),
        work_kind=kind,
        entity_id=entity_id,
        source_refs=[{"kind": kind, "id": entity_id}],
        status=status,
        dispatch_nonce=dispatch_nonce,
        claimed_at=claimed_at,
        provider_attempt_started_at=provider_started_at,
    )


def _dispatch_operation(db, operation_id: str) -> dict:
    return ingest_outbox.dispatch_one(
        db,
        operation_id=operation_id,
        publishers_by_kind=graph_ingest_tasks._task_publishers_by_kind(),
    )


def _episode(
    *,
    body: str = "Subject candidate: Graph Candidate\nSkills: Python\tPostgreSQL",
):
    return episode_module.Episode(
        name="candidate-7-manifest",
        body=body,
        source_description="candidate.profile",
        reference_time=datetime.now(timezone.utc),
        group_id="org:1",
    )


def test_manifest_is_exact_idempotent_and_never_overwritten_on_payload_drift(db):
    nonce = str(uuid.uuid4())
    row = _operation(status=GRAPH_INGEST_CLAIMED)
    row.worker_attempt_nonce = nonce
    db.add(row)
    db.commit()
    claim = ingest_outbox.WorkerClaim(row.operation_id, nonce)
    episodes = [_episode()]

    assert ingest_outbox.record_operation_manifest(
        db,
        claim,
        work_kind="candidate",
        entity_id=7,
        episodes=episodes,
    ) is True
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    exact_manifest = persisted.operation_manifest
    exact_digest = persisted.operation_manifest_sha256
    assert ingest_outbox.record_operation_manifest(
        db,
        claim,
        work_kind="candidate",
        entity_id=7,
        episodes=episodes,
    ) is True

    with pytest.raises(ingest_outbox.OperationManifestConflict):
        ingest_outbox.record_operation_manifest(
            db,
            claim,
            work_kind="candidate",
            entity_id=7,
            episodes=[_episode(body="Subject candidate: changed payload")],
        )
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.operation_manifest == exact_manifest
    assert persisted.operation_manifest_sha256 == exact_digest


@pytest.mark.parametrize(
    "manifest,digest",
    (
        (None, None),
        ({"malformed": True}, "a" * 64),
        (
            {
                "version": 1,
                "work_kind": "candidate",
                "entity_id": 7,
                "episode_count": 0,
                "episodes": [],
            },
            ingest_manifest.manifest_sha256(
                {
                    "version": 1,
                    "work_kind": "candidate",
                    "entity_id": 7,
                    "episode_count": 0,
                    "episodes": [],
                }
            ),
        ),
    ),
)
def test_provider_marker_fences_missing_or_tampered_manifest_without_recovery(
    db,
    manifest,
    digest,
):
    nonce = str(uuid.uuid4())
    row = _operation(status=GRAPH_INGEST_CLAIMED)
    row.worker_attempt_nonce = nonce
    row.operation_manifest = manifest
    row.operation_manifest_sha256 = digest
    db.add(row)
    db.commit()
    claim = ingest_outbox.WorkerClaim(row.operation_id, nonce)

    assert ingest_outbox.mark_provider_attempt_started(db, claim) is False
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_RECONCILIATION
    assert persisted.provider_attempt_started_at is None
    assert row.operation_id not in ingest_outbox.recoverable_operation_ids(db)


@pytest.mark.parametrize(
    "bad_episode",
    (
        _episode(body="Subject candidate:\ncontrol\x00character"),
        _episode(body="x" * (ingest_manifest.MAX_EPISODE_PAYLOAD_BYTES + 1)),
    ),
)
def test_unrepresentable_manifest_is_terminal_support_not_a_poison_retry(
    db,
    bad_episode,
):
    nonce = str(uuid.uuid4())
    row = _operation(status=GRAPH_INGEST_CLAIMED)
    row.worker_attempt_nonce = nonce
    db.add(row)
    db.commit()
    claim = ingest_outbox.WorkerClaim(row.operation_id, nonce)

    with pytest.raises(ingest_outbox.OperationManifestConflict) as caught:
        ingest_outbox.record_operation_manifest(
            db,
            claim,
            work_kind="candidate",
            entity_id=7,
            episodes=[bad_episode],
        )
    assert ingest_outbox.finish_provider_attempt(
        db,
        claim,
        succeeded=False,
        error=caught.value,
    ) == "support_review_required"
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_RECONCILIATION
    assert persisted.provider_attempt_started_at is None
    assert row.operation_id not in ingest_outbox.recoverable_operation_ids(db)


def test_manifest_payload_byte_limit_handles_four_byte_unicode_exactly(db):
    exact_body = "😀" * (ingest_manifest.MAX_EPISODE_PAYLOAD_BYTES // 4)
    manifest, digest = ingest_manifest.build_operation_manifest(
        work_kind="candidate",
        entity_id=7,
        episodes=[_episode(body=exact_body)],
    )
    assert manifest["episode_count"] == 1
    assert len(digest) == 64

    with pytest.raises(ValueError, match="byte limit"):
        ingest_manifest.build_operation_manifest(
            work_kind="candidate",
            entity_id=7,
            episodes=[_episode(body=exact_body + "😀")],
        )


def test_outbox_has_no_task_layer_import():
    imports: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(ingest_outbox))):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            imports.update(f"{module}.{alias.name}" for alias in node.names)

    assert not any("tasks.graph_ingest_tasks" in name for name in imports)


def test_dispatch_task_injects_live_task_mapping():
    fake_db = MagicMock()
    expected = {"status": "queued", "operation_id": "operation-1"}
    with patch.object(
        graph_ingest_tasks, "SessionLocal", return_value=fake_db
    ), patch.object(ingest_outbox, "dispatch_one", return_value=expected) as dispatch:
        result = graph_ingest_tasks.dispatch_graph_ingest_outbox.run("operation-1")

    assert result == expected
    fake_db.close.assert_called_once_with()
    call = dispatch.call_args
    assert call.args == (fake_db,)
    assert call.kwargs["operation_id"] == "operation-1"
    publishers = call.kwargs["publishers_by_kind"]
    assert publishers == {
        "candidate": graph_ingest_tasks.sync_candidate_to_graph,
        "interview": graph_ingest_tasks.sync_interview_to_graph,
        "event": graph_ingest_tasks.sync_event_to_graph,
    }


def _candidate_with_application(db, *, stage: str, paused: bool = False):
    org = Organization(name="Graph outbox org", slug=f"graph-outbox-{uuid.uuid4().hex}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Graph outbox role",
        source="manual",
        agentic_mode_enabled=True,
        agent_paused_at=datetime.now(timezone.utc) if paused else None,
    )
    candidate = Candidate(
        organization_id=org.id,
        email=f"graph-{uuid.uuid4().hex}@example.test",
        full_name="Graph Candidate",
    )
    db.add_all([role, candidate])
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        pipeline_stage=stage,
        status="active",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.commit()
    return org, role, candidate, application


def test_source_transaction_rollback_removes_durable_intent(db):
    candidate = Candidate(
        organization_id=None,
        email=f"rollback-{uuid.uuid4().hex}@example.test",
        full_name="Rollback Candidate",
    )
    db.add(candidate)
    db.flush()
    listeners._defer_until_commit(
        candidate,
        "candidate",
        int(candidate.id),
        source_kind="candidate",
        source_entity_id=int(candidate.id),
    )
    listeners._persist_pending_outbox(db)
    db.flush()
    operation_id = next(iter(db.info[listeners._OUTBOX_IDS_KEY]))
    assert db.get(GraphIngestDispatch, operation_id) is not None

    db.rollback()

    assert db.get(GraphIngestDispatch, operation_id) is None
    listeners._discard_after_rollback(db)
    assert listeners._OUTBOX_IDS_KEY not in db.info


def test_application_refresh_is_committed_before_postcommit_broker_kick(db):
    org, role, candidate, application = _candidate_with_application(
        db, stage="in_assessment"
    )
    listeners._defer_until_commit(
        application,
        "candidate",
        int(candidate.id),
        source_kind="application",
        source_entity_id=int(application.id),
    )
    listeners._persist_pending_outbox(db)
    db.flush()
    operation_id = next(iter(db.info[listeners._OUTBOX_IDS_KEY]))
    db.commit()

    with patch.object(
        graph_ingest_tasks.dispatch_graph_ingest_outbox, "delay"
    ) as publish:
        listeners._dispatch_after_commit(db)

    publish.assert_called_once_with(operation_id)
    row = db.get(GraphIngestDispatch, operation_id)
    assert row is not None
    assert row.organization_id == org.id
    assert row.work_kind == "candidate"
    assert row.entity_id == candidate.id
    assert row.source_refs == [{"kind": "application", "id": application.id}]


def test_multiple_flushes_coalesce_one_paid_refresh_and_keep_all_sources(db):
    _org, _role, candidate, application = _candidate_with_application(
        db, stage="in_assessment"
    )
    listeners._defer_until_commit(
        candidate,
        "candidate",
        int(candidate.id),
        source_kind="candidate",
        source_entity_id=int(candidate.id),
    )
    listeners._persist_pending_outbox(db)
    db.flush()
    listeners._defer_until_commit(
        application,
        "candidate",
        int(candidate.id),
        source_kind="application",
        source_entity_id=int(application.id),
    )
    listeners._persist_pending_outbox(db)
    db.flush()

    rows = db.query(GraphIngestDispatch).all()
    assert len(rows) == 1
    assert rows[0].source_refs == [
        {"kind": "application", "id": application.id},
        {"kind": "candidate", "id": candidate.id},
    ]


def _install_real_session_hooks(db):
    event.listen(db, "after_flush_postexec", listeners._persist_pending_outbox)
    event.listen(db, "after_commit", listeners._dispatch_after_commit)
    event.listen(db, "after_soft_rollback", listeners._discard_rolled_back_work)


def _remove_real_session_hooks(db):
    event.remove(db, "after_flush_postexec", listeners._persist_pending_outbox)
    event.remove(db, "after_commit", listeners._dispatch_after_commit)
    event.remove(db, "after_soft_rollback", listeners._discard_rolled_back_work)


def test_nested_rollback_keeps_root_kick_and_discards_nested_kick(db):
    root_candidate = Candidate(email="root-savepoint@example.test", full_name="Root")
    nested_candidate = Candidate(
        email="nested-savepoint@example.test", full_name="Nested"
    )
    db.add_all([root_candidate, nested_candidate])
    db.commit()
    root_id = int(root_candidate.id)
    nested_id = int(nested_candidate.id)
    _install_real_session_hooks(db)
    try:
        with patch.object(
            graph_ingest_tasks.dispatch_graph_ingest_outbox, "delay"
        ) as publish:
            listeners._defer_until_commit(root_candidate, "candidate", root_id)
            root_candidate.full_name = "Root changed"
            db.flush()
            root_operation = next(iter(db.info[listeners._OUTBOX_IDS_KEY]))

            savepoint = db.begin_nested()
            listeners._defer_until_commit(
                nested_candidate, "candidate", nested_id
            )
            nested_candidate.full_name = "Nested rolled back"
            db.flush()
            assert len(db.info[listeners._OUTBOX_IDS_KEY]) == 2
            publish.assert_not_called()
            savepoint.rollback()

            assert set(db.info[listeners._OUTBOX_IDS_KEY]) == {root_operation}
            publish.assert_not_called()
            db.commit()

        publish.assert_called_once_with(root_operation)
        assert db.get(GraphIngestDispatch, root_operation) is not None
        assert (
            db.query(GraphIngestDispatch)
            .filter(GraphIngestDispatch.entity_id == nested_id)
            .count()
            == 0
        )
    finally:
        _remove_real_session_hooks(db)


def test_nested_commit_waits_for_outer_commit_then_publishes_both_ids(db):
    root_candidate = Candidate(email="root-commit@example.test", full_name="Root")
    nested_candidate = Candidate(
        email="nested-commit@example.test", full_name="Nested"
    )
    db.add_all([root_candidate, nested_candidate])
    db.commit()
    root_id = int(root_candidate.id)
    nested_id = int(nested_candidate.id)
    _install_real_session_hooks(db)
    try:
        with patch.object(
            graph_ingest_tasks.dispatch_graph_ingest_outbox, "delay"
        ) as publish:
            listeners._defer_until_commit(root_candidate, "candidate", root_id)
            root_candidate.full_name = "Root changed"
            db.flush()

            savepoint = db.begin_nested()
            listeners._defer_until_commit(
                nested_candidate, "candidate", nested_id
            )
            nested_candidate.full_name = "Nested committed"
            db.flush()
            committed_ids = set(db.info[listeners._OUTBOX_IDS_KEY])
            assert len(committed_ids) == 2
            savepoint.commit()
            # SQLAlchemy's nested after_commit fires while the root transaction
            # is live; the production callback must not publish here.
            publish.assert_not_called()
            db.commit()

        assert {call.args[0] for call in publish.call_args_list} == committed_ids
        assert publish.call_count == 2
    finally:
        _remove_real_session_hooks(db)


def test_broker_failure_keeps_row_pending_for_recovery(db):
    row = _operation()
    db.add(row)
    db.commit()

    with patch.object(
        graph_ingest_tasks.sync_candidate_to_graph,
        "delay",
        side_effect=ConnectionError("broker unavailable"),
    ):
        result = _dispatch_operation(db, row.operation_id)

    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert result["status"] == "retry"
    assert persisted.status == GRAPH_INGEST_PENDING
    assert persisted.next_attempt_at is not None
    assert persisted.last_error_code == "broker_publish:ConnectionError"
    assert "broker unavailable" not in persisted.last_error_code


def test_duplicate_dispatch_and_worker_claims_are_fenced(db):
    row = _operation()
    db.add(row)
    db.commit()

    with patch.object(graph_ingest_tasks.sync_candidate_to_graph, "delay") as publish:
        first = _dispatch_operation(db, row.operation_id)
        second = _dispatch_operation(db, row.operation_id)

    assert first["status"] == "queued"
    assert second["status"] == "already_handled"
    publish.assert_called_once()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    claim = ingest_outbox.claim_worker_attempt(
        db,
        operation_id=row.operation_id,
        dispatch_nonce=persisted.dispatch_nonce,
        work_kind="candidate",
        entity_id=7,
    )
    duplicate = ingest_outbox.claim_worker_attempt(
        db,
        operation_id=row.operation_id,
        dispatch_nonce=persisted.dispatch_nonce,
        work_kind="candidate",
        entity_id=7,
    )
    assert claim is not None
    assert duplicate is None
    assert db.get(GraphIngestDispatch, row.operation_id).status == GRAPH_INGEST_CLAIMED


def test_stale_preprovider_claim_is_recoverable(db):
    row = _operation(
        status=GRAPH_INGEST_CLAIMED,
        dispatch_nonce="old-dispatch",
        claimed_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    row.worker_attempt_nonce = "abandoned-worker"
    db.add(row)
    db.commit()

    assert row.operation_id in ingest_outbox.recoverable_operation_ids(db)
    with patch.object(graph_ingest_tasks.sync_candidate_to_graph, "delay") as publish:
        result = _dispatch_operation(db, row.operation_id)

    assert result["status"] == "queued"
    publish.assert_called_once()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_QUEUED
    assert persisted.dispatch_nonce != "old-dispatch"
    assert persisted.worker_attempt_nonce is None


def test_stale_postprovider_marker_requires_reconciliation_without_replay(db):
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    row = _operation(
        status=GRAPH_INGEST_PROVIDER_STARTED,
        dispatch_nonce="accepted",
        claimed_at=old,
        provider_started_at=old,
    )
    row.worker_attempt_nonce = "ambiguous-worker"
    db.add(row)
    db.commit()

    assert row.operation_id not in ingest_outbox.recoverable_operation_ids(db)
    assert ingest_outbox.reconcile_stale_provider_attempts(db) == 1
    assert row.operation_id not in ingest_outbox.recoverable_operation_ids(db)
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_RECONCILIATION
    assert persisted.last_error_code == "provider_attempt_worker_lost"
    with patch.object(graph_ingest_tasks.sync_candidate_to_graph, "delay") as publish:
        result = _dispatch_operation(db, row.operation_id)
    assert result["status"] == "already_handled"
    publish.assert_not_called()


def test_stale_provider_snapshot_cannot_fence_a_fresh_attempt_aba(db):
    old = datetime.now(timezone.utc) - timedelta(hours=5)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    row = _operation(
        status=GRAPH_INGEST_PROVIDER_STARTED,
        provider_started_at=old,
    )
    old_nonce = "old-ambiguous-worker"
    row.worker_attempt_nonce = old_nonce
    db.add(row)
    db.commit()

    # A fresh worker replaces both fencing coordinates after the stale sweep
    # selected its snapshot but before the compare-and-update executes.
    fresh_started_at = datetime.now(timezone.utc)
    row.worker_attempt_nonce = "fresh-worker"
    row.provider_attempt_started_at = fresh_started_at
    db.commit()

    assert ingest_outbox._reconcile_exact_stale_attempt(
        db,
        operation_id=row.operation_id,
        attempt_nonce=old_nonce,
        provider_started_at=old,
        cutoff=cutoff,
        completed_at=datetime.now(timezone.utc),
    ) == 0
    db.commit()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_PROVIDER_STARTED
    assert persisted.worker_attempt_nonce == "fresh-worker"
    assert persisted.provider_attempt_started_at == fresh_started_at.replace(
        tzinfo=None
    )


def test_durable_candidate_cost_gate_finishes_without_provider(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db, stage="applied"
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()

    with patch("app.candidate_graph.sync.sync_candidate") as provider:
        result = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert result["reason"] == "below_cost_gate"
    provider.assert_not_called()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_SKIPPED
    assert persisted.provider_attempt_started_at is None


def test_durable_candidate_role_pause_finishes_without_provider(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db, stage="in_assessment", paused=True
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_DISPATCHING,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()

    with patch("app.candidate_graph.sync.sync_candidate") as provider:
        result = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert result["reason"] == "role_not_running"
    provider.assert_not_called()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_SKIPPED
    assert persisted.provider_attempt_started_at is None


def test_one_durable_delivery_can_cross_provider_boundary_only_once(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db, stage="in_assessment"
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()

    episode = episode_module.Episode(
        name=f"candidate-{candidate.id}-profile",
        body="Subject candidate: Graph Candidate",
        source_description="candidate.profile",
        reference_time=datetime.now(timezone.utc),
        group_id=f"org:{candidate.organization_id}",
    )

    def _successful(_candidate, **kwargs):
        assert kwargs["operation_manifest_callback"]([episode]) is True
        assert kwargs["provider_attempt_callback"]() is True
        return 1

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.sync.sync_candidate", side_effect=_successful
    ) as provider:
        first = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()
        second = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert first["status"] == "ok"
    assert second["status"] == "fenced"
    provider.assert_called_once()
    db.expire_all()
    assert db.get(GraphIngestDispatch, row.operation_id).status == GRAPH_INGEST_COMPLETE


def test_failure_before_wrapped_provider_marker_reopens_exact_operation(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db, stage="in_assessment"
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.sync.sync_candidate",
        side_effect=RuntimeError("graph construction failed"),
    ):
        result = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert result["status"] == "retry"
    assert result["reason"] == "pre_provider_failure"
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_PENDING
    assert persisted.provider_attempt_started_at is None
    assert persisted.last_error_code == "pre_provider_failure:RuntimeError"


def test_missing_graphiti_runtime_never_false_completes_durable_operation(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db, stage="in_assessment"
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()
    one_episode = episode_module.Episode(
        name=f"candidate-{candidate.id}-dependency-regression",
        body="Subject candidate: Graph Candidate",
        source_description="candidate.profile",
        reference_time=datetime.now(timezone.utc),
        group_id=f"org:{candidate.organization_id}",
    )

    def _sync_with_missing_dependency(_candidate, **kwargs):
        return episode_module.dispatch(
            [one_episode],
            db=kwargs["db"],
            bill_organization_id=kwargs["bill_organization_id"],
            bill_role_id=kwargs["bill_role_id"],
            require_hard_admission=True,
            require_role_admission=kwargs["require_role_admission"],
            raise_on_error=kwargs["raise_on_error"],
            provider_attempt_callback=kwargs["provider_attempt_callback"],
            operation_manifest_callback=kwargs["operation_manifest_callback"],
        )

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch.object(
        episode_module,
        "_episode_text_source",
        side_effect=ImportError("graphiti_core missing"),
    ), patch(
        "app.candidate_graph.sync.sync_candidate",
        side_effect=_sync_with_missing_dependency,
    ):
        result = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()
        # Legacy explicit/backfill callers retain their established best-effort
        # behavior, but the durable worker above must fail closed.
        assert episode_module.dispatch([one_episode], raise_on_error=False) == 0

    assert result["status"] == "retry"
    assert result["reason"] == "pre_provider_failure"
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_PENDING
    assert persisted.status != GRAPH_INGEST_COMPLETE
    assert persisted.provider_attempt_started_at is None
    assert persisted.completed_at is None
    assert (
        persisted.last_error_code
        == "pre_provider_failure:GraphProviderRuntimeError"
    )


def test_failure_after_wrapped_provider_marker_is_never_blindly_replayed(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db, stage="in_assessment"
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()

    def _ambiguous(_candidate, **kwargs):
        episode = episode_module.Episode(
            name=f"candidate-{candidate.id}-ambiguous",
            body="Subject candidate: Graph Candidate",
            source_description="candidate.profile",
            reference_time=datetime.now(timezone.utc),
            group_id=f"org:{candidate.organization_id}",
        )
        assert kwargs["operation_manifest_callback"]([episode]) is True
        assert kwargs["provider_attempt_callback"]() is True
        # Multiple wrapped calls in one Graphiti operation share the marker.
        assert kwargs["provider_attempt_callback"]() is True
        raise TimeoutError("response lost after acceptance")

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.sync.sync_candidate", side_effect=_ambiguous
    ) as provider:
        first = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()
        second = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert first["status"] == "reconciliation_required"
    assert second["status"] == "fenced"
    provider.assert_called_once()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_RECONCILIATION
    assert persisted.provider_attempt_started_at is not None
    assert persisted.last_error_code == "provider_outcome_ambiguous:TimeoutError"


def _candidate_episode_set(candidate):
    profile = episode_module.build_candidate_profile_episodes(
        candidate,
        max_episodes=int(settings.GRAPHITI_MAX_EPISODES_PER_CANDIDATE),
    )
    cv_episode = episode_module.build_cv_text_episode(candidate)
    return profile + ([cv_episode] if cv_episode is not None else [])


def test_ordinary_unchanged_candidate_completes_as_a_zero_cost_no_provider_noop(db):
    _org, _role, candidate, _application = _candidate_with_application(
        db,
        stage="in_assessment",
    )
    episodes = _candidate_episode_set(candidate)
    db.add(
        GraphSyncState(
            candidate_id=int(candidate.id),
            content_hash=sync_module._episodes_content_hash(episodes),
            sync_version=1,
        )
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    db.add(row)
    db.commit()

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.episodes.dispatch"
    ) as provider_dispatch:
        result = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert result["status"] == "ok"
    provider_dispatch.assert_not_called()
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_COMPLETE
    assert persisted.provider_attempt_started_at is None
    assert persisted.operation_manifest["episode_count"] == 0


def test_owner_absence_retry_bypasses_unchanged_cost_skip_and_replays_exact_manifest(
    db,
):
    org, _role, candidate, _application = _candidate_with_application(
        db,
        stage="in_assessment",
    )
    episodes = _candidate_episode_set(candidate)
    manifest, digest = ingest_manifest.build_operation_manifest(
        work_kind="candidate",
        entity_id=int(candidate.id),
        episodes=episodes,
    )
    db.add(
        GraphSyncState(
            candidate_id=int(candidate.id),
            content_hash=sync_module._episodes_content_hash(episodes),
            sync_version=1,
        )
    )
    nonce = str(uuid.uuid4())
    row = _operation(
        entity_id=int(candidate.id),
        status=GRAPH_INGEST_RECONCILIATION,
        dispatch_nonce=nonce,
    )
    row.organization_id = int(org.id)
    row.operation_manifest = manifest
    row.operation_manifest_sha256 = digest
    row.worker_attempt_nonce = str(uuid.uuid4())
    row.provider_attempt_started_at = datetime.now(timezone.utc)
    row.completed_at = datetime.now(timezone.utc)
    row.reconciliation_history = [
        ingest_reconciliation._resolution_entry(
            row,
            action=ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
            actor_id=1,
        )
    ]
    row.status = GRAPH_INGEST_QUEUED
    row.worker_attempt_nonce = None
    row.provider_attempt_started_at = None
    row.completed_at = None
    db.add(row)
    db.commit()
    provider_calls = []

    def _dispatch_exact(rebuilt, **kwargs):
        rebuilt = list(rebuilt)
        provider_calls.append(rebuilt)
        assert kwargs["operation_manifest_callback"](rebuilt) is True
        assert kwargs["provider_attempt_callback"]() is True
        return len(rebuilt)

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.episodes.dispatch",
        side_effect=_dispatch_exact,
    ):
        result = graph_ingest_tasks.sync_candidate_to_graph.apply(
            args=[int(candidate.id)],
            kwargs={"operation_id": row.operation_id, "dispatch_nonce": nonce},
        ).get()

    assert result["status"] == "ok"
    assert len(provider_calls) == 1
    assert len(provider_calls[0]) == len(episodes) > 0
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_COMPLETE
    assert persisted.provider_attempt_started_at is not None
    assert persisted.operation_manifest_sha256 == digest


def test_partial_or_forged_replay_history_never_grants_exact_paid_replay(db):
    nonce = str(uuid.uuid4())
    row = _operation(
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=nonce,
    )
    manifest, digest = ingest_manifest.build_operation_manifest(
        work_kind="candidate",
        entity_id=int(row.entity_id),
        episodes=[_episode()],
    )
    row.operation_manifest = manifest
    row.operation_manifest_sha256 = digest
    row.reconciliation_history = [
        {
            "version": 2,
            "action": "retry_after_entire_operation_absent",
            "attestation": {
                "entire_exact_operation_present": False,
                "entire_exact_operation_absent": True,
            },
            "prior_state": {"operation_manifest_sha256": digest},
        }
    ]
    db.add(row)
    db.commit()

    claim = ingest_outbox.claim_worker_attempt(
        db,
        operation_id=row.operation_id,
        dispatch_nonce=nonce,
        work_kind="candidate",
        entity_id=int(row.entity_id),
    )

    assert claim is not None
    assert claim.replay_exact_payload is False


def test_valid_history_from_another_operation_cannot_authorize_same_manifest(db):
    org = Organization(name="Replay binding", slug=f"replay-{uuid.uuid4().hex}")
    db.add(org)
    db.flush()
    manifest, digest = ingest_manifest.build_operation_manifest(
        work_kind="candidate",
        entity_id=7,
        episodes=[_episode()],
    )
    source = _operation(status=GRAPH_INGEST_RECONCILIATION)
    source.organization_id = int(org.id)
    source.operation_manifest = manifest
    source.operation_manifest_sha256 = digest
    source.worker_attempt_nonce = str(uuid.uuid4())
    source.provider_attempt_started_at = datetime.now(timezone.utc)
    source.completed_at = datetime.now(timezone.utc)
    source_history = ingest_reconciliation._resolution_entry(
        source,
        action=ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
        actor_id=1,
    )

    dispatch_nonce = str(uuid.uuid4())
    target = _operation(
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=dispatch_nonce,
    )
    target.organization_id = int(org.id)
    target.operation_manifest = deepcopy(manifest)
    target.operation_manifest_sha256 = digest
    target.reconciliation_history = [deepcopy(source_history)]
    db.add(target)
    db.commit()

    claim = ingest_outbox.claim_worker_attempt(
        db,
        operation_id=target.operation_id,
        dispatch_nonce=dispatch_nonce,
        work_kind="candidate",
        entity_id=7,
    )

    assert claim is not None
    assert claim.replay_exact_payload is False


def test_empty_interview_and_bookkeeping_event_are_terminal_no_provider_operations(db):
    org, _role, _candidate, application = _candidate_with_application(
        db,
        stage="in_assessment",
    )
    interview = ApplicationInterview(
        organization_id=int(org.id),
        application_id=int(application.id),
        stage="screening",
        source="manual",
        transcript_text=None,
        summary=None,
    )
    event = CandidateApplicationEvent(
        organization_id=int(org.id),
        application_id=int(application.id),
        event_type="pipeline_initialized",
        actor_type="system",
    )
    db.add_all([interview, event])
    db.flush()
    interview_nonce = str(uuid.uuid4())
    event_nonce = str(uuid.uuid4())
    interview_operation = _operation(
        kind="interview",
        entity_id=int(interview.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=interview_nonce,
    )
    event_operation = _operation(
        kind="event",
        entity_id=int(event.id),
        status=GRAPH_INGEST_QUEUED,
        dispatch_nonce=event_nonce,
    )
    db.add_all([interview_operation, event_operation])
    db.commit()

    with patch("app.candidate_graph.client.is_configured", return_value=True), patch(
        "app.candidate_graph.client.get_graphiti"
    ) as provider:
        interview_result = graph_ingest_tasks.sync_interview_to_graph.apply(
            args=[int(interview.id)],
            kwargs={
                "operation_id": interview_operation.operation_id,
                "dispatch_nonce": interview_nonce,
            },
        ).get()
        event_result = graph_ingest_tasks.sync_event_to_graph.apply(
            args=[int(event.id)],
            kwargs={
                "operation_id": event_operation.operation_id,
                "dispatch_nonce": event_nonce,
            },
        ).get()

    assert interview_result["status"] == "ok"
    assert event_result["status"] == "ok"
    provider.assert_not_called()
    db.expire_all()
    for operation in (interview_operation, event_operation):
        persisted = db.get(GraphIngestDispatch, operation.operation_id)
        assert persisted.status == GRAPH_INGEST_COMPLETE
        assert persisted.provider_attempt_started_at is None
        assert persisted.operation_manifest["episode_count"] == 0
