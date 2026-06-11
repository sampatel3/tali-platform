"""Deriver quality: junk filtering + must/preferred/constraint classification.

The Workable spec deriver used to emit every Requirements line as a
``preferred`` criterion, including leaked section headers ("Requirements",
"Benefits"), markdown lead-ins, bare connectives ("and") and boilerplate
prose. These lock in: that junk is dropped, and the bucket reflects the
language (explicit "must"/"required" => must, "nice to have" => preferred,
location/visa => constraint). A bare "N years" line is NOT auto-promoted to
must — years alone is ambiguous and auto-musts cause reject waves on a bulk
re-derive.
"""
from __future__ import annotations

from app.services.spec_normalizer import (
    derive_criteria,
    derive_criteria_texts,
    normalize_spec,
)

_SPEC = (
    "Description\n"
    "We are hiring a senior backend engineer.\n"
    "Requirements\n"
    "- 5+ years of Python experience\n"
    "- Must have production Postgres at scale\n"
    "- Requirements\n"  # leaked header line — must be dropped
    "Nice to have\n"
    "- Familiarity with Kubernetes\n"
    "- Exposure to Kafka is a plus\n"
    "Constraints\n"
    "- Must be located in US time zones\n"
    "- Eligible to work in the US (no visa sponsorship)\n"
    "Benefits\n"
    "- Health insurance and 401k\n"
    "- Competitive salary\n"
)


def test_section_headers_and_benefits_are_filtered():
    spec = normalize_spec(_SPEC)
    texts = derive_criteria_texts(spec.requirements)
    lowered = [t.lower() for t in texts]
    # Leaked "Requirements" header line dropped.
    assert "requirements" not in lowered
    # Perks/benefits never become criteria (they're in their own section AND
    # filtered if they bleed in).
    assert not any("health insurance" in t for t in lowered)
    assert not any("competitive salary" in t for t in lowered)
    # Real requirement lines survive.
    assert any("python" in t for t in lowered)
    assert any("postgres" in t for t in lowered)


def test_explicit_must_language_is_must_have():
    spec = normalize_spec(_SPEC)
    by_text = {c.text.lower(): c for c in derive_criteria(spec.requirements)}

    # Explicit "Must have ..." wording => must-have.
    pg = next(c for t, c in by_text.items() if "postgres" in t)
    assert pg.bucket == "must" and pg.must_have is True


def test_bare_years_of_experience_is_not_auto_must():
    # "5+ years of Python experience" carries no explicit must-language, so it
    # is NOT auto-promoted. Years alone is ambiguous, and auto-musts on a bulk
    # re-derive cause reject waves; recruiters promote must-haves explicitly.
    spec = normalize_spec(_SPEC)
    by_text = {c.text.lower(): c for c in derive_criteria(spec.requirements)}
    py = next(c for t, c in by_text.items() if "python" in t)
    assert py.bucket == "preferred" and py.must_have is False


def test_nice_to_have_is_preferred_not_must():
    spec = normalize_spec(_SPEC)
    by_text = {c.text.lower(): c for c in derive_criteria(spec.requirements)}
    k8s = next(c for t, c in by_text.items() if "kubernetes" in t)
    assert k8s.bucket == "preferred" and k8s.must_have is False
    kafka = next(c for t, c in by_text.items() if "kafka" in t)
    assert kafka.bucket == "preferred"


def test_location_and_eligibility_are_constraints():
    spec = normalize_spec(_SPEC)
    by_text = {c.text.lower(): c for c in derive_criteria(spec.requirements)}
    tz = next(c for t, c in by_text.items() if "time zone" in t)
    assert tz.bucket == "constraint"
    visa = next(c for t, c in by_text.items() if "eligible to work" in t)
    assert visa.bucket == "constraint"


def test_ambiguous_line_defaults_to_preferred():
    # No must/preferred/constraint signal => never auto-promoted to must-have.
    items = derive_criteria("Requirements\n- Comfortable with code review")
    assert items, "should derive the line"
    assert all(c.bucket == "preferred" for c in items)
    assert all(c.must_have is False for c in items)


# The exact pollution pattern seen on a real Workable-synced role: bold prose
# lead-ins, a bare "and" left by a wrapped line, and culture/mission prose.
_JUNK_SPEC = (
    "Requirements\n"
    "**You will have experience in:**\n"
    "- AWS Glue, PySpark, and ETL pipeline development\n"
    "and\n"
    "**You should also have knowledge of:**\n"
    "- Lakehouse architecture and Medallion design\n"
    "As an AI consultancy, our greatest asset is the expertise of our people and their drive.\n"
    "While technical mastery is the foundation of what we do, the ability to communicate matters.\n"
    "If you thrive on the challenge of presenting cutting-edge solutions as much as building them.\n"
)


def test_markdown_headers_connectives_and_prose_are_dropped():
    items = derive_criteria_texts(_JUNK_SPEC)
    lowered = [t.lower() for t in items]
    # Bold lead-in headers (they end in a colon) are not requirements.
    assert not any("you will have experience in" in t for t in lowered)
    assert not any("you should also have knowledge" in t for t in lowered)
    # A bare connective left by a naive line split is dropped.
    assert "and" not in lowered
    # Culture/mission boilerplate prose is dropped (various sentence openers).
    assert not any("ai consultancy" in t for t in lowered)
    assert not any("technical mastery" in t for t in lowered)
    assert not any("if you thrive" in t for t in lowered)
    # The real skill lines survive, with markdown stripped.
    assert any("aws glue" in t for t in lowered)
    assert any("lakehouse" in t for t in lowered)
    # No stored criterion keeps markdown bold markers.
    assert not any("**" in t for t in items)


def test_short_requirement_starting_with_opener_word_survives():
    # A terse requirement that merely starts with an opener word must NOT be
    # dropped as prose — the prose filter also requires real sentence length.
    items = derive_criteria_texts("Requirements\n- We use Python and AWS daily")
    assert any("python" in t.lower() for t in items)


def test_soft_language_does_not_auto_promote_to_must():
    # "minimum", "at least", "proven", "demonstrated" are boilerplate JD phrasing
    # ("minimum 5 years", "proven track record"), NOT decisive must-have wording.
    # They stay preferred so a bulk re-derive doesn't plant tenure/soft hard-bars.
    items = derive_criteria(
        "Requirements\n"
        "- A minimum of 6 years in cybersecurity\n"
        "- Proven track record of delivering platforms at scale\n"
        "- Demonstrated ability to mentor junior engineers\n"
    )
    assert items
    assert all(c.bucket == "preferred" and c.must_have is False for c in items)


def test_decisive_language_still_promotes_to_must():
    # Explicit "required" / "essential" / "must" / "mandatory" => must-have.
    items = derive_criteria(
        "Requirements\n"
        "- Professional certification is required\n"
        "- Essential experience with Kubernetes in production\n"
    )
    assert items
    assert all(c.bucket == "must" and c.must_have is True for c in items)
