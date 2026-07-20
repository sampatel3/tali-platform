"""Retired compatibility action for standalone Workable notes.

Structured movement and decision workflows use their own provider write-back
paths. Keeping this former public action as a no-write boundary makes stale
callers fail closed without affecting those workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..services.ats_note_policy import STANDALONE_ATS_NOTES_DISABLED_MESSAGE
from .types import Actor


@dataclass(frozen=True)
class PostWorkableNoteResult:
    application_id: int
    status: str  # "posted" | "skipped" | "failed"
    detail: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "application_id": self.application_id,
            "status": self.status,
            "detail": self.detail,
        }


def run(
    db: Session,
    actor: Actor,
    *,
    organization_id: int,
    application_id: int,
    body: str,
) -> PostWorkableNoteResult:
    return PostWorkableNoteResult(
        application_id=application_id,
        status="skipped",
        detail=STANDALONE_ATS_NOTES_DISABLED_MESSAGE,
    )
