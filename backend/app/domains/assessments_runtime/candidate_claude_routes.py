from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ...components.assessments.repository import get_active_assessment, validate_assessment_token
from ...components.assessments.terminal_runtime import terminal_capabilities
from ...platform.database import get_db
from ...schemas.assessment import ClaudeRequest

router = APIRouter()

_CHAT_DISABLED_MESSAGE = (
    "Assessment chat mode is disabled. Assessments are terminal-only (Claude CLI)."
)


def _raise_chat_disabled() -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "message": _CHAT_DISABLED_MESSAGE,
            "requires_terminal": True,
            "terminal_capabilities": terminal_capabilities(),
        },
    )


@router.post("/{assessment_id}/claude")
def chat_with_claude(
    assessment_id: int,
    data: ClaudeRequest,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    _raise_chat_disabled()


@router.post("/{assessment_id}/claude/retry")
def retry_claude_after_outage(
    assessment_id: int,
    x_assessment_token: str = Header(..., description="Assessment access token"),
    db: Session = Depends(get_db),
):
    assessment = get_active_assessment(assessment_id, db)
    validate_assessment_token(assessment, x_assessment_token)
    _raise_chat_disabled()
