"""End-to-end writeback handler — Phase 6 §7 of the writeback patterns.

Called from ``teach_decision.run`` (and ``override_decision.run`` once
overrides also accept hints). Validates each hint, commits or queues
according to sensitivity, and returns a structured report.

Failures never roll back the source ``decision_feedback`` row — the
graph is a derived view of Postgres-of-truth.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from ..models.decision_feedback import DecisionFeedback
from ..models.graph_writeback import GraphWritebackQueueItem
from ..candidate_graph import client as graph_client
from ..candidate_graph import agent_episodes
from .contracts import GraphWriteHint, ValidationResult, WritebackReport
from .sensitivity import classify_hint, load_blocklist


logger = logging.getLogger("taali.graph_writeback.pipeline")


def _write_feedback_episode(feedback: DecisionFeedback) -> str | None:
    """Anchor episode for the writeback. Returns episode uuid or None."""
    if not graph_client.is_configured():
        return None
    try:
        ok = agent_episodes.emit_recruiter_action_event(
            organization_id=int(feedback.organization_id),
            decision_id=int(feedback.decision_id),
            recruiter_id=int(feedback.reviewer_id),
            action="teach",
            reason=feedback.correction_text,
            happened_at=feedback.created_at or datetime.now(timezone.utc),
        )
        if ok:
            return f"teach:{feedback.id}"
    except Exception as exc:
        logger.warning("feedback episode emit failed: %s", exc)
    return None


def _coerce_hint(raw: dict | GraphWriteHint) -> GraphWriteHint | None:
    """Best-effort coercion from a raw dict (as stored on
    ``decision_feedback.graph_write_hints``) to the Pydantic hint.

    Returns None on validation failure — the pipeline logs the issue
    and skips the hint, but doesn't fail the whole writeback.
    """
    if isinstance(raw, GraphWriteHint):
        return raw
    try:
        return GraphWriteHint.model_validate(raw)
    except Exception as exc:
        logger.warning("hint validation failed: %s\nhint=%r", exc, raw)
        return None


def _commit_low_risk_hint(
    hint: GraphWriteHint,
    *,
    feedback: DecisionFeedback,
    episode_uuid: str | None,
) -> bool:
    """Auto-commit a low-risk hint to the graph.

    The graph path goes through the Graphiti driver if configured;
    when not configured, the hint is logged to the audit trail (queue
    row with status=committed and committed_at set) so the Postgres
    side stays consistent for replay.
    """
    # We DO NOT issue raw Cypher MERGE statements from feedback hints
    # in this pre-pilot rollout. Instead the writeback pipeline emits
    # a structured episode that Graphiti's extractor turns into the
    # right edges. This preserves the bi-temporal semantics from
    # graph_writeback_patterns.md §2 without us hand-rolling each
    # MERGE — same merging machinery as the rest of the codebase.
    if not graph_client.is_configured():
        return True  # treated as committed for audit purposes
    try:
        # Compose a tiny narrative episode describing the assertion.
        from ..candidate_graph.episodes import Episode, dispatch
        from ..candidate_graph import schema as gs
        group_id = graph_client.group_id_for_org(int(feedback.organization_id))
        body = "\n".join(
            [
                f"Recruiter {feedback.reviewer_id} asserted (via teach feedback "
                f"id={feedback.id}, episode={episode_uuid}):",
                f"  action={hint.action} edge_type={hint.edge_type} "
                f"from={hint.from_node_id} to={hint.to_node_id} "
                f"confidence={hint.confidence:.2f}",
                f"Rationale: {hint.rationale}",
            ]
        )
        episode = Episode(
            name=f"writeback-{feedback.id}-{hint.action}-{hint.edge_type}",
            body=body,
            source_description=f"{gs.EPISODE_SOURCE_FEEDBACK}.{hint.action}",
            reference_time=feedback.created_at or datetime.now(timezone.utc),
            group_id=group_id,
        )
        dispatch([episode])
        return True
    except Exception as exc:
        logger.warning("low-risk hint commit failed: %s", exc)
        return False


def write_back_from_feedback(
    db: Session,
    *,
    feedback: DecisionFeedback,
    proposing_user_id: int,
) -> WritebackReport:
    """Apply the hints on a feedback row.

    Reads from ``feedback.graph_write_hints``; writes auto-committed
    hints to the graph and queues medium-sensitivity ones in
    ``graph_writeback_queue``.
    """
    report = WritebackReport()
    hints_raw = feedback.graph_write_hints or []
    if not hints_raw:
        return report

    episode_uuid = _write_feedback_episode(feedback)
    report.feedback_episode_uuid = episode_uuid
    blocklist = load_blocklist()

    for raw in hints_raw:
        hint = _coerce_hint(raw)
        if hint is None:
            report.blocked.append((GraphWriteHint.model_construct(**(raw or {})), "schema_invalid"))
            continue
        result = classify_hint(hint, blocklist=blocklist)
        if not result.accepted:
            report.blocked.append((hint, result.reason or "rejected"))
            _persist_blocked(db, feedback=feedback, hint=hint, reason=result.reason or "rejected",
                             proposing_user_id=proposing_user_id, episode_uuid=episode_uuid)
            continue
        if result.sensitivity == "high":
            report.blocked.append((hint, "protected_attribute"))
            _persist_blocked(db, feedback=feedback, hint=hint, reason="protected_attribute",
                             proposing_user_id=proposing_user_id, episode_uuid=episode_uuid)
            continue
        if result.sensitivity == "medium":
            _persist_queued(db, feedback=feedback, hint=hint,
                            proposing_user_id=proposing_user_id, episode_uuid=episode_uuid)
            report.queued_for_cosign.append(hint)
            continue
        # low
        ok = _commit_low_risk_hint(hint, feedback=feedback, episode_uuid=episode_uuid)
        if ok:
            _persist_committed(db, feedback=feedback, hint=hint,
                               proposing_user_id=proposing_user_id, episode_uuid=episode_uuid)
            report.auto_committed.append(hint)
        else:
            report.blocked.append((hint, "commit_failed"))

    return report


# ---------------------------------------------------------------------------
# Row persistence helpers
# ---------------------------------------------------------------------------


def _persist_committed(
    db: Session, *,
    feedback: DecisionFeedback, hint: GraphWriteHint,
    proposing_user_id: int, episode_uuid: str | None,
) -> None:
    row = GraphWritebackQueueItem(
        organization_id=int(feedback.organization_id),
        source_feedback_id=int(feedback.id),
        hint_json=hint.model_dump(),
        sensitivity="low",
        status="committed",
        proposed_by_user_id=int(proposing_user_id),
        committed_at=datetime.now(timezone.utc),
        feedback_episode_uuid=episode_uuid,
    )
    db.add(row); db.flush()


def _persist_queued(
    db: Session, *,
    feedback: DecisionFeedback, hint: GraphWriteHint,
    proposing_user_id: int, episode_uuid: str | None,
) -> None:
    row = GraphWritebackQueueItem(
        organization_id=int(feedback.organization_id),
        source_feedback_id=int(feedback.id),
        hint_json=hint.model_dump(),
        sensitivity="medium",
        status="pending_cosign",
        proposed_by_user_id=int(proposing_user_id),
        feedback_episode_uuid=episode_uuid,
    )
    db.add(row); db.flush()


def _persist_blocked(
    db: Session, *,
    feedback: DecisionFeedback, hint: GraphWriteHint, reason: str,
    proposing_user_id: int, episode_uuid: str | None,
) -> None:
    row = GraphWritebackQueueItem(
        organization_id=int(feedback.organization_id),
        source_feedback_id=int(feedback.id),
        hint_json=hint.model_dump(),
        sensitivity="high",
        status="blocked",
        blocked_reason=reason,
        proposed_by_user_id=int(proposing_user_id),
        feedback_episode_uuid=episode_uuid,
    )
    db.add(row); db.flush()


# ---------------------------------------------------------------------------
# Co-sign + commit helpers (admin dashboard calls these)
# ---------------------------------------------------------------------------


def cosign_pending(
    db: Session,
    *,
    item: GraphWritebackQueueItem,
    cosigner_user_id: int,
    cosign_note: str | None = None,
) -> bool:
    """Co-sign a pending queue row, commit it to the graph, mark committed.

    Refuses self-cosign (proposer == cosigner) and double-cosign.
    """
    if item.status != "pending_cosign":
        return False
    if int(item.proposed_by_user_id) == int(cosigner_user_id):
        return False
    hint = GraphWriteHint.model_validate(item.hint_json or {})
    item.cosigned_by_user_id = int(cosigner_user_id)
    item.cosigned_at = datetime.now(timezone.utc)
    item.cosign_note = cosign_note
    # For medium-risk hints we still go through the episode-based commit
    # path; the queue row becomes the audit trail.
    fake_feedback = type("_F", (), {
        "id": item.source_feedback_id,
        "reviewer_id": item.proposed_by_user_id,
        "organization_id": item.organization_id,
        "correction_text": cosign_note or "",
        "created_at": item.cosigned_at,
    })()
    ok = _commit_low_risk_hint(hint, feedback=fake_feedback, episode_uuid=item.feedback_episode_uuid)
    if ok:
        item.status = "committed"
        item.committed_at = datetime.now(timezone.utc)
    db.flush()
    return ok


def reject_pending(
    db: Session,
    *,
    item: GraphWritebackQueueItem,
    cosigner_user_id: int,
    reason: str,
) -> None:
    if item.status != "pending_cosign":
        return
    item.status = "rejected"
    item.cosigned_by_user_id = int(cosigner_user_id)
    item.cosigned_at = datetime.now(timezone.utc)
    item.rejection_reason = reason
    db.flush()


__all__ = [
    "cosign_pending",
    "reject_pending",
    "write_back_from_feedback",
]
