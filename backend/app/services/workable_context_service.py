"""Render a candidate's full Workable metadata as text for the pre-screen LLM.

The pre-screen prompt was originally CV-only. Hard constraints expressed
solely in Workable (e.g. salary expectation given as a questionnaire
answer on LinkedIn apply, or a notice-period recruiter comment) were
invisible to the LLM, so candidates exceeding the role's constraints
passed pre-screen instead of being filtered out.

This module flattens every Workable surface we store on a Candidate into
a structured, plaintext block tagged with ``<WORKABLE_*>`` regions the
LLM can reason about. Output is deterministic and bounded so it fits
cleanly inside the pre-screen prompt's variable (per-candidate) cache
block without exploding the static-block cache.
"""

from __future__ import annotations

import logging
from typing import Any

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication
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

    sections: list[str] = []

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
            "<WORKABLE_PROFILE>\n" + "\n".join(profile_lines) + "\n</WORKABLE_PROFILE>"
        )

    # ── Candidate self-description ────────────────────────────────────
    summary = _trim(candidate.summary, _MAX_SUMMARY_LEN)
    if summary:
        sections.append(
            f"<WORKABLE_SUMMARY>\n{summary}\n</WORKABLE_SUMMARY>"
        )

    # ── Questionnaire answers (this is what LinkedIn applicants fill) ─
    workable_data = candidate.workable_data if isinstance(candidate.workable_data, dict) else {}
    answers = workable_data.get("answers")
    if isinstance(answers, list) and answers:
        formatted_answers = [
            line for line in (_format_answer(a) for a in answers[:_MAX_ANSWERS]) if line
        ]
        if formatted_answers:
            sections.append(
                "<WORKABLE_QUESTIONNAIRE_ANSWERS>\n"
                + "\n\n".join(formatted_answers)
                + "\n</WORKABLE_QUESTIONNAIRE_ANSWERS>"
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
        sections.append(
            "<WORKABLE_TAGS>\n" + "\n".join(tag_lines) + "\n</WORKABLE_TAGS>"
        )

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
                "<WORKABLE_EDUCATION>\n"
                + "\n".join(f"- {line}" for line in formatted)
                + "\n</WORKABLE_EDUCATION>"
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
                "<WORKABLE_EXPERIENCE>\n"
                + "\n".join(f"- {line}" for line in formatted)
                + "\n</WORKABLE_EXPERIENCE>"
            )

    # ── Recruiter comments ────────────────────────────────────────────
    if isinstance(candidate.workable_comments, list) and candidate.workable_comments:
        formatted = [
            line
            for line in (
                _format_comment(c) for c in candidate.workable_comments[:_MAX_COMMENTS]
            )
            if line
        ]
        if formatted:
            sections.append(
                "<WORKABLE_RECRUITER_COMMENTS>\n"
                + "\n\n".join(formatted)
                + "\n</WORKABLE_RECRUITER_COMMENTS>"
            )

    # ── Activity log ──────────────────────────────────────────────────
    if isinstance(candidate.workable_activities, list) and candidate.workable_activities:
        formatted = [
            line
            for line in (
                _format_activity(a) for a in candidate.workable_activities[:_MAX_ACTIVITIES]
            )
            if line
        ]
        if formatted:
            sections.append(
                "<WORKABLE_ACTIVITY_LOG>\n"
                + "\n".join(f"- {line}" for line in formatted)
                + "\n</WORKABLE_ACTIVITY_LOG>"
            )

    return "\n\n".join(sections)


# ── Structured surfaces for the candidate-detail UI ───────────────────
# The pre-screen prompt wants one text blob (``format_workable_context``);
# the recruiter UI's Notes tab wants structured rows it can lay out. These
# reuse the same parsers so the LLM and the UI never disagree about what a
# comment / answer / activity says. Bounded by the same caps as the prompt.


def workable_questionnaire_answers(candidate: Candidate | None) -> list[dict[str, str]]:
    """Structured ``[{question, answer}]`` from the candidate's Workable
    questionnaire / LinkedIn-apply answers. Empty list when none."""
    if candidate is None:
        return []
    workable_data = candidate.workable_data if isinstance(candidate.workable_data, dict) else {}
    answers = workable_data.get("answers")
    if not isinstance(answers, list):
        return []
    out: list[dict[str, str]] = []
    for entry in answers[:_MAX_ANSWERS]:
        parsed = _parse_answer(entry)
        if parsed:
            out.append({"question": parsed[0], "answer": parsed[1]})
    return out


def workable_recruiter_comments(candidate: Candidate | None) -> list[dict[str, str | None]]:
    """Structured ``[{author, created_at, body}]`` recruiter notes synced from
    Workable — true comments plus recruiter ratings that carry a written note.

    Comments come from ``workable_comments``; ratings are pulled from
    ``workable_activities`` (where the sync stores them) so a recruiter
    evaluation added after the decision still shows on the profile. Ratings are
    surfaced here only — they are deliberately kept out of ``workable_comments``
    storage so they never enter the pre-screen scoring context. Newest first."""
    if candidate is None:
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

    comments = candidate.workable_comments
    if isinstance(comments, list):
        for entry in comments:
            _append(entry)
    activities = candidate.workable_activities
    if isinstance(activities, list):
        for entry in activities:
            if _is_rating_note(entry):
                _append(entry)
    # Newest first so a just-added note lands at the top of the card.
    out.sort(key=lambda c: (c.get("created_at") or ""), reverse=True)
    return out[:_MAX_COMMENTS]


def workable_activity_log(candidate: Candidate | None) -> list[dict[str, str | None]]:
    """Structured ``[{action, stage, body, created_at}]`` activity entries
    synced from Workable. Empty list when none."""
    if candidate is None:
        return []
    activities = candidate.workable_activities
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
