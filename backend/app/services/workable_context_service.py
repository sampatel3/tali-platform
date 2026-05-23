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

import ast
import logging
import re
from typing import Any

from ..models.candidate import Candidate
from ..models.candidate_application import CandidateApplication

logger = logging.getLogger(__name__)


# Legacy rows stored skills/tags as ``str(dict)`` reprs (e.g. ``"{'name':
# 'AWS'}"``). New ingestion writes clean labels, but historical data
# still needs to render readably — this regex pulls the label out.
_LEGACY_DICT_LABEL_RE = re.compile(
    r"""[\"']?(?:name|body|text|label)[\"']?\s*:\s*[\"']([^\"']+)[\"']""",
    re.IGNORECASE,
)


def _label_from_legacy_repr(text: str) -> str | None:
    """Pull a readable label out of a legacy ``str(dict)`` row.

    Tries ``ast.literal_eval`` first (handles well-formed Python dict
    reprs) and falls back to a regex scrape so a malformed string still
    yields something useful for the LLM rather than a noisy repr.
    """
    stripped = text.strip()
    if not stripped or not (stripped.startswith("{") or "'name'" in stripped or '"name"' in stripped):
        return None
    try:
        parsed = ast.literal_eval(stripped)
    except (ValueError, SyntaxError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("name", "body", "text", "label"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    match = _LEGACY_DICT_LABEL_RE.search(stripped)
    if match:
        return match.group(1).strip()
    return None


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


def _parse_answer(answer: dict) -> tuple[str, str] | None:
    """Parse one Workable questionnaire answer into ``(question, answer)``.

    Workable's payload shape varies. Two known forms in the wild:

    Nested (what we've actually seen in prod):
        {"question": {"body": "..."}, "answer": {"body": "..."}}
        {"question": {"body": "..."}, "answer": {"checked": true}}

    Flat (documented in some Workable API spec versions):
        {"question_key": "...", "body": "...", "checked": ...}

    We look in both places for body / checked / choices so callers handle
    either shape without needing to normalise first. Returns ``None`` when
    the entry has no usable question or answer text.
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

    # Inner answer block — present in nested-shape payloads, absent in
    # flat-shape. Fall through to the top level for flat payloads.
    inner = answer.get("answer") if isinstance(answer.get("answer"), dict) else {}

    parts: list[str] = []
    body = inner.get("body") if inner else None
    if body is None:
        body = answer.get("body")
    if body not in (None, ""):
        parts.append(_trim(body))

    checked = inner.get("checked") if inner else None
    if checked is None:
        checked = answer.get("checked")
    if isinstance(checked, bool):
        parts.append("Yes" if checked else "No")

    # ``choices`` can either be a list of dicts ({id, body, selected}) or
    # a list of strings the candidate picked. ``selected`` is sometimes a
    # parallel array; handle both. Also check the nested ``answer`` block.
    choices = (inner.get("choices") if inner else None) or answer.get("choices")
    selected = (inner.get("selected") if inner else None) or answer.get("selected")
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
    return question_text, answer_text


def _format_answer(answer: dict) -> str | None:
    """Render one Workable questionnaire answer as ``Q: …\\nA: …`` text."""
    parsed = _parse_answer(answer)
    if parsed is None:
        return None
    question_text, answer_text = parsed
    return f"Q: {question_text}\nA: {answer_text}"


def _comment_fields(comment: dict) -> tuple[str, str, str] | None:
    """Parse one recruiter comment into ``(author, created_at, body)``.

    Returns ``None`` when there is no comment body to show.
    """
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
    return _trim(author_name, 160), _trim(created_at, 32), body


def _format_comment(comment: dict) -> str | None:
    fields = _comment_fields(comment)
    if fields is None:
        return None
    author_name, created_at, body = fields
    header = _join_nonempty([author_name, created_at])
    if header:
        return f"[{header}] {body}"
    return body


def _activity_fields(activity: dict) -> dict | None:
    """Parse one Workable activity-log entry into structured fields.

    We're permissive about shape because Workable returns many activity
    types (stage transitions, automated emails, ratings, comments echoed
    back). Returns ``{action, stage, body, created_at}`` or ``None`` when
    the entry carries nothing meaningful.
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
    if stage_from and stage_to:
        stage = f"{stage_from} → {stage_to}"
    elif stage_from:
        stage = stage_from
    else:
        stage = ""
    return {
        "action": action,
        "stage": stage,
        "body": body,
        "created_at": created_at,
    }


def _format_activity(activity: dict) -> str | None:
    """Render one Workable activity-log entry as a single text line."""
    fields = _activity_fields(activity)
    if fields is None:
        return None
    pieces: list[str] = []
    if fields["action"]:
        pieces.append(fields["action"])
    if fields["stage"]:
        pieces.append(fields["stage"])
    if fields["created_at"]:
        pieces.append(fields["created_at"])
    header = " · ".join(pieces)
    body = fields["body"]
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
    """Structured ``[{author, created_at, body}]`` recruiter comments synced
    from Workable. Empty list when none."""
    if candidate is None:
        return []
    comments = candidate.workable_comments
    if not isinstance(comments, list):
        return []
    out: list[dict[str, str | None]] = []
    for entry in comments[:_MAX_COMMENTS]:
        fields = _comment_fields(entry)
        if fields is None:
            continue
        author_name, created_at, body = fields
        out.append(
            {
                "author": author_name or None,
                "created_at": created_at or None,
                "body": body,
            }
        )
    return out


def workable_activity_log(candidate: Candidate | None) -> list[dict[str, str | None]]:
    """Structured ``[{action, stage, body, created_at}]`` activity entries
    synced from Workable. Empty list when none."""
    if candidate is None:
        return []
    activities = candidate.workable_activities
    if not isinstance(activities, list):
        return []
    out: list[dict[str, str | None]] = []
    for entry in activities[:_MAX_ACTIVITIES]:
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
