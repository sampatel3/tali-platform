"""Agent-drafted interview scorecards from a transcript.

Taali is agentic: it removes the toil, never the judgment. An interview
scorecard used to be a blank form the interviewer typed from memory. When a
Fireflies transcript is linked to an interview, the AGENT instead DRAFTS the
scorecard — a recommendation, the 5-Ds dimension ratings, per-competency
notes, and a grounded summary quoting the transcript — and saves it as a
DRAFT (``submitted_at`` NULL). The interviewer then edits and submits it; the
human keeps the verdict. The agent NEVER submits.

The draft is one metered ``generate_structured`` (forced tool-use) call per
(application, interviewer, interview), so re-drafting edits the existing draft
in place and never touches a card the human already submitted.

The pure pieces (``select_draftable_interview``, ``build_scorecard_messages``,
``apply_scorecard_draft``) are unit-tested without an LLM; the single LLM call
goes through ``app.llm.structured.generate_structured`` on the METERED client.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..llm.core import MeteringContext
from ..llm.structured import StructuredResult, generate_structured
from ..models.application_interview import ApplicationInterview
from ..models.interview_feedback import INTERVIEW_RECOMMENDATIONS, InterviewFeedback
from ..models.user import User
from ..platform.config import settings
from .claude_client_resolver import get_metered_client
from .pricing_service import Feature
from .provider_error_evidence import safe_provider_error_code

logger = logging.getLogger("taali.scorecard_draft")

_SCORECARD_FEATURE = Feature.SCORECARD_DRAFT
_MAX_TOKENS = 2000
# Keep the transcript we send under a sane ceiling — an hour of talk is well
# within this, and it caps the per-draft input cost.
_TRANSCRIPT_CHAR_BUDGET = 60000

# The 5-Ds axes the dimension ratings are keyed by (mirrors the interview-
# feedback route's DIMENSION_AXES). Labels are what the agent scores against.
DIMENSION_AXES = ("delegation", "description", "discernment", "diligence", "deliverable")
_DIMENSION_LABELS = {
    "delegation": "Delegation — frames the task, sets direction for others/agents",
    "description": "Description — communicates clearly and precisely",
    "discernment": "Discernment — judgement, prioritisation, knowing what matters",
    "diligence": "Diligence — rigour, follow-through, attention to detail",
    "deliverable": "Deliverable — ships outcomes, quality of what's produced",
}


class SubmittedCardError(RuntimeError):
    """Raised when a draft is requested for a card the human already submitted.

    A submitted card is human-owned and untouchable — the agent must never
    overwrite it, and we raise BEFORE the LLM call so no cost is incurred.
    """


class CompetencyDraft(BaseModel):
    name: str
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = None


class ScorecardDraftExtraction(BaseModel):
    """The structured scorecard the agent drafts from the transcript. All
    optional so a thin transcript yields a thin draft the human completes."""

    overall_recommendation: Optional[str] = None  # strong_yes..strong_no / no_decision
    overall_rating: Optional[int] = Field(default=None, ge=1, le=4)
    dimension_ratings: Optional[dict[str, int]] = None  # keyed by the 5-Ds axes, 1-5
    competencies: Optional[list[CompetencyDraft]] = None
    notes: Optional[str] = None  # grounded summary quoting the transcript


_SYSTEM_PROMPT = (
    "You are Taali's interview-scorecard agent. From an interview transcript, "
    "draft the interviewer's scorecard so they can review and submit it rather "
    "than author it from a blank form.\n\n"
    "Draft: an overall recommendation, an overall 1-4 rating, a 1-5 rating on "
    "each of the 5-Ds axes, a short grounded comment per axis, and overall "
    "notes. GROUND everything in the transcript — quote the candidate's own "
    "words as evidence; never invent a fact the transcript doesn't support. If "
    "the transcript doesn't cover an axis, leave its rating null and say so in "
    "the comment. This is a DRAFT for a human to correct and own — be faithful "
    "and specific, and lean conservative when the evidence is thin.\n\n"
    "overall_recommendation must be one of: strong_yes, yes, neutral, no, "
    "strong_no, or no_decision when the transcript is too thin to lean either "
    "way."
)


def select_draftable_interview(
    app, *, interview_id: Optional[int] = None
) -> Optional[ApplicationInterview]:
    """Pick the interview to draft from. With ``interview_id``, return that
    interview only if it belongs to the app and carries a transcript. Otherwise
    return the most recent transcript-bearing interview, or ``None``."""
    interviews = [iv for iv in (app.interviews or []) if (iv.transcript_text or "").strip()]
    if interview_id is not None:
        for iv in interviews:
            if iv.id == interview_id:
                return iv
        return None
    if not interviews:
        return None

    def _recency(iv: ApplicationInterview):
        return iv.meeting_date or iv.linked_at or iv.created_at

    return sorted(interviews, key=_recency, reverse=True)[0]


def build_scorecard_messages(
    *,
    role_name: Optional[str],
    candidate_name: Optional[str],
    transcript_text: str,
    summary: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """Pure: build (system, messages) for one scorecard-draft pass."""
    transcript = (transcript_text or "").strip()[:_TRANSCRIPT_CHAR_BUDGET]
    axes = "\n".join(f"- {_DIMENSION_LABELS[a]}" for a in DIMENSION_AXES)
    header = (
        f"ROLE: {role_name or 'the role'}\n"
        f"CANDIDATE: {candidate_name or 'the candidate'}\n\n"
        "5-Ds axes to rate (1-5), each grounded in the transcript:\n"
        f"{axes}\n\n"
    )
    if summary:
        header += "INTERVIEW SUMMARY:\n" + str(summary).strip() + "\n\n"
    user = (
        header
        + "INTERVIEW TRANSCRIPT:\n"
        + transcript
        + "\n\nDraft the scorecard from this transcript. Quote the candidate's "
        "words as evidence; leave any axis the transcript doesn't cover null."
    )
    return _SYSTEM_PROMPT, [{"role": "user", "content": user}]


def _clean_dimension_ratings(ratings: Optional[dict[str, int]]) -> Optional[dict[str, int]]:
    """Keep only valid 5-Ds axes with an int 1-5 — the agent occasionally emits
    an off-axis key or an out-of-range score; drop rather than fail the draft."""
    if not ratings:
        return None
    cleaned = {
        axis: score
        for axis, score in ratings.items()
        if axis in DIMENSION_AXES and isinstance(score, int) and 1 <= score <= 5
    }
    return cleaned or None


def existing_scorecard(
    db: Session, *, org_id: int, application_id: int, interviewer_user_id: int, interview_id: Optional[int]
) -> Optional[InterviewFeedback]:
    """The caller's own card for (application, interviewer, interview), if any."""
    interview_filter = (
        InterviewFeedback.interview_id.is_(None)
        if interview_id is None
        else InterviewFeedback.interview_id == interview_id
    )
    return (
        db.query(InterviewFeedback)
        .filter(
            InterviewFeedback.organization_id == org_id,
            InterviewFeedback.application_id == application_id,
            InterviewFeedback.interviewer_user_id == interviewer_user_id,
            interview_filter,
        )
        .first()
    )


