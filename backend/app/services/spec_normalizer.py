"""Parse a job spec into Description / Requirements / Benefits sections.

Pure regex; no Claude call. Handles common heading variants. When no
recognized headings appear, the entire text is returned as ``description``
and Requirements/Benefits are empty — that signals the recruiter to add
explicit must-haves rather than the system silently anchoring on boilerplate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bucket vocabulary mirrors app.models.org_criterion (imported lazily where the
# ORM is needed; duplicated as plain strings here to keep this module ORM-free
# and cheap to unit-test).
_BUCKET_MUST = "must"
_BUCKET_PREFERRED = "preferred"
_BUCKET_CONSTRAINT = "constraint"


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


# Sub-heading lines inside the Requirements block. Two kinds, both dropped
# (never emitted as criteria):
#   * mode-setters — an EXPLICIT "Must have:" / "Nice to have:" / "Constraints:"
#     sub-heading tells us the bucket for the lines that follow.
#   * generic junk headers — "Requirements", "Qualifications", "Skills" etc.
#     that leak in; dropped WITHOUT changing the bucket, because a plain bullet
#     under a generic "Requirements" heading should stay conservatively
#     preferred (we never auto-promote ambiguous lines to a hard must-have).
_SUBHEAD_MUST = re.compile(
    r"^(?:must[\s-]?haves?|required|essentials?|mandatory)\s*:?\s*$",
    re.IGNORECASE,
)
_SUBHEAD_PREFERRED = re.compile(
    r"^(?:nice[\s-]?to[\s-]?haves?|preferred(?:\s+qualifications?)?|bonus(?:\s+points)?"
    r"|good[\s-]?to[\s-]?haves?|desirable|plus(?:es)?|pluses|ideal(?:ly)?)\s*:?\s*$",
    re.IGNORECASE,
)
_SUBHEAD_CONSTRAINT = re.compile(
    r"^(?:constraints?|eligibility|work\s+authoriz\w*|locations?)\s*:?\s*$",
    re.IGNORECASE,
)
_JUNK_HEADER = re.compile(
    r"^(?:requirements?|qualifications?|minimum\s+qualifications?|responsibilities"
    r"|key\s+responsibilities|skills?|technical\s+skills?|description|about(?:\s+the\s+role)?"
    r"|overview|summary|what\s+you'?ll\s+do|what\s+we'?re\s+looking\s+for"
    r"|benefits?|perks?|compensation)\s*:?\s*$",
    re.IGNORECASE,
)

# Per-line bucket signals.
_YEARS_RE = re.compile(r"\b\d+\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_MUST_HINT = re.compile(
    r"\b(?:must|required|require[sd]?|essential|mandatory|minimum|at\s+least|proven"
    r"|demonstrated)\b",
    re.IGNORECASE,
)
_PREFERRED_HINT = re.compile(
    r"\b(?:preferred|nice[\s-]?to[\s-]?have|bonus|a\s+plus|is\s+a\s+plus|ideally"
    r"|desirable|good[\s-]?to[\s-]?have|familiarity)\b",
    re.IGNORECASE,
)
_CONSTRAINT_HINT = re.compile(
    r"\b(?:located\s+in|based\s+in|location|time[\s-]?zone|remote|hybrid|on[\s-]?site"
    r"|onsite|relocat|work\s+authoriz|authoriz(?:ed|ation)\s+to\s+work|visa"
    r"|eligible\s+to\s+work|security\s+clearance|willing\s+to\s+travel)\b",
    re.IGNORECASE,
)

# Lines that are perks/benefits, not selection criteria. The Requirements
# section sometimes bleeds these in (e.g. a recruiter pasted a single blob).
_BENEFIT_NOISE = re.compile(
    r"\b(?:health\s+insurance|dental|vision|401\s*\(?k\)?|pension|paid\s+time\s+off"
    r"|\bpto\b|vacation\s+days?|stock\s+options?|equity|free\s+lunch|gym\s+member"
    r"|wellness\s+stipend|parental\s+leave|signing\s+bonus|competitive\s+salary)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DerivedCriterion:
    text: str
    bucket: str  # one of must / preferred / constraint

    @property
    def must_have(self) -> bool:
        return self.bucket == _BUCKET_MUST


def _classify_bucket(line: str, *, section_mode: str) -> str:
    """Bucket a single requirement line.

    ``section_mode`` is the bucket implied by the most recent sub-heading
    ("Nice to have:" → preferred). Per-line keyword hints override it; a
    location/eligibility phrase always wins as a constraint.
    """
    if _CONSTRAINT_HINT.search(line):
        return _BUCKET_CONSTRAINT
    if _PREFERRED_HINT.search(line):
        return _BUCKET_PREFERRED
    if _MUST_HINT.search(line) or _YEARS_RE.search(line):
        return _BUCKET_MUST
    if section_mode in (_BUCKET_MUST, _BUCKET_PREFERRED, _BUCKET_CONSTRAINT):
        return section_mode
    # Default conservatively to preferred — never auto-promote an ambiguous
    # line to a must-have (a spurious must-have causes hard rejects).
    return _BUCKET_PREFERRED


def derive_criteria(
    requirements_section: str | None, *, max_items: int = 16
) -> list[DerivedCriterion]:
    """Parse the Requirements section into bucketed criteria.

    Improvements over the raw text split:
      - drops leaked sub-heading lines ("Requirements", "Nice to have:") and
        uses them to set the bucket of the lines that follow;
      - drops perks/benefits noise that bled into the section;
      - classifies each line into must / preferred / constraint via keyword
        + years-of-experience heuristics (default preferred — we never
        auto-promote an ambiguous line to a must-have).
    """
    if not requirements_section:
        return []

    parts = re.split(r"[\n;]+", str(requirements_section))
    items: list[DerivedCriterion] = []
    seen: set[str] = set()
    section_mode = ""  # no sub-heading seen yet
    for raw in parts:
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[\).\-\s])\s*", "", str(raw or "")).strip()
        if not cleaned:
            continue
        compact = re.sub(r"\s+", " ", cleaned)

        # Mode-setting sub-heading: set the bucket for following lines, drop it.
        if _SUBHEAD_MUST.match(compact):
            section_mode = _BUCKET_MUST
            continue
        if _SUBHEAD_PREFERRED.match(compact):
            section_mode = _BUCKET_PREFERRED
            continue
        if _SUBHEAD_CONSTRAINT.match(compact):
            section_mode = _BUCKET_CONSTRAINT
            continue
        # Generic junk header ("Requirements", "Skills", "Benefits") or a leaked
        # top-level section header — drop it WITHOUT changing the bucket mode.
        if _JUNK_HEADER.match(compact) or _match_section(compact):
            continue
        # Perks/benefits that bled into Requirements — not a selection signal.
        if _BENEFIT_NOISE.search(compact):
            continue
        # A bare label with no real content ("Responsibilities:", "Skills:").
        if compact.endswith(":") and len(compact.split()) <= 3:
            continue
        # Must contain at least one letter (drop stray "5+", "—", etc.).
        if not re.search(r"[A-Za-z]", compact):
            continue

        lowered = compact.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        bucket = _classify_bucket(compact, section_mode=section_mode)
        items.append(DerivedCriterion(text=compact[:220], bucket=bucket))
        if len(items) >= max_items:
            break
    return items


def derive_criteria_texts(requirements_section: str | None, *, max_items: int = 16) -> list[str]:
    """Back-compat: text-only view of :func:`derive_criteria`.

    Splits on newlines and semicolons, strips bullet markers + junk
    (leaked headers, perks), deduplicates case-insensitively, and clamps
    each item to 220 chars.
    """
    return [c.text for c in derive_criteria(requirements_section, max_items=max_items)]
