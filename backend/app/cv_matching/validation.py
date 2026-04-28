"""Validation: evidence grounding, cross-field consistency, injection scanner.

Three layers run after the LLM responds and the JSON parses:

1. ``validate_evidence_grounding`` — every met/partially_met requirement
   has its quotes verified verbatim against the CV text. Quotes that
   don't appear are dropped; if no quotes survive on a requirement, it
   downgrades to ``unknown``.
2. ``validate_cross_field_consistency`` — schema-internal invariants the
   Pydantic model can't express.
3. ``scan_for_injection`` / ``check_suspicious_score`` — heuristic
   prompt-injection defense + thin-CV / high-score sanity check.

A consistency failure raises ``ValidationFailure``; the runner catches that
and triggers the (single) retry with the error appended to the prompt.
Grounding failures are not raised — they're logged and the assessment is
mutated in place.
"""

from __future__ import annotations

import logging
import re

from .schemas import CVMatchResult, RequirementInput, Status

logger = logging.getLogger("taali.cv_match.validation")


_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+previous", re.IGNORECASE),
    re.compile(r"ignore\s+all", re.IGNORECASE),
    re.compile(r"\bsystem:\s", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"new\s+instructions", re.IGNORECASE),
]

_SUSPICIOUS_SCORE_THRESHOLD = 95.0
_THIN_CV_WORD_COUNT = 200


class ValidationFailure(RuntimeError):
    """Raised when cross-field consistency fails. Triggers a single retry."""


def validate_evidence_grounding(result: CVMatchResult, cv_text: str) -> int:
    """Verify each met/partially_met requirement has verbatim CV quotes.

    Mutates ``result.requirements_assessment`` in place: quotes that don't
    appear in the CV are dropped from the list. If no quotes survive on a
    met/partially_met assessment, the status is downgraded to ``unknown``.

    Returns the number of assessments that were downgraded.
    """
    downgraded = 0
    cv_text = cv_text or ""

    for assessment in result.requirements_assessment:
        if assessment.status not in (Status.MET, Status.PARTIALLY_MET):
            continue

        cleaned: list[str] = []
        first_idx = -1
        first_len = 0
        for raw_quote in assessment.evidence_quotes or []:
            quote = (raw_quote or "").strip()
            if not quote:
                continue
            idx = cv_text.find(quote)
            if idx < 0:
                logger.warning(
                    "Dropped hallucinated quote on requirement %s: %r",
                    assessment.requirement_id,
                    quote[:80],
                )
                continue
            cleaned.append(quote)
            if first_idx < 0:
                first_idx = idx
                first_len = len(quote)

        if not cleaned:
            assessment.status = Status.UNKNOWN
            assessment.match_tier = "missing"
            assessment.evidence_quotes = []
            assessment.evidence_start_char = -1
            assessment.evidence_end_char = -1
            downgraded += 1
            continue

        assessment.evidence_quotes = cleaned
        assessment.evidence_start_char = first_idx
        assessment.evidence_end_char = first_idx + first_len

    return downgraded


def validate_cross_field_consistency(
    result: CVMatchResult,
    requirements: list[RequirementInput],
) -> None:
    """Schema-internal invariants. Raises ``ValidationFailure`` on first issue.

    Enforces:
    - top-level scores in 0-100 (Pydantic also enforces — defense in depth)
    - met/partially_met requirements have non-empty evidence_quotes
    - ``match_tier == "missing"`` only valid when status in (MISSING, UNKNOWN)
    - every supplied recruiter requirement appears in the assessment
    - assessment requirement_ids are unique
    """
    if not (0 <= result.skills_match_score <= 100):
        raise ValidationFailure(
            f"skills_match_score out of range: {result.skills_match_score}"
        )
    if not (0 <= result.experience_relevance_score <= 100):
        raise ValidationFailure(
            f"experience_relevance_score out of range: {result.experience_relevance_score}"
        )

    seen_ids: set[str] = set()
    for assessment in result.requirements_assessment:
        if assessment.requirement_id in seen_ids:
            raise ValidationFailure(
                f"Duplicate requirement_id in assessment: {assessment.requirement_id}"
            )
        seen_ids.add(assessment.requirement_id)

        if assessment.status in (Status.MET, Status.PARTIALLY_MET):
            if not assessment.evidence_quotes:
                raise ValidationFailure(
                    f"Requirement {assessment.requirement_id} has status="
                    f"{assessment.status.value} but evidence_quotes is empty"
                )

        if assessment.match_tier == "missing" and assessment.status not in (
            Status.MISSING,
            Status.UNKNOWN,
        ):
            raise ValidationFailure(
                f"Requirement {assessment.requirement_id} has match_tier='missing' "
                f"but status='{assessment.status.value}' — these must align"
            )

    if requirements:
        recruiter_ids = {r.id for r in requirements}
        missing = recruiter_ids - seen_ids
        if missing:
            raise ValidationFailure(
                f"Recruiter requirements missing from assessment: {sorted(missing)}"
            )


def scan_for_injection(cv_text: str) -> bool:
    """Heuristic scan for prompt-injection-flavored phrases in CV text."""
    if not cv_text:
        return False
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cv_text):
            return True
    return False


def check_suspicious_score(
    *, requirements_match_score: float, cv_text: str
) -> bool:
    """Sanity check: very high score on a thin CV is suspicious."""
    if requirements_match_score < _SUSPICIOUS_SCORE_THRESHOLD:
        return False
    word_count = len((cv_text or "").split())
    return word_count < _THIN_CV_WORD_COUNT
