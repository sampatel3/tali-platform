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
from difflib import SequenceMatcher

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

# Fuzzy-quote-match threshold. The LLM frequently paraphrases when
# generating ``evidence_quotes``: a phrase that's nearly verbatim in
# the CV (off by a word, an extra space, a hyphen) gets emitted as a
# quote that doesn't strictly substring-match. Strict matching dropped
# legitimate evidence; fuzzy matching with a high similarity threshold
# preserves the grounding intent (the quote must substantially appear
# in the CV) without punishing close paraphrases.
_FUZZY_THRESHOLD = 0.85
# Window scan: for each LLM quote, slide a CV window of similar length
# and check the best similarity. _FUZZY_WINDOW_PAD is how much extra
# CV context to consider on either side of the quote-length window.
_FUZZY_WINDOW_PAD = 20


def _normalise_for_fuzzy(s: str) -> str:
    """Collapse whitespace and lowercase for similarity comparison."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _fuzzy_locate(quote: str, cv_text: str) -> tuple[int, int] | None:
    """Find the best fuzzy match for ``quote`` in ``cv_text``.

    Returns ``(start, end)`` of the best matching CV span if similarity
    >= ``_FUZZY_THRESHOLD``, else ``None``. Exact substring hits are
    fast-pathed; only quotes that miss the exact path pay the
    O(len(cv_text)) sliding-window cost.
    """
    if not quote or not cv_text:
        return None
    # Fast path: exact substring (most quotes hit this).
    idx = cv_text.find(quote)
    if idx >= 0:
        return (idx, idx + len(quote))

    # Slow path: case-insensitive whitespace-normalised substring.
    cv_lower = cv_text.lower()
    quote_lower = quote.lower()
    idx = cv_lower.find(quote_lower)
    if idx >= 0:
        return (idx, idx + len(quote))

    # Slowest path: sliding-window fuzzy match.
    quote_norm = _normalise_for_fuzzy(quote)
    if not quote_norm or len(quote_norm) < 8:
        # Don't fuzzy-match tiny quotes — too easy to false-positive.
        return None

    cv_norm = _normalise_for_fuzzy(cv_text)
    matcher = SequenceMatcher(None, cv_norm, quote_norm, autojunk=False)
    blocks = matcher.get_matching_blocks()
    if not blocks:
        return None
    # The longest matching block tells us where in the CV the quote
    # most likely came from. Build a window around it and score
    # similarity to decide whether to accept.
    longest = max(blocks, key=lambda b: b.size)
    if longest.size == 0:
        return None
    win_start = max(0, longest.a - _FUZZY_WINDOW_PAD)
    win_end = min(len(cv_norm), longest.a + len(quote_norm) + _FUZZY_WINDOW_PAD)
    window = cv_norm[win_start:win_end]
    sim = SequenceMatcher(None, window, quote_norm, autojunk=False).ratio()
    if sim < _FUZZY_THRESHOLD:
        return None
    # Re-locate the matched window in the original (unnormalised) cv_text.
    # The normalisation collapsed whitespace, so character offsets shift —
    # we approximate by finding the first matched word from the block.
    pivot_words = quote.split()[:3]
    if pivot_words:
        pivot = " ".join(pivot_words)
        pivot_idx = cv_text.lower().find(pivot.lower())
        if pivot_idx >= 0:
            return (pivot_idx, pivot_idx + len(pivot))
    return (0, min(len(cv_text), len(quote)))


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
            located = _fuzzy_locate(quote, cv_text)
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
