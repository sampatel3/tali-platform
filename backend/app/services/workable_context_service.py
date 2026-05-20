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

logger = logging.getLogger(__name__)


# Caps keep the block bounded for the LLM. Pre-screen runs at ~256 output
# tokens and a small Haiku model; we don't need an unbounded log.
_MAX_ANSWERS = 30
_MAX_COMMENTS = 15
_MAX_ACTIVITIES = 25
_MAX_EDUCATION = 6
_MAX_EXPERIENCE = 10
_MAX_FIELD_LEN = 1200  # per question/comment body — guard against essays
_MAX_SUMMARY_LEN = 2000


def _trim(value: Any, limit: int = _MAX_FIELD_LEN) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _join_nonempty(parts: list[str], sep: str = " · ") -> str:
    return sep.join(p for p in (str(p or "").strip() for p in parts) if p)


def _format_answer(answer: dict) -> str | None:
    """Render one Workable questionnaire answer.

    Workable's answer payloads are heterogeneous: free-text answers carry
    a ``body``, multi-choice answers carry ``choices`` + ``selected``,
    boolean answers carry ``checked``. Question text lives under
    ``question.body`` (or ``question_key`` as a fallback).
    """
    if not isinstance(answer, dict):
        return None
    question = answer.get("question") or {}
    if isinstance(question, dict):
        question_text = (
            question.get("body")
            or question.get("text")
            or question.get("title")
            or answer.get("question_key")
            or ""
        )
    else:
        question_text = answer.get("question_key") or ""
    question_text = _trim(question_text, 400)
    if not question_text:
        return None

    parts: list[str] = []
    body = answer.get("body")
    if body:
        parts.append(_trim(body))

    checked = answer.get("checked")
    if isinstance(checked, bool):
        parts.append("Yes" if checked else "No")

    # ``choices`` can either be a list of dicts ({id, body, selected}) or
    # a list of strings the candidate picked. ``selected`` is sometimes a
    # parallel array; handle both.
    choices = answer.get("choices")
    selected = answer.get("selected")
    chosen: list[str] = []
    if isinstance(choices, list):
        for c in choices:
            if isinstance(c, dict):
                if c.get("selected") or c.get("checked"):
                    label = _trim(c.get("body") or c.get("text"), 200)
                    if label:
                        chosen.append(label)
            elif isinstance(c, str):
                chosen.append(_trim(c, 200))
    if isinstance(selected, list):
        for s in selected:
            label = _trim(s, 200) if isinstance(s, str) else None
            if label and label not in chosen:
                chosen.append(label)
    if chosen:
        parts.append("Selected: " + "; ".join(chosen))

    answer_text = _join_nonempty(parts, sep=" — ")
    if not answer_text:
        return None
    return f"Q: {question_text}\nA: {answer_text}"


def _format_comment(comment: dict) -> str | None:
    if not isinstance(comment, dict):
        return None
    body = _trim(comment.get("body") or comment.get("text"))
    if not body:
        return None
    author = comment.get("member") or comment.get("user") or {}
    if isinstance(author, dict):
        author_name = author.get("name") or author.get("full_name") or ""
    else:
        author_name = str(author or "")
    created_at = (
        comment.get("created_at")
        or comment.get("posted_at")
        or comment.get("updated_at")
        or ""
    )
    header = _join_nonempty([author_name, _trim(created_at, 32)])
    if header:
        return f"[{header}] {body}"
    return body


def _format_activity(activity: dict) -> str | None:
    """Render one Workable activity-log entry.

    We're permissive about shape because Workable returns many activity
    types (stage transitions, automated emails, ratings, comments echoed
    back). The pre-screener mostly cares about anything with a body or a
    stage change.
    """
    if not isinstance(activity, dict):
        return None
    body = _trim(activity.get("body") or activity.get("comment") or activity.get("message"))
    action = _trim(activity.get("action") or activity.get("type") or activity.get("kind"), 64)
    stage_from = _trim(activity.get("stage_name") or activity.get("from_stage"), 64)
    stage_to = _trim(activity.get("to_stage"), 64)
    created_at = _trim(activity.get("created_at") or activity.get("posted_at"), 32)
    if not (body or action or stage_from or stage_to):
        return None
    pieces: list[str] = []
    if action:
        pieces.append(action)
    if stage_from and stage_to:
        pieces.append(f"{stage_from} → {stage_to}")
    elif stage_from:
        pieces.append(stage_from)
    if created_at:
        pieces.append(created_at)
    header = " · ".join(pieces)
    if body and header:
        return f"[{header}] {body}"
    return body or header or None


def _format_education(entry: dict) -> str | None:
    if not isinstance(entry, dict):
        return None
    school = _trim(entry.get("school"), 160)
    degree = _trim(entry.get("degree"), 120)
    field = _trim(entry.get("field_of_study"), 120)
    start = _trim(entry.get("start_date"), 24)
    end = _trim(entry.get("end_date"), 24)
    if not (school or degree or field):
        return None
    when = ""
    if start and end:
        when = f" ({start}–{end})"
    elif end:
        when = f" (–{end})"
    elif start:
        when = f" ({start}–)"
    head = ", ".join(p for p in (school, degree, field) if p)
    return f"{head}{when}"


def _format_experience(entry: dict) -> str | None:
    if not isinstance(entry, dict):
        return None
    company = _trim(entry.get("company"), 160)
    title = _trim(entry.get("title"), 160)
    start = _trim(entry.get("start_date"), 24)
    end = _trim(entry.get("end_date"), 24)
    current = bool(entry.get("current"))
    summary = _trim(entry.get("summary"), 600)
    industry = _trim(entry.get("industry"), 80)
    if not (company or title):
        return None
    when = ""
    if current:
        when = f" ({start or '?'}–present)"
    elif start and end:
        when = f" ({start}–{end})"
    elif end:
        when = f" (–{end})"
    elif start:
        when = f" ({start}–)"
    head = " — ".join(p for p in (title, company) if p)
    if industry:
        head = f"{head} [{industry}]"
    if summary:
        return f"{head}{when}\n  {summary}"
    return f"{head}{when}"


def _social_lines(socials: Any) -> list[str]:
    if not isinstance(socials, list):
        return []
    out: list[str] = []
    for s in socials:
        if not isinstance(s, dict):
            continue
        kind = _trim(s.get("type") or s.get("name"), 32)
        url = _trim(s.get("url"), 240)
        username = _trim(s.get("username"), 80)
        label = _join_nonempty([kind, url or username])
        if label:
            out.append(label)
    return out


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
    tag_lines: list[str] = []
    if isinstance(candidate.skills, list) and candidate.skills:
        skills = [_trim(s, 80) for s in candidate.skills if s]
        if skills:
            tag_lines.append("Skills: " + ", ".join(skills[:40]))
    if isinstance(candidate.tags, list) and candidate.tags:
        tags = [_trim(t, 80) for t in candidate.tags if t]
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
