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
from ..llm import ValidationFailure, fuzzy_locate
from .schemas import (
    Confidence,
    CVMatchResult,
    RequirementAssessment,
    RequirementInput,
    Status,
)

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


# ``ValidationFailure`` and ``fuzzy_locate`` are the shared gateway
# primitives (re-exported above). A cross-field-consistency raise here
# is caught by ``generate_structured`` and triggers the same single
# retry the runner used to drive by hand. ``fuzzy_locate`` is the
# verbatim-quote matcher cv_matching used to own — promoted to the
# gateway so pre-screen / agent / chat can ground claims the same way.


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
            located = fuzzy_locate(quote, cv_text)
            if located is None:
                logger.info(
                    "Dropped unverifiable quote on requirement %s: %r",
                    assessment.requirement_id,
                    quote[:80],
                )
                continue
            cleaned.append(quote)
            if first_idx < 0:
                first_idx = located[0]
                first_len = located[1] - located[0]

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
        requirements_by_id = {r.id: r for r in requirements}
        # Drop model-emitted assessments whose id isn't a recruiter id. A
        # typo'd / case-drifted requirement_id would otherwise survive in
        # ``requirements_assessment`` *and* get a synthesised placeholder
        # for the genuine id below — yielding extra/duplicate criteria.
        extra = seen_ids - recruiter_ids
        if extra:
            for stray_id in sorted(extra):
                logger.info(
                    "Dropped assessment for unknown requirement_id: %r",
                    stray_id,
                )
            result.requirements_assessment = [
                a for a in result.requirements_assessment
                if a.requirement_id in recruiter_ids
            ]
            seen_ids = seen_ids & recruiter_ids

        # Priority and requirement text are recruiter-owned inputs, never model
        # judgments.  The model is asked to echo them for readability but may
        # promote "preferred" to "must_have"; persisting that echo lets the
        # downstream hard-reject policy silently rewrite recruiter intent.
        # Canonicalize every surviving assessment before aggregation/persist.
        for assessment in result.requirements_assessment:
            canonical = requirements_by_id.get(assessment.requirement_id)
            if canonical is None:
                continue
            assessment.priority = canonical.priority
            assessment.requirement = canonical.requirement

        missing = recruiter_ids - seen_ids
        if missing:
            # **2026-05-22 — cost-optimization**. Previously raised
            # ValidationFailure for any missing criterion id, which on
            # 2026-05-21 caused 3,281 cv_match runs to fail validation
            # (84% of all scoring attempts) — each consuming Anthropic
            # tokens for two attempts (original + retry) and producing
            # zero usable output. ~$41 of Haiku spend that day was
            # validator-rejected work.
            #
            # New behaviour: synthesize an ``UNKNOWN`` placeholder for
            # each missing requirement (status=unknown, match_tier=missing,
            # empty evidence) so the rest of the assessment is usable.
            # The recruiter sees the partial result with un-assessed
            # criteria flagged, instead of a hard error and no score.
            # Only escalate to ValidationFailure if more than half the
            # criteria are missing — that's a genuine model failure
            # worth retrying.
            severity = len(missing) / max(1, len(recruiter_ids))
            if severity > 0.5:
                raise ValidationFailure(
                    f"Recruiter requirements missing from assessment "
                    f"(severe: {len(missing)}/{len(recruiter_ids)}): "
                    f"{sorted(missing)}"
                )
            # Partial miss → fill in placeholders, don't reject.
            for missing_id in missing:
                req = requirements_by_id[missing_id]
                result.requirements_assessment.append(
                    RequirementAssessment(
                        requirement_id=missing_id,
                        requirement=req.requirement,
                        priority=req.priority,
                        evidence_quotes=[],
                        reasoning="(not assessed by model — synthesised by validator after partial response)",
                        status=Status.UNKNOWN,
                        match_tier="missing",
                        impact="",
                        confidence=Confidence.LOW,
                    )
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
