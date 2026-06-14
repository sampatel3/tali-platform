"""Parse a candidate's salary expectation out of Workable questionnaire answers
into structured (amount, currency, AED-normalised) data at Workable sync time.

Salary expectation is otherwise only free text in the questionnaire answers
(rendered into the notes block by ``workable_context_service``). The grounded
"top N with salary <= X" search then had to send those notes to Anthropic
Citations on every query just to extract + cite the stated figure. By parsing
the figure ONCE at sync into ``candidate_applications.salary_expectation_*`` the
search reads a number directly and the cap verdict becomes a pure data lookup.

Two things make this fuzzy, so the parser is deliberately conservative — it only
returns a result when confident, and the search keeps the LLM-extraction path as
a fallback when the structured field is absent:

* WHICH questionnaire question is "salary expectation" varies per org. We accept
  a question only when it carries a salary keyword (salary / compensation / pay /
  package / CTC / remuneration), preferring an *expected/desired* phrasing over a
  *current* one, and we keep the raw answer alongside the parsed figure.
* The answer itself is free text: ranges ("18,000–22,000"), shorthand ("18k"),
  embedded currency ("AED 18,000", "65,000 GBP", "$5,000"), or a currency stated
  only in the question ("Expected monthly salary (AED)?"). We normalise to AED —
  Tali's market — and assume AED for a bare number, matching the prior
  comparison (which compared the bare figure against an AED cap).

Pure functions — no DB, no LLM, no network. Unit-tested in
``tests/test_workable_salary_parser.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Reuse the same questionnaire-answer parser the pre-screen context + recruiter
# UI use, so "what the answer says" never disagrees across surfaces.
from ....services.workable_context_parsers import _parse_answer


# ── Static FX → AED ───────────────────────────────────────────────────────
# Approximate spot rates (1 unit = N AED), used ONLY for rough salary-cap
# bucketing — never for anything financial. AED and SAR/QAR are USD-pegged, so
# their rates are effectively fixed; the rest drift a few percent but that is
# immaterial to a "<= 30,000 AED" band with a built-in 25% partial tolerance.
_FX_TO_AED: dict[str, float] = {
    "AED": 1.0,
    "USD": 3.6725,   # dirham peg
    "SAR": 0.9793,
    "QAR": 1.0089,
    "KWD": 11.95,
    "BHD": 9.74,
    "OMR": 9.54,
    "EUR": 3.97,
    "GBP": 4.66,
    "INR": 0.0432,
    "PKR": 0.0132,
    "CAD": 2.69,
    "AUD": 2.42,
    "EGP": 0.075,
}

DEFAULT_CURRENCY = "AED"  # Tali's market — assumed when none is stated.

# A question is a salary question only when it carries one of these keywords.
# The answer must ALSO yield a plausible figure (see ``_salary_numbers``), so an
# over-broad question match (e.g. "relocation package") is harmless — its
# non-numeric answer is rejected.
_SALARY_Q_RE = re.compile(
    r"\b(salar(?:y|ies)|compensation|remuneration|\bpay\b|\bpackage\b|"
    r"\bctc\b|wage|day\s*rate|daily\s+rate)\b",
    re.I,
)
# An *expected/desired/required* salary question beats a *current* one when both
# are present — we want the expectation, not what they earn today.
_EXPECTATION_Q_RE = re.compile(r"\b(expect\w*|desired|require\w*|asking|seeking|want\w*)\b", re.I)

# Currency detection, most-specific first; a bare "$" is the last resort so
# "US$" / "USD" win over it. Order matters.
_CURRENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"€"), "EUR"),
    (re.compile(r"£"), "GBP"),
    (re.compile(r"₹"), "INR"),
    (re.compile(r"د\.?إ"), "AED"),
    (re.compile(r"\bAED\b|\bDhs?\b|\bdirhams?\b", re.I), "AED"),
    (re.compile(r"\bUSD\b|US\$|\bus\s+dollars?\b|\bdollars?\b", re.I), "USD"),
    (re.compile(r"\bGBP\b|\bpounds?\b|\bsterling\b", re.I), "GBP"),
    (re.compile(r"\bEUR\b|\beuros?\b", re.I), "EUR"),
    (re.compile(r"\bSAR\b|\bSR\b|\briyals?\b", re.I), "SAR"),
    (re.compile(r"\bQAR\b", re.I), "QAR"),
    (re.compile(r"\bKWD\b", re.I), "KWD"),
    (re.compile(r"\bBHD\b", re.I), "BHD"),
    (re.compile(r"\bOMR\b", re.I), "OMR"),
    (re.compile(r"\bINR\b|\brupees?\b|\bRs\.?\b", re.I), "INR"),
    (re.compile(r"\bPKR\b", re.I), "PKR"),
    (re.compile(r"\bCAD\b", re.I), "CAD"),
    (re.compile(r"\bAUD\b", re.I), "AUD"),
    (re.compile(r"\bEGP\b", re.I), "EGP"),
    (re.compile(r"\$"), "USD"),
]

# A number with an optional k/m shorthand. Commas/decimals tolerated.
_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([km])?\b", re.I)
# Below this we treat a number as not-a-salary (a notice period, a count of
# years, a "0", …) so a stray small number in a salary answer is ignored.
_MIN_SALARY = 100.0


@dataclass(frozen=True)
class ParsedSalary:
    """A confidently-parsed salary expectation.

    ``amount`` is in ``currency`` (the lower bound when a range was stated);
    ``amount_aed`` is that figure normalised to AED for the cap comparison;
    ``raw`` is the verbatim answer text (kept for display / the evidence quote
    and so a human can audit a wrong parse); ``currency_explicit`` records
    whether the currency was stated (vs assumed AED)."""

    amount: float
    currency: str
    amount_aed: float
    raw: str
    currency_explicit: bool


def to_aed(amount: float, currency: str) -> float:
    """Convert ``amount`` of ``currency`` to AED using the static rate table.
    Unknown currencies are treated as AED (1:1) — callers only pass codes we set."""
    return amount * _FX_TO_AED.get((currency or "").upper(), 1.0)


def _detect_currency(text: str) -> tuple[str, bool]:
    """``(currency_code, explicit)``. Falls back to AED (Tali's market) with
    ``explicit=False`` when no currency token is present."""
    for pattern, code in _CURRENCY_PATTERNS:
        if pattern.search(text or ""):
            return code, True
    return DEFAULT_CURRENCY, False


def _salary_numbers(text: str) -> list[float]:
    """Plausible salary figures in ``text`` (k/m expanded, sub-100 dropped).

    Bare 4-digit year-like values (1950–2099, no thousands separator, no k/m)
    are dropped when other salary figures are present — so "expecting 20000,
    available from 2025" reads 20000, not 2025."""
    parsed: list[tuple[float, bool]] = []
    for m in _NUM_RE.finditer(text or ""):
        digits = m.group(1)
        value = float(digits.replace(",", ""))
        suffix = (m.group(2) or "").lower()
        year_like = suffix == "" and "," not in digits and "." not in digits and 1950 <= value <= 2099
        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000
        if value >= _MIN_SALARY:
            parsed.append((value, year_like))
    non_year = [v for v, year_like in parsed if not year_like]
    return non_year if non_year else [v for v, _ in parsed]


def parse_salary_answer(question_text: str | None, answer_text: str | None) -> ParsedSalary | None:
    """Parse one ``(question, answer)`` pair into a ``ParsedSalary`` or ``None``.

    Returns ``None`` unless the question is a salary question AND the answer
    yields exactly one figure (or a two-number range, taking the lower bound —
    the candidate's minimum ask, the conservative choice given an over-cap
    verdict hides the candidate). More than two figures is treated as ambiguous
    and left to the LLM fallback."""
    if not _SALARY_Q_RE.search(question_text or ""):
        return None
    answer_text = (answer_text or "").strip()
    if not answer_text:
        return None

    numbers = _salary_numbers(answer_text)
    if len(numbers) == 1:
        amount = numbers[0]
    elif len(numbers) == 2:
        amount = min(numbers)  # range → lower bound (minimum expectation)
    else:
        return None

    currency, explicit = _detect_currency(answer_text)
    if not explicit:
        # Currency is often only in the question ("Expected salary in AED?").
        q_currency, q_explicit = _detect_currency(question_text or "")
        if q_explicit:
            currency, explicit = q_currency, True

    return ParsedSalary(
        amount=amount,
        currency=currency,
        amount_aed=round(to_aed(amount, currency), 2),
        raw=answer_text[:500],
        currency_explicit=explicit,
    )


def extract_salary_expectation(answers: object) -> ParsedSalary | None:
    """Scan a candidate's Workable questionnaire ``answers`` for the salary
    expectation and return the parsed figure, or ``None`` when none is found.

    Conservative by design: only salary questions with a parseable figure are
    considered, and an *expected/desired* phrasing is preferred over a *current*
    one. When nothing qualifies the caller leaves the structured columns unset
    and the search falls back to LLM extraction."""
    if not isinstance(answers, list):
        return None
    best: ParsedSalary | None = None
    best_score = 0
    for entry in answers:
        parsed_qa = _parse_answer(entry) if isinstance(entry, dict) else None
        if not parsed_qa:
            continue
        question_text, answer_text = parsed_qa
        salary = parse_salary_answer(question_text, answer_text)
        if salary is None:
            continue
        score = 2 if _EXPECTATION_Q_RE.search(question_text) else 1
        # Strictly-greater keeps the FIRST occurrence among equal scores.
        if score > best_score:
            best, best_score = salary, score
    return best
