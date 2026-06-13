"""Unit tests for the Workable salary-expectation parser.

The parser turns a free-text questionnaire answer ("18k", "18,000–22,000",
"65,000 GBP") into a structured (amount, currency, AED) figure at sync, so the
grounded search reads a number instead of LLM-extracting it per query. These
cover the value/range/shorthand/currency handling and the conservative
question-matching that decides WHICH answer is the salary expectation.
"""

from __future__ import annotations

import pytest

from app.components.integrations.workable.salary_parser import (
    ParsedSalary,
    extract_salary_expectation,
    parse_salary_answer,
    to_aed,
)


# ── single value / shorthand ──────────────────────────────────────────────


def test_plain_number_assumes_aed():
    s = parse_salary_answer("What is your salary expectation?", "18000")
    assert s is not None
    assert s.amount == 18000.0
    assert s.currency == "AED"
    assert s.currency_explicit is False
    assert s.amount_aed == 18000.0
    assert s.raw == "18000"


def test_thousands_separator():
    s = parse_salary_answer("Expected salary", "18,000")
    assert s.amount == 18000.0
    assert s.amount_aed == 18000.0


def test_k_shorthand():
    s = parse_salary_answer("Salary expectation?", "27k AED")
    assert s.amount == 27000.0
    assert s.currency == "AED"
    assert s.amount_aed == 27000.0


def test_million_shorthand_annual():
    s = parse_salary_answer("Expected annual compensation", "1.2m")
    assert s.amount == 1_200_000.0


# ── ranges → lower bound ───────────────────────────────────────────────────


def test_range_en_dash_takes_lower_bound():
    s = parse_salary_answer("Salary expectation", "18,000–22,000")
    assert s.amount == 18000.0  # minimum ask — conservative for a cap


def test_range_with_words_and_k():
    s = parse_salary_answer("Expected pay", "18k to 22k")
    assert s.amount == 18000.0


# ── currency detection + AED normalisation ─────────────────────────────────


def test_currency_in_answer_gbp_normalised():
    s = parse_salary_answer("Salary expectation", "65,000 GBP")
    assert s.currency == "GBP"
    assert s.currency_explicit is True
    assert s.amount == 65000.0
    assert s.amount_aed == pytest.approx(65000 * 4.66, rel=1e-6)


def test_dollar_symbol_is_usd():
    s = parse_salary_answer("Expected monthly salary", "$5,000")
    assert s.currency == "USD"
    assert s.amount_aed == pytest.approx(5000 * 3.6725, rel=1e-6)


def test_currency_from_question_when_answer_bare():
    # Currency is frequently stated only in the question.
    s = parse_salary_answer("Expected monthly salary in AED?", "18000")
    assert s.currency == "AED"
    assert s.currency_explicit is True
    assert s.amount_aed == 18000.0


def test_answer_currency_wins_over_question():
    s = parse_salary_answer("Expected salary in AED", "5000 USD")
    assert s.currency == "USD"


def test_to_aed_known_and_unknown():
    assert to_aed(1000, "AED") == 1000.0
    assert to_aed(1000, "USD") == pytest.approx(3672.5)
    assert to_aed(1000, "ZZZ") == 1000.0  # unknown → 1:1


# ── conservative gating ────────────────────────────────────────────────────


def test_non_salary_question_returns_none():
    assert parse_salary_answer("How many years of experience?", "18000") is None


def test_salary_question_non_numeric_answer_returns_none():
    assert parse_salary_answer("Salary expectation?", "Negotiable") is None
    assert parse_salary_answer("Salary expectation?", "") is None


def test_sub_floor_numbers_ignored():
    # "5 LPA" etc. — 5 is below the salary floor, so nothing parses.
    assert parse_salary_answer("Expected CTC?", "5") is None


def test_three_or_more_numbers_is_ambiguous():
    # Too many figures → leave it to the LLM fallback rather than guess.
    assert parse_salary_answer("Salary expectation", "18000, 22000, 30000") is None


def test_stray_year_dropped_when_a_salary_is_present():
    s = parse_salary_answer("Expected salary", "20000, can start from 2025")
    assert s is not None
    assert s.amount == 20000.0


# ── extract_salary_expectation over a full answers list ────────────────────


def _qa(question: str, answer: str) -> dict:
    return {"question": {"body": question}, "answer": {"body": answer}}


def test_extract_finds_salary_among_answers():
    answers = [
        _qa("Are you willing to relocate?", "Yes"),
        _qa("What is your expected salary?", "AED 25,000"),
        _qa("Notice period?", "30 days"),
    ]
    s = extract_salary_expectation(answers)
    assert s is not None
    assert s.amount_aed == 25000.0
    assert s.currency == "AED"


def test_extract_prefers_expected_over_current():
    answers = [
        _qa("Current salary", "30,000 AED"),
        _qa("Expected salary", "22,000 AED"),
    ]
    s = extract_salary_expectation(answers)
    assert s.amount == 22000.0  # the expectation, not what they earn today


def test_extract_falls_back_to_current_when_no_expected():
    answers = [_qa("Current monthly salary", "19,000 AED")]
    s = extract_salary_expectation(answers)
    assert s is not None and s.amount == 19000.0


def test_extract_returns_none_when_no_salary_question():
    answers = [
        _qa("Are you willing to relocate?", "Yes"),
        _qa("Notice period?", "2 months"),
    ]
    assert extract_salary_expectation(answers) is None


def test_extract_handles_flat_answer_shape():
    # Flat Workable shape: body at the top level rather than nested.
    answers = [{"question": {"body": "Salary expectation"}, "body": "18k"}]
    s = extract_salary_expectation(answers)
    assert s is not None and s.amount == 18000.0


def test_extract_non_list_is_none():
    assert extract_salary_expectation(None) is None
    assert extract_salary_expectation("18000") is None
