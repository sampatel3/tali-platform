from __future__ import annotations

import pytest

from app.scripts import railway_worker_start as worker_start


def test_database_schema_revisions_handles_unmigrated_database():
    assert worker_start._database_schema_revisions("sqlite://") == ()


def test_worker_refuses_image_with_multiple_schema_heads(monkeypatch):
    class _Scripts:
        @staticmethod
        def get_heads():
            return ("head_one", "head_two")

    monkeypatch.setattr(
        worker_start.ScriptDirectory,
        "from_config",
        lambda _config: _Scripts(),
    )

    with pytest.raises(SystemExit, match="exactly one Alembic head; found 2"):
        worker_start._expected_schema_revision()


def test_worker_waits_until_database_reaches_code_revision(monkeypatch):
    observed: list[str] = []
    revisions = iter([
        ("181_bind_candidate_request_proof",),
        ("182_candidate_clipboard",),
    ])

    monkeypatch.setattr(worker_start, "_database_url", lambda: "postgresql://database")
    monkeypatch.setattr(
        worker_start,
        "_expected_schema_revision",
        lambda: "182_candidate_clipboard",
    )
    monkeypatch.setattr(
        worker_start,
        "_database_schema_revisions",
        lambda database_url: observed.append(database_url) or next(revisions),
    )
    monkeypatch.setattr(worker_start.time, "sleep", lambda _seconds: None)

    worker_start._wait_for_schema_revision(timeout_seconds=5, interval_seconds=0)

    assert observed == ["postgresql://database", "postgresql://database"]


def test_worker_schema_wait_fails_closed_on_timeout(monkeypatch):
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(worker_start, "_database_url", lambda: "postgresql://database")
    monkeypatch.setattr(
        worker_start,
        "_expected_schema_revision",
        lambda: "182_candidate_clipboard",
    )
    monkeypatch.setattr(
        worker_start,
        "_database_schema_revisions",
        lambda _database_url: ("181_bind_candidate_request_proof",),
    )
    monkeypatch.setattr(worker_start.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(worker_start.time, "sleep", lambda _seconds: None)

    with pytest.raises(SystemExit, match="expected 182_candidate_clipboard"):
        worker_start._wait_for_schema_revision(timeout_seconds=1, interval_seconds=0)


def test_worker_checks_schema_before_redis_and_celery(monkeypatch):
    events: list[str] = []

    def stop_at_exec(*_args):
        events.append("celery")
        raise RuntimeError("exec")

    monkeypatch.setattr(worker_start, "_configuration_failures", lambda: [])
    monkeypatch.setattr(
        worker_start,
        "_wait_for_database",
        lambda **_kwargs: events.append("database"),
    )
    monkeypatch.setattr(
        worker_start,
        "_wait_for_schema_revision",
        lambda **_kwargs: events.append("schema"),
    )
    monkeypatch.setattr(
        worker_start,
        "_wait_for_redis",
        lambda **_kwargs: events.append("redis"),
    )
    monkeypatch.setattr(worker_start.os, "execvp", stop_at_exec)
    monkeypatch.setenv("RAILWAY_DEPENDENCY_WAIT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("RAILWAY_DEPENDENCY_WAIT_INTERVAL_SECONDS", "0")

    with pytest.raises(RuntimeError, match="exec"):
        worker_start.main()

    assert events == ["database", "schema", "redis", "celery"]
