from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...models.candidate_application import CandidateApplication
from ...models.candidate_application_event import CandidateApplicationEvent

PIPELINE_STAGES = ("applied", "invited", "in_assessment", "review")
APPLICATION_OUTCOMES = ("open", "rejected", "withdrawn", "hired")
PIPELINE_STAGE_SOURCES = ("system", "recruiter", "sync")

_RECRUITER_STAGE_TRANSITIONS = {
    ("applied", "invited"),
    ("review", "invited"),
}
_SYSTEM_STAGE_TRANSITIONS = {
    ("invited", "in_assessment"),
    ("in_assessment", "review"),
}
_LEGACY_COMPAT_EDGES: dict[str, list[tuple[str, str]]] = {
    "applied": [("invited", "recruiter")],
    "invited": [("in_assessment", "system")],
    "in_assessment": [("review", "system")],
    "review": [("invited", "recruiter")],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_pipeline_key(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_pipeline_stage(value: str | None) -> str:
    normalized = normalize_pipeline_key(value)
    if normalized not in PIPELINE_STAGES:
        raise HTTPException(status_code=422, detail=f"Unsupported pipeline_stage={value!r}")
    return normalized


def normalize_application_outcome(value: str | None) -> str:
    normalized = normalize_pipeline_key(value)
    if normalized not in APPLICATION_OUTCOMES:
        raise HTTPException(status_code=422, detail=f"Unsupported application_outcome={value!r}")
    return normalized


def normalize_stage_source(value: str | None) -> str:
    normalized = normalize_pipeline_key(value)
    if normalized not in PIPELINE_STAGE_SOURCES:
        raise HTTPException(status_code=422, detail=f"Unsupported pipeline_stage_source={value!r}")
    return normalized


def map_legacy_status_to_pipeline(status: str | None) -> tuple[str, str]:
    key = normalize_pipeline_key(status)
    if key in {"invited", "pending", "assessment_sent"}:
        return "invited", "open"
    if key in {"in_progress", "started"}:
        return "in_assessment", "open"
    if key in {"review", "completed", "completed_due_to_timeout", "scored"}:
        return "review", "open"
    if key in {"rejected", "declined", "disqualified"}:
        return "review", "rejected"
    if key in {"withdrawn"}:
        return "review", "withdrawn"
    if key in {"hired", "offer_accepted"}:
        return "review", "hired"
    return "applied", "open"


def status_from_pipeline(stage: str, outcome: str) -> str:
    normalized_stage = normalize_pipeline_stage(stage)
    normalized_outcome = normalize_application_outcome(outcome)
    if normalized_outcome in {"rejected", "withdrawn", "hired"}:
        return normalized_outcome
    if normalized_stage == "in_assessment":
        return "in_progress"
    return normalized_stage


def _event_to_payload(event: CandidateApplicationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "application_id": event.application_id,
        "organization_id": event.organization_id,
        "event_type": event.event_type,
        "from_stage": event.from_stage,
        "to_stage": event.to_stage,
        "from_outcome": event.from_outcome,
        "to_outcome": event.to_outcome,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "reason": event.reason,
        "metadata": event.event_metadata or {},
        "idempotency_key": event.idempotency_key,
        "created_at": event.created_at,
    }


def list_application_events(
    db: Session,
    *,
    organization_id: int,
    application_id: int,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.organization_id == organization_id,
            CandidateApplicationEvent.application_id == application_id,
        )
        .order_by(CandidateApplicationEvent.created_at.desc(), CandidateApplicationEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_event_to_payload(item) for item in rows]


def stage_external_drift(app: CandidateApplication) -> bool:
    external = normalize_pipeline_key(app.external_stage_normalized or app.external_stage_raw or app.workable_stage)
    if not external:
        return False
    local = normalize_pipeline_key(app.pipeline_stage)
    return bool(local and local != external)


def ensure_pipeline_fields(
    app: CandidateApplication,
    *,
    source: str = "system",
) -> None:
    now = _utcnow()
    normalized_source = normalize_stage_source(source)
    stage = normalize_pipeline_key(app.pipeline_stage)
    outcome = normalize_pipeline_key(app.application_outcome)
    if stage not in PIPELINE_STAGES or outcome not in APPLICATION_OUTCOMES:
        mapped_stage, mapped_outcome = map_legacy_status_to_pipeline(app.status)
        stage = mapped_stage
        outcome = mapped_outcome
    app.pipeline_stage = stage
    app.application_outcome = outcome
    if not app.pipeline_stage_updated_at:
        app.pipeline_stage_updated_at = now
    if not app.application_outcome_updated_at:
        app.application_outcome_updated_at = now
    app.pipeline_stage_source = normalize_stage_source(app.pipeline_stage_source or normalized_source)
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    if app.version is None or app.version < 1:
        app.version = 1


def _guard_stage_transition(*, source: str, from_stage: str, to_stage: str, app: CandidateApplication) -> None:
    if from_stage == to_stage:
        return
    if source == "recruiter":
        if (from_stage, to_stage) not in _RECRUITER_STAGE_TRANSITIONS:
            raise HTTPException(
                status_code=409,
                detail=f"Recruiter transition {from_stage}->{to_stage} is not allowed",
            )
        return
    if source == "system":
        if (from_stage, to_stage) not in _SYSTEM_STAGE_TRANSITIONS:
            raise HTTPException(
                status_code=409,
                detail=f"System transition {from_stage}->{to_stage} is not allowed",
            )
        return
    if source == "sync" and app.version > 1:
        raise HTTPException(
            status_code=409,
            detail="Sync cannot override local pipeline_stage after recruiter/system updates",
        )


def _legacy_compatibility_path(from_stage: str, to_stage: str) -> list[tuple[str, str]] | None:
    start = normalize_pipeline_stage(from_stage)
    target = normalize_pipeline_stage(to_stage)
    if start == target:
        return []

    queue: deque[tuple[str, list[tuple[str, str]]]] = deque([(start, [])])
    visited: set[str] = {start}

    while queue:
        current, path = queue.popleft()
        for next_stage, source in _LEGACY_COMPAT_EDGES.get(current, []):
            step_path = [*path, (next_stage, source)]
            if next_stage == target:
                return step_path
            if next_stage in visited:
                continue
            visited.add(next_stage)
            queue.append((next_stage, step_path))
    return None


def _existing_idempotent_event(
    db: Session,
    *,
    application_id: int,
    idempotency_key: str | None,
) -> CandidateApplicationEvent | None:
    token = str(idempotency_key or "").strip()
    if not token:
        return None
    return (
        db.query(CandidateApplicationEvent)
        .filter(
            CandidateApplicationEvent.application_id == application_id,
            CandidateApplicationEvent.idempotency_key == token,
        )
        .first()
    )


def _append_event(
    db: Session,
    *,
    app: CandidateApplication,
    event_type: str,
    actor_type: str,
    actor_id: int | None = None,
    from_stage: str | None = None,
    to_stage: str | None = None,
    from_outcome: str | None = None,
    to_outcome: str | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> CandidateApplicationEvent:
    event = CandidateApplicationEvent(
        application_id=app.id,
        organization_id=app.organization_id,
        event_type=event_type,
        from_stage=from_stage,
        to_stage=to_stage,
        from_outcome=from_outcome,
        to_outcome=to_outcome,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=(reason or "").strip() or None,
        event_metadata=metadata or None,
        idempotency_key=(str(idempotency_key or "").strip() or None),
    )
    db.add(event)
    return event


def initialize_pipeline_event_if_missing(
    db: Session,
    *,
    app: CandidateApplication,
    actor_type: str = "system",
    actor_id: int | None = None,
    reason: str | None = None,
) -> None:
    ensure_pipeline_fields(app)
    existing = (
        db.query(CandidateApplicationEvent.id)
        .filter(
            CandidateApplicationEvent.application_id == app.id,
            CandidateApplicationEvent.event_type == "pipeline_initialized",
        )
        .first()
    )
    if existing:
        return
    _append_event(
        db,
        app=app,
        event_type="pipeline_initialized",
        actor_type=actor_type,
        actor_id=actor_id,
        to_stage=app.pipeline_stage,
        to_outcome=app.application_outcome,
        reason=reason or "Pipeline initialized",
        metadata={"legacy_status": app.status},
    )


def transition_stage(
    db: Session,
    *,
    app: CandidateApplication,
    to_stage: str,
    source: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> CandidateApplication:
    ensure_pipeline_fields(app, source=source)
    source_key = normalize_stage_source(source)
    target = normalize_pipeline_stage(to_stage)
    from_stage = normalize_pipeline_stage(app.pipeline_stage)
    if expected_version is not None and int(expected_version) != int(app.version or 0):
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected={expected_version}, current={app.version}",
        )

    existing_idempotent = _existing_idempotent_event(
        db,
        application_id=app.id,
        idempotency_key=idempotency_key,
    )
    if existing_idempotent:
        return app

    _guard_stage_transition(source=source_key, from_stage=from_stage, to_stage=target, app=app)
    if from_stage == target:
        return app

    now = _utcnow()
    previous_status = app.status
    app.pipeline_stage = target
    app.pipeline_stage_updated_at = now
    app.pipeline_stage_source = source_key
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    app.version = int(app.version or 1) + 1

    _append_event(
        db,
        app=app,
        event_type="pipeline_stage_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=from_stage,
        to_stage=target,
        from_outcome=app.application_outcome,
        to_outcome=app.application_outcome,
        reason=reason,
        metadata={
            "source": source_key,
            "legacy_status_before": previous_status,
            **(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )
    return app


def transition_outcome(
    db: Session,
    *,
    app: CandidateApplication,
    to_outcome: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    expected_version: int | None = None,
) -> CandidateApplication:
    ensure_pipeline_fields(app)
    target = normalize_application_outcome(to_outcome)
    from_outcome = normalize_application_outcome(app.application_outcome)
    if expected_version is not None and int(expected_version) != int(app.version or 0):
        raise HTTPException(
            status_code=409,
            detail=f"Version mismatch: expected={expected_version}, current={app.version}",
        )

    existing_idempotent = _existing_idempotent_event(
        db,
        application_id=app.id,
        idempotency_key=idempotency_key,
    )
    if existing_idempotent:
        return app

    if from_outcome == target:
        return app

    now = _utcnow()
    previous_status = app.status
    app.application_outcome = target
    app.application_outcome_updated_at = now
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    app.version = int(app.version or 1) + 1

    _append_event(
        db,
        app=app,
        event_type="application_outcome_changed",
        actor_type=actor_type,
        actor_id=actor_id,
        from_stage=app.pipeline_stage,
        to_stage=app.pipeline_stage,
        from_outcome=from_outcome,
        to_outcome=target,
        reason=reason,
        metadata={
            "legacy_status_before": previous_status,
            **(metadata or {}),
        },
        idempotency_key=idempotency_key,
    )
    return app


def apply_legacy_status_update(
    db: Session,
    *,
    app: CandidateApplication,
    status: str,
    actor_type: str,
    actor_id: int | None = None,
    reason: str | None = None,
    expected_version: int | None = None,
) -> CandidateApplication:
    target_stage, target_outcome = map_legacy_status_to_pipeline(status)
    current_stage = normalize_pipeline_stage(app.pipeline_stage)
    current_outcome = normalize_application_outcome(app.application_outcome)
    legacy_metadata = {"legacy_status_input": status, "compatibility_mode": True}
    stage_changed = False
    next_expected_version = expected_version

    if target_stage != current_stage:
        path = _legacy_compatibility_path(current_stage, target_stage)
        if path is None:
            raise HTTPException(
                status_code=409,
                detail=f"Legacy status cannot reach stage {target_stage!r} from {current_stage!r}",
            )
        for stage_name, source_name in path:
            transition_stage(
                db,
                app=app,
                to_stage=stage_name,
                source=source_name,
                actor_type=actor_type,
                actor_id=actor_id,
                reason=reason or f"Legacy status update: {status}",
                metadata=legacy_metadata,
                expected_version=next_expected_version,
            )
            stage_changed = True
            next_expected_version = None
    if target_outcome != current_outcome:
        transition_outcome(
            db,
            app=app,
            to_outcome=target_outcome,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason or f"Legacy status update: {status}",
            metadata=legacy_metadata,
            expected_version=next_expected_version if not stage_changed else None,
        )
    app.status = status_from_pipeline(app.pipeline_stage, app.application_outcome)
    return app


def role_pipeline_counts(
    db: Session,
    *,
    organization_id: int,
    role_id: int,
) -> dict[str, int]:
    rows = (
        db.query(
            CandidateApplication.pipeline_stage,
            func.count(CandidateApplication.id),
        )
        .filter(
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
            CandidateApplication.deleted_at.is_(None),
            CandidateApplication.application_outcome == "open",
        )
        .group_by(CandidateApplication.pipeline_stage)
        .all()
    )
    counts = {stage: 0 for stage in PIPELINE_STAGES}
    for stage, total in rows:
        normalized = normalize_pipeline_key(stage)
        if normalized in counts:
            counts[normalized] = int(total or 0)
    return counts
