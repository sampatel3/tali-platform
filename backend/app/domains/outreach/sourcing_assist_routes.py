"""Sourcing search assist API — LinkedIn X-ray strings + paste-a-profile drafts.

Two recruiter-auth endpoints on the role, both mounted under ``/api/v1``:

- ``POST /roles/{role_id}/sourcing-searches`` — deterministic Google X-ray +
  LinkedIn boolean, plus a metered Haiku expansion (fail-open: still 200 with a
  ``warning`` if the LLM call fails).
- ``POST /roles/{role_id}/outreach-draft`` — a personalised first-touch draft
  grounded in a pasted profile. Nothing is persisted; ``profile_text`` is PII.

Everything here produces copy-paste artefacts for the recruiter — NO LinkedIn
API, scraping, or automation.
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...deps import get_current_user
from ...domains.assessments_runtime.role_support import get_role
from ...models.user import User
from ...platform.database import get_db
from ...services.role_budget_gate import can_spend_on_role
from ...services.sourcing_assist_service import (
    build_search_strings,
    draft_outreach,
)

router = APIRouter(tags=["Sourcing assist"])


class OutreachDraftRequest(BaseModel):
    # 8000-char cap enforced at the schema layer → a too-long body 422s before
    # any Claude call.
    profile_text: str = Field(..., min_length=1, max_length=8000)
    tone: str = "warm"
    channel: str = "linkedin"


@router.post("/roles/{role_id}/sourcing-searches")
def create_sourcing_searches(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Build the deterministic X-ray/boolean and the metered refined alternates.

    404 when the role isn't the caller's org's. Fail-open on LLM failure."""
    role = get_role(role_id, current_user.organization_id, db)
    return build_search_strings(db, role)


@router.post("/roles/{role_id}/outreach-draft")
def create_outreach_draft(
    role_id: int,
    payload: OutreachDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Draft a first-touch message grounded in the pasted profile. Nothing is
    persisted; ``profile_text`` is never logged. 404 for a foreign role."""
    role = get_role(role_id, current_user.organization_id, db)
    # The draft IS the product (no deterministic fallback), so a spent role
    # budget is a hard 402 — same convention as candidate_claude_chat_routes.
    if not can_spend_on_role(db, role=role):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"message": "This role's monthly Claude budget has been reached."},
        )
    return draft_outreach(
        db,
        role,
        profile_text=payload.profile_text,
        tone=payload.tone,
        channel=payload.channel,
    )
