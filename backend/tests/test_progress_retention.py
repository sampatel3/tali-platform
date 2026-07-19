from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from types import SimpleNamespace

from app.domains.assessments_runtime import applications_routes
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.domains.assessments_runtime.progress_retention import (
    RECENT_TERMINAL_PROGRESS_TTL,
    get_retained_progress,
    retained_progress_items,
    set_bounded_progress,
)


def test_terminal_progress_is_visible_until_ttl_then_evicted() -> None:
    started = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    store: dict[int, dict] = {}
    progress = {"status": "completed", "total": 3}

    set_bounded_progress(store, 7, progress, now=started)

    assert progress["terminal_at"] == started
    assert retained_progress_items(
        store,
        now=started + RECENT_TERMINAL_PROGRESS_TTL - timedelta(seconds=1),
    ) == [(7, progress)]

    assert (
        retained_progress_items(
            store,
            now=started + RECENT_TERMINAL_PROGRESS_TTL,
        )
        == []
    )
    assert store == {}


def test_active_progress_is_never_ttl_evicted_and_clears_old_terminal_time() -> None:
    now = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    progress = {
        "status": "running",
        "terminal_at": now - timedelta(days=30),
    }
    store = {9: progress}

    set_bounded_progress(store, 9, progress, now=now)

    assert "terminal_at" not in progress
    assert get_retained_progress(store, 9, now=now + timedelta(days=365)) is progress


def test_legacy_terminal_progress_gets_a_bounded_grace_period() -> None:
    first_seen = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    progress = {"status": "failed"}
    store = {4: progress}

    assert get_retained_progress(store, 4, now=first_seen) is progress
    assert progress["terminal_at"] == first_seen
    assert (
        get_retained_progress(
            store,
            4,
            now=first_seen + RECENT_TERMINAL_PROGRESS_TTL,
        )
        is None
    )


def test_cancelled_progress_uses_existing_iso_terminal_timestamp() -> None:
    terminal_at = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    store = {
        2: {
            "status": "cancelled",
            "terminal_at": terminal_at.isoformat(),
        }
    }

    assert (
        get_retained_progress(
            store,
            2,
            now=terminal_at + RECENT_TERMINAL_PROGRESS_TTL,
        )
        is None
    )


def test_single_scope_read_does_not_iterate_or_prune_unrelated_entries() -> None:
    now = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)

    class _NonIterableStore(dict):
        def __iter__(self):
            raise AssertionError(
                "single-scope reads must not iterate the progress store"
            )

        def items(self):
            raise AssertionError("single-scope reads must not scan progress items")

    unrelated = {
        "status": "failed",
        "terminal_at": now - RECENT_TERMINAL_PROGRESS_TTL,
    }
    store = _NonIterableStore(
        {
            1: {
                "status": "completed",
                "terminal_at": now - RECENT_TERMINAL_PROGRESS_TTL,
            },
            2: unrelated,
        }
    )

    assert get_retained_progress(store, 1, now=now) is None
    assert store.get(2) is unrelated


