"""Contract tests for the domain-neutral search plan and eval fixtures."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.candidate_search.evals.contracts import (
    Citation,
    ConstructedDataset,
    ConstructedWorld,
    Document,
    Fact,
    WorldEntity,
)
from app.candidate_search.search_plan import (
    BooleanOperator,
    Comparison,
    ComparisonOperator,
    Criterion,
    EvidencePolicy,
    Expression,
    Modality,
    Predicate,
    SearchObject,
    SearchPlan,
    TemporalConstraint,
)


def _criterion(
    criterion_id: str,
    *,
    modality: Modality = Modality.MUST,
) -> Criterion:
    return Criterion(
        id=criterion_id,
        predicate=Predicate(name="has_capability"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value=criterion_id),
        comparison=Comparison(operator=ComparisonOperator.EXISTS),
        temporal=TemporalConstraint(minimum_duration_months=12),
        modality=modality,
        evidence=EvidencePolicy(
            require_direct_subject=True,
            require_citation_span=True,
            minimum_sources=1,
        ),
    )


def test_search_plan_supports_nested_all_any_not_round_trip() -> None:
    criteria = (
        _criterion("python"),
        _criterion("go"),
        _criterion("training_only", modality=Modality.MUST_NOT),
        _criterion("graphdb", modality=Modality.SHOULD),
    )
    root = Expression.all(
        Expression.any(Expression.leaf("python"), Expression.leaf("go")),
        Expression.not_(Expression.leaf("training_only")),
    )

    plan = SearchPlan(
        query="Python or Go, not training-only; GraphDB preferred",
        criteria=criteria,
        root=root,
        limit=25,
    )

    restored = SearchPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.root.operator is BooleanOperator.ALL
    assert restored.root.referenced_criterion_ids() == {
        "python",
        "go",
        "training_only",
    }
    assert [criterion.id for criterion in restored.ranking_criteria] == ["graphdb"]


def test_search_plan_rejects_invalid_tree_and_unknown_references() -> None:
    with pytest.raises(ValidationError, match="NOT requires exactly one child"):
        Expression(
            operator=BooleanOperator.NOT,
            children=(Expression.leaf("python"), Expression.leaf("go")),
        )

    with pytest.raises(ValidationError, match="unknown criterion"):
        SearchPlan(
            query="Python",
            criteria=(_criterion("python"),),
            root=Expression.leaf("rust"),
        )


def test_search_plan_rejects_unreferenced_mandatory_criteria() -> None:
    with pytest.raises(ValidationError, match="mandatory criteria must appear"):
        SearchPlan(
            query="Python and Go",
            criteria=(_criterion("python"), _criterion("go")),
            root=Expression.leaf("python"),
        )


def test_true_root_supports_preference_only_search_without_fake_evidence() -> None:
    plan = SearchPlan(
        query="GraphDB preferred",
        criteria=(_criterion("graphdb", modality=Modality.SHOULD),),
        root=Expression.true(),
    )

    assert plan.root.operator is BooleanOperator.TRUE
    assert plan.eligibility_criteria == ()


def test_preferred_criterion_cannot_accidentally_become_hard_filter() -> None:
    with pytest.raises(ValidationError, match="SHOULD criteria cannot appear"):
        SearchPlan(
            query="GraphDB preferred",
            criteria=(_criterion("graphdb", modality=Modality.SHOULD),),
            root=Expression.leaf("graphdb"),
        )


def test_modality_must_match_boolean_polarity() -> None:
    with pytest.raises(ValidationError, match="MUST_NOT criteria require NOT"):
        SearchPlan(
            query="Exclude training-only evidence",
            criteria=(_criterion("training", modality=Modality.MUST_NOT),),
            root=Expression.leaf("training"),
        )

    with pytest.raises(ValidationError, match="MUST criteria cannot be negated"):
        SearchPlan(
            query="Exclude training-only evidence",
            criteria=(_criterion("training"),),
            root=Expression.not_(Expression.leaf("training")),
        )


def test_constructed_fixture_has_independent_intents_not_expected_ids() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "app"
        / "candidate_search"
        / "evals"
        / "fixtures"
        / "domain_neutral_v1.json"
    )
    raw = fixture.read_text(encoding="utf-8")
    dataset = ConstructedDataset.model_validate_json(raw)

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    answer_shaped_keys = {
        key
        for key in keys(json.loads(raw))
        if "expected" in key.casefold() and "id" in key.casefold()
    }
    assert answer_shaped_keys == set()
    assert dataset.version == "1.1.0"
    assert len(dataset.world.entities) >= 8
    assert len(dataset.intents) >= 5
    assert {intent.plan.query for intent in dataset.intents}
    assert all(document.content_sha256 for document in dataset.world.documents)
    predicates = {fact.predicate for fact in dataset.world.facts}
    assert {
        "demonstrated",
        "notice_period_days",
        "studied_at",
        "worked_at",
        "verified",
    } <= predicates


def test_constructed_fixture_exposes_kubernetes_directness_and_duration() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "app"
        / "candidate_search"
        / "evals"
        / "fixtures"
        / "domain_neutral_v1.json"
    )
    dataset = ConstructedDataset.model_validate_json(
        fixture.read_text(encoding="utf-8")
    )
    documents = {
        document.id: document
        for document in dataset.retrieval_corpus.documents
    }

    assert "Built and operated Kubernetes" in documents["p1-k8s"].content
    assert "2022-01-01" in documents["p1-k8s"].content
    assert "2024-07-01" in documents["p1-k8s"].content
    assert "did not operate it directly" in documents["p2-k8s"].content


def test_constructed_fixture_matches_version_pinned_digest() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "app"
        / "candidate_search"
        / "evals"
        / "fixtures"
        / "domain_neutral_v1.json"
    )
    raw = fixture.read_bytes()
    dataset = ConstructedDataset.model_validate_json(raw)
    pinned_digests = {
        "1.1.0": "a8357fdf9d39b61408724f26db303ab1a0f387f87aae5017a9ba7c838dea7479"
    }

    assert sha256(raw).hexdigest() == pinned_digests[dataset.version]


def test_document_hash_makes_constructed_truth_immutable() -> None:
    content = "Grounded source text"
    document = Document(
        id="doc",
        entity_id="person",
        source_type="cv",
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
    )
    assert document.content_sha256 == sha256(content.encode()).hexdigest()

    with pytest.raises(ValidationError, match="content_sha256 does not match"):
        Document(
            id="doc",
            entity_id="person",
            source_type="cv",
            content="Changed source text",
            content_sha256=document.content_sha256,
        )


def test_constructed_world_rejects_invalid_provenance_span() -> None:
    content = "Evidence"
    document = Document(
        id="doc",
        entity_id="person",
        source_type="cv",
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
    )

    with pytest.raises(ValidationError, match="citation exceeds document"):
        ConstructedWorld(
            id="invalid-world",
            entities=(WorldEntity(id="person", kind="person"),),
            documents=(document,),
            facts=(
                Fact(
                    id="fact",
                    subject_id="person",
                    predicate="demonstrated",
                    object=SearchObject(kind="capability", value="testing"),
                    provenance=(
                        Citation(
                            document_id="doc",
                            start=0,
                            end=20,
                            quote="Evidence",
                        ),
                    ),
                ),
            ),
        )
