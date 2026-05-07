"""Per-role event-debounce primitive for the autonomous agent.

Bursts of new applications hitting one role would otherwise produce one
agent cycle per event — each paying the full system-prompt evaluation
cost. The debounce coalesces them: the first event in a window
atomically claims a slot on ``role.agent_next_run_at`` and schedules a
Celery task with a 60s countdown; subsequent events in the same window
no-op. The agent task clears the slot on entry so events arriving
during the cycle start a fresh window.

The atomic-claim is a single ``UPDATE … WHERE agent_next_run_at IS NULL
OR agent_next_run_at <= now`` — that's race-safe against concurrent
events on Postgres (the row-level lock from the UPDATE serializes them)
and works the same way on SQLite for tests.

Wired into:
- ``services.application_events.on_application_created`` (gate the
  enqueue)
- ``tasks.agent_tasks.agent_react_to_event`` (clear on entry)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from ..models.role import Role


# The window length. Tuned to ~60s: long enough to coalesce a typical
# Workable bulk-import batch into one cycle, short enough that recruiters
# don't perceive lag. The Celery task is enqueued with this same value
# as countdown.
DEFAULT_DEBOUNCE_SECONDS = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def try_claim_event_window(
    db: Session,
    *,
    role: Role,
    debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
    now: Optional[datetime] = None,
) -> bool:
    """Atomically reserve the next event-cycle slot for ``role``.

    Returns ``True`` if this caller won the lottery and is responsible
    for enqueuing the Celery task; ``False`` if another event is already
    pending and this one should no-op.

    Implementation: a single UPDATE that succeeds only when the slot is
    free (``agent_next_run_at`` IS NULL) or stale (the timestamp is in
    the past, meaning the previous task either failed silently or hasn't
    cleared it yet — recovery path). The DB enforces the at-most-one
    invariant via row-level locking.
    """
    moment = now or _utcnow()
    deadline = moment + timedelta(seconds=int(debounce_seconds))
    # ``synchronize_session=False`` skips SQLAlchemy's in-Python WHERE
    # evaluator, which can't compare a naive timestamp from SQLite against
    # an aware ``moment`` here. The DB-side comparison is the source of
    # truth either way; we refresh the row below to see the result.
    rows = db.execute(
        update(Role)
        .where(Role.id == role.id)
        .where(
            or_(
                Role.agent_next_run_at.is_(None),
                Role.agent_next_run_at <= moment,
            )
        )
        .values(agent_next_run_at=deadline)
        .execution_options(synchronize_session=False)
    ).rowcount or 0
    db.commit()
    if rows > 0:
        db.refresh(role)
        return True
    return False


def clear_event_window(db: Session, *, role: Role) -> None:
    """Release the debounce slot at the start of a cycle.

    Called by the agent task on entry, before ``run_cycle``. Events
    arriving after this point claim a new window — that's the desired
    behaviour: a long-running cycle should not block new triggers.
    """
    role.agent_next_run_at = None
    db.add(role)
    db.commit()


__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "try_claim_event_window",
    "clear_event_window",
]
