"""CV fraud signals computed deterministically inside the pre-screen agent.

Today the only signal is *job-spec copy-paste* — n-gram overlap between the
candidate's CV text and the role's job description. Catches CVs where the
candidate has pasted chunks of the JD verbatim to game keyword-matching ATS
filters.

The detector is deliberately deterministic (no LLM, no perplexity guess) so
its output can be cited as evidence in the standing report. When a signal
fires, the pre-screen agent caps the score below ``PRE_SCREEN_THRESHOLD`` so
the candidate is filtered before the expensive v3 CV-match call runs.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# 8-word windows are short enough to catch lifted sentences but long enough
# that generic phrasing ("strong written and verbal communication skills")
# rarely creates false positives. Tuned empirically on real recruiter data;
# revisit if we see noise.
_NGRAM_SIZE = 8

# Cap the number of evidence snippets we persist on the application — the
# report only ever shows a few, and a candidate who copy-pasted half the
# spec doesn't need 200 separate matches in the DB.
_MAX_EVIDENCE_SNIPPETS = 10

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class FraudEvidenceSnippet:
    """One contiguous run of CV tokens that matched a JD n-gram window.

    Offsets are *word* offsets (not character offsets) so callers can
    re-tokenize and highlight without worrying about whitespace drift.
    """

    text: str
    cv_word_offset: int
    jd_word_offset: int
    word_count: int


@dataclass
class CopyPasteResult:
    score: float  # matched_chars / total_cv_chars, 0.0–1.0
    matched_chars: int
    cv_chars: int
    triggered: bool
    threshold: float
    evidence: list[FraudEvidenceSnippet] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "matched_chars": self.matched_chars,
            "cv_chars": self.cv_chars,
            "triggered": self.triggered,
            "threshold": self.threshold,
            "evidence": [asdict(snippet) for snippet in self.evidence],
        }


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def detect_cv_copy_paste(
    cv_text: str,
    jd_text: str,
    *,
    threshold: float = 0.05,
    ngram_size: int = _NGRAM_SIZE,
    max_evidence: int = _MAX_EVIDENCE_SNIPPETS,
) -> CopyPasteResult:
    """Score how much of the CV is lifted verbatim from the job description.

    Algorithm: build the set of N-grams from the JD, then walk the CV. When
    we hit a JD N-gram, extend the match forward as far as the words keep
    matching (so a copy-pasted paragraph counts as one snippet, not dozens
    of overlapping windows). Score is matched-CV-chars / total-CV-chars.
    """
    cv_chars = len(cv_text or "")
    cv_tokens = _tokenize(cv_text)
    jd_tokens = _tokenize(jd_text)

    if len(cv_tokens) < ngram_size or len(jd_tokens) < ngram_size:
        return CopyPasteResult(
            score=0.0,
            matched_chars=0,
            cv_chars=cv_chars,
            triggered=False,
            threshold=threshold,
        )

    # Map each JD n-gram to its first word offset for evidence reconstruction.
    jd_ngrams: dict[tuple[str, ...], int] = {}
    for i in range(len(jd_tokens) - ngram_size + 1):
        gram = tuple(jd_tokens[i : i + ngram_size])
        if gram not in jd_ngrams:
            jd_ngrams[gram] = i

    evidence: list[FraudEvidenceSnippet] = []
    matched_word_chars = 0
    i = 0
    while i <= len(cv_tokens) - ngram_size:
        gram = tuple(cv_tokens[i : i + ngram_size])
        jd_offset = jd_ngrams.get(gram)
        if jd_offset is None:
            i += 1
            continue
        # Extend the match forward while tokens keep matching on both sides.
        end = i + ngram_size
        jd_end = jd_offset + ngram_size
        while (
            end < len(cv_tokens)
            and jd_end < len(jd_tokens)
            and cv_tokens[end] == jd_tokens[jd_end]
        ):
            end += 1
            jd_end += 1
        snippet_tokens = cv_tokens[i:end]
        snippet_text = " ".join(snippet_tokens)
        matched_word_chars += len(snippet_text)
        if len(evidence) < max_evidence:
            evidence.append(
                FraudEvidenceSnippet(
                    text=snippet_text,
                    cv_word_offset=i,
                    jd_word_offset=jd_offset,
                    word_count=end - i,
                )
            )
        i = end  # skip past the match — no overlapping double-count

    score = (matched_word_chars / cv_chars) if cv_chars else 0.0
    triggered = score >= threshold
    return CopyPasteResult(
        score=score,
        matched_chars=matched_word_chars,
        cv_chars=cv_chars,
        triggered=triggered,
        threshold=threshold,
        evidence=evidence,
    )


def apply_fraud_penalty(
    score: float | None,
    fraud: CopyPasteResult,
    *,
    cap_score: float,
) -> tuple[float | None, bool]:
    """Cap the pre-screen score below the gate when fraud is detected.

    Returns ``(adjusted_score, was_capped)``. We use a hard cap rather than a
    multiplicative penalty so the gate fires reliably regardless of how
    generous the LLM was — fraud is a hard "skip the expensive scoring"
    signal, not a nuance.
    """
    if not fraud.triggered or score is None:
        return score, False
    if score <= cap_score:
        return score, False  # already below cap, nothing to do
    return cap_score, True


def build_fraud_signals_payload(fraud: CopyPasteResult) -> dict[str, Any]:
    """Shape stored under ``pre_screen_evidence['fraud_signals']``."""
    return {"cv_copy_paste": fraud.to_dict()}
