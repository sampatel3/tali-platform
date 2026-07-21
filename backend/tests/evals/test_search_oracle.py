"""Independent fact-derived oracle tests."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path

from app.candidate_search.evals.contracts import (
    Citation,
    ConstructedDataset,
    ConstructedWorld,
    Document,
    Fact,
    QueryIntent,
    TruthValue,
    WorldEntity,
)
from app.candidate_search.evals.oracle import derive_judgments
from app.candidate_search.search_plan import (
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


def _citation(document_id: str, content: str, quote: str) -> Citation:
    start = content.index(quote)
    return Citation(
        document_id=document_id,
        start=start,
        end=start + len(quote),
        quote=quote,
    )


def _document(
    document_id: str,
    entity_id: str,
    content: str,
    *,
    source_type: str = "cv",
    source_id: str | None = None,
) -> Document:
    return Document(
        id=document_id,
        entity_id=entity_id,
        source_type=source_type,
        source_id=source_id,
        content=content,
        content_sha256=sha256(content.encode()).hexdigest(),
    )


def _capability(
    criterion_id: str,
    capability: str,
    *,
    modality: Modality = Modality.MUST,
    weight: float = 1.0,
) -> Criterion:
    return Criterion(
        id=criterion_id,
        predicate=Predicate(name="demonstrated"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value=capability),
        comparison=Comparison(operator=ComparisonOperator.EXISTS),
        temporal=TemporalConstraint(minimum_duration_months=12),
        modality=modality,
        evidence=EvidencePolicy(
            require_direct_subject=True,
            require_citation_span=True,
            minimum_sources=1,
            minimum_confidence=0.8,
        ),
        weight=weight,
    )


def _world_and_intent() -> tuple[ConstructedWorld, QueryIntent]:
    alpha_text = (
        "Alpha delivered distributed systems and used a graph database "
        "from 2023 through 2025."
    )
    beta_text = "Beta's team delivered distributed systems from 2022 through 2025."
    delta_text = "Delta delivered distributed systems and used a graph database."
    documents = (
        _document("doc-alpha", "alpha", alpha_text),
        _document("doc-beta", "beta", beta_text),
        _document("doc-delta", "delta", delta_text),
    )
    facts = (
        Fact(
            id="alpha-distributed",
            subject_id="alpha",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="distributed systems"),
            valid_from=date(2023, 1, 1),
            valid_to=date(2025, 1, 1),
            confidence=0.95,
            direct_subject=True,
            provenance=(
                _citation("doc-alpha", alpha_text, "delivered distributed systems"),
            ),
        ),
        Fact(
            id="alpha-graph",
            subject_id="alpha",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="graph database"),
            valid_from=date(2023, 6, 1),
            valid_to=date(2025, 1, 1),
            confidence=0.9,
            direct_subject=True,
            provenance=(
                _citation("doc-alpha", alpha_text, "used a graph database"),
            ),
        ),
        Fact(
            id="beta-team-distributed",
            subject_id="beta",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="distributed systems"),
            valid_from=date(2022, 1, 1),
            valid_to=date(2025, 1, 1),
            confidence=0.99,
            direct_subject=False,
            provenance=(
                _citation("doc-beta", beta_text, "team delivered distributed systems"),
            ),
        ),
        Fact(
            id="gamma-distributed",
            subject_id="gamma",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="distributed systems"),
            valid_from=date(2021, 1, 1),
            valid_to=date(2024, 1, 1),
            confidence=0.95,
            direct_subject=True,
            provenance=(),
        ),
        Fact(
            id="delta-distributed",
            subject_id="delta",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="distributed systems"),
            valid_from=date(2024, 1, 1),
            valid_to=date(2025, 7, 1),
            confidence=0.95,
            direct_subject=True,
            provenance=(
                _citation("doc-delta", delta_text, "delivered distributed systems"),
            ),
        ),
        Fact(
            id="delta-graph",
            subject_id="delta",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="graph database"),
            valid_from=date(2025, 1, 1),
            valid_to=date(2025, 7, 1),
            confidence=0.95,
            direct_subject=True,
            provenance=(
                _citation("doc-delta", delta_text, "used a graph database"),
            ),
        ),
    )
    world = ConstructedWorld(
        id="oracle-world",
        entities=tuple(
            WorldEntity(id=name, kind="person")
            for name in ("alpha", "beta", "gamma", "delta", "empty")
        ),
        documents=documents,
        facts=facts,
        closed_world_predicates=("demonstrated",),
    )
    required = _capability("distributed", "distributed systems")
    preferred = _capability(
        "graph",
        "graph database",
        modality=Modality.SHOULD,
        weight=2.0,
    )
    plan = SearchPlan(
        query="Distributed systems for a year; graph database preferred",
        criteria=(required, preferred),
        root=Expression.leaf("distributed"),
    )
    return world, QueryIntent(id="distributed-search", plan=plan)


def test_oracle_derives_eligibility_from_facts_and_evidence_policy() -> None:
    world, intent = _world_and_intent()
    judgments = derive_judgments(world, intent)
    by_id = {judgment.entity_id: judgment for judgment in judgments}

    assert {row.entity_id for row in judgments if row.eligible} == {"alpha", "delta"}
    assert by_id["beta"].failed_criteria == ("distributed",)  # team attribution
    assert by_id["gamma"].failed_criteria == ("distributed",)  # no citation
    assert by_id["empty"].failed_criteria == ("distributed",)


def test_oracle_uses_preferences_only_for_graded_relevance() -> None:
    world, intent = _world_and_intent()
    by_id = {row.entity_id: row for row in derive_judgments(world, intent)}

    assert by_id["alpha"].eligible is True
    assert by_id["delta"].eligible is True
    assert by_id["alpha"].relevance > by_id["delta"].relevance
    assert by_id["delta"].matched_criteria == ("distributed",)


def test_oracle_obeys_nested_any_and_not_semantics() -> None:
    world, intent = _world_and_intent()
    distributed = intent.plan.criteria_by_id["distributed"]
    forbidden = Criterion(
        id="graph",
        predicate=intent.plan.criteria_by_id["graph"].predicate,
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="graph database"),
        comparison=Comparison(operator=ComparisonOperator.EXISTS),
        modality=Modality.MUST_NOT,
        evidence=EvidencePolicy(require_citation_span=True),
    )
    plan = SearchPlan(
        query="Distributed systems but not graph database",
        criteria=(distributed, forbidden),
        root=Expression.all(
            Expression.leaf("distributed"),
            Expression.not_(Expression.leaf("graph")),
        ),
    )

    judgments = derive_judgments(world, QueryIntent(id="not-graph", plan=plan))
    assert {row.entity_id for row in judgments if row.eligible} == set()


def test_oracle_applies_typed_numeric_comparisons() -> None:
    content = "Seven years of platform engineering"
    world = ConstructedWorld(
        id="numeric-world",
        entities=(WorldEntity(id="person-1", kind="person"),),
        documents=(
            _document("experience-doc", "person-1", content),
        ),
        facts=(
            Fact(
                id="experience-fact",
                subject_id="person-1",
                predicate="experience_years",
                object=SearchObject(kind="capability", value="platform engineering"),
                value=7,
                provenance=(
                    _citation("experience-doc", content, "Seven years"),
                ),
            ),
        ),
        closed_world_predicates=("experience_years",),
    )
    criterion = Criterion(
        id="five-years",
        predicate=Predicate(name="experience_years"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="platform engineering"),
        comparison=Comparison(operator=ComparisonOperator.GTE, value=5),
        evidence=EvidencePolicy(require_citation_span=True),
    )
    intent = QueryIntent(
        id="numeric-query",
        plan=SearchPlan(
            query="At least five years of platform engineering",
            criteria=(criterion,),
            root=Expression.leaf("five-years"),
        ),
    )

    judgment = derive_judgments(world, intent)[0]

    assert judgment.eligible is True
    assert judgment.relevance == 1.0


def test_not_requires_closed_world_truth_instead_of_unknown_as_absent() -> None:
    forbidden = Criterion(
        id="training",
        predicate=Predicate(name="trained_in"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="Python"),
        modality=Modality.MUST_NOT,
        evidence=EvidencePolicy(minimum_sources=0, require_citation_span=False),
    )
    intent = QueryIntent(
        id="not-training",
        plan=SearchPlan(
            query="Not trained in Python",
            criteria=(forbidden,),
            root=Expression.not_(Expression.leaf("training")),
        ),
    )
    open_world = ConstructedWorld(
        id="open",
        entities=(WorldEntity(id="person", kind="person"),),
    )
    closed_world = open_world.model_copy(
        update={"closed_world_predicates": ("trained_in",)}
    )

    assert derive_judgments(open_world, intent)[0].eligibility is TruthValue.UNKNOWN
    assert derive_judgments(closed_world, intent)[0].eligibility is TruthValue.TRUE


def test_calendar_duration_and_explicit_ongoing_current_semantics() -> None:
    texts = {
        "exact": "Kubernetes from 2022 through 2024",
        "ongoing": "Kubernetes since 2023 and still current",
        "undated": "Kubernetes",
    }
    documents = tuple(
        _document(f"doc-{entity_id}", entity_id, content)
        for entity_id, content in texts.items()
    )
    facts = (
        Fact(
            id="exact-fact",
            subject_id="exact",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="Kubernetes"),
            valid_from=date(2022, 1, 1),
            valid_to=date(2024, 1, 1),
            provenance=(
                _citation("doc-exact", texts["exact"], "Kubernetes"),
            ),
        ),
        Fact(
            id="ongoing-fact",
            subject_id="ongoing",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="Kubernetes"),
            valid_from=date(2023, 1, 1),
            ongoing=True,
            provenance=(
                _citation("doc-ongoing", texts["ongoing"], "Kubernetes"),
            ),
        ),
        Fact(
            id="undated-fact",
            subject_id="undated",
            predicate="demonstrated",
            object=SearchObject(kind="capability", value="Kubernetes"),
            provenance=(
                _citation("doc-undated", texts["undated"], "Kubernetes"),
            ),
        ),
    )
    world = ConstructedWorld(
        id="time-world",
        entities=tuple(
            WorldEntity(id=entity_id, kind="person") for entity_id in texts
        ),
        documents=documents,
        facts=facts,
        closed_world_predicates=("demonstrated",),
    )
    duration = _capability("duration", "Kubernetes")
    duration = duration.model_copy(
        update={"temporal": TemporalConstraint(minimum_duration_months=24)}
    )
    duration_intent = QueryIntent(
        id="duration",
        plan=SearchPlan(
            query="Two years Kubernetes",
            criteria=(duration,),
            root=Expression.leaf("duration"),
        ),
    )
    duration_truth = {
        row.entity_id: row.eligible for row in derive_judgments(world, duration_intent)
    }
    assert duration_truth == {"exact": True, "ongoing": False, "undated": False}

    current = duration.model_copy(
        update={
            "id": "current",
            "temporal": TemporalConstraint(
                minimum_duration_months=36,
                current_only=True,
                as_of=date(2026, 1, 1),
            ),
        }
    )
    current_intent = QueryIntent(
        id="current",
        plan=SearchPlan(
            query="Currently using Kubernetes for three years",
            criteria=(current,),
            root=Expression.leaf("current"),
        ),
    )
    current_truth = {
        row.entity_id: row.eligible for row in derive_judgments(world, current_intent)
    }
    assert current_truth == {"exact": False, "ongoing": True, "undated": False}


def test_minimum_sources_counts_independent_origins_not_document_chunks() -> None:
    first = "Python delivery one"
    second = "Python delivery two"
    docs = (
        _document("chunk-1", "person", first),
        _document("chunk-2", "person", second),
    )
    fact = Fact(
        id="python",
        subject_id="person",
        predicate="demonstrated",
        object=SearchObject(kind="capability", value="Python"),
        provenance=(
            _citation("chunk-1", first, "Python"),
            _citation("chunk-2", second, "Python"),
        ),
    )
    criterion = Criterion(
        id="python-two-sources",
        predicate=Predicate(name="demonstrated"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="Python"),
        evidence=EvidencePolicy(minimum_sources=2),
    )
    intent = QueryIntent(
        id="two-sources",
        plan=SearchPlan(
            query="Python verified by two sources",
            criteria=(criterion,),
            root=Expression.leaf(criterion.id),
        ),
    )
    one_origin = ConstructedWorld(
        id="one-origin",
        entities=(WorldEntity(id="person", kind="person"),),
        documents=docs,
        facts=(fact,),
        closed_world_predicates=("demonstrated",),
    )
    independent_docs = (
        docs[0],
        docs[1].model_copy(update={"source_id": "independent-reference"}),
    )
    two_origins = ConstructedWorld(
        id="two-origins",
        entities=one_origin.entities,
        documents=independent_docs,
        facts=(fact,),
        closed_world_predicates=("demonstrated",),
    )

    assert derive_judgments(one_origin, intent)[0].eligible is False
    assert derive_judgments(two_origins, intent)[0].eligible is True


def test_constructed_fixture_derives_diverse_truth_from_facts() -> None:
    fixture = (
        Path(__file__).parents[2]
        / "app"
        / "candidate_search"
        / "evals"
        / "fixtures"
        / "domain_neutral_v1.json"
    )
    dataset = ConstructedDataset.model_validate_json(fixture.read_text())
    derived = {
        intent.id: {
            row.entity_id
            for row in derive_judgments(dataset.world, intent)
            if row.eligible
        }
        for intent in dataset.intents
    }

    assert derived == {
        "sustained-kubernetes": {"p1"},
        "language-with-exclusion": {"p3", "p4", "p6"},
        "graph-relations": {"p6"},
        "notice-period-comparison": {"p6", "p7"},
        "education-and-employer": {"p7"},
        "grounded-empty-result": set(),
        "two-source-clearance": {"p8"},
    }
