"""CV fraud signals computed deterministically inside the pre-screen agent.

The primary signal is *job-spec copy-paste* — n-gram overlap between the
candidate's CV text and the role's job description. It surfaces CVs containing
chunks of the JD verbatim for human review.

The detector is deliberately deterministic (no LLM, no perplexity guess) so
its output can be cited as evidence in the standing report. Detection itself
never implies a hiring verdict: score capping is a separate, explicit operator
policy (``FRAUD_COPY_PASTE_ACTION=cap``) and defaults to flag-only.
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

# Unicode-aware letters/numbers without underscores. The old ASCII-only regex
# silently made the detector ineffective for Arabic, Cyrillic, CJK and other
# non-Latin CVs/JDs, creating a language-dependent integrity control.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


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
    return _WORD_RE.findall((text or "").casefold())


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


def build_fraud_signals_payload(
    fraud: CopyPasteResult,
    *,
    action: str = "flag",
) -> dict[str, Any]:
    """Shape stored under ``pre_screen_evidence['fraud_signals']``.

    Persist policy separately from the raw detector result so an audit can
    distinguish a shadow observation from a recruiter flag or a score cap.
    """
    copy_paste = fraud.to_dict()
    copy_paste["action"] = action
    copy_paste["review_flagged"] = fraud.triggered and action in {"flag", "cap"}
    return {"cv_copy_paste": copy_paste}


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
    fraud_signals = build_fraud_signals_payload(fraud, action="cap")
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
    toks = _tokenize(str(name or ""))
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


# ── Prong 1: evidence-grounding coverage (anti spec-gaming) ────────────────
# A high CV↔spec match is ambiguous — genuinely qualified OR gamed-the-spec — so
# it is NEVER, on its own, a fraud signal. The discriminator is GROUNDING: among
# the role's MUST-HAVE requirements the model graded met/partial, the fraction
# that retain a VERBATIM CV quote (after the holistic grounding pass drops
# quotes that don't locate in the CV) vs a bare, spec-echoing assertion. The
# conjunction `high match × LOW grounding coverage` is the gamed-suspect tell.
# A genuine candidate's claims locate in the CV → high coverage → untouched; a
# tailored CV's claims don't → low coverage → flagged (and optionally nudged).
_POSITIVE_STATUSES = {"met", "partially_met", "partial"}


def _enum_norm(v: Any) -> str:
    """Normalise a pydantic enum / string to a bare lowercase token
    ('Status.MET' or Status.MET or 'met' → 'met')."""
    val = getattr(v, "value", None)
    s = str(val if val is not None else v).strip().lower().replace(" ", "_")
    return s.rsplit(".", 1)[-1] if "." in s else s


@dataclass
class GroundingCoverageResult:
    met_must_haves: int
    grounded_must_haves: int
    coverage: float  # grounded / met must-haves; 1.0 when no met must-haves
    ungrounded_requirements: list[str]  # met-but-unevidenced must-have texts
    ungrounded_match: bool  # high match × low coverage conjunction

    def to_dict(self) -> dict[str, Any]:
        return {
            "met_must_haves": self.met_must_haves,
            "grounded_must_haves": self.grounded_must_haves,
            "coverage": round(self.coverage, 3),
            "ungrounded_requirements": self.ungrounded_requirements[:10],
            "ungrounded_match": self.ungrounded_match,
        }


def compute_grounding_coverage(
    requirements_assessment: Iterable[Any] | None,
    overall_score: float | None,
    *,
    high_match_threshold: float = 55.0,
    low_coverage_threshold: float = 0.5,
    min_must_haves: int = 2,
) -> GroundingCoverageResult:
    """Grounding coverage over MUST-HAVE requirements graded met/partial.

    A requirement is *grounded* when it retains a verbatim CV quote
    (``evidence_quotes`` non-empty after the holistic grounding pass). Coverage
    = grounded / met-must-haves. ``ungrounded_match`` fires ONLY on the
    conjunction high-match × low-coverage with enough met must-haves to be
    meaningful — never on a high match alone. Fails open (coverage 1.0, no flag)
    when there are no met must-haves. Reads pydantic objects OR plain dicts.
    """
    met: list[Any] = [
        ra
        for ra in (requirements_assessment or [])
        if "must" in _enum_norm(_attr(ra, "priority"))
        and _enum_norm(_attr(ra, "status")) in _POSITIVE_STATUSES
    ]
    grounded = [ra for ra in met if _attr(ra, "evidence_quotes")]
    ungrounded_names = [
        n
        for n in (
            str(_attr(ra, "requirement") or "").strip()[:160]
            for ra in met
            if not _attr(ra, "evidence_quotes")
        )
        if n
    ]
    coverage = (len(grounded) / len(met)) if met else 1.0
    ungrounded_match = bool(
        overall_score is not None
        and overall_score >= high_match_threshold
        and len(met) >= min_must_haves
        and coverage <= low_coverage_threshold
    )
    return GroundingCoverageResult(
        met_must_haves=len(met),
        grounded_must_haves=len(grounded),
        coverage=coverage,
        ungrounded_requirements=ungrounded_names,
        ungrounded_match=ungrounded_match,
    )


def apply_grounding_discount(
    score: float | None,
    coverage: GroundingCoverageResult,
    *,
    max_discount: float,
) -> tuple[float | None, float]:
    """Bounded discount when a high match is driven by un-evidenced must-haves —
    proportional to the un-evidenced fraction, capped at ``max_discount`` so a
    terse-but-genuine CV is nudged + flagged, never single-handedly rejected.
    Returns ``(adjusted_score, discount_applied)``; no-op unless
    ``ungrounded_match``."""
    if not coverage.ungrounded_match or score is None or max_discount <= 0:
        return score, 0.0
    ungrounded_fraction = max(0.0, min(1.0, 1.0 - coverage.coverage))
    discount = round(min(max_discount, max_discount * ungrounded_fraction), 2)
    if discount <= 0:
        return score, 0.0
    return max(0.0, round(score - discount, 2)), discount


# ── Wave 4: CV-internal coherence (deterministic, flag-only) ────────────────
@dataclass
class ExperienceInflationResult:
    triggered: bool
    years_claimed: float | None
    years_evidenced: float  # span: latest_end − earliest_start (generous)
    gap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "years_claimed": self.years_claimed,
            "years_evidenced": self.years_evidenced,
            "gap": round(self.gap, 1),
        }


def detect_experience_inflation(
    years_claimed: float | None,
    timeline: Iterable[Any] | None,
    *,
    tolerance_years: float = 2.0,
    now_year: int | None = None,
) -> ExperienceInflationResult:
    """Flag a claimed total-experience figure that exceeds the candidate's whole
    career *span* (latest end − earliest start) by more than ``tolerance_years``.

    Deliberately uses the generous span (gaps included) as the evidenced figure,
    so this only fires on the arithmetically impossible case — "15 years" when
    the first job started 9 years ago — keeping false positives near zero. Career
    breaks, parallel roles and rounding never trip it. Fail-open on missing data.
    """
    if years_claimed is None:
        return ExperienceInflationResult(False, None, 0.0, 0.0)
    current = now_year if now_year is not None else datetime.now(timezone.utc).year
    starts: list[int] = []
    ends: list[int] = []
    for e in timeline or []:
        # Accept BOTH shapes: the LLM snapshot timeline (``start_year`` /
        # ``end_year`` ints) and raw parsed ``cv_sections.experience`` (``start`` /
        # ``end`` date strings). The caller must feed the FULL CV history here, not
        # the 5-capped snapshot timeline — dropping a candidate's oldest roles is
        # exactly what manufactured a fake "claims 15, evidenced 9" gap for anyone
        # with more than five employers.
        s = _attr(e, "start_year")
        if not isinstance(s, int):
            s = _first_year(_attr(e, "start"), _attr(e, "start_date"))
        if not isinstance(s, int):
            continue
        en = _attr(e, "end_year")
        if not isinstance(en, int):
            en = _first_year(_attr(e, "end"), _attr(e, "end_date"))
        # Ongoing / unparseable end → the current year. Generous by design:
        # widening the span only ever REDUCES false "inflation" flags.
        if _attr(e, "is_current") or not isinstance(en, int):
            en = current
        starts.append(s)
        ends.append(en if en >= s else s)
    if not starts:
        return ExperienceInflationResult(False, years_claimed, 0.0, 0.0)
    span = float(max(ends) - min(starts))
    gap = float(years_claimed) - span
    return ExperienceInflationResult(gap > tolerance_years, years_claimed, span, gap)


# Curated tool → first-public-release year. Conservative, well-known tools only;
# a tool claimed in a role that ENDED before the tool existed is a hard tell.
_TOOL_RELEASE_YEAR: dict[str, int] = {
    "go": 2009, "golang": 2009, "kafka": 2011, "docker": 2013, "react": 2013,
    "reactjs": 2013, "databricks": 2013, "kubernetes": 2014, "k8s": 2014,
    "spark": 2014, "terraform": 2014, "snowflake": 2014, "swift": 2014,
    "vue": 2014, "vue.js": 2014, "rust": 2015, "tensorflow": 2015,
    "airflow": 2015, "graphql": 2015, "pytorch": 2016, "kotlin": 2016,
    "angular": 2016, "next.js": 2016, "nextjs": 2016, "svelte": 2016, "dbt": 2016,
    "transformers": 2017, "kubeflow": 2017, "bert": 2018, "fastapi": 2018,
    "deno": 2018, "langchain": 2022, "chatgpt": 2022, "gpt-4": 2023,
}


@dataclass
class AnachronismResult:
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {"triggered": self.triggered, "issues": self.issues[:8]}


def detect_tech_anachronism(
    experience_entries: Iterable[Any] | None,
    *,
    table: dict[str, int] | None = None,
) -> AnachronismResult:
    """Flag a tool named in an experience entry whose role ENDED before the tool
    first existed (e.g. "Kubernetes" in a role that ended 2012). Deterministic,
    near-zero FP — a tool literally cannot have been used before its release.
    Word-boundary matching so "go" never matches "good"."""
    tbl = table or _TOOL_RELEASE_YEAR
    issues: list[dict[str, Any]] = []
    for e in experience_entries or []:
        end = _first_year(_attr(e, "end"), _attr(e, "end_year"))
        if not isinstance(end, int):
            continue
        bullets = _attr(e, "bullets") or []
        text = " ".join(
            [str(_attr(e, "title") or "")]
            + [str(b) for b in (bullets if isinstance(bullets, list) else [bullets])]
        ).lower()
        if not text.strip():
            continue
        for tool, year in tbl.items():
            if end < year and re.search(
                r"(?<![a-z0-9])" + re.escape(tool) + r"(?![a-z0-9])", text
            ):
                issues.append(
                    {
                        "tool": tool,
                        "release_year": year,
                        "role_end": end,
                        "company": str(_attr(e, "company") or "")[:120],
                    }
                )
        if len(issues) >= 8:
            break
    return AnachronismResult(issues)


# ── Wave 4: triangulation aggregator ────────────────────────────────────────
# The rule (CV_FRAUD_FUNNEL_DESIGN §2): require MULTIPLE independent
# disagreements before it bites. One source disagreeing = a question (flag);
# several, or a deterministic artifact, = action. Pure read over the assembled
# ``integrity_signals`` dict — adds a ``triangulation`` summary, changes no score.
def aggregate_triangulation(integrity_signals: dict[str, Any] | None) -> dict[str, Any]:
    sig = integrity_signals if isinstance(integrity_signals, dict) else {}

    def _get(key: str) -> dict[str, Any]:
        v = sig.get(key)
        return v if isinstance(v, dict) else {}

    soft: list[str] = []  # independent disagreements (probabilistic)
    corroborations: list[str] = []  # positive agreements
    deterministic: list[str] = []  # artifacts that act on their own

    # Deterministic artifacts (manipulation of the scorer / the file).
    dh = _get("document_hygiene")
    if dh.get("injection_detected") or dh.get("has_tag_chars"):
        deterministic.append("hidden_text")
    # Bytes-level PDF scan promoted from ingest (document_hygiene.pdf).
    # Invisible render-mode text is a deterministic tamper artifact; metadata
    # keyword-stuffing stays advisory (warning-only — higher FP).
    pdf = dh.get("pdf") if isinstance(dh.get("pdf"), dict) else {}
    if (pdf.get("render") or {}).get("triggered"):
        deterministic.append("hidden_text_pdf")
    tl = _get("timeline")
    if tl.get("triggered") or tl.get("issues"):
        deterministic.append("impossible_timeline")

    # Soft, independent corroboration axes.
    # NOTE: grounding (must-haves scored "met" without verbatim CV evidence) is
    # NOT counted here. Each requirement is already graded 0-100 by the focused
    # ``cv_matching.graded`` pass, and that grade — which directly drives the
    # score and is shown per requirement — already reflects weak evidence (a
    # thinly-evidenced "met" grades low). A separate grounding warning beside it
    # was redundant and confusing, so it's out of the integrity readout.
    if _get("jd_shingle").get("triggered"):
        soft.append("jd_mirroring")
    # NOTE: the CV↔Workable history diff is deliberately NOT counted here. In
    # production it fired on ~54% of candidates (mostly "role on the CV but not in
    # the Workable form", which is benign, plus noisy date matches), so it drowned
    # the real signals and trained recruiters to ignore the panel. It stays
    # computed (for a future, stricter name/date matcher) but neither warns nor
    # moves the trust band until it earns its place back.
    if int(_get("unverified_employers").get("count") or 0) > 0:
        soft.append("unverified_employers")
    if _get("experience_inflation").get("triggered"):
        soft.append("experience_inflation")
    if _get("tech_anachronism").get("triggered"):
        soft.append("tech_anachronism")

    gc = _get("graph_corroboration")
    if gc.get("status") == "anomaly":
        soft.append("graph_anomaly")
    elif gc.get("status") == "corroborated":
        corroborations.append("graph")
    gh = _get("github")
    if gh.get("status") == "not_found":
        soft.append("github_not_found")
    elif gh.get("status") == "corroborated":
        corroborations.append("github")

    # Verdict: deterministic artifact OR >=2 independent soft disagreements =
    # strong; exactly one soft = review (a question); none = ok.
    if deterministic or len(soft) >= 2:
        verdict = "strong_review"
    elif len(soft) == 1:
        verdict = "review"
    else:
        verdict = "ok"

    # Trust band — the recruiter-facing readout that sits BESIDE the match score
    # (the "two readouts" model). It never lowers the match number itself; it
    # summarises how much we trust the match is real. Categorical, not another
    # 0-100 to interpret.
    trust_band = {"strong_review": "low", "review": "medium", "ok": "high"}[verdict]

    return {
        "verdict": verdict,
        "trust_band": trust_band,
        "to_verify": len(deterministic) + len(soft),
        "soft_disagreements": soft,
        "deterministic_artifacts": deterministic,
        "corroborations": corroborations,
        "disagreement_count": len(soft),
    }


def build_integrity_warnings(integrity_signals: dict[str, Any] | None) -> list[str]:
    """Canonical, human-readable integrity / corroboration warnings — the ONE
    place the wording lives. Consumed by the candidate report, the agent-decision
    surfaces and the summary text (the FE no longer re-derives them). Deterministic
    artifacts first, then probabilistic disagreements. Warns, never blocks.
    """
    sig = integrity_signals if isinstance(integrity_signals, dict) else {}

    def _g(k: str) -> dict[str, Any]:
        v = sig.get(k)
        return v if isinstance(v, dict) else {}

    out: list[str] = []
    dh = _g("document_hygiene")
    if dh.get("injection_detected"):
        out.append("Hidden prompt-injection text aimed at the screener was found in the CV file (removed before scoring).")
    elif dh.get("has_tag_chars"):
        out.append("Invisible Unicode (Tags-block) characters were embedded in the CV file.")
    elif int(dh.get("invisible_char_count") or 0) >= 8:
        out.append(f"{dh['invisible_char_count']} invisible characters were embedded in the CV file.")

    pdf = dh.get("pdf") if isinstance(dh.get("pdf"), dict) else {}
    if (pdf.get("render") or {}).get("triggered"):
        out.append("Text drawn in an invisible render mode was found inside the PDF file.")
    if (pdf.get("metadata") or {}).get("metadata_keyword_stuffing"):
        out.append("The PDF's hidden metadata is stuffed with keywords that don't appear in the visible document.")

    for issue in (_g("timeline").get("issues") or [])[:6]:
        detail = (issue.get("detail") if isinstance(issue, dict) else str(issue)) or ""
        if detail:
            out.append(f"Timeline: {detail}")

    # Grounding (un-evidenced "met" must-haves) is intentionally not a warning —
    # see aggregate_triangulation: each requirement's 0-100 grade already encodes
    # evidence strength and drives the score, so a separate warning duplicated it.

    sh = _g("jd_shingle")
    if sh.get("triggered"):
        out.append(f"CV closely mirrors the job description ({round((float(sh.get('similarity') or 0)) * 100)}% phrase overlap).")

    ue = _g("unverified_employers")
    if int(ue.get("count") or 0) > 0:
        names = ", ".join(ue.get("companies") or [])
        tail = f": {names}" if names else ""
        out.append(f"{ue['count']} employer name{'' if int(ue['count']) == 1 else 's'} not found verbatim in the CV text{tail}.")

    # The CV↔Workable history diff is intentionally not surfaced as a warning —
    # see aggregate_triangulation for why (too noisy: ~54% fire rate, mostly
    # benign "on the CV but not in the Workable form"). Revive only behind a
    # stricter matcher.

    ei = _g("experience_inflation")
    if ei.get("triggered"):
        out.append(f"Claims ~{ei.get('years_claimed')} years' experience but the career history spans only ~{ei.get('years_evidenced')} years.")

    for issue in (_g("tech_anachronism").get("issues") or [])[:6]:
        if isinstance(issue, dict) and issue.get("tool"):
            out.append(f'Lists "{issue["tool"]}" in a role ending {issue.get("role_end")}, before it existed ({issue.get("release_year")}).')

    gc = _g("graph_corroboration")
    if gc.get("status") == "anomaly":
        cos = [
            c.get("company") for c in (gc.get("companies") or [])
            if isinstance(c, dict) and c.get("status") == "anomaly" and c.get("company")
        ]
        where = ", ".join(cos) if cos else "that employer"
        out.append(f"Claimed tech stack is unlike what other candidates from {where} show — verify it's genuine, not spec-tailoring.")

    ghc = _g("github")
    if ghc.get("status") == "not_found":
        out.append(f"The GitHub link on the CV doesn't resolve (github.com/{ghc.get('username')}) — confirm it's correct.")

    return [str(s).strip() for s in out if str(s).strip()]


def build_corroboration_notes(integrity_signals: dict[str, Any] | None) -> list[str]:
    """Canonical POSITIVE cross-source corroborations — the counterpart to
    ``build_integrity_warnings``. These are the checks we ran AND confirmed
    against an independent source (the candidate's public GitHub, the collective
    graph), so the recruiter sees what we verified, not only what we doubt. Same
    "one place the wording lives" rule; the FE never re-derives them.
    """
    sig = integrity_signals if isinstance(integrity_signals, dict) else {}

    def _g(k: str) -> dict[str, Any]:
        v = sig.get(k)
        return v if isinstance(v, dict) else {}

    out: list[str] = []
    gh = _g("github")
    if gh.get("status") == "corroborated":
        user = gh.get("username")
        where = f" (github.com/{user})" if user else ""
        skills = [str(s) for s in (gh.get("matched_skills") or []) if s]
        if skills:
            out.append(f"GitHub profile{where} backs up the CV — public repositories use {', '.join(skills[:4])}.")
        else:
            out.append(f"GitHub profile{where} matches the candidate named on the CV.")

    gc = _g("graph_corroboration")
    if gc.get("status") == "corroborated":
        out.append("Claimed tech stack lines up with what other candidates from the same employers show.")

    return [str(s).strip() for s in out if str(s).strip()]
