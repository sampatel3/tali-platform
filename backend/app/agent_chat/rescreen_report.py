"""Post-re-screen impact report — the "feels instant" completion message.

When the agent changes a constraint (e.g. a salary cap), the authoritative
filter is the LLM re-screen, which runs async. Rather than leave the recruiter
waiting, a background task (``agent_chat_tasks.report_rescreen_impact``) waits
for the role's re-score to settle and then posts a proactive agent message:
how the qualified pool moved, and — if it shrank — the score-threshold lever
to recover volume, ready to apply on one word.

The message is composed deterministically (no LLM): the numbers come straight
from the same impact math the chat tools use, so the report is exact and free.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    MESSAGE_KIND_ACTION,
    MESSAGE_KIND_CHAT,
    AgentConversation,
    AgentConversationMessage,
)
from ..models.candidate_application import CandidateApplication
from ..models.cv_score_job import CvScoreJob
from ..models.role import Role
from . import impact as _impact


# Latest-job-per-app statuses that mean the re-score is still in flight.
_INFLIGHT_STATUSES = ("pending", "running", "stale")


def count_inflight_score_jobs(db: Session, role_id: int) -> int:
    """How many of the role's applications are still re-scoring.

    CvScoreJob rows are append-only (a fresh rescore adds a new row rather
    than mutating the stale one), so we scope to the latest job per app —
    mirrors ``tasks.scoring_tasks.sweep_stale_scores``.
    """
    from sqlalchemy import func

    latest = (
        db.query(
            CvScoreJob.application_id.label("app_id"),
            func.max(CvScoreJob.queued_at).label("max_q"),
        )
        .join(CandidateApplication, CandidateApplication.id == CvScoreJob.application_id)
        .filter(CandidateApplication.role_id == int(role_id))
        .group_by(CvScoreJob.application_id)
        .subquery()
    )
    return int(
        db.query(func.count(CvScoreJob.id))
        .join(
            latest,
            (CvScoreJob.application_id == latest.c.app_id)
            & (CvScoreJob.queued_at == latest.c.max_q),
        )
        .filter(CvScoreJob.status.in_(_INFLIGHT_STATUSES))
        .scalar()
        or 0
    )


def _compose(role_name: str, threshold: float | None, baseline: int, now: int, reco: dict) -> str:
    cut = f"{threshold:.0f}" if isinstance(threshold, (int, float)) else "current"
    if now < baseline:
        dropped = baseline - now
        line = (
            f"Re-screen complete on {role_name}. Candidates clearing your {cut} cut-off went "
            f"from {baseline} to {now} — the tighter requirement filtered {dropped} out."
        )
        add = int(reco.get("projected_additional") or 0)
        rec = reco.get("recommended_threshold")
        if add > 0 and rec is not None:
            names = reco.get("added_sample") or []
            who = (": " + ", ".join(names[:4])) if names else ""
            line += (
                f" If you want volume back, dropping the cut-off from {cut} to {rec:.0f} "
                f"would bring {add} back through{who}. Want me to apply it?"
            )
        return line
    if now > baseline:
        return (
            f"Re-screen complete on {role_name}. Candidates clearing your {cut} cut-off went "
            f"up from {baseline} to {now}."
        )
    return (
        f"Re-screen complete on {role_name}. No change to who clears your {cut} cut-off ({now})."
    )


def post_rescreen_impact(
    db: Session,
    *,
    conversation: AgentConversation,
    role: Role,
    baseline_qualified: int,
) -> AgentConversationMessage:
    """Compose + persist the proactive completion message. Caller commits."""
    rows = _impact.load_open_candidates(db, role)
    threshold = _impact.effective_threshold(db, role)
    above, _below = _impact.split_by_threshold(rows, threshold)
    now_qualified = len(above)

    reco = _impact.recommend_threshold(db, role)
    text = _compose(role.name, threshold, int(baseline_qualified), now_qualified, reco)

    # Attach the recommendation card only when it actually recovers candidates.
    actions: list[dict[str, Any]] = []
    if now_qualified < int(baseline_qualified) and int(reco.get("projected_additional") or 0) > 0:
        actions = [reco]

    msg = AgentConversationMessage(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        role_id=role.id,
        author_role=AUTHOR_ROLE_ASSISTANT,
        kind=MESSAGE_KIND_ACTION if actions else MESSAGE_KIND_CHAT,
        content=[{"type": "text", "text": text}],
        text=text,
        actions=actions or None,
        model=None,
        stop_reason="rescreen_report",
    )
    db.add(msg)
    now = datetime.now(timezone.utc)
    conversation.last_message_at = now
    conversation.updated_at = now
    db.flush()
    return msg


__all__ = ["count_inflight_score_jobs", "post_rescreen_impact"]
