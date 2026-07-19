"""Durable, role-scoped successor intents for scoring batches."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models.background_job_run import (
    JOB_KIND_SCORING_BATCH,
    SCOPE_KIND_ROLE,
    BackgroundJobRun,
)
from ..platform.database import SessionLocal


logger = logging.getLogger(__name__)
SUCCESSOR_KEY = "queued_successor"
QUEUE_CONTRACT = "background_job_run_successor_v1"
CLAIM_SECONDS = 120
_CLAIM_CLOCK_SKEW_SECONDS = 5


def successor_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    queue_id = value.get("queue_id")
    include_scored = value.get("include_scored")
    applied_after = value.get("applied_after")
    if not isinstance(queue_id, str) or not queue_id:
        return None
    if type(include_scored) is not bool:
        return None
    if applied_after is not None and type(applied_after) is not str:
        return None
    return dict(value)


def _run_query(db, *, run_id: int, role_id: int, organization_id: int):
    return db.query(BackgroundJobRun).filter(
        BackgroundJobRun.id == int(run_id),
        BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
        BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
        BackgroundJobRun.scope_id == int(role_id),
        BackgroundJobRun.organization_id == int(organization_id),
    )


def queue_scoring_successor(
    run_id: int | None,
    *,
    role_id: int,
    organization_id: int,
    include_scored: bool,
    applied_after: str | None,
    queue_id: str,
) -> bool:
    """Durably coalesce one successor intent onto an exact scoring run."""

    if not run_id or not queue_id:
        return False
    db = SessionLocal()
    try:
        run = (
            _run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .filter(
                BackgroundJobRun.cancel_requested_at.is_(None),
                BackgroundJobRun.status.notin_(("cancelling", "cancelled")),
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return False
        counters = dict(run.counters or {})
        counters["queue_contract"] = QUEUE_CONTRACT
        counters[SUCCESSOR_KEY] = {
            "queue_id": str(queue_id),
            "include_scored": bool(include_scored),
            "applied_after": applied_after,
            "state": "pending",
            "dispatch_attempt": 0,
        }
        run.counters = counters
        db.commit()
        return True
    except Exception:
        logger.exception("scoring successor queue failed run_id=%s", run_id)
        db.rollback()
        return False
    finally:
        db.close()


def claim_scoring_successor(
    run_id: int | None,
    *,
    role_id: int,
    organization_id: int,
) -> dict[str, Any] | None:
    """Lease a durable successor to exactly one reconciler at a time."""

    if not run_id:
        return None
    db = SessionLocal()
    try:
        run = (
            _run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .filter(
                BackgroundJobRun.cancel_requested_at.is_(None),
                BackgroundJobRun.status.notin_(("cancelling", "cancelled")),
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return None
        counters = dict(run.counters or {})
        payload = successor_payload(counters.get(SUCCESSOR_KEY))
        if payload is None:
            return None
        now = datetime.now(timezone.utc)
        claimed_at = None
        if isinstance(payload.get("claimed_at"), str):
            try:
                claimed_at = datetime.fromisoformat(
                    payload["claimed_at"].replace("Z", "+00:00")
                )
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        if (
            payload.get("state") == "claimed"
            and claimed_at is not None
            and claimed_at <= now + timedelta(seconds=_CLAIM_CLOCK_SKEW_SECONDS)
            and now - claimed_at < timedelta(seconds=CLAIM_SECONDS)
        ):
            return None
        payload.update(
            state="claimed",
            claim_token=secrets.token_hex(16),
            claimed_at=now.isoformat(),
        )
        counters[SUCCESSOR_KEY] = payload
        run.counters = counters
        db.commit()
        return payload
    except Exception:
        logger.exception("scoring successor claim failed run_id=%s", run_id)
        db.rollback()
        return None
    finally:
        db.close()


def _mutate_claim(
    run_id: int | None,
    *,
    role_id: int,
    organization_id: int,
    queue_id: str,
    claim_token: str,
    consume: bool,
) -> bool:
    if not run_id or not queue_id or not claim_token:
        return False
    db = SessionLocal()
    try:
        run = (
            _run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return False
        counters = dict(run.counters or {})
        payload = successor_payload(counters.get(SUCCESSOR_KEY))
        if payload is None or payload.get("queue_id") != queue_id:
            return False
        if payload.get("claim_token") != claim_token:
            return False
        if consume:
            counters.pop(SUCCESSOR_KEY, None)
            counters["last_started_successor"] = {
                "queue_id": queue_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            payload.pop("claim_token", None)
            payload.pop("claimed_at", None)
            payload["state"] = "pending"
            payload["dispatch_attempt"] = int(payload.get("dispatch_attempt") or 0) + 1
            counters[SUCCESSOR_KEY] = payload
        run.counters = counters
        db.commit()
        return True
    except Exception:
        logger.exception("scoring successor mutation failed run_id=%s", run_id)
        db.rollback()
        return False
    finally:
        db.close()


def complete_scoring_successor(run_id: int | None, **scope: Any) -> bool:
    return _mutate_claim(run_id, consume=True, **scope)


def release_scoring_successor(run_id: int | None, **scope: Any) -> bool:
    return _mutate_claim(run_id, consume=False, **scope)


def clear_scoring_successor(
    run_id: int | None,
    *,
    role_id: int,
    organization_id: int,
) -> bool:
    if not run_id:
        return False
    db = SessionLocal()
    try:
        run = (
            _run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return False
        counters = dict(run.counters or {})
        counters.pop(SUCCESSOR_KEY, None)
        run.counters = counters
        db.commit()
        return True
    except Exception:
        logger.exception("scoring successor clear failed run_id=%s", run_id)
        db.rollback()
        return False
    finally:
        db.close()


def find_scoring_successor_child(
    dispatch_key: str,
    *,
    role_id: int,
    organization_id: int,
    queue_id: str,
) -> int | None:
    """Resolve only the child created for this exact scoped queue receipt."""

    if not dispatch_key or not queue_id:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.dispatch_key == dispatch_key,
                BackgroundJobRun.kind == JOB_KIND_SCORING_BATCH,
                BackgroundJobRun.scope_kind == SCOPE_KIND_ROLE,
                BackgroundJobRun.scope_id == int(role_id),
                BackgroundJobRun.organization_id == int(organization_id),
            )
            .one_or_none()
        )
        if row is None:
            return None
        if dict(row.counters or {}).get("successor_queue_id") != queue_id:
            return None
        return int(row.id)
    except Exception:
        logger.exception("scoring successor child lookup failed key=%s", dispatch_key)
        return None
    finally:
        db.close()


def settle_ambiguous_successor_create(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    queue_id: str,
    claim_token: str,
    dispatch_key: str,
    target_application_ids: list[int],
) -> dict[str, Any]:
    """Converge an unconfirmed create without ever changing its stable key."""

    state = "unknown"
    child_id = None
    db = SessionLocal()
    try:
        child = (
            db.query(BackgroundJobRun)
            .filter(BackgroundJobRun.dispatch_key == dispatch_key)
            .one_or_none()
        )
        if child is None:
            state = "absent"
        elif (
            child.kind == JOB_KIND_SCORING_BATCH
            and child.scope_kind == SCOPE_KIND_ROLE
            and int(child.scope_id) == int(role_id)
            and int(child.organization_id) == int(organization_id)
            and dict(child.counters or {}).get("successor_queue_id") == queue_id
        ):
            state = "exact"
            child_id = int(child.id)
        else:
            state = "conflict"
    except Exception:
        logger.exception("scoring successor child lookup failed key=%s", dispatch_key)
    finally:
        db.close()

    scope = {
        "role_id": role_id,
        "organization_id": organization_id,
        "queue_id": queue_id,
        "claim_token": claim_token,
    }
    if state == "exact":
        complete_scoring_successor(run_id, **scope)
        return {
            "outcome": "deduplicated",
            "run_id": child_id,
            "target_application_ids": target_application_ids,
        }
    if state == "conflict":
        if _quarantine_scoring_successor_claim(
            run_id,
            dispatch_key=dispatch_key,
            reason="successor_dispatch_key_conflict",
            **scope,
        ):
            return {
                "outcome": "invalid",
                "reason": "successor_dispatch_key_conflict",
                "target_application_ids": target_application_ids,
            }
    release_scoring_successor(run_id, **scope)
    return {"outcome": "released", "target_application_ids": target_application_ids}


def _quarantine_scoring_successor_claim(
    run_id: int,
    *,
    role_id: int,
    organization_id: int,
    queue_id: str,
    claim_token: str,
    dispatch_key: str,
    reason: str,
) -> bool:
    db = SessionLocal()
    try:
        run = (
            _run_query(
                db,
                run_id=run_id,
                role_id=role_id,
                organization_id=organization_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if run is None:
            return False
        counters = dict(run.counters or {})
        payload = successor_payload(counters.get(SUCCESSOR_KEY))
        if (
            payload is None
            or payload.get("queue_id") != queue_id
            or payload.get("claim_token") != claim_token
        ):
            return False
        counters.pop(SUCCESSOR_KEY, None)
        counters["quarantined_scoring_successor"] = {
            "payload": payload,
            "dispatch_key": dispatch_key,
            "reason": reason,
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
        }
        run.counters = counters
        db.commit()
        return True
    except Exception:
        logger.exception("scoring successor quarantine failed run_id=%s", run_id)
        db.rollback()
        return False
    finally:
        db.close()


__all__ = [
    "claim_scoring_successor",
    "clear_scoring_successor",
    "complete_scoring_successor",
    "find_scoring_successor_child",
    "QUEUE_CONTRACT",
    "queue_scoring_successor",
    "release_scoring_successor",
    "settle_ambiguous_successor_create",
    "successor_payload",
]
