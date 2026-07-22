"""Bind routed workflow completion to the owning domain transaction."""

from __future__ import annotations

import logging
from typing import Final

from sqlalchemy import event
from sqlalchemy.orm import Session

from .execution import RouteExecution

logger = logging.getLogger("taali.ai_routing.transaction_completion")

_PENDING_COMPLETIONS_KEY: Final = "ai_routing_pending_completions"


def _pending_completions(session: Session) -> dict[str, tuple[RouteExecution, bool]]:
    pending = session.info.get(_PENDING_COMPLETIONS_KEY)
    if pending is None:
        pending = {}
        session.info[_PENDING_COMPLETIONS_KEY] = pending
    return pending


def _finish_safely(execution: RouteExecution, *, succeeded: bool) -> None:
    if execution.terminal_status is not None:
        return
    try:
        execution.finish_workflow(succeeded=succeeded)
    except Exception:
        # Routing telemetry is important, but a callback that runs after the
        # database transaction resolves must never turn a committed domain
        # transaction into an apparent application failure (or mask rollback).
        logger.exception(
            "could not finish route invocation=%s succeeded=%s",
            execution.invocation_id,
            succeeded,
        )


def _finish_pending(session: Session, *, transaction_succeeded: bool) -> None:
    # SessionEvents fire for SAVEPOINT transactions too. A routed workflow is
    # owned by the outer domain transaction, so an inner savepoint may neither
    # publish success nor convert a recoverable sub-operation into failure.
    if session.in_nested_transaction():
        return

    pending = session.info.pop(_PENDING_COMPLETIONS_KEY, {})
    for execution, requested_success in pending.values():
        _finish_safely(
            execution,
            succeeded=bool(transaction_succeeded and requested_success),
        )


@event.listens_for(Session, "after_commit")
def _finish_routes_after_commit(session: Session) -> None:
    _finish_pending(session, transaction_succeeded=True)


@event.listens_for(Session, "after_rollback")
def _fail_routes_after_rollback(session: Session) -> None:
    _finish_pending(session, transaction_succeeded=False)


@event.listens_for(Session, "after_transaction_end")
def _fail_routes_after_implicit_close(session: Session, transaction) -> None:
    """Catch Session.close()'s implicit rollback, which emits no rollback event."""

    if transaction.parent is None and session.info.get(_PENDING_COMPLETIONS_KEY):
        _finish_pending(session, transaction_succeeded=False)


def finish_route_with_transaction(
    session: Session,
    execution: RouteExecution | None,
    *,
    succeeded: bool,
) -> None:
    """Complete a route consistently with its workflow's domain writes.

    A successful feature result is provisional until the caller commits its
    SQLAlchemy ``Session``. If that outer transaction rolls back, the route is
    marked failed instead. Known workflow failures are terminal immediately and
    remove any previously queued success for the same invocation.

    Completion is deliberately best-effort: telemetry callback failures are
    logged and swallowed so they cannot break a commit or obscure a rollback.
    """

    if execution is None or execution.terminal_status is not None:
        return
    if not session.in_transaction():
        _finish_safely(execution, succeeded=False)
        return
    pending = _pending_completions(session)
    existing = pending.get(execution.invocation_id)
    requested_success = bool(succeeded and (existing[1] if existing else True))
    pending[execution.invocation_id] = (execution, requested_success)


__all__ = ["finish_route_with_transaction"]
