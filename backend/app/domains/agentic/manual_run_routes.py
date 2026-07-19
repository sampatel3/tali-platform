"""Durable manual agent-run dispatch endpoint."""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...domains.assessments_runtime.job_authorization import (
    JobPermission,
    require_job_permission,
)
from ...models.user import User
from ...platform.database import get_db
from ...platform.request_context import get_request_id
from ...services.manual_agent_run_dispatch import (
    ManualRunDispatchConflict,
    publish_manual_run,
)
from ...services.manual_run_application_scope import (
    resolve_manual_run_application,
)
from ...services.workspace_agent_control import workspace_agent_pause_state


router = APIRouter()
logger = logging.getLogger("taali.agentic.routes")


class RunNowBody(BaseModel):
    application_id: Optional[int] = Field(default=None, ge=1)
    # Stable across client retries. The conventional Idempotency-Key header is
    # also accepted; supplying both requires an exact match.
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=128)


class RunNowResult(BaseModel):
    role_id: int
    queued: bool
    task_id: Optional[str] = None
    detail: Optional[str] = None
    blocked: bool = False
    pause_scope: Optional[Literal["workspace", "role"]] = None
    status: Optional[str] = None
    application_id: Optional[int] = None
    agent_run_id: Optional[int] = None
    dispatch_pending: bool = False
    replayed: bool = False
    intent_persisted: bool = False
    # True only when this request's Celery publish returned successfully;
    # None means a replay cannot know whether an earlier ambiguous attempt was
    # accepted and therefore must not claim it was queued.
    broker_accepted: Optional[bool] = None
    idempotency_key: Optional[str] = None


def _run_now_dispatch_identity(
    *,
    organization_id: int,
    user_id: int,
    body_key: str | None,
    header_key: str | None,
) -> tuple[str, str | None]:
    """Return a scoped durable key and the explicit client key, if supplied.

    Legacy callers that send neither idempotency field inherit the request id
    installed by middleware. Retrying with the same ``X-Request-ID`` therefore
    replays the same intent; new ordinary clicks still receive distinct ids.
    """

    normalized_body = str(body_key or "").strip() or None
    normalized_header = str(header_key or "").strip() or None
    if header_key is not None and normalized_header is None:
        raise HTTPException(status_code=422, detail="Idempotency-Key cannot be empty")
    if normalized_header is not None and len(normalized_header) > 128:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key must be 128 characters or fewer",
        )
    if normalized_body is not None and normalized_header is not None:
        if normalized_body != normalized_header:
            raise HTTPException(
                status_code=422,
                detail="Body and header idempotency keys must match",
            )
    explicit_key = normalized_header or normalized_body
    request_token = (
        explicit_key or str(get_request_id() or "").strip() or secrets.token_urlsafe(24)
    )
    digest = hashlib.sha256(request_token.encode("utf-8")).hexdigest()
    return (
        f"http-run-now/{int(organization_id)}/{int(user_id)}/{digest}",
        explicit_key,
    )


@router.post(
    "/roles/{role_id}/agent/run-now",
    response_model=RunNowResult,
    response_model_exclude_unset=True,
)
def run_now(
    role_id: int,
    body: RunNowBody = Body(default_factory=RunNowBody),
    idempotency_key_header: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.CONTROL_AGENT,
    )
    if not bool(role.agentic_mode_enabled):
        raise HTTPException(
            status_code=409,
            detail="agent is not enabled for this role",
        )
    workspace_pause = workspace_agent_pause_state(
        db,
        organization_id=int(current_user.organization_id),
        current_user_id=int(current_user.id),
    )
    if bool(workspace_pause["paused"]):
        return RunNowResult(
            role_id=role_id,
            queued=False,
            task_id=None,
            blocked=True,
            pause_scope="workspace",
            detail="agent run blocked while the workspace agent is paused",
        )
    if role.agent_paused_at is not None:
        return RunNowResult(
            role_id=role_id,
            queued=False,
            task_id=None,
            blocked=True,
            pause_scope="role",
            detail=f"agent is paused: {role.agent_paused_reason or 'unspecified'}",
        )

    application_id = (
        int(body.application_id) if body.application_id is not None else None
    )
    if application_id is not None:
        application = resolve_manual_run_application(
            db,
            role=role,
            organization_id=int(current_user.organization_id),
            application_id=application_id,
        )
        if application is None:
            raise HTTPException(
                status_code=404,
                detail="application not found for this role",
            )

    dispatch_key, explicit_key = _run_now_dispatch_identity(
        organization_id=int(current_user.organization_id),
        user_id=int(current_user.id),
        body_key=body.idempotency_key,
        header_key=idempotency_key_header,
    )
    try:
        publish_result = publish_manual_run(
            role=role,
            application_id=application_id,
            dispatch_key=dispatch_key,
        )
    except ManualRunDispatchConflict as exc:
        logger.info(
            "manual run idempotency conflict role_id=%s user_id=%s",
            role_id,
            current_user.id,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MANUAL_RUN_IDEMPOTENCY_CONFLICT",
                "message": "That idempotency key was already used for a different run scope.",
            },
        ) from exc

    dispatch_pending = bool(publish_result.get("dispatch_pending"))
    replayed = bool(publish_result.get("replayed"))
    response: dict[str, Any] = {
        "role_id": int(role.id),
        "application_id": application_id,
        # ``queued`` means this request observed successful broker publication,
        # not merely that a durable intent exists.
        "queued": bool(publish_result.get("broker_accepted") is True),
        "task_id": publish_result.get("task_id"),
        "status": "dispatch_pending"
        if dispatch_pending
        else str(publish_result.get("status") or "queued"),
        "agent_run_id": publish_result.get("agent_run_id"),
        "dispatch_pending": dispatch_pending,
        "replayed": replayed,
        "intent_persisted": bool(publish_result.get("intent_persisted")),
        "broker_accepted": publish_result.get("broker_accepted"),
    }
    if dispatch_pending:
        response["detail"] = (
            "Run request saved; broker dispatch is pending automatic recovery."
        )
    elif replayed:
        response["detail"] = "Existing run request replayed without another publish."
    if explicit_key is not None:
        response["idempotency_key"] = explicit_key

    return RunNowResult(**response)
