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
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


def persist_fraud_filtered_prescreen(app, fraud: CopyPasteResult, *, cap_score: float) -> dict[str, Any]:
    """Persist a deterministic fraud-gate filter on ``app`` (no LLM ran) and
    return the ``execute_pre_screen_only`` result dict.

    Used when the CV↔JD copy-paste gate fires before the pre-screen LLM —
    the candidate is filtered for free. Mirrors the post-LLM fraud-capped
    persistence, but ``llm_score_100`` is ``None`` (no model call) and the
    evidence is tagged ``gated_by="fraud"``.
    """
    from datetime import datetime, timezone

    from .document_service import sanitize_json_for_storage

    cap = float(cap_score)
    fraud_signals = build_fraud_signals_payload(fraud)
    summary = (
        f"Pre-screen filtered: CV contains {fraud.score:.0%} text copied "
        f"verbatim from the job description (threshold {fraud.threshold:.0%})."
    )
    app.pre_screen_score_100 = cap
    # The fraud-capped score IS the genuine pre-screen verdict; record it in
    # the durable column too (never overwritten by later cv_match scoring).
    app.genuine_pre_screen_score_100 = cap
    app.requirements_fit_score_100 = cap
    app.pre_screen_recommendation = "Below threshold"
    app.pre_screen_error_reason = None
    app.pre_screen_evidence = sanitize_json_for_storage(
        {
            "summary": summary,
            "matching_skills": [],
            "missing_skills": [],
            "concerns": [],
            "score_rationale_bullets": [],
            "requirements_coverage": {},
            "requirements_assessment": [],
            "decision": "no",
            "trace_id": None,
            "prompt_version": None,
            "cache_hit": False,
            "fraud_signals": fraud_signals,
            "fraud_capped": True,
            "llm_score_100": None,
            "gated_by": "fraud",
        }
    )
    app.pre_screen_run_at = datetime.now(timezone.utc)
    app.rank_score = cap
    return {
        "status": "ok",
        "score": cap,
        "recommendation": "Below threshold",
        "decision": "no",
        "reason": summary,
        "cache_hit": False,
        "fraud_capped": True,
        "prompt_version": None,
        "trace_id": None,
        "fraud_signals": fraud_signals,
        "llm_score_100": None,
        "gated_by": "fraud",
    }


def apply_unverified_claim_prescreen_penalty(
    score: float | None,
    flagged: bool,
    *,
    penalty: float,
) -> tuple[float | None, bool]:
    """Soft pre-screen deduction when the gate flags an extraordinary,
    CV-uncorroborated claim. Unlike the copy-paste *cap*, this is a small
    nudge: a few points can't single-handedly drop a plausible candidate
    below the gate. Returns ``(adjusted_score, was_penalised)``.
    """
    if not flagged or score is None or penalty <= 0:
        return score, False
    return max(0.0, round(score - penalty, 2)), True


# ── CV integrity signals (v3 full scoring) ────────────────────────────────────
# Beyond copy-paste, two signals feed the bounded v3 integrity penalty:
#   1. Timeline inconsistencies — deterministic arithmetic over the
#      LLM-extracted ``candidate_snapshot.timeline`` (future dates,
#      end-before-start, impossible single-role spans, too many concurrent
#      "current" roles).
#   2. Unverified extraordinary claims — the v3 model tags these in
#      ``claims_to_verify``; we count the ones it can neither corroborate from
#      the CV nor place as a known event/credential.
# Both are BOUNDED soft penalties, never hard caps. The timeline is
# LLM-extracted and claim familiarity is a model prior, so a false positive
# must cost a candidate at most a few points — enough that fraud can't inflate
# someone into interview, never enough to auto-reject on a shaky signal.
# ──────────────────────────────────────────────────────────────────────────────

# A single role spanning >60 years is a data/parse error or fabrication, not a
# real tenure. Below this we don't flag long tenures (a 40-year career is fine).
_MAX_SINGLE_ROLE_SPAN_YEARS = 60
# Up to two concurrent "current" roles is plausible (e.g. a day job + an
# advisory/board seat). Three or more marked current at once is not.
_MAX_CONCURRENT_CURRENT_ROLES = 2

# Corroboration / familiarity values that make a claim penalisable. Matched
# case-insensitively; ANYTHING not listed fails open (no penalty) so a model
# paraphrase or a real-but-obscure event never costs the candidate points.
_UNCORROBORATED_VALUES = {
    "uncorroborated",
    "not_corroborated",
    "not corroborated",
    "uncorroberated",  # common misspelling
    "unverified",
    "false",
    "no",
    "none",
}
_LOW_FAMILIARITY_VALUES = {
    "unknown",
    "implausible",
    "unverifiable",
    "fabricated",
    "fake",
}


def _attr(entry: Any, key: str) -> Any:
    """Read ``key`` from a pydantic model OR a plain dict."""
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


