"""Durable pre-publish intents for recruiter-confirmed manual agent runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from ..models.agent_run import AGENT_RUN_DISPATCHING, AgentRun
from ..models.role import Role

_APPLICATION_ID_KEY = "dispatch_application_id"
_ATTEMPTS_KEY = "dispatch_attempts"
_NEXT_ATTEMPT_AT_KEY = "dispatch_next_attempt_at"
MANUAL_RUN_PUBLISH_RETRY = timedelta(minutes=2)
_MAX_PUBLISH_RETRY = timedelta(minutes=30)
logger = logging.getLogger("taali.agent_chat.manual_run_dispatch")


class ManualRunDispatchConflict(RuntimeError):
    """A dispatch key was reused outside its original security scope."""


@dataclass(frozen=True)
class ManualRunIntent:
    run: AgentRun
    application_id: int | None


def _normalise_application_id(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def intent_application_id(run: AgentRun) -> int | None:
    snapshot = run.agent_state_snapshot if isinstance(run.agent_state_snapshot, dict) else {}
    return _normalise_application_id(snapshot.get(_APPLICATION_ID_KEY))


def with_dispatch_metadata(
    snapshot: dict[str, Any] | None,
    *,
    application_id: int | None,
) -> dict[str, Any]:
    """Preserve recovery identity alongside the run's calibration snapshot."""

    result = dict(snapshot or {})
    result[_APPLICATION_ID_KEY] = _normalise_application_id(application_id)
    return result


def ensure_manual_run_intent(
    db: Session,
    *,
    role: Role,
    application_id: int | None,
    dispatch_key: str,
) -> ManualRunIntent:
    """Stage or return the one AgentRun row that owns a confirmed dispatch."""

    key = str(dispatch_key or "").strip()
    if not key or len(key) > 200:
        raise ManualRunDispatchConflict("invalid manual-run dispatch key")
    expected_application_id = _normalise_application_id(application_id)
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        # The intent may not exist yet. Serialise first creation independently
        # of the unique index so a concurrent replay can deterministically read
        # the winner instead of surfacing an IntegrityError.
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('manual_agent_run_dispatch'), hashtext(:dispatch_key))"
            ),
            {"dispatch_key": key},
        )
    run = db.query(AgentRun).filter(AgentRun.dispatch_key == key).one_or_none()
    if run is None:
        run = AgentRun(
            organization_id=int(role.organization_id),
            role_id=int(role.id),
            trigger="manual",
            dispatch_key=key,
            status=AGENT_RUN_DISPATCHING,
            agent_state_snapshot={_APPLICATION_ID_KEY: expected_application_id},
        )
        db.add(run)
        db.flush()
    actual_application_id = intent_application_id(run)
    if (
        int(run.organization_id) != int(role.organization_id)
        or int(run.role_id) != int(role.id)
        or str(run.trigger) != "manual"
        or actual_application_id != expected_application_id
    ):
        raise ManualRunDispatchConflict("manual-run dispatch scope mismatch")
    return ManualRunIntent(run=run, application_id=actual_application_id)


def dispatch_payload(run: AgentRun) -> dict[str, Any]:
    """Return the exact task payload persisted in a dispatching intent."""

    if str(run.status) != AGENT_RUN_DISPATCHING or not run.dispatch_key:
        raise ManualRunDispatchConflict("agent run is not awaiting dispatch")
    return {
        "organization_id": int(run.organization_id),
        "role_id": int(run.role_id),
        "application_id": intent_application_id(run),
        "dispatch_key": str(run.dispatch_key),
    }