def test_active_batch_discovery_keeps_only_owned_active_and_recent_jobs(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    store = {
        1: {
            "status": "running",
            "organization_id": 11,
            "role_name": "Role 1",
        },
        2: {
            "status": "completed",
            "organization_id": 11,
            "run_id": 202,
            "started_at": now - timedelta(minutes=2),
            "terminal_at": now,
            "role_name": "Role 2",
        },
        3: {
            "status": "failed",
            "organization_id": 11,
            "terminal_at": now,
            "role_name": "Role 3",
        },
        4: {
            "status": "cancelled",
            "organization_id": 11,
            "terminal_at": now - RECENT_TERMINAL_PROGRESS_TTL - timedelta(minutes=1),
        },
        5: {"status": "running", "organization_id": 12},
    }
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(
        applications_routes, "_recent_scoring_runs", lambda *_args, **_kwargs: []
    )

    response = applications_routes.get_active_batch_scores(
        db=object(), current_user=SimpleNamespace(organization_id=11)
    )

    assert {entry["role_id"] for entry in response["active"]} == {1, 2, 3}
    completed = next(entry for entry in response["active"] if entry["role_id"] == 2)
    assert completed["run_id"] == 202
    assert completed["started_at"] == now - timedelta(minutes=2)
    assert completed["terminal_at"] == now
    assert 4 not in store
    assert 5 in store


def test_batch_score_status_returns_the_exact_run_identity(monkeypatch) -> None:
    started_at = datetime(2026, 7, 18, 7, tzinfo=timezone.utc)
    terminal_at = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    store = {
        42: {
            "status": "completed",
            "organization_id": 11,
            "run_id": 910,
            "started_at": started_at,
            "terminal_at": terminal_at,
            "total": 0,
        }
    }
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(
        applications_routes, "_latest_scoring_run", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        applications_routes, "_recent_scoring_runs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        applications_routes,
        "require_job_permission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(applications_routes, "_read_batch_meta", lambda _role_id: None)
    monkeypatch.setattr(
        applications_routes, "_claim_batch_queue", lambda _role_id: None
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_role_name",
        lambda *_args, **_kwargs: "Platform Engineer",
    )

    response = applications_routes.batch_score_status(
        42,
        db=object(),
        current_user=SimpleNamespace(organization_id=11),
    )

    assert response["run_id"] == 910
    assert response["started_at"] == started_at
    assert response["terminal_at"] == terminal_at


def test_batch_score_recovery_metadata_persists_run_identity(monkeypatch) -> None:
    writes = []

    class _Redis:
        def set(self, key, value, **kwargs):
            writes.append((key, json.loads(value), kwargs))

    monkeypatch.setattr(applications_routes, "_redis_client", lambda: _Redis())
    started_at = datetime(2026, 7, 18, 7, tzinfo=timezone.utc)

    applications_routes._write_batch_meta(
        42,
        total=8,
        started_at=started_at,
        include_scored=True,
        run_id=910,
    )

    assert writes[0][1] == {
        "total": 8,
        "started_at": started_at.isoformat(),
        "include_scored": True,
        "run_id": 910,
    }


def test_redis_recovered_terminal_batch_is_persisted_and_finalized(
    monkeypatch,
) -> None:
    started_at = datetime(2026, 7, 18, 7, tzinfo=timezone.utc)
    store = {}
    deleted_meta = []
    durable_updates = []
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(
        applications_routes, "_latest_scoring_run", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        applications_routes,
        "require_job_permission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        applications_routes,
        "_read_batch_meta",
        lambda _role_id: {
            "total": 8,
            "started_at": started_at.isoformat(),
            "include_scored": True,
            "run_id": 910,
        },
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_terminal_counts",
        lambda *_args, **_kwargs: (8, 0, 0),
    )
    monkeypatch.setattr(
        applications_routes, "_claim_batch_queue", lambda _role_id: None
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_role_name",
        lambda *_args, **_kwargs: "Platform Engineer",
    )
    monkeypatch.setattr(
        applications_routes,
        "_delete_batch_meta",
        lambda role_id: deleted_meta.append(role_id),
    )

    def _record_durable_update(run_id, **kwargs):
        durable_updates.append((run_id, kwargs))
        return True

    monkeypatch.setattr(applications_routes, "_update_job_run", _record_durable_update)

    response = applications_routes.batch_score_status(
        42,
        db=object(),
        current_user=SimpleNamespace(organization_id=11),
    )

    assert response["status"] == "completed"
    assert response["run_id"] == 910
    assert store[42]["status"] == "completed"
    assert store[42]["scored"] == 8
    assert store[42]["terminal_at"] is not None
    assert deleted_meta == [42]
    assert durable_updates == [
        (
            910,
            {
                "status": "completed",
                "counters": {
                    "total": 8,
                    "scored": 8,
                    "errors": 0,
                    "pre_screened_out": 0,
                    "include_scored": True,
                },
                "finished": True,
            },
        )
    ]


def test_ordinary_terminal_batch_publishes_fresh_counters(monkeypatch) -> None:
    started_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    store = {
        42: {
            "status": "running",
            "organization_id": 11,
            "run_id": 910,
            "started_at": started_at,
            "total": 8,
            "scored": 0,
            "errors": 0,
            "pre_screened_out": 0,
        }
    }
    monkeypatch.setattr(applications_routes, "_batch_score_progress", store)
    monkeypatch.setattr(
        applications_routes, "_latest_scoring_run", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        applications_routes, "_recent_scoring_runs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        applications_routes,
        "require_job_permission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_terminal_counts",
        lambda *_args, **_kwargs: (8, 0, 0),
    )
    monkeypatch.setattr(
        applications_routes, "_claim_batch_queue", lambda _role_id: None
    )
    monkeypatch.setattr(
        applications_routes,
        "batch_score_role_name",
        lambda *_args, **_kwargs: "Platform Engineer",
    )
    monkeypatch.setattr(
        applications_routes, "_delete_batch_meta", lambda _role_id: None
    )
    monkeypatch.setattr(
        applications_routes, "_update_job_run", lambda *_args, **_kwargs: True
    )

    response = applications_routes.batch_score_status(
        42,
        db=object(),
        current_user=SimpleNamespace(organization_id=11),
    )
    discovered = applications_routes.get_active_batch_scores(
        db=object(), current_user=SimpleNamespace(organization_id=11)
    )["active"][0]

    assert response["status"] == "completed"
    assert response["scored"] == 8
    assert discovered["status"] == "completed"
    assert discovered["scored"] == 8
    assert discovered["total"] == 8


def test_prune_does_not_delete_entry_that_turns_active_during_sweep() -> None:
    now = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    progress = {
        "status": "completed",
        "terminal_at": now - RECENT_TERMINAL_PROGRESS_TTL,
    }

    class _RacingStore(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            if value is progress:
                value["status"] = "running"
            return value

    store = _RacingStore({1: progress})

    assert retained_progress_items(store, now=now) == [(1, progress)]
    assert progress["status"] == "running"


def test_prune_and_active_replacement_are_serialized_at_delete_boundary() -> None:
    now = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    expired = {
        "status": "completed",
        "terminal_at": now - RECENT_TERMINAL_PROGRESS_TTL,
    }
    pop_entered = Event()
    allow_pop = Event()
    setter_started = Event()
    setter_finished = Event()

    class _DeleteBoundaryStore(dict):
        def pop(self, key, default=None):
            pop_entered.set()
            assert allow_pop.wait(1)
            return super().pop(key, default)

    store = _DeleteBoundaryStore({1: expired})
    replacement = {"status": "running", "total": 4}

    pruning = Thread(target=retained_progress_items, args=(store,), kwargs={"now": now})
    pruning.start()
    assert pop_entered.wait(1)

    def replace() -> None:
        setter_started.set()
        set_bounded_progress(store, 1, replacement, now=now)
        setter_finished.set()

    setting = Thread(target=replace)
    setting.start()
    assert setter_started.wait(1)
    assert not setter_finished.wait(0.05)

    allow_pop.set()
    pruning.join(1)
    setting.join(1)

    assert not pruning.is_alive()
    assert not setting.is_alive()
    assert store[1] is replacement
    assert "terminal_at" not in replacement


def test_fetch_start_clears_stale_cancel_and_publishes_running_before_thread(
    monkeypatch,
) -> None:
    role_id = 19
    organization_id = 11
    store = {
        role_id: {
            "status": "completed",
            "terminal_at": datetime.now(timezone.utc),
        }
    }
    role = SimpleNamespace(name="Platform Engineer")
    organization = SimpleNamespace(workable_connected=True)
    application = SimpleNamespace(cv_text=None)

    class _Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args):
            return self

        def first(self):
            return organization if self.model is Organization else None

        def all(self):
            return [application] if self.model is CandidateApplication else []

    class _Db:
        def query(self, model):
            return _Query(model)

    cleared = []
    state_at_thread_start = []

    class _Thread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            state_at_thread_start.append(dict(store[role_id]))

    monkeypatch.setattr(applications_routes, "_batch_fetch_cvs_progress", store)
    monkeypatch.setattr(
        applications_routes, "require_job_permission", lambda *_args, **_kwargs: role
    )
    monkeypatch.setattr(applications_routes, "_create_job_run", lambda **_kwargs: 91)
    monkeypatch.setattr(
        applications_routes,
        "_clear_cancel_flag",
        lambda prefix, scope: cleared.append((prefix, scope)),
    )
    monkeypatch.setattr(applications_routes.threading, "Thread", _Thread)

    response = applications_routes.batch_fetch_cvs_role(
        role_id,
        dry_run=False,
        db=_Db(),
        current_user=SimpleNamespace(organization_id=organization_id),
    )

    assert response == {"status": "started", "total": 1}
    assert cleared == [(applications_routes._BATCH_FETCH_CANCEL_PREFIX, role_id)]
    assert state_at_thread_start[0]["status"] == "running"
    assert state_at_thread_start[0]["total"] == 1
    assert "terminal_at" not in state_at_thread_start[0]


def test_batch_fetch_failure_persists_only_stable_error_code(
    monkeypatch, caplog
) -> None:
    role_id = 31
    secret_marker = "workable-provider-response-secret-must-not-persist"
    store = {role_id: {"run_id": 301, "status": "running"}}
    updates: list[tuple[int | None, dict]] = []

    class _Db:
        def query(self, _model):
            raise RuntimeError(secret_marker)

        def close(self):
            return None

    monkeypatch.setattr(applications_routes, "SessionLocal", _Db)
    monkeypatch.setattr(applications_routes, "_batch_fetch_cvs_progress", store)
    monkeypatch.setattr(
        applications_routes,
        "_update_job_run",
        lambda run_id, **kwargs: updates.append((run_id, kwargs)),
    )

    applications_routes._run_batch_fetch_cvs(role_id, 12)

    assert updates == [
        (
            301,
            {
                "status": "failed",
                "error": "batch_cv_fetch_failed:RuntimeError",
                "finished": True,
            },
        )
    ]
    assert secret_marker not in caplog.text


def test_graph_sync_failure_persists_only_stable_error_code(
    monkeypatch, caplog
) -> None:
    from app.candidate_graph import client as graph_client

    org_id = 41
    secret_marker = "graphiti-provider-response-secret-must-not-persist"
    store = {org_id: {"run_id": 401, "status": "running"}}
    updates: list[tuple[int | None, dict]] = []

    class _Db:
        def close(self):
            return None

    monkeypatch.setattr(applications_routes, "SessionLocal", _Db)
    monkeypatch.setattr(applications_routes, "_sync_graph_progress", store)
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        applications_routes,
        "_select_graph_sync_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret_marker)),
    )
    monkeypatch.setattr(
        applications_routes,
        "_update_job_run",
        lambda run_id, **kwargs: updates.append((run_id, kwargs)),
    )

    applications_routes._run_sync_graph(org_id)

    assert updates == [
        (
            401,
            {
                "status": "failed",
                "error": "graph_sync_failed:RuntimeError",
                "finished": True,
            },
        )
    ]
    assert secret_marker not in caplog.text
