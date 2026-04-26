"""Parse a job spec into Description / Requirements / Benefits sections.

Pure regex; no Claude call. Handles common heading variants. When no
recognized headings appear, the entire text is returned as ``description``
and Requirements/Benefits are empty — that signals the recruiter to add
explicit must-haves rather than the system silently anchoring on boilerplate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_HEADING_PATTERNS: dict[str, list[str]] = {
    "description": [
        r"^\s*(?:job\s+)?description\s*:?\s*$",
        r"^\s*about\s+(?:the\s+)?role\s*:?\s*$",
        r"^\s*about\s+(?:the\s+)?(?:position|job)\s*:?\s*$",
        r"^\s*overview\s*:?\s*$",
        r"^\s*summary\s*:?\s*$",
        r"^\s*role\s+summary\s*:?\s*$",
    ],
    "requirements": [
        r"^\s*requirements?\s*:?\s*$",
        r"^\s*qualifications?\s*:?\s*$",
        r"^\s*minimum\s+qualifications?\s*:?\s*$",
        r"^\s*what\s+(?:we'?re\s+looking\s+for|you'?ll\s+need|you\s+bring)\s*:?\s*$",
        r"^\s*must[\s-]?have(?:s)?\s*:?\s*$",
        r"^\s*responsibilities\s*:?\s*$",
        r"^\s*key\s+responsibilities\s*:?\s*$",
        r"^\s*skills?\s*:?\s*$",
    ],
    "benefits": [
        r"^\s*benefits?\s*:?\s*$",
        r"^\s*perks?\s*:?\s*$",
        r"^\s*what\s+(?:we|you)\s+offer\s*:?\s*$",
        r"^\s*compensation\s*:?\s*$",
        r"^\s*(?:salary|pay)\s+(?:range|details?)\s*:?\s*$",
    ],
}

_MARKDOWN_HEADING_PREFIX = re.compile(r"^\s*#{1,6}\s+")
_BOLD_WRAPPER = re.compile(r"^\s*\*{1,3}\s*(.+?)\s*\*{1,3}\s*$")


@dataclass
class NormalizedSpec:
    description: str
    requirements: str
    benefits: str


def _normalize_heading_candidate(line: str) -> str:
    stripped = _MARKDOWN_HEADING_PREFIX.sub("", line).strip()
    bold = _BOLD_WRAPPER.match(stripped)
    if bold:
        stripped = bold.group(1).strip()
    return stripped


def _match_section(line: str) -> str | None:
    candidate = _normalize_heading_candidate(line)
    if not candidate:
        return None
    for section, patterns in _HEADING_PATTERNS.items():
        for pattern in patterns:
            if re.match(pattern, candidate, flags=re.IGNORECASE):
                return section
    return None


def normalize_spec(text: str | None) -> NormalizedSpec:
    if not text or not str(text).strip():
        return NormalizedSpec(description="", requirements="", benefits="")

    sections: dict[str, list[str]] = {
        "_pre": [],
        "description": [],
        "requirements": [],
        "benefits": [],
    }
    current = "_pre"

    for line in str(text).splitlines():
        section = _match_section(line)
        if section:
            current = section
            continue
        sections[current].append(line)

    description = "\n".join(sections["_pre"] + sections["description"]).strip()
    requirements = "\n".join(sections["requirements"]).strip()
    benefits = "\n".join(sections["benefits"]).strip()
    return NormalizedSpec(description=description, requirements=requirements, benefits=benefits)


def derive_criteria_texts(requirements_section: str | None, *, max_items: int = 16) -> list[str]:
    """Pull bullet-style criteria out of the Requirements section text.

    Splits on newlines and semicolons, strips bullet markers, deduplicates
    case-insensitively, and clamps each item to 220 chars.
    """
    if not requirements_section:
        return []

    parts = re.split(r"[\n;]+", str(requirements_section))
    items: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[\).\-\s])\s*", "", str(raw or "")).strip()
        if not cleaned:
            continue
        compact = re.sub(r"\s+", " ", cleaned)
        lowered = compact.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(compact[:220])
        if len(items) >= max_items:
            break
    return items
