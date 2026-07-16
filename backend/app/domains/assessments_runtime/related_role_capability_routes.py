"""Versioned capability handshake for durable related-role ATS transitions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...models.role import ROLE_KIND_SISTER
from ...models.user import User
from ...platform.database import get_db
from ...schemas.role import ApplicationResponse, WorkableMoveStageRequest
from .job_authorization import JobPermission, require_job_permission


router = APIRouter(tags=["Related-role ATS capability"])


@router.get("/roles/{role_id}/related-ats-transition-capability")
def related_ats_transition_capability(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Prove this backend owns provider confirmation and local projection.

    The frontend calls this before sending a shared-ATS move for a related
    role. Older rolling-deploy backends return 404, so the browser can fail
    before mutating the provider instead of relying on a non-durable local
    follow-up after polling.
    """

    role = require_job_permission(
        db,
        current_user=current_user,
        role_id=role_id,
        permission=JobPermission.EDIT_ROLE,
        lock_for_update=False,
    )
    if (
        str(role.role_kind or "") != ROLE_KIND_SISTER
        or role.ats_owner_role_id is None
    ):
        raise HTTPException(status_code=409, detail="Role is not a related role")
    return {
        "protocol_version": 1,
        "provider_confirmation_managed": True,
        "related_stage_projection_managed": True,
    }


@router.post(
    "/roles/{role_id}/applications/{application_id}/ats/managed-move-stage-v1",
    response_model=ApplicationResponse,
)
def managed_related_ats_move(
    role_id: int,
    application_id: int,
    data: WorkableMoveStageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApplicationResponse:
    """Queue an ATS move whose related projection is owned by the worker.

    This versioned path deliberately does not exist on older backends. During
    a rolling deploy, a request that reaches an old instance therefore fails
    before it can mutate the provider or strand a browser-owned follow-up.
    """

    if data.acting_role_id is not None and int(data.acting_role_id) != int(role_id):
        raise HTTPException(
            status_code=422,
            detail="acting_role_id must match the related role in the route",
        )
    payload = data.model_copy(update={"acting_role_id": int(role_id)})
    from .applications_routes import move_application_in_active_ats

    response = move_application_in_active_ats(
        application_id=application_id,
        data=payload,
        db=db,
        current_user=current_user,
    )
    response.ats_related_transition_protocol = 1
    response.ats_related_stage_managed = True
    return response


__all__ = ["router"]
