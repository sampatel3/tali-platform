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
_MUST_HINT = re.compile(
    # Only DECISIVE wording auto-promotes to must-have. Soft signals — "minimum",
    # "at least", "proven", "demonstrated" — are deliberately excluded: they're
    # boilerplate JD phrasing ("minimum 5 years", "proven track record") that
    # over-promotes tenure/soft criteria into hard rejects. Recruiters promote
    # those deliberately.
    r"\b(?:must|require[sd]?|essential|mandatory)\b",
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

# Inline markdown to strip from a candidate line before classifying it, so a
# bolded lead-in ("**You will have experience in:**") is seen as the bare
# header it is (then dropped on its trailing colon), and a bolded real bullet
# ("**AWS Glue**") is stored as clean text. Internal single _/* are preserved
# so identifiers like "spark_sql" survive.
_LEADING_MD_HEADING = re.compile(r"^\s*#{1,6}\s*")
_BOLD_MARKERS = re.compile(r"\*\*|__")
_EDGE_EMPHASIS = re.compile(r"^[\*_\s]+|[\*_\s]+$")

# A bare connective / filler left behind by a naive line split ("and", "or",
# a lone "etc."). Never a selection criterion on its own.
_CONNECTIVE_ONLY = re.compile(
    r"^(?:and|or|but|nor|so|yet|plus|also|including|as\s+well\s+as|and\s*/\s*or"
    r"|etc\.?|i\.?\s*e\.?|e\.?\s*g\.?)$",
    re.IGNORECASE,
)

# Narrative/boilerplate prose that bleeds in from culture/mission paragraphs
# ("As an AI consultancy, our greatest asset…", "While technical mastery is
# the foundation…"). Dropped only when it BOTH opens like a sentence AND runs
# long, so terse real requirements never trip it.
_PROSE_OPENER = re.compile(
    r"^(?:as|while|whilst|if|our|we|at|it|this|that|these|those|here|there|whether"
    r"|because|since|although|though|you\s+will|you'?ll|we'?re|we\s+are)\b",
    re.IGNORECASE,
)


def _strip_inline_md(text: str) -> str:
    """Strip markdown emphasis/heading markers from a single candidate line."""
    s = _LEADING_MD_HEADING.sub("", text)
    s = _BOLD_MARKERS.sub("", s)
    s = _EDGE_EMPHASIS.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


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
    if _MUST_HINT.search(line):
        return _BUCKET_MUST
    if section_mode in (_BUCKET_MUST, _BUCKET_PREFERRED, _BUCKET_CONSTRAINT):
        return section_mode
    # Default conservatively to preferred — never auto-promote an ambiguous
    # line to a must-have (a spurious must-have causes hard rejects). A bare
    # "N years experience" line is deliberately NOT auto-promoted: years alone
    # is an ambiguous signal, and silently turning it into a hard bar triggers
    # reject waves on a bulk re-derive. Must-haves stay an explicit call —
    # either "must/required" wording in the spec, or a recruiter promotion.
    return _BUCKET_PREFERRED


def derive_criteria(
    requirements_section: str | None, *, max_items: int = 16
) -> list[DerivedCriterion]:
    """Parse the Requirements section into bucketed criteria.

    Improvements over the raw text split:
      - strips inline markdown, then drops markdown/header lead-ins (anything
        ending in a colon), leaked sub-headings ("Requirements",
        "Nice to have:"), bare connectives ("and"), perks/benefits, and
        culture/mission boilerplate prose;
      - uses explicit "Must have:" / "Nice to have:" sub-headings to set the
        bucket of the lines that follow;
      - classifies each surviving line into must / preferred / constraint via
        keyword heuristics (default preferred — we never auto-promote an
        ambiguous line, including a bare "N years" line, to a must-have).
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
        # Strip inline markdown so a bolded lead-in ("**You will have
        # experience in:**") is seen as the bare header it is, and a bolded
        # real bullet ("**AWS Glue**") is stored clean.
        compact = _strip_inline_md(compact)
        if not compact:
            continue

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
        # A lead-in label that introduces a list — the trailing colon marks it
        # as a header, not a requirement ("You will have experience in:",
        # "You should also have knowledge of:", "Skills:"). Drop it.
        if compact.endswith(":"):
            continue
        # A bare connective left over from a naive line split ("and", "or").
        if _CONNECTIVE_ONLY.match(compact):
            continue
        # Must contain at least one letter (drop stray "5+", "—", etc.).
        if not re.search(r"[A-Za-z]", compact):
            continue
        # Culture/mission boilerplate prose ("As an AI consultancy, our
        # greatest asset…"): a sentence opener with real length. A terse
        # requirement never trips both conditions.
        if _PROSE_OPENER.match(compact) and len(compact.split()) >= 12:
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
