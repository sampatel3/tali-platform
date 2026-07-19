"""Owner visibility and no-blind-replay graph reconciliation contracts."""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.candidate_graph import (
    episodes as episode_module,
    ingest_manifest,
    ingest_outbox,
    ingest_reconciliation,
)
from app.models.graph_ingest_dispatch import (
    GRAPH_INGEST_COMPLETE,
    GRAPH_INGEST_PENDING,
    GRAPH_INGEST_RECONCILIATION,
    GraphIngestDispatch,
)
from app.models.organization import Organization
from app.models.user import User
from tests.conftest import auth_headers


def _row(
    organization_id: int,
    *,
    status: str = GRAPH_INGEST_RECONCILIATION,
    source_refs=None,
    history=None,
) -> GraphIngestDispatch:
    now = datetime.now(timezone.utc)
    manifest, manifest_sha256 = ingest_manifest.build_operation_manifest(
        work_kind="candidate",
        entity_id=91,
        episodes=[
            episode_module.Episode(
                name="candidate-91-profile",
                body="Subject candidate: Reconciliation Candidate (taali_id=91)",
                source_description="candidate.profile",
                reference_time=now - timedelta(minutes=6),
                group_id=f"org:{int(organization_id)}",
            )
        ],
    )
    return GraphIngestDispatch(
        operation_id=str(uuid.uuid4()),
        organization_id=int(organization_id),
        work_kind="candidate",
        entity_id=91,
        source_refs=(
            [{"kind": "candidate", "id": 91}] if source_refs is None else source_refs
        ),
        status=status,
        dispatch_attempts=3,
        dispatch_nonce=str(uuid.uuid4()),
        worker_attempt_nonce=str(uuid.uuid4()),
        dispatched_at=now - timedelta(minutes=8),
        claimed_at=now - timedelta(minutes=7),
        provider_attempt_started_at=now - timedelta(minutes=6),
        completed_at=now - timedelta(minutes=1),
        last_error_code="provider_outcome_ambiguous:TimeoutError",
        operation_manifest=manifest,
        operation_manifest_sha256=manifest_sha256,
        reconciliation_history=history,
    )


def _url(row: GraphIngestDispatch) -> str:
    return f"/api/v1/background-jobs/graph-ingest-reconciliations/{row.operation_id}"


def _payload(
    row: GraphIngestDispatch,
    *,
    action: str,
    present: bool = False,
    absent: bool = False,
) -> dict:
    return {
        "action": action,
        "expected_attempt_nonce": str(row.worker_attempt_nonce),
        "entire_operation_present_attested": present,
        "entire_operation_absent_attested": absent,
    }


