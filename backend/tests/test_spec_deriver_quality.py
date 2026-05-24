"""Deriver quality: junk filtering + must/preferred/constraint classification.

The Workable spec deriver used to emit every Requirements line as a
``preferred`` criterion, including leaked section headers ("Requirements",
"Benefits") and perks. These lock in: headers/perks are dropped, and the
bucket reflects the language (years-of-experience / "must" => must,
"nice to have" => preferred, location/visa => constraint).
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


def test_must_have_classification():
    spec = normalize_spec(_SPEC)
    by_text = {c.text.lower(): c for c in derive_criteria(spec.requirements)}

    # Years-of-experience + explicit "must" => must-have.
    py = next(c for t, c in by_text.items() if "python" in t)
    assert py.bucket == "must" and py.must_have is True
    pg = next(c for t, c in by_text.items() if "postgres" in t)
    assert pg.bucket == "must" and pg.must_have is True


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
