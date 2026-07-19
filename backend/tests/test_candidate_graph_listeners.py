"""Transaction boundaries for candidate-graph SQLAlchemy listeners."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import event

from app.candidate_graph import listeners
from app.models.application_interview import ApplicationInterview
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.candidate_application_event import CandidateApplicationEvent
from app.models.organization import Organization
from app.models.role import Role
from app.tasks import graph_ingest_tasks


def _remove_registered_listeners() -> None:
    for target, event_name, handler in listeners._listener_specs():
        if event.contains(target, event_name, handler):
            event.remove(target, event_name, handler)
    listeners._registered = False


@pytest.fixture(autouse=True)
def _isolated_listener_registration():
    prior_registered = listeners._registered
    prior_handlers = [
        (target, event_name, handler)
        for target, event_name, handler in listeners._listener_specs()
        if event.contains(target, event_name, handler)
    ]
    _remove_registered_listeners()
    try:
        yield
    finally:
        _remove_registered_listeners()
        for target, event_name, handler in prior_handlers:
            if not event.contains(target, event_name, handler):
                event.listen(target, event_name, handler)
        listeners._registered = prior_registered


@pytest.fixture
def queued(monkeypatch):
    calls = {"candidate": [], "interview": [], "event": []}
    monkeypatch.setattr(
        graph_ingest_tasks.sync_candidate_to_graph,
        "delay",
        lambda row_id: calls["candidate"].append(int(row_id)),
    )
    monkeypatch.setattr(
        graph_ingest_tasks.sync_interview_to_graph,
        "delay",
        lambda row_id: calls["interview"].append(int(row_id)),
    )
    monkeypatch.setattr(
        graph_ingest_tasks.sync_event_to_graph,
        "delay",
        lambda row_id: calls["event"].append(int(row_id)),
    )
    return calls


def _register(monkeypatch) -> None:
    monkeypatch.setattr(listeners.graph_client, "is_configured", lambda: True)
    listeners.register_listeners()


def _seed_org_role(db) -> tuple[int, int]:
    org = Organization(name="Graph listener org", slug=f"graph-listener-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(organization_id=int(org.id), name="Graph listener role", source="manual")
    db.add(role)
    db.flush()
    ids = (int(org.id), int(role.id))
    db.commit()
    return ids


def _seed_application(
    db,
    *,
    org_id: int | None = None,
    role_id: int | None = None,
    label: str = "one",
) -> tuple[int, int, int, int]:
    if org_id is None or role_id is None:
        org_id, role_id = _seed_org_role(db)
    candidate = Candidate(
        organization_id=int(org_id),
        email=f"graph-listener-{label}-{id(db)}@example.test",
        full_name=f"Candidate {label}",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=int(org_id),
        candidate_id=int(candidate.id),
        role_id=int(role_id),
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        source="manual",
        version=1,
    )
    db.add(application)
    db.flush()
    ids = (int(org_id), int(role_id), int(candidate.id), int(application.id))
    db.commit()
    return ids


def _new_event(*, application_id: int, organization_id: int, reason: str):
    return CandidateApplicationEvent(
        application_id=int(application_id),
        organization_id=int(organization_id),
        event_type="application_outcome_changed",
        actor_type="system",
        reason=reason,
    )


def test_flush_stages_and_root_commit_deduplicates_candidate(
    db, monkeypatch, queued
):
    org_id, role_id = _seed_org_role(db)
    _register(monkeypatch)
    listeners.register_listeners()  # idempotent registration

    candidate = Candidate(
        organization_id=org_id,
        email=f"dedupe-{id(db)}@example.test",
        full_name="Before",
    )
    db.add(candidate)
    db.flush()
    candidate_id = int(candidate.id)

    application = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate_id,
        role_id=role_id,
        status="applied",
        pipeline_stage="applied",
        application_outcome="open",
        source="manual",
        version=1,
    )
    db.add(application)
    db.flush()

    candidate.full_name = "After"
    application.notes = "application update maps to the same candidate"
    db.flush()

    assert queued == {"candidate": [], "interview": [], "event": []}
    db.commit()
    assert queued == {"candidate": [candidate_id], "interview": [], "event": []}

    # A reused Session starts a fresh transaction ledger after commit. Dedupe
    # is per transaction, so a later real update still gets its own delivery.
    application = db.get(CandidateApplication, int(application.id))
    application.notes = "second committed transaction"
    db.commit()
    assert queued == {
        "candidate": [candidate_id, candidate_id],
        "interview": [],
        "event": [],
    }


def test_interview_and_event_coverage_waits_for_root_commit(
    db, monkeypatch, queued
):
    org_id, _role_id, _candidate_id, application_id = _seed_application(db)
    _register(monkeypatch)

    interview = ApplicationInterview(
        organization_id=org_id,
        application_id=application_id,
        stage="screening",
        source="manual",
        status="linked",
        transcript_text="first transcript",
    )
    pipeline_event = _new_event(
        application_id=application_id,
        organization_id=org_id,
        reason="first event",
    )
    db.add_all([interview, pipeline_event])
    db.flush()
    interview_id = int(interview.id)
    event_id = int(pipeline_event.id)

    interview.transcript_text = "updated transcript"
    db.flush()

    assert queued == {"candidate": [], "interview": [], "event": []}
    db.commit()
    assert queued == {
        "candidate": [],
        "interview": [interview_id],
        "event": [event_id],
    }


def test_root_rollback_emits_nothing_and_session_reuse_has_no_stale_ids(
    db, monkeypatch, queued
):
    org_id, _role_id, candidate_id, application_id = _seed_application(db)
    _register(monkeypatch)

    application = db.get(CandidateApplication, application_id)
    application.application_outcome = "rejected"
    db.add(
        _new_event(
            application_id=application_id,
            organization_id=org_id,
            reason="rolled back rejection",
        )
    )
    db.flush()
    assert queued == {"candidate": [], "interview": [], "event": []}

    db.rollback()
    assert queued == {"candidate": [], "interview": [], "event": []}

    # Reuse the same Session for a real commit. Rolled-back IDs must not leak
    # into this later transaction or create duplicate delivery.
    application = db.get(CandidateApplication, application_id)
    application.notes = "committed after rollback"
    db.commit()
    assert queued == {"candidate": [candidate_id], "interview": [], "event": []}


@pytest.mark.parametrize("reset_method", ["close", "reset"])
def test_implicit_session_reset_discards_staged_ids_before_reuse(
    db, monkeypatch, queued, reset_method
):
    org_id, _role_id, candidate_id, application_id = _seed_application(db)
    _register(monkeypatch)

    application = db.get(CandidateApplication, application_id)
    application.application_outcome = "rejected"
    db.add(
        _new_event(
            application_id=application_id,
            organization_id=org_id,
            reason=f"implicitly rolled back by Session.{reset_method}",
        )
    )
    db.flush()
    assert queued == {"candidate": [], "interview": [], "event": []}
    assert listeners._SESSION_PENDING_KEY in db.info

    getattr(db, reset_method)()
    assert listeners._SESSION_PENDING_KEY not in db.info

    # An unrelated transaction on the reused Session must not release IDs
    # staged by the implicitly rolled-back transaction.
    org = db.get(Organization, org_id)
    org.name = f"unrelated commit after {reset_method}"
    db.commit()
    assert queued == {"candidate": [], "interview": [], "event": []}

    # The listener remains installed and a fresh relevant transaction still
    # emits normally, proving cleanup did not disable Session reuse.
    application = db.get(CandidateApplication, application_id)
    application.notes = f"relevant commit after {reset_method}"
    db.commit()
    assert queued == {"candidate": [candidate_id], "interview": [], "event": []}


def test_strict_reject_failure_after_flush_queues_nothing_on_rollback(
    db, monkeypatch, queued
):
    from app.actions import reject_application
    from app.actions.types import Actor
    from app.platform.config import settings
    from app.services import workable_actions_service
    from app.services.workable_actions_service import (
        WorkableWritebackError,
        strict_workable_writes,
    )

    org_id, _role_id, _candidate_id, application_id = _seed_application(db)
    org = db.get(Organization, org_id)
    org.workable_connected = True
    org.workable_access_token = "test-token"
    org.workable_subdomain = "test-workspace"
    application = db.get(CandidateApplication, application_id)
    application.workable_candidate_id = "workable-candidate"
    db.commit()
    _register(monkeypatch)
    monkeypatch.setattr(settings, "MVP_DISABLE_WORKABLE", False)

    provider_error = WorkableWritebackError(
        action="disqualify",
        code="api_error",
        message="Workable unavailable",
        retriable=True,
    )

    def _strict_provider_failure(*_args, **_kwargs):
        raise provider_error

    monkeypatch.setattr(
        workable_actions_service,
        "disqualify_candidate_in_workable",
        _strict_provider_failure,
    )
    # reject_application flushes the outcome + pipeline events before it calls
    # the provider. In a listener-enabled process, the strict failure must
    # still roll back without graph work escaping that earlier flush.
    with strict_workable_writes():
        with pytest.raises(WorkableWritebackError):
            reject_application.run(
                db,
                Actor.system(),
                organization_id=org_id,
                application_id=application_id,
            )

    assert queued == {"candidate": [], "interview": [], "event": []}
    db.rollback()
    assert queued == {"candidate": [], "interview": [], "event": []}

    application = db.get(CandidateApplication, application_id)
    assert application.application_outcome == "open"
    assert (
        db.query(CandidateApplicationEvent)
        .filter(CandidateApplicationEvent.application_id == application_id)
        .count()
        == 0
    )


def test_savepoint_commit_defers_delivery_until_root_commit(
    db, monkeypatch, queued
):
    org_id, _role_id, candidate_id, application_id = _seed_application(db)
    _register(monkeypatch)

    application = db.get(CandidateApplication, application_id)
    with db.begin_nested():
        application.notes = "inside committed savepoint"
        pipeline_event = _new_event(
            application_id=application_id,
            organization_id=org_id,
            reason="committed savepoint event",
        )
        db.add(pipeline_event)
        db.flush()
        event_id = int(pipeline_event.id)
        assert queued == {"candidate": [], "interview": [], "event": []}

    assert queued == {"candidate": [], "interview": [], "event": []}
    # Nested after_commit transfers ownership to the still-open root rather
    # than leaving an arbitrary bucket for root commit to union later.
    pending = db.info[listeners._SESSION_PENDING_KEY]
    assert set(pending) == {db.get_transaction()}
    db.commit()
    assert queued == {
        "candidate": [candidate_id],
        "interview": [],
        "event": [event_id],
    }


def test_savepoint_rollback_discards_nested_ids_but_preserves_outer_id(
    db, monkeypatch, queued
):
    org_id, role_id, first_candidate_id, first_application_id = _seed_application(db)
    _org_id, _role_id, second_candidate_id, second_application_id = _seed_application(
        db,
        org_id=org_id,
        role_id=role_id,
        label="two",
    )
    _register(monkeypatch)

    first = db.get(CandidateApplication, first_application_id)
    second = db.get(CandidateApplication, second_application_id)
    first.notes = "outer update"
    db.flush()

    savepoint = db.begin_nested()
    first.status = "review"
    second.notes = "nested-only update"
    db.add(
        _new_event(
            application_id=second_application_id,
            organization_id=org_id,
            reason="nested-only event",
        )
    )
    db.flush()
    savepoint.rollback()

    db.commit()
    assert queued == {
        "candidate": [first_candidate_id],
        "interview": [],
        "event": [],
    }
    assert second_candidate_id not in queued["candidate"]


def test_parent_savepoint_rollback_discards_committed_child_lineage(
    db, monkeypatch, queued
):
    _org_id, _role_id, _candidate_id, application_id = _seed_application(db)
    _register(monkeypatch)

    application = db.get(CandidateApplication, application_id)
    parent = db.begin_nested()
    application.notes = "parent savepoint update"
    db.flush()

    child = db.begin_nested()
    application.status = "review"
    db.flush()
    child.commit()
    assert queued == {"candidate": [], "interview": [], "event": []}

    parent.rollback()
    db.commit()
    assert queued == {"candidate": [], "interview": [], "event": []}


def test_broker_exception_is_suppressed_and_other_kinds_still_dispatch(
    db, monkeypatch, queued, caplog
):
    org_id, _role_id, _candidate_id, application_id = _seed_application(db)
    _register(monkeypatch)

    def _broker_down(_row_id):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(graph_ingest_tasks.sync_candidate_to_graph, "delay", _broker_down)
    application = db.get(CandidateApplication, application_id)
    application.notes = "must still commit"
    pipeline_event = _new_event(
        application_id=application_id,
        organization_id=org_id,
        reason="must still enqueue",
    )
    db.add(pipeline_event)
    db.flush()
    event_id = int(pipeline_event.id)

    with caplog.at_level(logging.ERROR, logger="taali.candidate_graph.listeners"):
        db.commit()

    assert db.get(CandidateApplication, application_id).notes == "must still commit"
    assert queued["event"] == [event_id]
    assert "failed to enqueue candidate graph sync" in caplog.text


def test_root_commit_discards_unowned_orphan_bucket(db, monkeypatch, queued, caplog):
    org_id, _role_id, candidate_id, _application_id = _seed_application(db)
    _register(monkeypatch)

    # Model a lifecycle bug or stale state from an older process. Root commit
    # may dispatch only its own transaction bucket, never an arbitrary ledger
    # entry merely because it remains in Session.info.
    listeners_pending = listeners._PendingGraphIds(candidate_ids={candidate_id})
    db.info[listeners._SESSION_PENDING_KEY] = {object(): listeners_pending}
    org = db.get(Organization, org_id)
    org.name = "unrelated root commit"
    with caplog.at_level(logging.WARNING, logger="taali.candidate_graph.listeners"):
        db.commit()

    assert queued == {"candidate": [], "interview": [], "event": []}
    assert listeners._SESSION_PENDING_KEY not in db.info
    assert "orphaned graph listener transaction bucket" in caplog.text


def test_unconfigured_registration_is_a_true_noop(db, monkeypatch, queued):
    monkeypatch.setattr(listeners.graph_client, "is_configured", lambda: False)
    listeners.register_listeners()

    assert listeners._registered is False
    assert all(
        not event.contains(target, event_name, handler)
        for target, event_name, handler in listeners._listener_specs()
    )

    org_id, _role_id = _seed_org_role(db)
    candidate = Candidate(
        organization_id=org_id,
        email=f"unconfigured-{id(db)}@example.test",
        full_name="No listener",
    )
    db.add(candidate)
    db.flush()
    db.commit()
    assert queued == {"candidate": [], "interview": [], "event": []}
