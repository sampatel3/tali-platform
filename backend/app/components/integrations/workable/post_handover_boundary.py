"""Clean transaction boundary before Workable post-handover reconciliation."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ....domains.assessments_runtime.pipeline_service import (
    is_post_handover_workable_stage,
)
from ....models.candidate_application import CandidateApplication
from ....models.role import Role


def prepare_post_handover_reconciliation(
    db: Session,
    *,
    app: CandidateApplication,
    role: Role,
) -> tuple[Role, CandidateApplication]:
    """Release prior App ownership, then reload a clean Role/App context."""
    if not is_post_handover_workable_stage(app.workable_stage):
        return role, app

    application_id = int(app.id)
    role_id = int(role.id)
    organization_id = int(role.organization_id)
    # Candidate/profile upsert already flushed this Application. Committing is
    # safe because the sync upsert is idempotent; any later failure retries it.
    db.commit()
    live_role = (
        db.query(Role)
        .filter(
            Role.id == role_id,
            Role.organization_id == organization_id,
        )
        .populate_existing()
        .one()
    )
    live_app = (
        db.query(CandidateApplication)
        .filter(
            CandidateApplication.id == application_id,
            CandidateApplication.organization_id == organization_id,
            CandidateApplication.role_id == role_id,
        )
        .populate_existing()
        .one()
    )
    return live_role, live_app


__all__ = ["prepare_post_handover_reconciliation"]
