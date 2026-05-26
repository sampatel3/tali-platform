"""Tests for ``app.llm.grounding.fuzzy_locate`` — the shared evidence-
grounding primitive promoted out of cv_matching/validation.py.

cv_matching covered this indirectly through ``validate_evidence_grounding``;
these are the direct unit tests for the primitive so future consumers
(pre_screen reasoning, agent explanations, chat answers) have a single
verified spec to rely on.
"""

from __future__ import annotations

from app.llm.grounding import (
    FUZZY_THRESHOLD,
    MIN_FUZZY_LEN,
    fuzzy_locate,
)


# --------------------------------------------------------------------------- #
# Fast paths                                                                   #
# --------------------------------------------------------------------------- #


def test_exact_substring_match_returns_position():
    text = "Python developer for 6 years at FinTechCo"
    quote = "Python developer for 6 years"
    located = fuzzy_locate(quote, text)
    assert located == (0, len(quote))


def test_exact_substring_in_middle():
    text = "We built data pipelines at scale on AWS Glue."
    quote = "AWS Glue"
    located = fuzzy_locate(quote, text)
    assert located == (text.find(quote), text.find(quote) + len(quote))


def test_case_insensitive_match_when_exact_misses():
    text = "Worked at REGIONAL BANK in Dubai"
    quote = "Regional Bank"
    located = fuzzy_locate(quote, text)
    assert located is not None
    start, end = located
    # The match returns the original casing's span length.
    assert end - start == len(quote)


# --------------------------------------------------------------------------- #
# Fuzzy paths (paraphrased / whitespace-varying quotes)                        #
# --------------------------------------------------------------------------- #


def test_fuzzy_match_tolerates_whitespace_collapse():
    """LLM emitted the quote with collapsed whitespace; the source has
    extra spacing. Fuzzy path normalises both and locates the quote.

    Text and quote must be close in length — the window-similarity check
    rejects matches where surrounding context dilutes the ratio below
    ``FUZZY_THRESHOLD``. That's the intended trade: tolerate paraphrasing
    of THE quote, not "find this short quote anywhere in a long source".
    """
    text = "Operated 30+   data pipelines   on AWS Glue"
    quote = "Operated 30+ data pipelines on AWS Glue"
    located = fuzzy_locate(quote, text)
    assert located is not None


def test_fuzzy_match_tolerates_minor_paraphrase():
    """Close paraphrase (an extra word) above threshold should still locate."""
    text = "Built the streaming pipelines on Kinesis and Spark Structured Streaming"
    quote = "Built streaming pipelines on Kinesis and Spark Structured Streaming"
    located = fuzzy_locate(quote, text)
    assert located is not None


# --------------------------------------------------------------------------- #
# Negative paths — the hallucination guard                                     #
# --------------------------------------------------------------------------- #


def test_unrelated_quote_returns_none():
    """Fully fabricated quote must not match — this is the anti-hallucination
    guarantee cv_matching's grounding validator depends on."""
    text = "Python developer for 6 years at FinTechCo"
    quote = "Built a quantum compiler at Lockheed Martin"
    assert fuzzy_locate(quote, text) is None


def test_tiny_quote_below_min_length_no_fuzzy():
    """Tiny quotes (< MIN_FUZZY_LEN) must NOT take the fuzzy path —
    they false-positive too easily. Exact / case-insensitive still work."""
    text = "Senior Python developer with broad backend experience"
    short_unrelated = "Java"  # 4 chars, far below MIN_FUZZY_LEN
    assert len(short_unrelated) < MIN_FUZZY_LEN
    assert fuzzy_locate(short_unrelated, text) is None


def test_empty_quote_or_text_returns_none():
    assert fuzzy_locate("", "some text") is None
    assert fuzzy_locate("some quote", "") is None
    assert fuzzy_locate("", "") is None


def test_threshold_is_strict_enough_to_reject_random_text():
    """Random unrelated long text shouldn't sneak past the threshold."""
    text = "Banking analyst with treasury management experience in Lagos"
    quote = "AWS Glue pipeline engineer in Dubai banking sector"
    assert fuzzy_locate(quote, text) is None


# --------------------------------------------------------------------------- #
# Module surface                                                               #
# --------------------------------------------------------------------------- #


def test_module_exports_threshold_constant():
    """Consumers can read the threshold (e.g. to log "matched at 0.87")."""
    assert 0.5 < FUZZY_THRESHOLD <= 1.0


def test_reexported_from_app_llm_package():
    """Public surface: ``from app.llm import fuzzy_locate`` works."""
    from app.llm import fuzzy_locate as gateway_fuzzy_locate

    assert gateway_fuzzy_locate is fuzzy_locate
