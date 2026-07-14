"""Serialized Bullhorn connect transaction plus post-commit full-sync kick."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...components.integrations.bullhorn import bootstrap
from ...components.integrations.bullhorn import stage_map as stage_map_mod
from ...components.integrations.bullhorn.sync_runner import (
    BullhornMutexUnavailable,
    _acquire_mutex,
    _release_mutex,
)
from ...models.organization import Organization
from .connect import ConnectResult, run_connect
from .schemas import ConnectRequest


class BullhornConnectBusy(RuntimeError):
    def __init__(self, *, lock_unavailable: bool = False):
        super().__init__("Bullhorn is busy; connect can be retried safely")
        self.lock_unavailable = lock_unavailable


@dataclass(frozen=True)
class ConnectedLifecycleResult:
    connect: ConnectResult
    initial_sync: dict
    unmapped_status_count: int


def connect_and_start_full_sync(
    db: Session,
    org: Organization,
    body: ConnectRequest,
) -> ConnectedLifecycleResult:
    """Hold the token-rotation mutex until the new lineage commits."""
    try:
        mutex = _acquire_mutex(int(org.id))
    except BullhornMutexUnavailable as exc:
        raise BullhornConnectBusy(lock_unavailable=True) from exc
    if mutex is None:
        raise BullhornConnectBusy()

    try:
        result = run_connect(
            db,
            org,
            username=body.username.strip(),
            client_id=body.client_id.strip(),
            client_secret=body.client_secret,
            password=body.password,
        )
        config = dict(org.bullhorn_config) if isinstance(org.bullhorn_config, dict) else {}
        config["status_list"] = list(result.statuses)
        org.bullhorn_config = config
        intent = bootstrap.prepare_initial_full_sync(org)
        db.add(org)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        _release_mutex(mutex)

    initial_sync = bootstrap.dispatch_initial_full_sync(
        db,
        org_id=int(org.id),
        intent=intent,
    )
    return ConnectedLifecycleResult(
        connect=result,
        initial_sync=initial_sync,
        unmapped_status_count=len(stage_map_mod.unmapped_statuses(db, org)),
    )
