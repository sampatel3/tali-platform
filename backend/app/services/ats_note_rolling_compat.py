"""Narrow old-producer/new-worker compatibility for durable ATS notes."""

from __future__ import annotations

import json
from typing import Literal

from sqlalchemy.orm import Session

from ..models.background_job_run import JOB_KIND_WORKABLE_OP, BackgroundJobRun
from ..platform.config import settings
from ..platform.database import SessionLocal
from ..platform.secrets import encrypt_text
from .background_job_runs import ATS_MAX_DELIVERY_ATTEMPTS
from .ats_note_dispatch import AtsNoteQueueError, prepare_application_ats_note_payload
from .ats_note_dispatch_identity import prepare_note_dispatch_identity
from .ats_note_provider import note_provider_failure

_LEGACY_NOTE_FIELDS = frozenset({"application_id", "user_id", "body"})
_INTERNAL_FIELDS = frozenset({"_job_run_id", "_should_yield"})


def prepare_post_note_runtime_payload(
    db: Session,
    *,
    organization_id: int,
    payload: dict,
) -> tuple[dict, bool]:
    """Upgrade only the exact legacy Workable broker payload shape."""

    public_fields = frozenset(payload).difference(_INTERNAL_FIELDS)
    is_legacy = public_fields == _LEGACY_NOTE_FIELDS
    prepared = dict(payload)
    if is_legacy:
        job_run_id = payload.get("_job_run_id")
        if (
            isinstance(job_run_id, bool)
            or not isinstance(job_run_id, int)
            or job_run_id <= 0
        ):
            raise note_provider_failure(
                "invalid_note_operation",
                "Legacy ATS note delivery has no durable job identity",
            )
        try:
            prepared = prepare_application_ats_note_payload(
                db,
                organization_id=int(organization_id),
                application_id=int(payload.get("application_id") or 0),
                body=str(payload.get("body") or ""),
                provider="workable",
                actor_type="recruiter",
                actor_id=(
                    int(payload["user_id"])
                    if payload.get("user_id") is not None
                    else None
                ),
            )
        except AtsNoteQueueError as exc:
            raise note_provider_failure(exc.code, exc.message) from None
        except (TypeError, ValueError):
            raise note_provider_failure(
                "invalid_note_operation", "Legacy ATS note identity is invalid"
            ) from None
        prepared.update(
            _job_run_id=int(job_run_id),
            note_operation_id=f"legacy-job:{int(job_run_id)}",
        )
    prepared, _operation_id, _identity = prepare_note_dispatch_identity(
        prepared,
        organization_id=int(organization_id),
        dispatch_key=str(prepared.get("note_operation_id") or "") or None,
    )
    return prepared, is_legacy


def claim_legacy_post_note_run(
    *,
    run_id: int,
    organization_id: int,
    payload: dict,
) -> Literal["claimed", "not_claimable", "persistence_failed"]:
    """Atomically add recovery authority and claim one legacy queued run."""

    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        row = (
            db.query(BackgroundJobRun)
            .filter(
                BackgroundJobRun.id == int(run_id),
                BackgroundJobRun.organization_id == int(organization_id),
                BackgroundJobRun.kind == JOB_KIND_WORKABLE_OP,
                BackgroundJobRun.status.in_(("dispatching", "queued")),
                BackgroundJobRun.finished_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            return "not_claimable"
        counters = dict(row.counters or {})
        if str(counters.get("op_type") or "") != "post_note":
            return "not_claimable"
        attempts = int(counters.get("delivery_attempts") or 0)
        if attempts >= ATS_MAX_DELIVERY_ATTEMPTS:
            counters.update(
                code="delivery_attempts_exhausted",
                provider_called=False,
                delivery_attempts=attempts,
            )
            row.counters = counters
            row.status = "failed"
            row.error = "ATS delivery attempt limit exhausted"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
            return "not_claimable"
        counters.update(
            recovery_payload=encrypt_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                settings.SECRET_KEY,
            ),
            note_body_sha256=str(payload.get("note_body_sha256") or ""),
            note_intent_sha256=str(payload.get("note_intent_sha256") or ""),
            note_dispatch_sha256=str(payload.get("note_dispatch_sha256") or ""),
            last_started_at=datetime.now(timezone.utc).isoformat(),
            delivery_attempts=attempts + 1,
            rolling_legacy_payload_upgraded=True,
        )
        row.counters = counters
        row.status = "running"
        db.commit()
        return "claimed"
    except Exception:
        db.rollback()
        return "persistence_failed"
    finally:
        db.close()


def is_unrecoverable_legacy_note(counters: dict) -> bool:
    """Identify an old queued note whose payload exists only in the broker."""

    return bool(
        str(counters.get("op_type") or "") == "post_note"
        and not str(counters.get("recovery_payload") or "").strip()
        and not counters.get("note_intent_sha256")
    )


__all__ = [
    "claim_legacy_post_note_run",
    "is_unrecoverable_legacy_note",
    "prepare_post_note_runtime_payload",
]
