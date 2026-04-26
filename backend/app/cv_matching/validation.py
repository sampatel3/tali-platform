"""Validation: evidence grounding, cross-field consistency, injection scanner.

Three layers run after the LLM responds and the JSON parses:

1. ``validate_evidence_grounding`` — every met/partially_met requirement must
   have a verbatim CV substring. Failed substring checks downgrade status to
   ``unknown`` (we trust nothing the LLM hallucinated). Offsets are re-resolved
   against the CV text rather than trusted from the LLM.
2. ``validate_cross_field_consistency`` — schema-internal invariants the
   Pydantic model can't express (e.g. "every met has non-empty quote",
   "every recruiter requirement appears in the assessment").
3. ``scan_for_injection`` / ``check_suspicious_score`` — Phase 7 prompt
   injection defense. Sets flags on the output; never blocks.

A consistency failure raises ``ValidationFailure``; the runner catches that
and triggers the (single) retry with the error appended to the prompt.
Grounding failures are not raised — they're logged and the assessment is
mutated in place (status downgraded). The intent: the LLM did its job, we
just can't trust its quote, so we mark the requirement unknown rather than
discarding the whole run.
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
    """Verify each met/partially_met requirement has a verbatim CV substring.

    Mutates ``result.requirements_assessment`` in place: assessments whose
    ``evidence_quote`` cannot be located in the CV (substring match, case-
    sensitive) are downgraded to ``Status.UNKNOWN`` with offsets reset.

    The LLM may report incorrect offsets even when the quote *is* in the CV.
    We re-resolve offsets via ``cv_text.find(quote)`` and overwrite both
    ``evidence_start_char`` and ``evidence_end_char``. This means the LLM's
    self-reported offsets are advisory only.

    Returns the number of assessments that were downgraded.
    """
    downgraded = 0
    cv_text = cv_text or ""

    for assessment in result.requirements_assessment:
        if assessment.status not in (Status.MET, Status.PARTIALLY_MET):
            # Missing/unknown don't require evidence.
            continue

        quote = (assessment.evidence_quote or "").strip()
        if not quote:
            # met/partially_met without any quote → can't verify, downgrade.
            assessment.status = Status.UNKNOWN
            assessment.evidence_quote = ""
            assessment.evidence_start_char = -1
            assessment.evidence_end_char = -1
            downgraded += 1
            logger.info(
                "Downgraded requirement %s: status was %s but evidence_quote was empty",
                assessment.requirement_id,
                assessment.status.value,
            )
            continue

        idx = cv_text.find(quote)
        if idx < 0:
            # LLM hallucinated the quote.
            logger.warning(
                "Downgraded requirement %s: evidence_quote not a verbatim CV substring",
                assessment.requirement_id,
            )
            assessment.status = Status.UNKNOWN
            assessment.evidence_quote = ""
            assessment.evidence_start_char = -1
            assessment.evidence_end_char = -1
            downgraded += 1
            continue

        # Re-resolve offsets — LLM offsets are advisory.
        assessment.evidence_start_char = idx
        assessment.evidence_end_char = idx + len(quote)

    return downgraded


def validate_cross_field_consistency(
    result: CVMatchResult,
    requirements: list[RequirementInput],
) -> None:
    """Schema-internal invariants. Raises ``ValidationFailure`` on first issue.

    Enforces:
    - scores in 0-100 (Pydantic already does this, defense in depth)
    - met/partially_met requirements have non-empty evidence_quote
    - every supplied recruiter requirement appears in the assessment
    - assessment requirement_ids are unique

    We don't enforce "no nulls" — Pydantic's defaults handle that on parse.
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
            if not assessment.evidence_quote:
                raise ValidationFailure(
                    f"Requirement {assessment.requirement_id} has status={assessment.status.value} "
                    "but evidence_quote is empty"
                )

    if requirements:
        recruiter_ids = {r.id for r in requirements}
        missing = recruiter_ids - seen_ids
        if missing:
            raise ValidationFailure(
                f"Recruiter requirements missing from assessment: {sorted(missing)}"
            )


def scan_for_injection(cv_text: str) -> bool:
    """Heuristic scan for prompt-injection-flavored phrases in CV text.

    Returns True if a known pattern matches. Caller surfaces this as a flag
    on ``CVMatchOutput.injection_suspected`` — does not block.
    """
    if not cv_text:
        return False
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cv_text):
            return True
    return False


def check_suspicious_score(
    *, requirements_match_score: float, cv_text: str
) -> bool:
    """Sanity check: very high score on a thin CV is suspicious.

    True when ``requirements_match_score >= 95`` AND CV is < 200 words.
    Caller surfaces this as ``CVMatchOutput.suspicious_score``.
    """
    if requirements_match_score < _SUSPICIOUS_SCORE_THRESHOLD:
        return False
    word_count = len((cv_text or "").split())
    return word_count < _THIN_CV_WORD_COUNT
