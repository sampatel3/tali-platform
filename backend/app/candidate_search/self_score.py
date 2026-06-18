"""Self-referential "Taali score >= N" criteria, decided arithmetically.

A criterion like "Taali score >= 60" is SELF-REFERENTIAL: it gates on the
platform's own computed score (``CandidateApplication.taali_score_cache_100`` —
the same value the "Taali NN" badge and the ranking use), not on anything in the
CV or notes. Any LLM that only reads the CV + notes — the grounded report's
Citations pass, or the cv-match scorer — can therefore NEVER find evidence for
it and dutifully marks it "missing", even when the candidate's score clearly
clears the threshold.

So both surfaces decide these the same way: ARITHMETICALLY against the
candidate's Taali score, the way a salary cap is decided from the cited figure
rather than trusting the model's verdict word. This module is the single source
of truth for the detection, threshold parsing, and verdict wording so the two
consumers can't drift:

- the grounded top-N report (``top_candidates._recompute_self_score_verdict``),
  which adapts these onto ``CriterionVerdict`` objects, and
- the authed candidate detail/standing report (``role_support`` serialization),
  which adapts them onto stored ``requirements_assessment`` dicts.

Detection is anchored on "taali" so an unrelated CV criterion ("experience with
scoring models", "credit score modelling") can't match.
"""

from __future__ import annotations

import re

_SELF_SCORE_TOKEN_RE = re.compile(r"\b(score|fit)\b", re.I)
_SCORE_NUM_RE = re.compile(r"(\d[\d.]*)")
_SCORE_GEQ_RE = re.compile(r"(>=|>|at\s+least|min(?:imum)?|over|above|greater)", re.I)
_SCORE_LEQ_RE = re.compile(r"(<=|<|at\s+most|max(?:imum)?|under|below|up\s+to)", re.I)


def is_self_score_criterion(criterion: str) -> bool:
    """True for a "Taali score >= 60" style gate — anchored on "taali" plus a
    score/fit token and a number, so an unrelated CV criterion can't match."""
    c = (criterion or "").lower()
    return (
        "taali" in c
        and bool(_SELF_SCORE_TOKEN_RE.search(c))
        and bool(_SCORE_NUM_RE.search(c))
    )


def parse_score_threshold(criterion: str) -> tuple[str, float] | None:
    """``(op, value)`` for a "Taali score >= 60" style gate. Defaults to a
    MINIMUM ("geq") — a bare "Taali score 60" reads as a floor — and flips to
    "leq" only on an explicit at-most operator. ``None`` when there's no number."""
    m = _SCORE_NUM_RE.search(criterion or "")
    if not m:
        return None
    try:
        value = float(m.group(1))
    except (TypeError, ValueError):
        return None
    op = "leq" if (_SCORE_LEQ_RE.search(criterion) and not _SCORE_GEQ_RE.search(criterion)) else "geq"
    return op, value


def self_score_decision(criterion: str, taali_score: object) -> tuple[bool, str, float] | None:
    """Decide a self-referential "Taali score" criterion against the candidate's
    own Taali score.

    Returns ``(meets, op, threshold)``, or ``None`` when the criterion isn't a
    self-score gate, the threshold can't be parsed, or the candidate has no score
    yet — in which case the caller leaves the honest "couldn't find it" rather
    than asserting a pass/fail without data."""
    if not is_self_score_criterion(criterion):
        return None
    parsed = parse_score_threshold(criterion)
    if parsed is None:
        return None
    op, threshold = parsed
    try:
        score = float(taali_score)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    meets = score >= threshold if op == "geq" else score <= threshold
    return meets, op, threshold


def self_score_evidence_quote(taali_score: object) -> str:
    """The verdict's evidence — the score itself, the way a salary cap cites the
    stated figure. Rounded to mirror the "Taali NN" badge."""
    return f"Taali score {round(float(taali_score))}"  # type: ignore[arg-type]


def self_score_note(meets: bool, op: str, threshold: float, taali_score: object) -> str:
    """Plain-words reason for the verdict, shared so both surfaces read alike."""
    shown = round(float(taali_score))  # type: ignore[arg-type]
    sym = "≥" if op == "geq" else "≤"
    if meets:
        return f"Taali score {shown} meets the {sym} {threshold:g} threshold."
    rel = "below" if op == "geq" else "above"
    return f"Taali score {shown} is {rel} the {sym} {threshold:g} threshold."