def test_inventory_is_owner_only_org_scoped_and_secret_free(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-owner@example.com",
        organization_name="Graph Reconciliation Owner",
    )
    owner = db.query(User).filter(User.email == email).one()
    other_org = Organization(name="Hidden Graph Reconciliation Org")
    db.add(other_org)
    db.flush()
    own = _row(int(owner.organization_id))
    hidden = _row(int(other_org.id))
    non_reconcilable = _row(int(owner.organization_id), status=GRAPH_INGEST_PENDING)
    db.add_all([own, hidden, non_reconcilable])
    db.commit()

    response = client.get(
        "/api/v1/background-jobs/graph-ingest-reconciliations",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert [item["operation_id"] for item in response.json()["operations"]] == [
        own.operation_id
    ]
    evidence = response.json()["operations"][0]
    assert evidence["expected_attempt_nonce"] == own.worker_attempt_nonce
    assert evidence["source_refs"] == [{"kind": "candidate", "id": 91}]
    assert evidence["attempt_fence_available"] is True
    assert evidence["operation_manifest_state"] == "available"
    assert evidence["operation_manifest_sha256"] == own.operation_manifest_sha256
    assert evidence["operation_episode_count"] == 1
    assert evidence["operation_episodes"] == own.operation_manifest["episodes"]
    assert own.dispatch_nonce not in response.text
    assert hidden.operation_id not in response.text
    assert non_reconcilable.operation_id not in response.text

    detail = client.get(_url(own), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["operation_id"] == own.operation_id
    hidden_detail = client.get(_url(hidden), headers=headers)
    assert hidden_detail.status_code == 404
    assert hidden.operation_id not in hidden_detail.text

    owner.role = "member"
    db.commit()
    forbidden = client.get(
        "/api/v1/background-jobs/graph-ingest-reconciliations",
        headers=headers,
    )
    assert forbidden.status_code == 403


def test_confirm_entire_operation_present_preserves_exact_prior_evidence(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-confirm@example.com",
        organization_name="Graph Reconciliation Confirm",
    )
    owner = db.query(User).filter(User.email == email).one()
    row = _row(int(owner.organization_id))
    prior_nonce = str(row.worker_attempt_nonce)
    prior_dispatch_nonce = str(row.dispatch_nonce)
    prior_error = str(row.last_error_code)
    prior_sources = list(row.source_refs)
    db.add(row)
    db.commit()

    partial = client.post(
        f"{_url(row)}/resolve",
        headers=headers,
        json=_payload(
            row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
        ),
    )
    assert partial.status_code == 422
    assert "Partial or uncertain" in partial.text
    db.expire_all()
    assert (
        db.get(GraphIngestDispatch, row.operation_id).status
        == GRAPH_INGEST_RECONCILIATION
    )

    response = client.post(
        f"{_url(row)}/resolve",
        headers=headers,
        json=_payload(
            row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            present=True,
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == GRAPH_INGEST_COMPLETE
    assert response.json()["dispatch_status"] == "not_requested"

    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_COMPLETE
    assert persisted.source_refs == prior_sources
    assert persisted.dispatch_attempts == 3
    assert persisted.worker_attempt_nonce == prior_nonce
    assert len(persisted.reconciliation_history) == 1
    resolution = persisted.reconciliation_history[0]
    assert resolution["version"] == 2
    assert resolution["actor_id"] == owner.id
    assert resolution["attestation"] == {
        "entire_exact_operation_present": True,
        "entire_exact_operation_absent": False,
    }
    assert resolution["prior_state"]["worker_attempt_nonce"] == prior_nonce
    assert resolution["prior_state"]["dispatch_nonce"] == prior_dispatch_nonce
    assert resolution["prior_state"]["last_error_code"] == prior_error
    assert resolution["prior_state"]["operation_manifest_sha256"] == (
        persisted.operation_manifest_sha256
    )

    stale = client.post(
        f"{_url(row)}/resolve",
        headers=headers,
        json={
            "action": ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            "expected_attempt_nonce": prior_nonce,
            "entire_operation_present_attested": True,
            "entire_operation_absent_attested": False,
        },
    )
    assert stale.status_code == 409
    db.expire_all()
    assert (
        len(db.get(GraphIngestDispatch, row.operation_id).reconciliation_history) == 1
    )


def test_absence_attestation_reenters_dispatcher_and_fences_old_worker(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-retry@example.com",
        organization_name="Graph Reconciliation Retry",
    )
    owner = db.query(User).filter(User.email == email).one()
    row = _row(int(owner.organization_id))
    prior_nonce = str(row.worker_attempt_nonce)
    prior_provider_started_at = row.provider_attempt_started_at
    db.add(row)
    db.commit()

    with (
        patch(
            "app.tasks.graph_ingest_tasks.dispatch_graph_ingest_outbox.delay"
        ) as dispatch,
        patch(
            "app.tasks.graph_ingest_tasks.sync_candidate_to_graph.delay"
        ) as direct_provider,
    ):
        response = client.post(
            f"{_url(row)}/resolve",
            headers=headers,
            json=_payload(
                row,
                action=ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
                absent=True,
            ),
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == GRAPH_INGEST_PENDING
    assert response.json()["dispatch_status"] == "queued"
    dispatch.assert_called_once_with(row.operation_id)
    direct_provider.assert_not_called()

    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_PENDING
    assert persisted.dispatch_nonce is None
    assert persisted.worker_attempt_nonce is None
    assert persisted.provider_attempt_started_at is None
    assert persisted.next_attempt_at is not None
    assert persisted.source_refs == [{"kind": "candidate", "id": 91}]
    assert len(persisted.reconciliation_history) == 1
    prior = persisted.reconciliation_history[0]["prior_state"]
    assert prior["worker_attempt_nonce"] == prior_nonce
    assert datetime.fromisoformat(prior["provider_attempt_started_at"]) == (
        prior_provider_started_at.replace(tzinfo=None)
    )

    # A worker from the ambiguous attempt cannot complete the reopened row.
    stale_claim = ingest_outbox.WorkerClaim(row.operation_id, prior_nonce)
    assert (
        ingest_outbox.finish_provider_attempt(db, stale_claim, succeeded=True)
        == "fenced"
    )
    db.expire_all()
    assert db.get(GraphIngestDispatch, row.operation_id).status == GRAPH_INGEST_PENDING


def test_stale_or_cross_org_reconciliation_never_mutates_or_dispatches(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-stale@example.com",
        organization_name="Graph Reconciliation Stale",
    )
    owner = db.query(User).filter(User.email == email).one()
    other_org = Organization(name="Other Graph Reconciliation Workspace")
    db.add(other_org)
    db.flush()
    own = _row(int(owner.organization_id))
    hidden = _row(int(other_org.id))
    db.add_all([own, hidden])
    db.commit()

    stale_payload = _payload(
        own,
        action=ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
        absent=True,
    )
    stale_payload["expected_attempt_nonce"] = str(uuid.uuid4())
    with patch(
        "app.tasks.graph_ingest_tasks.dispatch_graph_ingest_outbox.delay"
    ) as dispatch:
        stale = client.post(f"{_url(own)}/resolve", headers=headers, json=stale_payload)
        hidden_response = client.post(
            f"{_url(hidden)}/resolve",
            headers=headers,
            json=_payload(
                hidden,
                action=ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
                absent=True,
            ),
        )

    assert stale.status_code == 409
    assert hidden_response.status_code == 404
    assert hidden.operation_id not in hidden_response.text
    dispatch.assert_not_called()
    db.expire_all()
    assert (
        db.get(GraphIngestDispatch, own.operation_id).status
        == GRAPH_INGEST_RECONCILIATION
    )
    assert db.get(GraphIngestDispatch, own.operation_id).reconciliation_history is None
    assert (
        db.get(GraphIngestDispatch, hidden.operation_id).status
        == GRAPH_INGEST_RECONCILIATION
    )


def test_missing_provider_start_evidence_cannot_be_attested(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-unfenced@example.com",
        organization_name="Graph Reconciliation Unfenced",
    )
    owner = db.query(User).filter(User.email == email).one()
    row = _row(int(owner.organization_id))
    row.provider_attempt_started_at = None
    db.add(row)
    db.commit()

    detail = client.get(_url(row), headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["attempt_fence_available"] is False
    assert detail.json()["expected_attempt_nonce"] is None

    response = client.post(
        f"{_url(row)}/resolve",
        headers=headers,
        json=_payload(
            row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            present=True,
        ),
    )
    assert response.status_code == 409
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_RECONCILIATION
    assert persisted.reconciliation_history is None


def test_broker_failure_retains_attestation_for_recovery_sweep(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-broker@example.com",
        organization_name="Graph Reconciliation Broker",
    )
    owner = db.query(User).filter(User.email == email).one()
    row = _row(int(owner.organization_id))
    db.add(row)
    db.commit()

    with patch(
        "app.tasks.graph_ingest_tasks.dispatch_graph_ingest_outbox.delay",
        side_effect=ConnectionError("redis://private-host"),
    ):
        response = client.post(
            f"{_url(row)}/resolve",
            headers=headers,
            json=_payload(
                row,
                action=ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT,
                absent=True,
            ),
        )

    assert response.status_code == 200, response.text
    assert response.json()["dispatch_status"] == "deferred_to_recovery_sweep"
    assert "private-host" not in response.text
    db.expire_all()
    persisted = db.get(GraphIngestDispatch, row.operation_id)
    assert persisted.status == GRAPH_INGEST_PENDING
    assert len(persisted.reconciliation_history) == 1


def test_unsafe_legacy_evidence_is_not_exposed_or_overwritten(client, db):
    headers, email = auth_headers(
        client,
        email="graph-reconciliation-unsafe@example.com",
        organization_name="Graph Reconciliation Unsafe",
    )
    owner = db.query(User).filter(User.email == email).one()
    source_row = _row(
        int(owner.organization_id),
        source_refs=[{"kind": "candidate", "id": 91, "token": "private-source"}],
    )
    history_row = _row(
        int(owner.organization_id),
        history=[{"authorization": "Bearer private-history"}],
    )
    db.add_all([source_row, history_row])
    db.commit()

    listed = client.get(
        "/api/v1/background-jobs/graph-ingest-reconciliations",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    evidence_by_id = {
        item["operation_id"]: item for item in listed.json()["operations"]
    }
    source_evidence = evidence_by_id[source_row.operation_id]
    assert source_evidence["source_refs"] == []
    assert source_evidence["source_evidence_state"] == "support_review_required"
    history_evidence = evidence_by_id[history_row.operation_id]
    assert history_evidence["reconciliation_history_state"] == "support_review_required"
    assert "private-source" not in listed.text
    assert "private-history" not in listed.text

    response = client.post(
        f"{_url(source_row)}/resolve",
        headers=headers,
        json=_payload(
            source_row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            present=True,
        ),
    )
    assert response.status_code == 409
    assert "private" not in response.text
    history_response = client.post(
        f"{_url(history_row)}/resolve",
        headers=headers,
        json=_payload(
            history_row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            present=True,
        ),
    )
    assert history_response.status_code == 409
    assert "private" not in history_response.text
    db.expire_all()
    persisted_source = db.get(GraphIngestDispatch, source_row.operation_id)
    assert persisted_source.status == GRAPH_INGEST_RECONCILIATION
    assert persisted_source.reconciliation_history is None
    persisted_history = db.get(GraphIngestDispatch, history_row.operation_id)
    assert persisted_history.status == GRAPH_INGEST_RECONCILIATION
    assert persisted_history.reconciliation_history == [
        {"authorization": "Bearer private-history"}
    ]


def test_reconciliation_service_has_no_task_layer_import():
    imports: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(ingest_reconciliation))):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            imports.update(f"{module}.{alias.name}" for alias in node.names)

    assert not any("tasks" in name for name in imports)


@pytest.mark.parametrize(
    "action,present,absent",
    (
        (ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT, True, False),
        (ingest_reconciliation.RETRY_AFTER_ENTIRE_OPERATION_ABSENT, False, True),
    ),
)
def test_legacy_or_malformed_manifest_blocks_both_owner_actions(
    client,
    db,
    action,
    present,
    absent,
):
    headers, email = auth_headers(
        client,
        email=f"graph-manifest-{action}@example.com",
        organization_name=f"Graph manifest {action}",
    )
    owner = db.query(User).filter(User.email == email).one()
    legacy = _row(int(owner.organization_id))
    legacy.operation_manifest = None
    legacy.operation_manifest_sha256 = None
    malformed = _row(int(owner.organization_id))
    malformed.operation_manifest = {"malformed": True}
    malformed.operation_manifest_sha256 = "a" * 64
    db.add_all([legacy, malformed])
    db.commit()

    evidence = {
        item["operation_id"]: item
        for item in client.get(
            "/api/v1/background-jobs/graph-ingest-reconciliations",
            headers=headers,
        ).json()["operations"]
    }
    assert evidence[legacy.operation_id]["operation_manifest_state"] == (
        "legacy_unavailable"
    )
    assert evidence[malformed.operation_id]["operation_manifest_state"] == (
        "support_review_required"
    )
    assert evidence[legacy.operation_id]["attempt_fence_available"] is False
    assert evidence[malformed.operation_id]["attempt_fence_available"] is False

    for row in (legacy, malformed):
        response = client.post(
            f"{_url(row)}/resolve",
            headers=headers,
            json=_payload(
                row,
                action=action,
                present=present,
                absent=absent,
            ),
        )
        assert response.status_code == 409
    db.expire_all()
    for row in (legacy, malformed):
        persisted = db.get(GraphIngestDispatch, row.operation_id)
        assert persisted.status == GRAPH_INGEST_RECONCILIATION
        assert persisted.reconciliation_history is None


def test_zero_episode_manifest_cannot_authorize_an_ambiguous_provider_action(
    client,
    db,
):
    headers, email = auth_headers(
        client,
        email="graph-zero-manifest@example.com",
        organization_name="Graph zero manifest",
    )
    owner = db.query(User).filter(User.email == email).one()
    row = _row(int(owner.organization_id))
    row.operation_manifest, row.operation_manifest_sha256 = (
        ingest_manifest.build_operation_manifest(
            work_kind="candidate",
            entity_id=91,
            episodes=[],
        )
    )
    db.add(row)
    db.commit()

    detail = client.get(_url(row), headers=headers)
    assert detail.status_code == 200
    assert detail.json()["operation_manifest_state"] == "support_review_required"
    assert detail.json()["attempt_fence_available"] is False
    response = client.post(
        f"{_url(row)}/resolve",
        headers=headers,
        json=_payload(
            row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            present=True,
        ),
    )
    assert response.status_code == 409


def test_cursor_survives_resolution_between_pages_and_offset_remains_compatible(
    client,
    db,
):
    headers, email = auth_headers(
        client,
        email="graph-cursor@example.com",
        organization_name="Graph cursor",
    )
    owner = db.query(User).filter(User.email == email).one()
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = [_row(int(owner.organization_id)) for _ in range(3)]
    for ordinal, row in enumerate(rows):
        row.completed_at = base + timedelta(minutes=ordinal)
    db.add_all(rows)
    db.commit()

    endpoint = "/api/v1/background-jobs/graph-ingest-reconciliations"
    first = client.get(f"{endpoint}?limit=1", headers=headers)
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]
    first_id = first_payload["operations"][0]["operation_id"]
    first_row = next(row for row in rows if row.operation_id == first_id)

    resolved = client.post(
        f"{_url(first_row)}/resolve",
        headers=headers,
        json=_payload(
            first_row,
            action=ingest_reconciliation.CONFIRM_ENTIRE_OPERATION_PRESENT,
            present=True,
        ),
    )
    assert resolved.status_code == 200, resolved.text

    second = client.get(
        f"{endpoint}?limit=1&cursor={first_payload['next_cursor']}",
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["operations"][0]["operation_id"] == rows[1].operation_id

    legacy_offset = client.get(f"{endpoint}?limit=1&offset=1", headers=headers)
    assert legacy_offset.status_code == 200, legacy_offset.text
    assert legacy_offset.json()["offset"] == 1
    assert legacy_offset.json()["operations"][0]["operation_id"] == (
        rows[2].operation_id
    )
    mixed = client.get(
        f"{endpoint}?offset=1&cursor={first_payload['next_cursor']}",
        headers=headers,
    )
    assert mixed.status_code == 422
    invalid = client.get(f"{endpoint}?cursor=not-a-valid-cursor", headers=headers)
    assert invalid.status_code == 422
