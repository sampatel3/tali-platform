"""Deterministic grounding checks; no graph or model provider is used."""

from __future__ import annotations

import pytest

from app.candidate_search.evidence_matching import contains_grounding_value


@pytest.mark.parametrize(
    ("content", "value"),
    [
        ("No Kubernetes experience.", "Kubernetes"),
        ("No production Kubernetes experience.", "Kubernetes experience"),
        ("No experience with Terraform.", "Terraform"),
        ("Never deployed Docker in production.", "Docker experience"),
        ("Did not use Snowflake on any project.", "Snowflake"),
    ],
)
def test_explicit_negation_never_grounds_a_skill_or_experience(
    content: str,
    value: str,
):
    assert contains_grounding_value(content, value) is False


@pytest.mark.parametrize(
    ("content", "value"),
    [
        ("Built and operated Kubernetes production clusters.", "Kubernetes"),
        ("Built and operated Kubernetes production clusters.", "Kubernetes experience"),
        (
            "No Java experience, but built Kubernetes production clusters.",
            "Kubernetes experience",
        ),
        ("Implemented Terraform modules for the cloud platform.", "Terraform"),
        ("Used Snowflake to build the analytics warehouse.", "Snowflake experience"),
    ],
)
def test_affirmative_applied_evidence_remains_valid(content: str, value: str):
    assert contains_grounding_value(content, value) is True


@pytest.mark.parametrize(
    ("predicate", "value", "content"),
    [
        ("worked_at", "Acme Corp", "Never worked at Acme Corp."),
        ("worked_at", "Acme Corp", "Acme Corp was a client; worked at Globex."),
        ("worked_at", "Acme Corp", "Worked at Acme; Corp was another client."),
        (
            "studied_at",
            "Northern University",
            "Northern was a client and University projects were discussed.",
        ),
        ("studied_at", "Oxford", "Worked at Oxford Analytics after studying statistics."),
    ],
)
def test_relationships_reject_negation_and_mere_token_cooccurrence(
    predicate: str,
    value: str,
    content: str,
):
    assert contains_grounding_value(content, value, predicate=predicate) is False


@pytest.mark.parametrize(
    ("predicate", "value", "content"),
    [
        ("worked_at", "Acme Corp", "Worked at Acme Corp for five years."),
        (
            "worked_at",
            "Acme Corp",
            "Never worked at Globex; worked at Acme Corp for five years.",
        ),
        ("worked_at", "Acme Corp", "Senior Engineer at Acme Corp."),
        ("worked_at", "Acme Corp", "Employed by Acme Corp as an analyst."),
        (
            "studied_at",
            "University of Leeds",
            "Studied at University of Leeds from 2017 to 2020.",
        ),
        (
            "studied_at",
            "University of Leeds",
            "Graduated from University of Leeds with an MSc.",
        ),
    ],
)
def test_affirmative_relationship_evidence_requires_entity_bound_marker(
    predicate: str,
    value: str,
    content: str,
):
    assert contains_grounding_value(content, value, predicate=predicate) is True