@dataclass
class TimelineIssue:
    kind: str  # future_date | end_before_start | impossible_span | excess_current
    detail: str


@dataclass
class TimelineResult:
    issues: list[TimelineIssue] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "issues": [asdict(issue) for issue in self.issues],
        }


def detect_timeline_inconsistencies(
    timeline: Iterable[Any] | None,
    *,
    now_year: int | None = None,
) -> TimelineResult:
    """Deterministic sanity checks over the extracted career timeline.

    ``timeline`` entries may be pydantic ``TimelineEntry`` objects or plain
    dicts with ``start_year`` / ``end_year`` / ``is_current`` / ``company`` /
    ``role``. Only unambiguous fabrication tells are flagged. Year-granularity
    *overlap* is deliberately NOT checked: a mid-year job change legitimately
    shows two roles sharing a year and would false-positive constantly.
    """
    issues: list[TimelineIssue] = []
    if not timeline:
        return TimelineResult()

    current_year = now_year if now_year is not None else datetime.now(timezone.utc).year
    # CVs routinely list a near-future grad date or agreed start date.
    future_cutoff = current_year + 1
    current_count = 0

    for entry in timeline:
        start = _attr(entry, "start_year")
        end = _attr(entry, "end_year")
        is_current = bool(_attr(entry, "is_current") or False)
        label = (
            str(_attr(entry, "company") or "").strip()
            or str(_attr(entry, "role") or "").strip()
            or "role"
        )

        if is_current:
            current_count += 1

        for year, which in ((start, "start"), (end, "end")):
            if isinstance(year, int) and year > future_cutoff:
                issues.append(
                    TimelineIssue("future_date", f"{label}: {which} year {year} is in the future")
                )

        if isinstance(start, int) and isinstance(end, int):
            if end < start:
                issues.append(
                    TimelineIssue("end_before_start", f"{label}: ends {end} before it starts {start}")
                )
            elif end - start > _MAX_SINGLE_ROLE_SPAN_YEARS:
                issues.append(
                    TimelineIssue("impossible_span", f"{label}: spans {end - start} years")
                )

    if current_count > _MAX_CONCURRENT_CURRENT_ROLES:
        issues.append(
            TimelineIssue("excess_current", f"{current_count} roles marked current at once")
        )

    return TimelineResult(issues=issues)


def _claim_is_unverified(claim: Any) -> bool:
    """A claim is penalisable only when the CV does NOT corroborate it AND the
    model does not recognise the named event/credential — both conditions, so
    we fail open on anything ambiguous."""
    corroboration = str(_attr(claim, "corroboration") or "").strip().lower()
    familiarity = str(_attr(claim, "model_familiarity") or "").strip().lower()
    return corroboration in _UNCORROBORATED_VALUES and familiarity in _LOW_FAMILIARITY_VALUES


@dataclass
class IntegrityPenaltyResult:
    penalty: float
    unverified_claim_count: int
    timeline_issue_count: int
    capped: bool

    @property
    def triggered(self) -> bool:
        return self.penalty > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "penalty": round(self.penalty, 2),
            "unverified_claim_count": self.unverified_claim_count,
            "timeline_issue_count": self.timeline_issue_count,
            "capped": self.capped,
        }


def compute_integrity_penalty(
    claims: Iterable[Any] | None,
    timeline_result: TimelineResult | None,
    *,
    points_per_issue: float,
    max_penalty: float,
) -> IntegrityPenaltyResult:
    """Bounded soft penalty: ``points_per_issue`` for each unverified claim and
    each timeline inconsistency, capped at ``max_penalty``. The cap is the
    safety valve — integrity signals nudge the score down so fraud can't
    inflate a candidate into interview, but never single-handedly auto-reject.
    """
    n_claims = sum(1 for c in (claims or []) if _claim_is_unverified(c))
    n_timeline = len(timeline_result.issues) if timeline_result else 0
    raw = points_per_issue * (n_claims + n_timeline)
    penalty = min(max_penalty, raw) if max_penalty > 0 else 0.0
    return IntegrityPenaltyResult(
        penalty=penalty,
        unverified_claim_count=n_claims,
        timeline_issue_count=n_timeline,
        capped=raw > max_penalty > 0,
    )


def apply_integrity_penalty(role_fit: float, penalty: float) -> float:
    """Subtract the bounded integrity penalty from role_fit, clamped to [0, 100]."""
    return max(0.0, round(role_fit - penalty, 2))


def build_integrity_signals_payload(
    integrity: IntegrityPenaltyResult,
    timeline_result: TimelineResult | None,
) -> dict[str, Any]:
    """Shape stored under ``cv_match_details['integrity_signals']`` for the
    'verify before interview' UI block."""
    return {
        "integrity_penalty": integrity.to_dict(),
        "timeline": (
            timeline_result.to_dict()
            if timeline_result
            else {"triggered": False, "issues": []}
        ),
    }
