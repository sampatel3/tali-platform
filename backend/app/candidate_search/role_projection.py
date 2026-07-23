"""Safe presentation rules for independent logical-role candidate state."""

from __future__ import annotations

from typing import Any


OWNER_ROLE_JUDGMENT_FIELDS = frozenset(
    {
        "source_role_score",
        "operational_role_id",
        "operational_role_name",
        # Provider scores are judgments about the ATS owner's requisition, not
        # transport metadata. A related role may expose the provider stage in
        # ``ats_context`` as an action restriction, but it must never rank or
        # present candidates using the owner's Workable verdict.
        "workable_score",
        "workable_score_100",
        "workable_score_raw",
        "workable_score_source",
        "pre_screen_recommendation",
        "pre_screen_evidence",
        "auto_reject_state",
        "auto_reject_reason",
        "auto_reject_triggered_at",
        "manual_decision",
        "notes",
        "candidate_interview_kit",
        "screening_pack",
        "tech_interview_pack",
        "screening_interview_summary",
        "tech_interview_summary",
        "interview_evidence_summary",
        "interview_feedback",
        "interviews",
        "workable_comments",
        "workable_questionnaire_answers",
        "workable_activity_log",
    }
)


def strip_owner_role_judgments(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove judgments owned by a physical source/ATS application.

    Related roles may reuse candidate evidence and an optional ATS transport,
    but those links cannot contribute scores, recommendations, interview
    summaries, or automation verdicts to the logical role's public state.
    """

    projected = dict(payload)
    for field in OWNER_ROLE_JUDGMENT_FIELDS:
        projected.pop(field, None)
    return projected


__all__ = ["OWNER_ROLE_JUDGMENT_FIELDS", "strip_owner_role_judgments"]
