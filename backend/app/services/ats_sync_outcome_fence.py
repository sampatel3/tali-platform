"""Pre-mutation fence for inbound ATS updates that imply an outcome."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.candidate_application import CandidateApplication
from .auto_reject_operation_receipt import fence_auto_reject_outcome


def fence_inbound_outcome_before_mutation(
    db: Session,
    app: CandidateApplication,
    outcome: str | None,
) -> None:
    target = str(outcome or "").strip().lower()
    if target and str(app.application_outcome or "open").strip().lower() != target:
        fence_auto_reject_outcome(db, app, target, "sync", None)


__all__ = ["fence_inbound_outcome_before_mutation"]
