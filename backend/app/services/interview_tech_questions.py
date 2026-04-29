"""Glue layer between ``interview_support_service`` and the LLM-driven
tech-stage prompt.

Keeps the helpers (transcript selection, evidence-anchor formatting) +
the call-site wiring out of ``interview_support_service`` so that file
stays under the 500-LOC architecture gate. The deterministic fallbacks
remain inline in ``interview_support_service`` so the LLM is purely
additive.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models.candidate_application import CandidateApplication
from ..platform.config import settings
from .interview_tech_prompt import generate_tech_questions


_EVIDENCE_PREFIX_BY_SOURCE = {
    "cv": "CV § ",
    "transcript": "Screen § ",
    "recruiter": "Recruiter § ",
    "requirement": "Requirement § ",
}


def _ordered_interviews_local(items: list[Any]) -> list[Any]:
    """Local copy of the screen-stage ordering used by interview_support.

    Duplicated to keep this module self-contained. The original lives in
    ``interview_support_service`` and applies the same sort.
    """
    return sorted(
        items,
        key=lambda item: (
            getattr(item, "meeting_date", None)
            or getattr(item, "linked_at", None)
            or datetime.min.replace(tzinfo=timezone.utc),
            getattr(item, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )


def latest_screening_transcript_text(application: CandidateApplication) -> str | None:
    """Pick the best transcript text from the screening interviews.

    Prefers screening-stage interviews; falls back to any interview with a
    transcript. Returns the most recent non-empty transcript, or the
    summary if no transcript text is present. Returns ``None`` when no
    interviews exist.
    """
    interviews = list(application.interviews or [])
    if not interviews:
        return None
    screening = [
        item for item in interviews
        if str(getattr(item, "stage", "") or "").strip().lower() == "screening"
    ]
    ordered = _ordered_interviews_local(screening or interviews)
    for item in ordered:
        transcript = str(getattr(item, "transcript_text", "") or "").strip()
        if transcript:
            return transcript
        summary = str(getattr(item, "summary", "") or "").strip()
        if summary:
            return summary
    return None


def format_evidence_anchor(raw_q: dict[str, Any]) -> str | None:
    """Apply the per-source prefix (CV §, Screen §, etc.) to the LLM's
    raw evidence anchor so interviewers can trace each question back to
    its source.
    """
    anchor = str(raw_q.get("evidence_anchor") or "").strip()
    if not anchor:
        return None
    source = str(raw_q.get("evidence_source") or "").strip().lower()
    prefix = _EVIDENCE_PREFIX_BY_SOURCE.get(source, "")
    return f"{prefix}{anchor}" if prefix else anchor


def deterministic_tech_questions(
    missing_skills: list[str],
    screening_summary_text: str | None,
) -> list[dict[str, Any]]:
    """Fallback tech-stage questions used when the LLM call returns
    nothing. Pure string templating — no I/O. Returns the raw dicts the
    caller will wrap with ``_question`` for storage sanitisation.
    """
    out: list[dict[str, Any]] = []
    for skill in missing_skills[:3]:
        out.append(
            {
                "question": f"Walk through the most technically complex work you've done related to {skill.lower()}.",
                "why_this_matters": "This is a likely technical gap relative to the job requirements.",
                "evidence_anchor": skill,
                "positive_signals": [
                    "Deep implementation detail",
                    "Tradeoff reasoning",
                    "Debugging examples",
                ],
                "red_flags": [
                    "Only conceptual familiarity",
                    "No concrete decisions or outcomes",
                ],
                "follow_up_probe": "Ask about architecture choices, failure modes, and what they would optimize next.",
            }
        )
    summary = (screening_summary_text or "").strip()
    if summary:
        out.append(
            {
                "question": "Build on the first interview: what technical example best validates the strongest screening claim?",
                "why_this_matters": "Keeps the technical interview grounded in evidence already surfaced during screening.",
                "evidence_anchor": summary,
                "positive_signals": [
                    "Consistency with prior interview",
                    "Specific architecture detail",
                    "Honest tradeoffs",
                ],
                "red_flags": ["Inconsistent narrative", "Surface-level technical depth"],
                "follow_up_probe": "Ask which part they personally designed, debugged, and measured.",
            }
        )
    return out


def maybe_generate_tech_questions(
    application: CandidateApplication,
    role,
    cv_match_details: dict[str, Any] | None,
    pre_screen_evidence: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    """Run the LLM tech-question generator if the role has a JD and the
    Anthropic key is set. Returns ``None`` on any failure or when the
    inputs are insufficient — caller should fall back to the deterministic
    template path.
    """
    if not settings.ANTHROPIC_API_KEY:
        return None
    if role is None or not (getattr(role, "job_spec_text", "") or "").strip():
        return None

    # Skip regeneration when the existing pack was built from the same
    # CV scoring version. The prompt_version in cv_match_details changes
    # with each scoring model bump, so this stays current automatically.
    details = cv_match_details if isinstance(cv_match_details, dict) else {}
    current_cv_version = details.get("prompt_version") or details.get("scoring_version") or ""
    existing_pack = getattr(application, "tech_interview_pack", None)
    if isinstance(existing_pack, dict) and current_cv_version:
        if existing_pack.get("cv_match_prompt_version") == current_cv_version:
            return None  # reuse existing — no LLM call needed

    details = cv_match_details if isinstance(cv_match_details, dict) else {}
    requirements_assessment = details.get("requirements_assessment")
    if not isinstance(requirements_assessment, list):
        requirements_assessment = None

    try:
        return generate_tech_questions(
            job_spec_text=str(role.job_spec_text or "").strip(),
            recruiter_requirements=str(getattr(role, "additional_requirements", "") or "").strip() or None,
            requirements_assessment=requirements_assessment,
            transcript_text=latest_screening_transcript_text(application),
            recruiter_notes=str(getattr(application, "notes", "") or "").strip() or None,
            pre_screen_evidence=pre_screen_evidence,
        )
    except Exception:  # pragma: no cover — defensive
        return None
