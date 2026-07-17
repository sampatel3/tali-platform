"""Render a candidate's full Workable metadata as text for the pre-screen LLM.

The pre-screen prompt was originally CV-only. Hard constraints expressed
solely in Workable (e.g. salary expectation given as a questionnaire
answer on LinkedIn apply, or a notice-period recruiter comment) were
invisible to the LLM, so candidates exceeding the role's constraints
passed pre-screen instead of being filtered out.

This module flattens every Workable surface we store on a Candidate into
a structured, plaintext block tagged with ``<WORKABLE_*>`` regions the
LLM can reason about. Output is deterministic. Protected answers, comments,
and activities stay lossless here so holistic scoring can apply its single
exact provider-visible 32,000-character fail-closed boundary.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
from .workable_context_contract import (
    StructuredWorkableContext,
    WorkableEvidenceSection,
)
# Pure parsers/formatters live in workable_context_parsers now (kept this
# module under the 500-LOC architecture gate). Imported back so the renderer
# below and the structured surfaces reuse the exact same parsers.
from .workable_context_parsers import (
    _MAX_ACTIVITIES,
    _MAX_ANSWERS,
    _MAX_COMMENTS,
    _MAX_EDUCATION,
    _MAX_EXPERIENCE,
    _MAX_SUMMARY_LEN,
    _activity_fields,
    _comment_fields,
    _format_activity,
    _format_comment,
    _is_rating_note,
    _format_education,
    _format_experience,
    _format_answer,
    _join_nonempty,
    _label_from_legacy_repr,
    _parse_answer,
    _social_lines,
    _trim,
)

logger = logging.getLogger(__name__)


# ── Per-application context resolution ─────────────────────────────────
# Workable candidate ids — and their answers/comments/activities — are per
# JOB APPLICATION, but people are deduped into one Candidate row, so the
# candidate-level fields hold whichever application synced last. Readers
# prefer the application's own copy and fall back to the candidate-level
# fields only for legacy rows synced before the per-application columns.


def _resolved_answers(
    candidate: Candidate | None, application: CandidateApplication | None
) -> Any:
    if application is not None and isinstance(application.workable_answers, list):
        return application.workable_answers
    if candidate is None:
        return None
    workable_data = (
        candidate.workable_data if isinstance(candidate.workable_data, dict) else {}
    )
    return workable_data.get("answers")


def _resolved_comments(
    candidate: Candidate | None, application: CandidateApplication | None
) -> Any:
    if application is not None and isinstance(application.workable_comments, list):
        return application.workable_comments
    return candidate.workable_comments if candidate is not None else None


def _resolved_activities(
    candidate: Candidate | None, application: CandidateApplication | None
) -> Any:
    if application is not None and isinstance(application.workable_activities, list):
        return application.workable_activities
    return candidate.workable_activities if candidate is not None else None


def format_workable_context(
    candidate: Candidate | None,
    application: CandidateApplication | None = None,
) -> str:
    """Render every Workable surface we hold for ``candidate`` as text.

    Returns an empty string when the candidate has no Workable footprint
    (so callers can drop the block entirely instead of leaking empty tags
    into the prompt).
    """
    if candidate is None:
        return ""

    sections: list[WorkableEvidenceSection] = []

    # ── Profile snapshot ──────────────────────────────────────────────
    profile_lines: list[str] = []
    if candidate.full_name:
        profile_lines.append(f"Name: {_trim(candidate.full_name, 200)}")
    if candidate.headline:
        profile_lines.append(f"Headline: {_trim(candidate.headline, 240)}")
    location = _join_nonempty(
        [candidate.location_city, candidate.location_country], sep=", "
    )
    if location:
        profile_lines.append(f"Location: {location}")
    if candidate.phone:
        profile_lines.append(f"Phone: {_trim(candidate.phone, 64)}")
    if candidate.email:
        profile_lines.append(f"Email: {_trim(candidate.email, 200)}")
    if candidate.profile_url:
        profile_lines.append(f"Workable profile: {_trim(candidate.profile_url, 320)}")
    if application is not None:
        if application.workable_stage:
            profile_lines.append(f"Current stage: {_trim(application.workable_stage, 80)}")
        if application.workable_sourced is True:
            profile_lines.append("Sourced (recruiter-added, not inbound)")
        elif application.workable_sourced is False:
            profile_lines.append("Inbound application")
    socials = _social_lines(candidate.social_profiles)
    if socials:
        profile_lines.append("Social: " + "; ".join(socials[:8]))
    if profile_lines:
        sections.append(
            WorkableEvidenceSection("WORKABLE_PROFILE", "\n".join(profile_lines))
        )

    # ── Candidate self-description ────────────────────────────────────
    summary = _trim(candidate.summary, _MAX_SUMMARY_LEN)
    if summary:
        sections.append(WorkableEvidenceSection("WORKABLE_SUMMARY", summary))

    # ── Questionnaire answers (this is what LinkedIn applicants fill) ─
    answers = _resolved_answers(candidate, application)
    if isinstance(answers, list) and answers:
        formatted_answers = [
            line
            for line in (
                _format_answer(a, preserve_full_text=True) for a in answers
            )
            if line
        ]
        if formatted_answers:
            sections.append(
                WorkableEvidenceSection(
                    "WORKABLE_QUESTIONNAIRE_ANSWERS",
                    "\n\n".join(formatted_answers),
                )
            )

    # ── Skills / tags ─────────────────────────────────────────────────
    def _label(item: Any) -> str:
        # Skills and tags come back as either plain strings or
        # ``{"name": "AWS", ...}`` dicts depending on the Workable
        # endpoint version. Legacy rows ingested before the extractor
        # fix have ``str(dict)`` reprs stored as strings; try to rescue
        # those into readable labels so the prompt doesn't show noise.
        if isinstance(item, dict):
            return _trim(
                item.get("name")
                or item.get("body")
                or item.get("text")
                or item.get("label")
                or "",
                80,
            )
        if isinstance(item, str):
            rescued = _label_from_legacy_repr(item)
            if rescued:
                return _trim(rescued, 80)
        return _trim(item, 80)

    tag_lines: list[str] = []
    if isinstance(candidate.skills, list) and candidate.skills:
        skills = [_label(s) for s in candidate.skills if s]
        skills = [s for s in skills if s]
        if skills:
            tag_lines.append("Skills: " + ", ".join(skills[:40]))
    if isinstance(candidate.tags, list) and candidate.tags:
        tags = [_label(t) for t in candidate.tags if t]
        tags = [t for t in tags if t]
        if tags:
            tag_lines.append("Tags: " + ", ".join(tags[:40]))
    if tag_lines:
        sections.append(WorkableEvidenceSection("WORKABLE_TAGS", "\n".join(tag_lines)))

    # ── Education ─────────────────────────────────────────────────────
    if isinstance(candidate.education_entries, list) and candidate.education_entries:
        formatted = [
            line
            for line in (
                _format_education(e) for e in candidate.education_entries[:_MAX_EDUCATION]
            )
            if line
        ]
        if formatted:
            sections.append(
                WorkableEvidenceSection(
                    "WORKABLE_EDUCATION",
                    "\n".join(f"- {line}" for line in formatted),
                )
            )

    # ── Experience ────────────────────────────────────────────────────
    if isinstance(candidate.experience_entries, list) and candidate.experience_entries:
        formatted = [
            line
            for line in (
                _format_experience(e) for e in candidate.experience_entries[:_MAX_EXPERIENCE]
            )
            if line
        ]
        if formatted:
            sections.append(
                WorkableEvidenceSection(
                    "WORKABLE_EXPERIENCE",
                    "\n".join(f"- {line}" for line in formatted),
                )
            )

    # ── Recruiter comments ────────────────────────────────────────────
    ctx_comments = _resolved_comments(candidate, application)
    if isinstance(ctx_comments, list) and ctx_comments:
        formatted = [
            line
            for line in (
                _format_comment(c, preserve_full_text=True) for c in ctx_comments
            )
            if line
        ]
        if formatted:
            sections.append(
                WorkableEvidenceSection(
                    "WORKABLE_RECRUITER_COMMENTS",
                    "\n\n".join(formatted),
                )
            )

    # ── Activity log ──────────────────────────────────────────────────
    ctx_activities = _resolved_activities(candidate, application)
    if isinstance(ctx_activities, list) and ctx_activities:
        formatted = [
            line
            for line in (
                _format_activity(a, preserve_full_text=True) for a in ctx_activities
            )
            if line
        ]
        if formatted:
            sections.append(
                WorkableEvidenceSection(
                    "WORKABLE_ACTIVITY_LOG",
                    "\n".join(f"- {line}" for line in formatted),
                )
            )

    return StructuredWorkableContext(sections)


# ── Structured surfaces for the candidate-detail UI ───────────────────
# The pre-screen prompt wants one text blob (``format_workable_context``);
# the recruiter UI's Notes tab wants structured rows it can lay out. These
# reuse the same parsers so the LLM and the UI never disagree about what a
# comment / answer / activity says. These presentation surfaces stay bounded;
# the scoring formatter above uses the lossless parser mode instead.


def workable_questionnaire_answers(
    candidate: Candidate | None,
    application: CandidateApplication | None = None,
) -> list[dict[str, str]]:
    """Structured ``[{question, answer}]`` from the Workable questionnaire /
    LinkedIn-apply answers — this application's own when stored, otherwise
    the candidate-level legacy copy. Empty list when none."""
    if candidate is None and application is None:
        return []
    answers = _resolved_answers(candidate, application)
    if not isinstance(answers, list):
        return []
    out: list[dict[str, str]] = []
    for entry in answers[:_MAX_ANSWERS]:
        parsed = _parse_answer(entry)
        if parsed:
            out.append({"question": parsed[0], "answer": parsed[1]})
    return out


def workable_recruiter_comments(
    candidate: Candidate | None,
    application: CandidateApplication | None = None,
) -> list[dict[str, str | None]]:
    """Structured ``[{author, created_at, body}]`` recruiter notes synced from
    Workable — true comments plus recruiter ratings that carry a written note.

    Comments come from ``workable_comments`` (this application's own when
    stored, candidate-level legacy copy otherwise); ratings are pulled from
    ``workable_activities`` (where the sync stores them) so a recruiter
    evaluation added after the decision still shows on the profile. Ratings are
    surfaced here only — they are deliberately kept out of ``workable_comments``
    storage so they never enter the pre-screen scoring context. Newest first."""
    if candidate is None and application is None:
        return []
    out: list[dict[str, str | None]] = []

    def _append(entry: dict) -> None:
        fields = _comment_fields(entry)
        if fields is None:
            return
        author_name, created_at, body = fields
        out.append(
            {
                "author": author_name or None,
                "created_at": created_at or None,
                "body": body,
            }
        )

    comments = _resolved_comments(candidate, application)
    if isinstance(comments, list):
        for entry in comments:
            _append(entry)
    activities = _resolved_activities(candidate, application)
    if isinstance(activities, list):
        for entry in activities:
            if _is_rating_note(entry):
                _append(entry)
    # Newest first so a just-added note lands at the top of the card.
    out.sort(key=lambda c: (c.get("created_at") or ""), reverse=True)
    return out[:_MAX_COMMENTS]


def workable_activity_log(
    candidate: Candidate | None,
    application: CandidateApplication | None = None,
) -> list[dict[str, str | None]]:
    """Structured ``[{action, stage, body, created_at}]`` activity entries
    synced from Workable — this application's own when stored, otherwise the
    candidate-level legacy copy. Empty list when none."""
    if candidate is None and application is None:
        return []
    activities = _resolved_activities(candidate, application)
    if not isinstance(activities, list):
        return []
    out: list[dict[str, str | None]] = []
    for entry in activities:
        if len(out) >= _MAX_ACTIVITIES:
            break
        # Recruiter ratings-with-a-note render as notes (see
        # workable_recruiter_comments); keep them out of the timeline to
        # avoid showing the same text in both columns.
        if _is_rating_note(entry):
            continue
        fields = _activity_fields(entry)
        if fields is None:
            continue
        out.append(
            {
                "action": fields["action"] or None,
                "stage": fields["stage"] or None,
                "body": fields["body"] or None,
                "created_at": fields["created_at"] or None,
            }
        )
    return out
