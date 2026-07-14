"""Native-only deterministic pre-screen outcome helpers."""

from __future__ import annotations

from typing import Any

from ..domains.assessments_runtime.pipeline_service import (
    append_application_event,
    ensure_pipeline_fields,
    transition_outcome,
)
from ..models.candidate_application import CandidateApplication
from .pre_screening_service import mark_auto_reject_state


def try_native_careers_reject(
    db,
    *,
    app: CandidateApplication,
    decision: dict[str, Any],
    actor_type: str,
    actor_id: int | None,
) -> dict[str, Any] | None:
    """Reject an opted-in native applicant locally; return None otherwise.

    Native careers applications have no upstream ATS record. This narrow path
    applies only to the deterministic pre-screen decision already authorized by
    the caller; model/full-score/assessment rejects retain their HITL rail.
    """

    if str(getattr(app, "source", "") or "").strip().lower() != "careers":
        return None
    ensure_pipeline_fields(app)
    transition_outcome(
        db,
        app=app,
        to_outcome="rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason="Auto-rejected from deterministic native pre-screen",
    )
    snapshot = decision.get("snapshot") if isinstance(decision.get("snapshot"), dict) else {}
    config = decision.get("config") if isinstance(decision.get("config"), dict) else {}
    append_application_event(
        db,
        app=app,
        event_type="auto_rejected",
        actor_type=actor_type,
        actor_id=actor_id,
        reason=decision.get("reason"),
        metadata={
            "pre_screen_score": snapshot.get("pre_screen_score"),
            "threshold_100": config.get("threshold_100"),
            "workable_synced": False,
            "ats_provider": "standalone",
            "source": "native_public_apply",
        },
    )
    mark_auto_reject_state(
        app,
        state="rejected",
        reason=decision.get("reason"),
        triggered=True,
    )
    return {
        **decision,
        "performed": True,
        "state": "rejected",
        "workable_synced": False,
    }


__all__ = ["try_native_careers_reject"]
