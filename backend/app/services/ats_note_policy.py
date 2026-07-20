"""Policy helpers for ATS notes.

Free-form recruiter context belongs in Taali.  ATS write-back is reserved for
structured candidate movement and decision summaries composed by their owning
workflows. Assessment lifecycle and result details stay inside Taali even when
an ATS movement happens at the same time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .document_service import sanitize_text_for_storage

if TYPE_CHECKING:
    from ..models.candidate_application import CandidateApplication
    from ..models.role import Role

STANDALONE_ATS_NOTES_DISABLED_MESSAGE = (
    "Standalone ATS notes are disabled. Save recruiter context as an internal "
    "Taali note; only candidate movements and structured decision summaries are "
    "sent to Workable or Bullhorn."
)


_ASSESSMENT_LIFECYCLE_RE = re.compile(
    r"\b(?:assessment(?:s)?|test(?:s|ed|ing)?|"
    r"evaluat(?:e|es|ed|ing|ion|ions)|exercise(?:s|d)?|"
    r"result(?:s|ed|ing)?|score(?:s|d|ing)?|grade(?:s|d|ing)?|"
    r"report(?:s|ed|ing)?)\b|"
    r"\btake[- ]home\b|\bcoding challenge\b",
    re.IGNORECASE,
)
_ASSESSMENT_URL_RE = re.compile(
    r"(?:https?://|www\.)\S*(?:assessment|report|share)\S*|"
    r"(?:^|[\s(\"'])/(?:assessment|assessments|report|reports|share)(?:/|\b)",
    re.IGNORECASE,
)
_CANONICAL_SCORE_VALUE_RE = re.compile(r"\d+(?:\.\d+)?\s*/\s*100\s*\Z")
_CANONICAL_SCORE_LABELS = {
    "taali score",
    "taali score used",
    "related-role score",
    "related-role score used",
    "pre-screen score",
    "pre-screen score used",
    "original application score",
    "role threshold",
}
_DOUBLE_BRACE_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render_workable_note_template(template: str | None, **mapping: Any) -> str | None:
    raw_template = sanitize_text_for_storage(str(template or "").strip())
    if not raw_template:
        return None
    normalized = _DOUBLE_BRACE_PLACEHOLDER_RE.sub(r"{\1}", raw_template)
    safe_mapping = _SafeFormatDict(
        {
            key: sanitize_text_for_storage(str(value).strip()) if value is not None else ""
            for key, value in mapping.items()
        }
    )
    try:
        rendered = normalized.format_map(safe_mapping)
    except Exception:
        rendered = normalized
    cleaned = sanitize_text_for_storage(rendered).strip()
    return _truncate_ats_note(cleaned)


def _truncate_ats_note(value: str | None) -> str | None:
    """Keep provider note copy within the public 256-character contract."""
    cleaned = sanitize_text_for_storage(str(value or "")).strip()
    if not cleaned:
        return None
    if len(cleaned) <= 256:
        return cleaned
    return f"{cleaned[:255].rstrip()}…"


def build_workable_reject_note(
    *,
    app: CandidateApplication | None,
    role: Role | None,
    template: str | None,
    reason: str | None = None,
    threshold_100: float | int | None = None,
) -> str | None:
    candidate = getattr(app, "candidate", None)
    candidate_name = sanitize_text_for_storage(
        str(
            getattr(candidate, "full_name", None)
            or getattr(candidate, "email", None)
            or "Candidate"
        ).strip()
    ) or "Candidate"
    role_name = sanitize_text_for_storage(
        str(getattr(role, "name", None) or "Role").strip()
    ) or "Role"
    pre_screen_score = getattr(app, "pre_screen_score_100", None)
    recommendation = sanitize_text_for_storage(
        str(getattr(app, "pre_screen_recommendation", None) or "").strip()
    ) or None
    # Recruiter-authored context stays in Taali. The ATS receives only this
    # server-owned movement rationale, even when a direct/manual rejection
    # supplied a more detailed internal reason.
    public_reason = "The candidate was rejected in Taali." if str(reason or "").strip() else ""
    formatted_threshold = f"{float(threshold_100):.1f}" if threshold_100 is not None else ""
    rendered = render_workable_note_template(
        template,
        candidate_name=candidate_name,
        role_name=role_name,
        pre_screen_score=f"{float(pre_screen_score):.1f}" if pre_screen_score is not None else "",
        threshold=formatted_threshold,
        threshold_100=formatted_threshold,
        recommendation=recommendation or "",
        action_reason=public_reason,
    )
    if rendered:
        return rendered

    if pre_screen_score is not None and threshold_100 is not None:
        fallback = (
            "TAALI · Candidate rejected automatically\n\n"
            f"Pre-screen score: {float(pre_screen_score):.1f}/100\n"
            f"Role threshold: {float(threshold_100):.1f}/100\n"
            "Reason: The candidate did not meet the configured threshold."
        )
        return _truncate_ats_note(fallback)

    if public_reason:
        return _truncate_ats_note(
            "TAALI · Candidate rejected\n"
            f"Role: {role_name}\n"
            f"Reason: {public_reason}"
        )
    return None


def contains_assessment_lifecycle_content(
    body: str | None,
    *,
    trusted_role_values: Iterable[str] | None = None,
) -> bool:
    """Return whether an outbound ATS note contains assessment-only detail.

    This is a defence-in-depth guard at provider boundaries. Owning decision
    workflows should already omit assessment copy; the guard prevents a legacy
    or direct caller from leaking invite, result, or report details into an ATS
    note while still allowing the candidate movement itself to complete.
    """

    clean_body = str(body or "").strip()
    if not clean_body:
        return False
    if _ASSESSMENT_URL_RE.search(clean_body):
        return True
    # Structured movement summaries can legitimately target roles whose names
    # contain words such as "Assessment" or "Coding Challenge", and their
    # canonical numeric score labels necessarily contain the word "score".
    # Exempt a role line only when its complete value exactly matches provenance
    # supplied by the owning workflow. Arbitrary/default callers supply no
    # provenance, so they cannot hide assessment prose behind a ``Role:`` label.
    trusted_values = {
        " ".join(str(value or "").split()).casefold()
        for value in (trusted_role_values or ())
        if str(value or "").strip()
    }
    policy_lines = []
    for line in clean_body.splitlines():
        label, separator, value = line.partition(":")
        normalized_label = label.strip().casefold()
        normalized_value = " ".join(value.split()).casefold()
        if (
            separator
            and normalized_label in {"role", "original ats role"}
            and normalized_value in trusted_values
        ):
            continue
        if (
            separator
            and normalized_label in _CANONICAL_SCORE_LABELS
            and _CANONICAL_SCORE_VALUE_RE.fullmatch(value.strip())
        ):
            continue
        policy_lines.append(line)
    return _ASSESSMENT_LIFECYCLE_RE.search("\n".join(policy_lines)) is not None


__all__ = [
    "STANDALONE_ATS_NOTES_DISABLED_MESSAGE",
    "build_workable_reject_note",
    "contains_assessment_lifecycle_content",
    "render_workable_note_template",
]
