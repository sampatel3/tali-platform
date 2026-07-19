"""Application visibility guard for agent-originated assessment sends."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from ..models.role import ROLE_KIND_SISTER, Role


def load_sendable_application(
    db: Session,
    *,
    role: Role,
    application_id: int,
) -> tuple[CandidateApplication | None, dict[str, Any] | None]:
    """Load an application only when the running role owns its send policy."""

    application = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == int(application_id),
            CandidateApplication.organization_id == int(role.organization_id),
        )
        .first()
    )
    if application is None:
        return None, {
            "status": "not_found",
            "application_id": int(application_id),
            "detail": "application not found in this organization",
        }
    shared_with_running_role = bool(
        application.role_id is not None
        and str(getattr(role, "role_kind", "") or "") == ROLE_KIND_SISTER
        and int(getattr(role, "ats_owner_role_id", 0) or 0)
        == int(application.role_id)
    )
    if application.role_id is not None and (
        int(application.role_id) == int(role.id) or shared_with_running_role
    ):
        return application, None
    return None, {
        "status": "wrong_role",
        "application_id": int(application_id),
        "detail": (
            f"application {application_id} belongs to role "
            f"{application.role_id}, not the running role {int(role.id)}; "
            "refusing send to avoid bypassing the other role's HITL "
            "policy and budget/volume caps"
        ),
    }


__all__ = ["load_sendable_application"]
