from __future__ import annotations

from uuid import uuid4

from app.components.ai_routing.transaction_completion import (
    finish_route_with_transaction,
)
from app.models.organization import Organization


class _Route:
    def __init__(self) -> None:
        self.invocation_id = str(uuid4())
        self.terminal_status: str | None = None
        self.completions: list[bool] = []

    def finish_workflow(self, *, succeeded: bool) -> None:
        self.completions.append(succeeded)
        self.terminal_status = "succeeded" if succeeded else "failed"


class _ExplodingRoute(_Route):
    def finish_workflow(self, *, succeeded: bool) -> None:
        raise RuntimeError("telemetry unavailable")


def test_success_is_published_only_after_outer_commit(db) -> None:
    route = _Route()
    db.add(Organization(name="Committed domain row", slug=f"commit-{uuid4()}"))

    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    assert route.completions == []
    nested = db.begin_nested()
    nested.commit()
    assert route.completions == []

    db.commit()

    assert route.completions == [True]
    assert route.terminal_status == "succeeded"


def test_outer_rollback_converts_provisional_success_to_failure(db) -> None:
    route = _Route()
    db.add(Organization(name="Rolled back domain row", slug=f"rollback-{uuid4()}"))
    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    db.rollback()

    assert route.completions == [False]
    assert route.terminal_status == "failed"


def test_nested_rollback_does_not_fail_outer_workflow(db) -> None:
    route = _Route()
    db.add(Organization(name="Savepoint domain row", slug=f"savepoint-{uuid4()}"))
    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    nested = db.begin_nested()
    nested.rollback()
    assert route.completions == []

    db.commit()

    assert route.completions == [True]


def test_known_failure_waits_for_transaction_end_and_cancels_queued_success(db) -> None:
    route = _Route()
    db.add(Organization(name="Known failure", slug=f"known-failure-{uuid4()}"))
    db.flush()
    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    finish_route_with_transaction(db, route, succeeded=False)  # type: ignore[arg-type]

    assert route.completions == []
    db.commit()
    assert route.completions == [False]


def test_session_close_converts_provisional_success_to_failure(db) -> None:
    route = _Route()
    db.add(Organization(name="Implicit rollback", slug=f"close-{uuid4()}"))
    db.flush()
    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    db.close()

    assert route.completions == [False]


def test_completion_callback_error_cannot_break_domain_commit(db) -> None:
    route = _ExplodingRoute()
    slug = f"callback-{uuid4()}"
    db.add(Organization(name="Durable despite telemetry", slug=slug))
    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    db.commit()

    assert db.query(Organization).filter(Organization.slug == slug).one().name == (
        "Durable despite telemetry"
    )


def test_completion_callback_error_cannot_obscure_domain_rollback(db) -> None:
    route = _ExplodingRoute()
    db.add(Organization(name="Rollback survives telemetry", slug=f"error-{uuid4()}"))
    finish_route_with_transaction(db, route, succeeded=True)  # type: ignore[arg-type]

    db.rollback()
