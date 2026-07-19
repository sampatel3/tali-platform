"""Cross-worker serialization for one organization's nightly policy fitting."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

POLICY_FIT_LOCK_SCOPE = "nightly_policy_fit"


@dataclass
class _LocalLockEntry:
    lock: Lock
    users: int = 0


_LOCAL_LOCKS_GUARD = Lock()
_LOCAL_LOCKS: dict[int, _LocalLockEntry] = {}


@contextmanager
def _local_policy_fit_mutex(organization_id: int) -> Iterator[None]:
    with _LOCAL_LOCKS_GUARD:
        entry = _LOCAL_LOCKS.setdefault(
            int(organization_id),
            _LocalLockEntry(lock=Lock()),
        )
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _LOCAL_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0:
                _LOCAL_LOCKS.pop(int(organization_id), None)


@contextmanager
def policy_fit_mutex(db: Session, *, organization_id: int) -> Iterator[None]:
    """Serialize duplicate-spend checks without an ORM transaction or row lock.

    PostgreSQL owns a session advisory lock on a dedicated autocommit
    connection. The caller's ORM session therefore stays transaction-free while
    CPU/model/provider work runs, and ordinary organization writes remain
    independent. SQLite/dev uses an equivalent process-local keyed mutex.
    """

    assert not db.in_transaction(), "policy-fit mutex requires a detached ORM session"
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        with _local_policy_fit_mutex(int(organization_id)):
            yield
        return

    engine = getattr(bind, "engine", bind)
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    parameters = {
        "scope": POLICY_FIT_LOCK_SCOPE,
        "organization_id": int(organization_id),
    }
    acquired = False
    try:
        connection.execute(
            text(
                "SELECT pg_advisory_lock("
                "hashtext(:scope), :organization_id)"
            ),
            parameters,
        )
        acquired = True
        yield
    finally:
        if acquired:
            try:
                connection.execute(
                    text(
                        "SELECT pg_advisory_unlock("
                        "hashtext(:scope), :organization_id)"
                    ),
                    parameters,
                )
            finally:
                connection.close()
        else:
            connection.close()


__all__ = ["POLICY_FIT_LOCK_SCOPE", "policy_fit_mutex"]
