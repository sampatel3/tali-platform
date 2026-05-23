"""Pure parsers/formatters for Workable candidate metadata.

Extracted from ``workable_context_service`` to keep that module under the
500-LOC architecture gate. These are stateless helpers — no DB, no LLM —
that turn Workable's loosely-typed payloads (questionnaire answers,
recruiter comments, activity log entries, education/experience rows) into
trimmed, bounded text/tuples. Both the pre-screen prompt renderer and the
recruiter-UI structured surfaces reuse them so the LLM and the UI never
disagree about what a comment / answer / activity says.

``workable_context_service`` imports these back, so its public surface
(``format_workable_context`` + the structured ``workable_*`` helpers) is
unchanged.
"""

from __future__ import annotations

import ast
import re
from typing import Any


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