def apply_scorecard_draft(
    db: Session,
    *,
    app,
    interview: ApplicationInterview,
    interviewer_user_id: int,
    extraction: ScorecardDraftExtraction,
) -> InterviewFeedback:
    """Fold an extraction into the caller's draft card (upsert, keyed on
    application+interviewer+interview). The card stays a DRAFT (submitted_at
    NULL). A card the human already submitted is untouchable — raise rather
    than overwrite."""
    card = existing_scorecard(
        db,
        org_id=app.organization_id,
        application_id=app.id,
        interviewer_user_id=interviewer_user_id,
        interview_id=interview.id,
    )
    if card is not None and card.submitted_at is not None:
        raise SubmittedCardError("scorecard already submitted")

    rec = extraction.overall_recommendation
    if rec not in INTERVIEW_RECOMMENDATIONS:
        rec = "no_decision"

    if card is None:
        card = InterviewFeedback(
            organization_id=app.organization_id,
            application_id=app.id,
            role_id=app.role_id,
            interviewer_user_id=interviewer_user_id,
            interview_id=interview.id,
            interview_round="interview",
            overall_recommendation=rec,
        )
        db.add(card)
    else:
        card.overall_recommendation = rec

    if extraction.overall_rating is not None:
        card.overall_rating = extraction.overall_rating
    card.dimension_ratings = _clean_dimension_ratings(extraction.dimension_ratings)
    card.competencies = (
        [c.model_dump() for c in extraction.competencies] if extraction.competencies else None
    )
    if extraction.notes is not None:
        card.notes = extraction.notes.strip() or None
    return card


