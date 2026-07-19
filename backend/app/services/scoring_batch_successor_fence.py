"""Linearizable publication fence for durable scoring successors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging

from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..platform.database import SessionLocal
from .scoring_batch_successors import SUCCESSOR_KEY, successor_payload


logger = logging.getLogger(__name__)


@contextmanager
def scoring_successor_dispatch_fence(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    queue_id: str,
    claim_token: str,
    require_claim: bool = True,
) -> Iterator[bool]:
    """Linearize paid successor publication against parent cancellation."""

    db = SessionLocal()
    authorized = False
    hold_lock = False
    try:
        try:
            run = (
                db.query(BackgroundJobRun)
                .filter(
                    BackgroundJobRun.id == int(run_id),
                    BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
                    BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                    BackgroundJobRun.scope_id == int(role_id),
                    BackgroundJobRun.organization_id == int(organization_id),
                )
                .with_for_update()
                .one_or_none()
            )
            payload = successor_payload(
                dict(run.counters or {}).get(SUCCESSOR_KEY) if run is not None else None
            )
            claim_matches = bool(
                payload is not None
                and payload.get("state") == "claimed"
                and payload.get("queue_id") == queue_id
                and payload.get("claim_token") == claim_token
            )
            authorized = bool(
                (run is None and not require_claim)
                or (
                    run is not None
                    and run.cancel_requested_at is None
                    and str(run.status) not in {"cancelling", "cancelled"}
                    and (claim_matches or not require_claim)
                )
            )
            hold_lock = bool(
                authorized
                and run is not None
                and db.get_bind().dialect.name != "sqlite"
            )
            if not hold_lock:
                if authorized:
                    db.commit()
                else:
                    db.rollback()
        except Exception:
            logger.exception(
                "scoring successor dispatch fence failed run_id=%s", run_id
            )
            db.rollback()
            authorized = False
        yield authorized
        if hold_lock:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = ["scoring_successor_dispatch_fence"]
