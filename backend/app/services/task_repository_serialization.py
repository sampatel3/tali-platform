"""Cross-worker serialization for one task's canonical repository main branch."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

TASK_REPOSITORY_WRITE_LOCK_SCOPE = "task_repository_write"


@dataclass
class _LocalLockEntry:
    lock: Lock
    users: int = 0


_LOCAL_LOCKS_GUARD = Lock()
_LOCAL_LOCKS: dict[int, _LocalLockEntry] = {}


class TaskRepositoryBusyError(RuntimeError):
    """Another operation currently owns this task's canonical repository."""


@contextmanager
def _local_task_repository_write_mutex(
    task_id: int,
    *,
    wait: bool,
) -> Iterator[None]:
    with _LOCAL_LOCKS_GUARD:
        entry = _LOCAL_LOCKS.setdefault(int(task_id), _LocalLockEntry(lock=Lock()))
        entry.users += 1
    acquired = entry.lock.acquire(blocking=wait)
    if not acquired:
        with _LOCAL_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _LOCAL_LOCKS.pop(int(task_id), None)
        raise TaskRepositoryBusyError(
            f"Task {int(task_id)} repository is being updated"
        )
    try:
        yield
    finally:
        entry.lock.release()
        with _LOCAL_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _LOCAL_LOCKS.pop(int(task_id), None)


@contextmanager
def task_repository_write_mutex(
    db: Session,
    *,
    task_id: int,
    wait: bool = True,
) -> Iterator[None]:
    """Serialize capture→provider→commit without retaining ORM row locks.

    PostgreSQL uses a session advisory lock on a dedicated autocommit
    connection, so the caller can roll its ORM transaction back before slow
    filesystem/GitHub work without releasing repository-write ownership. Other
    dialects use a process-local keyed mutex for deterministic tests/dev.
    """

    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        with _local_task_repository_write_mutex(int(task_id), wait=wait):
            yield
        return

    engine = getattr(bind, "engine", bind)
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    parameters = {
        "scope": TASK_REPOSITORY_WRITE_LOCK_SCOPE,
        "task_id": int(task_id),
    }
    acquired = False
    try:
        lock_statement = text(
            "SELECT pg_advisory_lock(hashtext(:scope), :task_id)"
            if wait
            else "SELECT pg_try_advisory_lock(hashtext(:scope), :task_id)"
        )
        lock_result = connection.execute(
            lock_statement,
            parameters,
        ).scalar_one()
        if not wait and lock_result is not True:
            raise TaskRepositoryBusyError(
                f"Task {int(task_id)} repository is being updated"
            )
        acquired = True
        yield
    finally:
        if acquired:
            try:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:scope), :task_id)"),
                    parameters,
                )
            finally:
                connection.close()
        else:
            connection.close()


__all__ = [
    "TASK_REPOSITORY_WRITE_LOCK_SCOPE",
    "TaskRepositoryBusyError",
    "task_repository_write_mutex",
]
