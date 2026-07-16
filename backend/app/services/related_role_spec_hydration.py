"""Pure, conservative hydration helpers for cloned related-role job specs.

The related-role draft keeps the original job spec verbatim for publishing, but
the requisition gap engine reads structured ``RoleBrief`` fields.  This module
extracts only content that is explicitly labelled in the source document; it
does not infer responsibilities from a title or descriptive prose.
"""

from __future__ import annotations

import re
from typing import Any


_RESPONSIBILITY_HEADINGS = frozenset(
    {
        "responsibilities",
        "key responsibilities",
        "role responsibilities",
        "duties",
        "key duties",
        "what you'll do",
        "what you will do",
        "what you’ll do",
    }
)

_KNOWN_SECTION_HEADINGS = _RESPONSIBILITY_HEADINGS | frozenset(
    {
        "about the company",
        "about the role",
        "about us",
        "about you",
        "benefits",
        "candidate profile",
        "certifications",
        "compensation",
        "competencies",
        "dealbreakers",
        "description",
        "education",
        "employment details",
        "experience",
        "job description",
        "key requirements",
        "knowledge and experience",
        "location",
        "minimum qualifications",
        "must haves",
        "must-haves",
        "nice to haves",
        "nice-to-haves",
        "overview",
        "perks",
        "preferred qualifications",
        "qualifications",
        "required qualifications",
        "required skills",
        "requirements",
        "role requirements",
        "role summary",
        "security clearance",
        "skills",
        "summary",
        "technical skills",
        "what success looks like",
        "what we're looking for",
        "what we’re looking for",
        "why join us",
        "who you are",
    }
)

_MARKDOWN_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_BOLD_HEADING_RE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?[ \t]*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*\u2022\u00b7]|\d+[.)])\s+(.+?)\s*$")


def _heading_text(line: str) -> str | None:
    """Return a normalized explicit heading, else ``None``.

    Known plain-text headings are accepted alongside Markdown/bold headings.
    Unknown Markdown headings still count as section boundaries so content from
    a later section is never folded into responsibilities.
    """

    raw = str(line or "").strip()
    if not raw:
        return None
    # Bullet/numbered items are content even when their text is ALL CAPS or
    # ends in a colon; heading-style heuristics must never truncate the section.
    if _BULLET_RE.match(raw):
        return None
    markdown = bool(_MARKDOWN_HEADING_RE.match(raw))
    colon_heading = raw.endswith(":")
    candidate = _MARKDOWN_HEADING_RE.sub("", raw).strip()
    bold = _BOLD_HEADING_RE.match(candidate)
    if bold:
        candidate = bold.group(1).strip()
    candidate = candidate.rstrip(":").strip()
    normalized = re.sub(r"\s+", " ", candidate).casefold()
    if normalized in _KNOWN_SECTION_HEADINGS:
        return normalized
    return normalized if markdown or colon_heading else None


def _clean_item(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_explicit_responsibilities(job_spec_text: str | None) -> list[str]:
    """Extract an explicitly headed responsibilities section.

    Bulleted/numbered sections preserve one item per bullet and join wrapped
    continuation lines.  An unbulleted section preserves one item per paragraph.
    Duplicate items are removed case-insensitively while retaining source order.
    """

    text = str(job_spec_text or "").strip()
    if not text:
        return []

    lines = text.splitlines()
    section_lines: list[str] = []
    in_responsibilities = False
    for line in lines:
        heading = _heading_text(line)
        if heading is not None:
            if heading in _RESPONSIBILITY_HEADINGS:
                in_responsibilities = True
                section_lines = []
                continue
            if in_responsibilities:
                break
            continue
        if in_responsibilities:
            section_lines.append(line)

    if not section_lines:
        return []

    has_bullets = any(_BULLET_RE.match(line) for line in section_lines)
    items: list[str] = []
    if has_bullets:
        current = ""
        for line in section_lines:
            bullet = _BULLET_RE.match(line)
            if bullet:
                if current:
                    items.append(current)
                current = _clean_item(bullet.group(1))
                continue
            continuation = _clean_item(line)
            if continuation and current:
                current = _clean_item(f"{current} {continuation}")
            elif not continuation and current:
                items.append(current)
                current = ""
        if current:
            items.append(current)
    else:
        paragraphs = re.split(r"\n\s*\n", "\n".join(section_lines).strip())
        items = [_clean_item(paragraph) for paragraph in paragraphs]

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = _clean_item(item)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean[:1000])
        if len(out) >= 20:
            break
    return out


def hydrate_related_role_draft_from_saved_spec(
    brief: Any,
    job_spec_text: str | None = None,
) -> bool:
    """Backfill safe cloned-spec fields on a related-role draft.

    Older related-role drafts stored the cloned JD only in
    ``agent_state.jd_override``.  Newer drafts also keep it in ``raw_input`` so
    chat can revisit it.  This helper supports both shapes and only fills an
    empty structured responsibilities list from an explicitly headed section;
    recruiter-confirmed values are never replaced.

    Returns ``True`` when a structured responsibilities value was hydrated.
    ``raw_input`` may also be restored while returning ``False``; callers use
    the boolean specifically to decide whether meaningful source extraction
    occurred. The caller owns flushing and committing because this helper is
    also used while creating a new draft.
    """

    if not getattr(brief, "source_role_id", None):
        return False
    state = dict(getattr(brief, "agent_state", None) or {})
    saved_spec = str(
        job_spec_text
        if job_spec_text is not None
        else (
            state.get("jd_override")
            or getattr(brief, "raw_input", None)
            or ""
        )
    ).strip()
    if not saved_spec:
        return False

    if not str(getattr(brief, "raw_input", None) or "").strip():
        brief.raw_input = saved_spec

    custom = dict(getattr(brief, "custom_fields", None) or {})
    if not custom.get("responsibilities"):
        responsibilities = extract_explicit_responsibilities(saved_spec)
        if responsibilities:
            custom["responsibilities"] = responsibilities
            brief.custom_fields = custom
            return True
    return False


__all__ = [
    "extract_explicit_responsibilities",
    "hydrate_related_role_draft_from_saved_spec",
]
