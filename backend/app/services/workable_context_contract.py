"""Trusted section boundaries for untrusted Workable evidence.

The text inside every section remains candidate/recruiter supplied and must be
treated as untrusted data.  Only the section name and boundary are trusted:
they are created by :func:`format_workable_context`, rather than rediscovered
later by parsing a rendered string whose body may itself contain tag-shaped
text.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


WORKABLE_SECTION_TAGS = frozenset(
    {
        "WORKABLE_ACTIVITY_LOG",
        "WORKABLE_EDUCATION",
        "WORKABLE_EXPERIENCE",
        "WORKABLE_PROFILE",
        "WORKABLE_QUESTIONNAIRE_ANSWERS",
        "WORKABLE_RECRUITER_COMMENTS",
        "WORKABLE_SUMMARY",
        "WORKABLE_TAGS",
    }
)

# These sources can contain salary, notice-period, location, relocation, and
# work-authorisation constraints.  Dropping any suffix can therefore turn an
# unsafe/incomplete score into an automated rejection.
PROTECTED_WORKABLE_SECTION_TAGS = frozenset(
    {
        "WORKABLE_ACTIVITY_LOG",
        "WORKABLE_QUESTIONNAIRE_ANSWERS",
        "WORKABLE_RECRUITER_COMMENTS",
    }
)

# The ordinary holistic context target is deliberately small for cost and
# latency.  Protected evidence may expand beyond it, but this hard ceiling
# prevents an unbounded candidate/provider payload.  Above the ceiling the
# scoring path fails closed before consulting a result cache or an LLM.
PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS = 32_000


@dataclass(frozen=True)
class WorkableEvidenceSection:
    """One formatter-created boundary around an untrusted evidence body."""

    tag: str
    body: str


def neutralize_workable_delimiters(value: str) -> str:
    """Make untrusted angle-bracket markup unable to create prompt sections.

    Escape ampersands first so a candidate cannot pre-encode a reserved tag and
    rely on a later HTML-style decode.  The evidence remains readable (including
    comparisons such as ``< 30 days``) while literal formatter delimiters remain
    exclusively source-code controlled.
    """

    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_workable_section(section: WorkableEvidenceSection) -> str:
    """Render one trusted boundary with a neutralized untrusted body."""

    return (
        f"<{section.tag}>\n"
        f"{neutralize_workable_delimiters(section.body)}\n"
        f"</{section.tag}>"
    )


class StructuredWorkableContext(str):
    """A string-compatible context carrying formatter-trusted boundaries.

    It remains a :class:`str` so existing pre-screen, search, and scoring
    callers retain their public contract.  Holistic compaction can inspect
    :attr:`evidence_sections` without parsing candidate-controlled markup.
    """

    evidence_sections: tuple[WorkableEvidenceSection, ...]

    def __new__(
        cls,
        sections: Iterable[WorkableEvidenceSection | tuple[str, str]],
    ) -> StructuredWorkableContext:
        normalized: list[WorkableEvidenceSection] = []
        for value in sections:
            section = (
                value
                if isinstance(value, WorkableEvidenceSection)
                else WorkableEvidenceSection(*value)
            )
            if section.tag not in WORKABLE_SECTION_TAGS:
                raise ValueError(f"Unsupported Workable evidence tag: {section.tag}")
            normalized.append(
                WorkableEvidenceSection(tag=section.tag, body=str(section.body))
            )

        evidence_sections = tuple(normalized)
        rendered = "\n\n".join(
            render_workable_section(section) for section in evidence_sections
        )
        instance = super().__new__(cls, rendered)
        instance.evidence_sections = evidence_sections
        return instance

    def __reduce__(self):
        """Preserve trusted boundaries if a task boundary pickles the value."""

        return (type(self), (self.evidence_sections,))


__all__ = [
    "PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS",
    "PROTECTED_WORKABLE_SECTION_TAGS",
    "StructuredWorkableContext",
    "WORKABLE_SECTION_TAGS",
    "WorkableEvidenceSection",
    "neutralize_workable_delimiters",
    "render_workable_section",
]
