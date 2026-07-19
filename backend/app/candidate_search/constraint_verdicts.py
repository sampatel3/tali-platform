"""Deterministic constraint parsing and verdict correction for candidate search."""

from __future__ import annotations

import re

from ..models.candidate_application import CandidateApplication
from . import self_score as _ss
from .grounded_evidence import CriterionVerdict, Evidence

_CONSTRAINT_KW_RE = re.compile(
    r"\b(salar(?:y|ies)|compensation|\bpay\b|wage|notice period|visa|"
    r"work auth\w*|right to work|work permit|relocat\w*|based in|located in|"
    r"\blocation\b|nationality|citizen\w*)\b",
    re.I,
)
_THRESHOLD_RE = re.compile(
    r"\b(less than|under|below|at most|no more than|max(?:imum)?|at least|"
    r"min(?:imum)?|over|above|fewer than|more than|<=?|>=?)\b",
    re.I,
)
_UNIT_RE = re.compile(
    r"\b(aed|usd|eur|gbp|sar|inr|years?|yrs?|months?|days?|\d{3,})\b",
    re.I,
)
_CURRENCY_RE = re.compile(r"\b(aed|usd|eur|gbp|sar|inr)\b", re.I)
_LABEL_FRAGMENT_RE = re.compile(
    r"(salar(?:y|ies)|compensation|\bpay\b|package|day\s*rate|\brate\b|notice(?:\s+period)?)"
    r"(?:\s+(?:expectation|expected|requirement|req))?",
    re.I,
)
_VALUE_FRAGMENT_RE = re.compile(
    r"(?:<=|>=|<|>|less\s+than|under|below|over|above|at\s+most|at\s+least|"
    r"no\s+more\s+than|up\s+to|max(?:imum)?|min(?:imum)?)?\s*"
    r"\d[\d,\.]*\s*(?:k|m)?\s*"
    r"(?:aed|usd|eur|gbp|sar|inr|dirhams?|dollars?|pounds?|euros?)?\s*"
    r"(?:/\s*(?:year|month|yr|mo)|per\s+(?:year|month|annum)|p\.?a\.?)?",
    re.I,
)
_GEQ_RE = re.compile(
    r"\b(over|above|more\s+than|greater\s+than|at\s+least|min(?:imum)?|>=?)\b",
    re.I,
)
_LEQ_RE = re.compile(
    r"\b(under|below|less\s+than|at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?|<=?)\b",
    re.I,
)
_CAP_TOLERANCE = 1.25
_CAP_CRIT_RE = re.compile(
    r"(<=?|\b(?:under|below|less\s+than|at\s+most|no\s+more\s+than|up\s+to|max(?:imum)?)\b)",
    re.I,
)
_MONEY_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\b", re.I)


def _money_in(text: str) -> list[float]:
    values: list[float] = []
    for match in _MONEY_NUM_RE.finditer(text or ""):
        value = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000
        values.append(value)
    return values


def recompute_currency_cap_verdict(verdict: CriterionVerdict) -> None:
    """Correct a grounded salary/currency cap verdict arithmetically."""

    criterion = verdict.criterion or ""
    if not _CAP_CRIT_RE.search(criterion):
        return
    if not (
        _CURRENCY_RE.search(criterion)
        or re.search(r"salar|compensation|\bpay\b|wage|package", criterion, re.I)
    ):
        return
    caps = _money_in(criterion)
    if not caps:
        return
    cap = max(caps)
    in_band = [
        number
        for evidence in verdict.evidence
        for number in _money_in(evidence.quote)
        if 0.1 * cap <= number <= 10 * cap
    ]
    distinct = {round(number, 2) for number in in_band if abs(number - cap) > 1e-9} or {
        round(number, 2) for number in in_band
    }
    if len(distinct) != 1:
        return
    stated = next(iter(distinct))
    if stated <= cap:
        verdict.status = "met"
    elif stated <= _CAP_TOLERANCE * cap:
        verdict.status = "partially_met"
    else:
        verdict.status = "not_met"


def recompute_self_score_verdict(
    verdict: CriterionVerdict,
    application: CandidateApplication,
) -> None:
    """Decide a self-referential Taali-score criterion from the stored score."""

    score = getattr(application, "taali_score_cache_100", None)
    decision = _ss.self_score_decision(verdict.criterion, score)
    if decision is None:
        return
    meets, operator, threshold = decision
    verdict.status = "met" if meets else "not_met"
    verdict.grounded = True
    verdict.source = "taali_score"
    verdict.evidence = [
        Evidence(quote=_ss.self_score_evidence_quote(score), source="taali_score")
    ]
    verdict.note = _ss.self_score_note(meets, operator, threshold, score)


def is_constraint(criterion: str) -> bool:
    text = criterion or ""
    if _CONSTRAINT_KW_RE.search(text):
        return True
    if _THRESHOLD_RE.search(text) and _UNIT_RE.search(text):
        return True
    return bool(_CURRENCY_RE.search(text) and re.search(r"\d", text))


def merge_constraint_fragments(
    criteria: list[str],
    free_text: str | None,
) -> list[str]:
    """Reassemble parser-split labels and numeric values into one constraint."""

    label_index = value_index = None
    for index, criterion in enumerate(criteria):
        text = (criterion or "").strip()
        if label_index is None and _LABEL_FRAGMENT_RE.fullmatch(text):
            label_index = index
        elif (
            value_index is None
            and re.search(r"\d", text)
            and _VALUE_FRAGMENT_RE.fullmatch(text)
        ):
            value_index = index
    if label_index is None or value_index is None:
        return criteria

    raw_value = criteria[value_index].strip()
    operator_source = raw_value if _THRESHOLD_RE.search(raw_value) else (free_text or "")
    operator = (
        ">="
        if _GEQ_RE.search(operator_source) and not _LEQ_RE.search(operator_source)
        else "<="
    )
    value = _THRESHOLD_RE.sub("", raw_value).strip(" \t-–—")
    merged = f"{criteria[label_index].strip()} {operator} {value}".strip()
    result = [
        item
        for index, item in enumerate(criteria)
        if index not in (label_index, value_index)
    ]
    result.insert(min(label_index, value_index), merged)
    return result


is_self_score_criterion = _ss.is_self_score_criterion
parse_score_threshold = _ss.parse_score_threshold

__all__ = [
    "is_constraint",
    "is_self_score_criterion",
    "merge_constraint_fragments",
    "parse_score_threshold",
    "recompute_currency_cap_verdict",
    "recompute_self_score_verdict",
]