def run_scorecard_draft(
    db: Session,
    *,
    app,
    interview: ApplicationInterview,
    interviewer_user_id: int,
    role_name: Optional[str] = None,
    candidate_name: Optional[str] = None,
    client=None,
    model: Optional[str] = None,
) -> tuple[StructuredResult, Optional[InterviewFeedback]]:
    """Draft the caller's scorecard from ``interview``'s transcript through the
    METERED structured LLM. Returns ``(result, card)``; ``card`` is the saved
    draft when the call succeeded, else ``None``. Raises ``SubmittedCardError``
    (before spending on the LLM) if the human already submitted this card."""
    # Guard the submitted card BEFORE the billable call — no cost on a no-op.
    prior = existing_scorecard(
        db,
        org_id=app.organization_id,
        application_id=app.id,
        interviewer_user_id=interviewer_user_id,
        interview_id=interview.id,
    )
    if prior is not None and prior.submitted_at is not None:
        raise SubmittedCardError("scorecard already submitted")

    if client is None:
        client = get_metered_client(organization_id=app.organization_id)
    resolved_model = model or settings.resolved_claude_model
    system, messages = build_scorecard_messages(
        role_name=role_name,
        candidate_name=candidate_name,
        transcript_text=interview.transcript_text or "",
        summary=interview.summary if isinstance(interview.summary, str) else None,
    )
    result = generate_structured(
        client,
        model=resolved_model,
        system=system,
        messages=messages,
        output_model=ScorecardDraftExtraction,
        metering=MeteringContext(
            feature=_SCORECARD_FEATURE,
            organization_id=app.organization_id,
            role_id=app.role_id,
            entity_id=f"application:{app.id}:interview:{interview.id}",
        ),
        max_tokens=_MAX_TOKENS,
        temperature=0.0,
        use_tool_use=True,
    )
    if result.ok and result.value is not None:
        card = apply_scorecard_draft(
            db,
            app=app,
            interview=interview,
            interviewer_user_id=interviewer_user_id,
            extraction=result.value,
        )
        return result, card
    return result, None


def maybe_autodraft_from_webhook(
    db: Session, *, org, app, interview: ApplicationInterview
) -> Optional[InterviewFeedback]:
    """Flag-gated auto-draft when a Fireflies transcript is matched to an
    interview (path b). DEFAULT OFF — a billable LLM call only fires once the
    org opts in via ``SCORECARD_AUTODRAFT_ENABLED``.

    Attribution: a webhook has no logged-in caller, so the draft is filed under
    the Fireflies meeting owner (``fireflies_owner_email`` → a User in the org),
    who is typically the interviewer. If no such user exists we skip — the
    on-demand endpoint is always available to attribute the draft to whoever
    clicks. One draft per (application, owner, interview): if a card already
    exists we skip, so webhook re-delivery never re-spends. Never raises into
    the webhook and never submits.
    """
    if not getattr(settings, "SCORECARD_AUTODRAFT_ENABLED", False):
        return None
    if not (interview.transcript_text or "").strip():
        return None
    owner_email = str(getattr(org, "fireflies_owner_email", None) or "").strip().lower()
    if not owner_email:
        return None
    owner = (
        db.query(User)
        .filter(User.organization_id == org.id, User.email == owner_email)
        .first()
    )
    if owner is None:
        return None
    # One draft per (application, owner, interview) — skip if a card already
    # exists (draft or submitted) so re-delivery doesn't re-spend or clobber.
    if existing_scorecard(
        db,
        org_id=app.organization_id,
        application_id=app.id,
        interviewer_user_id=owner.id,
        interview_id=interview.id,
    ) is not None:
        return None
    try:
        _, card = run_scorecard_draft(
            db,
            app=app,
            interview=interview,
            interviewer_user_id=owner.id,
            role_name=getattr(getattr(app, "role", None), "name", None),
            candidate_name=(
                getattr(getattr(app, "candidate", None), "full_name", None)
                or getattr(getattr(app, "candidate", None), "email", None)
            ),
        )
        return card
    except Exception as exc:  # pragma: no cover - defensive; must never break the webhook
        logger.warning(
            "scorecard auto-draft failed application_id=%s error_code=%s",
            app.id,
            safe_provider_error_code(exc, operation="scorecard_auto_draft"),
        )
        return None