def claim_publish(
    run: AgentRun,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Advance a dispatching intent's retry bound and return its task payload.

    The caller must hold the row/advisory lock and commit before publishing.
    This deliberately reserves the next window before broker I/O: ambiguous
    acceptance and multi-Beat races can create at most one delivery per bound.
    """

    if str(run.status) != AGENT_RUN_DISPATCHING:
        return None
    current = _as_utc(now or datetime.now(timezone.utc))
    snapshot = dict(
        run.agent_state_snapshot
        if isinstance(run.agent_state_snapshot, dict)
        else {}
    )
    next_attempt_at = _parse_datetime(snapshot.get(_NEXT_ATTEMPT_AT_KEY))
    if next_attempt_at is not None and next_attempt_at > current:
        return None
    attempt = int(snapshot.get(_ATTEMPTS_KEY) or 0) + 1
    snapshot[_ATTEMPTS_KEY] = attempt
    retry_seconds = min(
        int(MANUAL_RUN_PUBLISH_RETRY.total_seconds())
        * (2 ** min(max(0, attempt - 1), 4)),
        int(_MAX_PUBLISH_RETRY.total_seconds()),
    )
    snapshot[_NEXT_ATTEMPT_AT_KEY] = (
        current + timedelta(seconds=retry_seconds)
    ).isoformat()
    run.agent_state_snapshot = snapshot
    return dispatch_payload(run)


def publish_due_filter(*, now: datetime | None = None):
    """Portable SQL predicate for dispatching intents whose retry is due."""

    current = _as_utc(now or datetime.now(timezone.utc)).isoformat()
    next_attempt = AgentRun.agent_state_snapshot[_NEXT_ATTEMPT_AT_KEY].as_string()
    return or_(next_attempt.is_(None), next_attempt <= current)


def manual_run_role_block_reason(db: Session, *, role: Role | None) -> str | None:
    """Return the stable reason a manual-run worker must refuse current work.

    HTTP and Agent Chat check the same switches before publishing, but a
    queued delivery can outlive any of them.  Workers and the recovery outbox
    therefore re-read this fail-closed baseline immediately before dispatch.
    """

    if role is None or getattr(role, "deleted_at", None) is not None:
        return "role_not_found"
    if not bool(getattr(role, "agentic_mode_enabled", False)):
        return "agent_disabled"

    from .workspace_agent_control import workspace_agent_is_paused

    if workspace_agent_is_paused(
        db,
        organization_id=int(role.organization_id),
    ):
        return "workspace_paused"
    if getattr(role, "agent_paused_at", None) is not None:
        return "agent_paused"
    return None


def manual_run_intent_for_scope(
    db: Session,
    *,
    dispatch_key: str | None,
    organization_id: int,
    role_id: int,
    application_id: int | None,
    lock: bool = False,
) -> AgentRun | None:
    """Resolve one receipt only when every durable authority field matches."""

    key = str(dispatch_key or "").strip()
    if not key:
        return None
    try:
        expected_organization_id = int(organization_id)
        expected_role_id = int(role_id)
        expected_application_id = _normalise_application_id(application_id)
    except (TypeError, ValueError):
        return None
    query = db.query(AgentRun).filter(AgentRun.dispatch_key == key)
    if lock:
        query = query.with_for_update()
    intent = query.one_or_none()
    if intent is None:
        return None
    if (
        int(intent.organization_id) != expected_organization_id
        or int(intent.role_id) != expected_role_id
        or str(intent.trigger) != "manual"
        or intent_application_id(intent) != expected_application_id
    ):
        return None
    return intent


def finish_manual_run_intent(
    db: Session,
    *,
    dispatch_key: str | None,
    organization_id: int,
    role_id: int,
    application_id: int | None,
    status: str,
    error: str | None = None,
) -> bool:
    """Finish one still-dispatching receipt without overwriting a newer result."""

    if status not in {"succeeded", "failed", "aborted"}:
        raise ValueError("manual-run terminal status is invalid")
    intent = manual_run_intent_for_scope(
        db,
        dispatch_key=dispatch_key,
        organization_id=organization_id,
        role_id=role_id,
        application_id=application_id,
        lock=True,
    )
    if intent is None or str(intent.status) != AGENT_RUN_DISPATCHING:
        return False
    intent.status = status
    intent.error = error
    intent.finished_at = datetime.now(timezone.utc)
    return True


def publish_manual_run(
    *,
    role: Role,
    application_id: int | None,
    dispatch_key: str | None,
) -> dict[str, Any]:
    """Persist a keyed intent, reserve one publish window, then kick Celery."""

    from .role_agent_dispatch import dispatch_role_agent_cycle

    task_kwargs: dict[str, Any] = {
        "role_id": int(role.id),
        "application_id": _normalise_application_id(application_id),
    }
    publish_role = role
    intent_id = None
    key = str(dispatch_key or "").strip() or None
    if key is not None:
        from ..platform.database import SessionLocal

        with SessionLocal() as dispatch_db:
            dispatch_role = dispatch_db.get(Role, int(role.id))
            if dispatch_role is None:
                raise ManualRunDispatchConflict("manual-run role no longer exists")
            intent = ensure_manual_run_intent(
                dispatch_db,
                role=dispatch_role,
                application_id=application_id,
                dispatch_key=key,
            )
            intent_status = str(intent.run.status)
            intent_id = int(intent.run.id)
            claimed_payload = claim_publish(intent.run)
            dispatch_db.commit()
        if intent_status != AGENT_RUN_DISPATCHING:
            return {
                "type": "manual_agent_run",
                "status": intent_status,
                # This request replayed a terminal receipt; it did not publish
                # another broker message.
                "queued": False,
                "broker_accepted": None,
                "dispatch_pending": False,
                "intent_persisted": True,
                "replayed": True,
                "role_id": int(role.id),
                "application_id": _normalise_application_id(application_id),
                "agent_run_id": intent_id,
                "task_id": None,
            }
        if claimed_payload is None:
            return {
                "type": "manual_agent_run",
                "status": "queued",
                # A prior request owns the current publish window. It may have
                # reached the broker or may be awaiting the recovery sweep, so
                # never claim broker acceptance on this replay.
                "queued": False,
                "broker_accepted": None,
                "dispatch_pending": True,
                "intent_persisted": True,
                "replayed": True,
                "role_id": int(role.id),
                "application_id": _normalise_application_id(application_id),
                "agent_run_id": intent_id,
                "task_id": None,
            }
        task_kwargs = claimed_payload
    try:
        async_result = dispatch_role_agent_cycle(
            publish_role,
            manual=True,
            application_id=_normalise_application_id(task_kwargs.get("application_id")),
            dispatch_key=str(task_kwargs.get("dispatch_key") or "").strip() or None,
        )
    except Exception:
        if key is None:
            raise
        logger.exception(
            "manual agent run publish failed; durable recovery owns dispatch_key=%s",
            key,
        )
        return {
            "type": "manual_agent_run",
            "status": "dispatch_pending",
            "queued": False,
            "broker_accepted": False,
            "dispatch_pending": True,
            "intent_persisted": True,
            "replayed": False,
            "role_id": int(role.id),
            "application_id": _normalise_application_id(application_id),
            "agent_run_id": intent_id,
            "task_id": None,
        }
    raw_task_id = getattr(async_result, "id", None)
    return {
        "type": "manual_agent_run",
        "status": "queued",
        "queued": True,
        "broker_accepted": True,
        "dispatch_pending": False,
        "intent_persisted": key is not None,
        "replayed": False,
        "role_id": int(role.id),
        "application_id": _normalise_application_id(application_id),
        "agent_run_id": intent_id,
        "task_id": str(raw_task_id) if raw_task_id is not None else None,
    }


__all__ = [
    "ManualRunDispatchConflict",
    "ManualRunIntent",
    "MANUAL_RUN_PUBLISH_RETRY",
    "claim_publish",
    "dispatch_payload",
    "ensure_manual_run_intent",
    "finish_manual_run_intent",
    "intent_application_id",
    "manual_run_intent_for_scope",
    "manual_run_role_block_reason",
    "publish_manual_run",
    "publish_due_filter",
    "with_dispatch_metadata",
]
