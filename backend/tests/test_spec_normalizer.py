"""Tests for the section-aware job spec normalizer."""

from __future__ import annotations

from app.services.spec_normalizer import derive_criteria_texts, normalize_spec


def test_normalize_returns_empty_for_blank_input() -> None:
    spec = normalize_spec("")
    assert spec.description == ""
    assert spec.requirements == ""
    assert spec.benefits == ""

    spec_none = normalize_spec(None)
    assert spec_none.description == ""


def test_normalize_splits_standard_three_section_spec() -> None:
    text = (
        "Description\n"
        "Senior backend engineer for our payments team.\n"
        "We move fast.\n"
        "Requirements\n"
        "- 5+ years Python\n"
        "- Postgres at scale\n"
        "Benefits\n"
        "- Health insurance\n"
        "- 401k match\n"
    )
    spec = normalize_spec(text)
    assert "Senior backend engineer" in spec.description
    assert "5+ years Python" in spec.requirements
    assert "Postgres at scale" in spec.requirements
    assert "Health insurance" in spec.benefits


def test_normalize_treats_pre_heading_text_as_description() -> None:
    text = (
        "Join our team. We are growing fast.\n"
        "Requirements:\n"
        "- TypeScript\n"
    )
    spec = normalize_spec(text)
    assert "Join our team" in spec.description
    assert spec.requirements.strip() == "- TypeScript"


def test_normalize_recognises_markdown_and_bold_headings() -> None:
    text = (
        "## About the role\n"
        "Lead the team.\n"
        "**Qualifications**\n"
        "- 7 years experience\n"
        "### Perks\n"
        "- Equity\n"
    )
    spec = normalize_spec(text)
    assert "Lead the team" in spec.description
    assert "7 years experience" in spec.requirements
    assert "Equity" in spec.benefits


def test_normalize_keeps_everything_in_description_when_no_headings() -> None:
    text = "Just one big paragraph with no headings at all."
    spec = normalize_spec(text)
    assert spec.description == text.strip()
    assert spec.requirements == ""
    assert spec.benefits == ""


def test_derive_criteria_texts_strips_bullet_markers_and_dedupes() -> None:
    text = (
        "- 5+ years Python\n"
        "* 5+ years Python\n"  # duplicate, case-insensitive
        "1. AWS or GCP\n"
        "  - Postgres\n"
    )
    items = derive_criteria_texts(text)
    assert items == ["5+ years Python", "AWS or GCP", "Postgres"]


def test_derive_criteria_texts_caps_at_max_items() -> None:
    text = "\n".join(f"- requirement {i}" for i in range(25))
    items = derive_criteria_texts(text, max_items=10)
    assert len(items) == 10
    assert items[0] == "requirement 0"


def test_derive_criteria_texts_returns_empty_for_empty_input() -> None:
    assert derive_criteria_texts("") == []
    assert derive_criteria_texts(None) == []
