"""Concise recruiter-facing candidate summaries for grounded search results."""

from __future__ import annotations

import re

from ..models.candidate_application import CandidateApplication

_COVER_NOTE_OPENERS = (
    "dear ",
    "hi ",
    "hi,",
    "hello",
    "i hope this",
    "i am writing",
    "to whom",
    "greetings",
    "i came across",
    "i recently came across",
)
_FIRST_SENTENCE_RE = re.compile(r"(.+?[.!?])(\s|$)", re.S)


def candidate_blurb(candidate) -> str | None:
    """Return a factual profile summary without leaking a cover letter."""

    if candidate is None:
        return None
    cv_sections = getattr(candidate, "cv_sections", None) or {}
    cv_summary = str(cv_sections.get("summary") or "").strip()
    if len(cv_summary) >= 40 and not cv_summary.lower().startswith(
        _COVER_NOTE_OPENERS
    ):
        return cv_summary[:400]

    parts: list[str] = []
    headline = str(getattr(candidate, "headline", "") or "").strip()
    if headline:
        parts.append(headline)
    experience = (
        cv_sections.get("experience")
        or getattr(candidate, "experience_entries", None)
        or []
    )
    if isinstance(experience, list) and experience and isinstance(experience[0], dict):
        recent = " at ".join(
            part
            for part in [
                str(experience[0].get("title") or "").strip(),
                str(experience[0].get("company") or "").strip(),
            ]
            if part
        )
        if recent:
            parts.append(f"most recently {recent}")
    skills = [
        str(skill).strip()
        for skill in (
            cv_sections.get("skills") or getattr(candidate, "skills", None) or []
        )[:5]
        if str(skill).strip()
    ]
    if skills:
        parts.append(", ".join(skills))
    if parts:
        return " · ".join(parts)[:400]

    summary = str(getattr(candidate, "summary", "") or "").strip()
    if summary and not summary.lower().startswith(_COVER_NOTE_OPENERS):
        return summary[:400]
    return None


def scoring_summary(
    application: CandidateApplication,
) -> tuple[str | None, str | None]:
    """Split the scoring report summary into headline and supporting detail."""

    details = getattr(application, "cv_match_details", None) or {}
    if not isinstance(details, dict):
        return None, None
    summary = str(details.get("summary") or "").strip()
    if not summary:
        return None, None
    match = _FIRST_SENTENCE_RE.match(summary)
    if match and match.end() < len(summary):
        return match.group(1).strip()[:200], summary[match.end() :].strip()[:700]
    return summary[:200], None


def years_experience(application: CandidateApplication) -> float | None:
    """Return the scoring snapshot's professional years, rounded to halves."""

    details = getattr(application, "cv_match_details", None) or {}
    if not isinstance(details, dict):
        return None
    snapshot = details.get("candidate_snapshot") or {}
    if not isinstance(snapshot, dict):
        return None
    try:
        years = float(snapshot.get("years_experience"))
    except (TypeError, ValueError):
        return None
    return round(years * 2) / 2 if years > 0 else None


__all__ = ["candidate_blurb", "scoring_summary", "years_experience"]
