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
    # Longest single contiguous run of CV words lifted verbatim from the JD.
    # Dilution-resistant: a candidate who pastes a JD paragraph then pads the
    # CV with original prose can push the *ratio* under ``threshold``, but the
    # absolute block length doesn't move. ``triggered`` factors this in when a
    # ``min_block_words`` floor is supplied to ``detect_cv_copy_paste``.
    longest_block_words: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "matched_chars": self.matched_chars,
            "cv_chars": self.cv_chars,
            "triggered": self.triggered,
            "threshold": self.threshold,
            "longest_block_words": self.longest_block_words,
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
    min_block_words: int = 0,
) -> CopyPasteResult:
    """Score how much of the CV is lifted verbatim from the job description.

    Algorithm: build the set of N-grams from the JD, then walk the CV. When
    we hit a JD N-gram, extend the match forward as far as the words keep
    matching (so a copy-pasted paragraph counts as one snippet, not dozens
    of overlapping windows). Score is matched-CV-chars / total-CV-chars.

    ``min_block_words`` (0 = off) is a dilution-resistant floor: the CV also
    triggers when its single longest contiguous lifted block reaches that many
    words, even if padding kept the ratio below ``threshold``.
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
    longest_block_words = 0
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
        longest_block_words = max(longest_block_words, end - i)
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
    triggered = score >= threshold or (
        min_block_words > 0 and longest_block_words >= min_block_words
    )
    return CopyPasteResult(
        score=score,
        matched_chars=matched_word_chars,
        cv_chars=cv_chars,
        triggered=triggered,
        threshold=threshold,
        evidence=evidence,
        longest_block_words=longest_block_words,
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


# ── Near-duplicate (paraphrased-JD) copy-paste ─────────────────────────────
# Verbatim 8-gram overlap collapses to ~0 after a single "reword this" pass on
# the JD. Word k-shingle Jaccard degrades gracefully: synonym swaps and
# sentence reordering still share many short shingles, so a CV that mirrors the
# spec scores far above an organically-written one. SOFT signal — it flags for
# review; only verbatim copy-paste hard-caps. Heavy semantic paraphrase (every
# word changed) needs embeddings — the T1 "too-aligned" check — not this.
_SHINGLE_SIZE = 4


@dataclass
class ShingleResult:
    similarity: float  # |CV∩JD shingles| / |CV shingles|, the fraud direction
    jaccard: float  # symmetric set similarity, 0–1
    shared_shingles: int
    cv_shingles: int
    triggered: bool
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "similarity": round(self.similarity, 4),
            "jaccard": round(self.jaccard, 4),
            "shared_shingles": self.shared_shingles,
            "cv_shingles": self.cv_shingles,
            "triggered": self.triggered,
            "threshold": self.threshold,
        }


def _shingles(tokens: list[str], size: int) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return set()
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def detect_jd_shingle_similarity(
    cv_text: str,
    jd_text: str,
    *,
    threshold: float = 0.34,
    shingle_size: int = _SHINGLE_SIZE,
) -> ShingleResult:
    """Near-duplicate overlap between CV and JD via word k-shingle Jaccard.

    ``similarity`` is the fraction of the CV's shingles that also appear in the
    JD — the fraud-relevant, asymmetric direction ("how much of this CV is the
    spec"). Triggers on ``similarity >= threshold``. Fails closed (no trigger)
    on inputs too short to shingle.
    """
    cv_set = _shingles(_tokenize(cv_text), shingle_size)
    jd_set = _shingles(_tokenize(jd_text), shingle_size)
    if not cv_set or not jd_set:
        return ShingleResult(0.0, 0.0, 0, len(cv_set), False, threshold)
    shared = cv_set & jd_set
    similarity = len(shared) / len(cv_set)
    union = len(cv_set | jd_set)
    jaccard = (len(shared) / union) if union else 0.0
    return ShingleResult(
        similarity=similarity,
        jaccard=jaccard,
        shared_shingles=len(shared),
        cv_shingles=len(cv_set),
        triggered=similarity >= threshold,
        threshold=threshold,
    )


# ── CV ↔ Workable structured-history diff ──────────────────────────────────
# The platform stores TWO independent structured views of the same career
# history: the CV-parsed ``cv_sections.experience`` and Workable's own
# self-reported ``experience_entries`` (synced from the candidate profile).
# Diffing them catches roles fabricated on the CV (present on the CV, absent
# from Workable), omitted roles, and shifted dates / inflated tenure. FLAG-ONLY
# and deliberately tolerant — people legitimately abbreviate, omit and round on
# one surface but not the other — so it raises a question for the recruiter,
# never a verdict, and never a score change.
_COMPANY_STOP = {
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
    "plc", "gmbh", "co", "llp", "pvt", "private", "sa", "ag", "bv", "the",
    "company", "group", "holdings",
}
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _company_tokens(name: Any) -> frozenset[str]:
    toks = _WORD_RE.findall((str(name or "")).lower())
    return frozenset(t for t in toks if t not in _COMPANY_STOP and len(t) > 1)


def _companies_match(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    inter = a & b
    if not inter:
        return False
    # Subset (one name is a shortening of the other) or majority overlap.
    return a <= b or b <= a or len(inter) / len(a | b) >= 0.5


def _first_year(*values: Any) -> int | None:
    for v in values:
        m = _YEAR_RE.search(str(v or ""))
        if m:
            return int(m.group(0))
    return None


@dataclass
class HistoryDiffIssue:
    kind: str  # cv_only_role | date_shift
    detail: str


@dataclass
class WorkableDiffResult:
    issues: list[HistoryDiffIssue] = field(default_factory=list)
    cv_role_count: int = 0
    workable_role_count: int = 0

    @property
    def triggered(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "cv_role_count": self.cv_role_count,
            "workable_role_count": self.workable_role_count,
            "issues": [asdict(i) for i in self.issues],
        }


def diff_cv_vs_workable_history(
    cv_experience: Iterable[Any] | None,
    workable_experience: Iterable[Any] | None,
    *,
    year_shift_tolerance: int = 1,
    max_issues: int = 8,
) -> WorkableDiffResult:
    """Diff the CV-parsed employment history against Workable's self-reported
    entries. Both are lists of dicts (or objects); company under ``company``,
    dates under ``start``/``end`` (CV) or ``start_date``/``end_date``
    (Workable). Only confident tells are flagged:

    * ``cv_only_role`` — a CV role whose employer matches NO Workable entry,
      *and only when Workable has its own entries to diff against* (so we never
      flag a candidate who simply didn't fill in Workable). Potential
      fabrication / spec-padding.
    * ``date_shift`` — a matched employer whose start or end year differs by
      more than ``year_shift_tolerance`` between the two surfaces. Possible
      inflated tenure.

    Fails open on anything ambiguous (missing company, unparseable dates).
    """
    cv_roles = [r for r in (cv_experience or [])]
    wk_roles = [r for r in (workable_experience or [])]
    result = WorkableDiffResult(cv_role_count=len(cv_roles), workable_role_count=len(wk_roles))
    if not cv_roles or not wk_roles:
        # Nothing to corroborate against — stay silent (fail open).
        return result

    wk_index = [
        (
            _company_tokens(_attr(r, "company")),
            _first_year(_attr(r, "start_date"), _attr(r, "start")),
            _first_year(_attr(r, "end_date"), _attr(r, "end")),
        )
        for r in wk_roles
    ]

    for cv_role in cv_roles:
        if len(result.issues) >= max_issues:
            break
        cv_company_raw = str(_attr(cv_role, "company") or "").strip()
        cv_tokens = _company_tokens(cv_company_raw)
        if not cv_tokens:
            continue  # no usable company name → can't diff, fail open
        match = next(
            ((wt, ws, we) for (wt, ws, we) in wk_index if _companies_match(cv_tokens, wt)),
            None,
        )
        if match is None:
            result.issues.append(
                HistoryDiffIssue(
                    "cv_only_role",
                    f"{cv_company_raw or 'role'}: on the CV but not in the Workable profile",
                )
            )
            continue
        _, wk_start, wk_end = match
        cv_start = _first_year(_attr(cv_role, "start"), _attr(cv_role, "start_date"))
        cv_end = _first_year(_attr(cv_role, "end"), _attr(cv_role, "end_date"))
        if (
            cv_start is not None
            and wk_start is not None
            and abs(cv_start - wk_start) > year_shift_tolerance
        ):
            result.issues.append(
                HistoryDiffIssue(
                    "date_shift",
                    f"{cv_company_raw}: CV start {cv_start} vs Workable {wk_start}",
                )
            )
        elif (
            cv_end is not None
            and wk_end is not None
            and abs(cv_end - wk_end) > year_shift_tolerance
        ):
            result.issues.append(
                HistoryDiffIssue(
                    "date_shift",
                    f"{cv_company_raw}: CV end {cv_end} vs Workable {wk_end}",
                )
            )
    return result


# ── Supplementary signal bundle (flag-only) ────────────────────────────────
def build_supplementary_fraud_signals(
    *,
    cv_text: str,
    jd_text: str,
    cv_experience: Iterable[Any] | None = None,
    workable_experience: Iterable[Any] | None = None,
    shingle_threshold: float = 0.34,
    workable_diff_enabled: bool = True,
) -> dict[str, Any]:
    """Bundle the deterministic flag-only signals that ride on the score but
    never cap it: JD near-duplicate (shingle) similarity, the CV↔Workable
    history diff, and the already-computed ``company_unverified`` employer
    flags surfaced from ``cv_sections.experience``. Each sub-signal is wrapped
    in its own try so one bad input never blocks the others (or the score).
    """
    signals: dict[str, Any] = {}
    try:
        signals["jd_shingle"] = detect_jd_shingle_similarity(
            cv_text, jd_text, threshold=shingle_threshold
        ).to_dict()
    except Exception:  # pragma: no cover — defensive
        pass

    cv_exp = list(cv_experience or [])
    if workable_diff_enabled:
        try:
            diff = diff_cv_vs_workable_history(cv_exp, workable_experience)
            if diff.cv_role_count or diff.workable_role_count:
                signals["workable_history_diff"] = diff.to_dict()
        except Exception:  # pragma: no cover — defensive
            pass

    try:
        unverified = [
            str(_attr(e, "company"))[:120]
            for e in cv_exp
            if _attr(e, "company_unverified") and _attr(e, "company")
        ]
        if unverified:
            signals["unverified_employers"] = {
                "count": len(unverified),
                "companies": unverified[:10],
            }
    except Exception:  # pragma: no cover — defensive
        pass

    return signals
