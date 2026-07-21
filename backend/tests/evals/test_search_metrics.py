"""Search quality metrics are computed at every retrieval stage."""

from __future__ import annotations

import math
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.candidate_search.evals.contracts import (
    Citation,
    CriterionEvidence,
    CriterionJudgment,
    Document,
    OracleJudgment,
    RankedHit,
    RetrievalStageOutput,
    TruthValue,
)
from app.candidate_search.evals.metrics import evaluate_stage
from app.candidate_search.search_plan import (
    Criterion,
    EvidencePolicy,
    Expression,
    Modality,
    Predicate,
    SearchObject,
    SearchPlan,
)


def _citation(document_id: str, content: str, quote: str) -> Citation:
    start = content.index(quote)
    return Citation(
        document_id=document_id,
        start=start,
        end=start + len(quote),
        quote=quote,
    )


def _setup() -> tuple[
    SearchPlan,
    tuple[Document, ...],
    tuple[OracleJudgment, ...],
]:
    content = "Built graph retrieval and deterministic evidence checks."
    documents = tuple(
        Document(
            id=f"doc-{entity_id}",
            entity_id=entity_id,
            source_type="cv",
            content=content,
            content_sha256=sha256(content.encode()).hexdigest(),
        )
        for entity_id in ("a", "b", "c")
    )
    criterion = Criterion(
        id="graph",
        predicate=Predicate(name="demonstrated"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="graph retrieval"),
        evidence=EvidencePolicy(minimum_sources=1),
    )
    plan = SearchPlan(
        query="Graph retrieval",
        criteria=(criterion,),
        root=Expression.leaf("graph"),
    )
    judgments = (
        OracleJudgment(
            entity_id="a",
            eligibility=TruthValue.TRUE,
            relevance=3.0,
            matched_criteria=("graph",),
            criterion_judgments=(
                CriterionJudgment(
                    criterion_id="graph",
                    truth=TruthValue.TRUE,
                    supporting_citations=(
                        _citation("doc-a", content, "graph retrieval"),
                    ),
                ),
            ),
        ),
        OracleJudgment(
            entity_id="b",
            eligibility=TruthValue.TRUE,
            relevance=1.0,
            matched_criteria=("graph",),
            criterion_judgments=(
                CriterionJudgment(
                    criterion_id="graph",
                    truth=TruthValue.TRUE,
                    supporting_citations=(
                        _citation("doc-b", content, "graph retrieval"),
                    ),
                ),
            ),
        ),
        OracleJudgment(
            entity_id="c",
            eligibility=TruthValue.FALSE,
            relevance=0.0,
            failed_criteria=("graph",),
            criterion_judgments=(
                CriterionJudgment(
                    criterion_id="graph",
                    truth=TruthValue.FALSE,
                ),
            ),
        ),
    )
    return plan, documents, judgments


def test_stage_metrics_include_precision_recall_mrr_and_ndcg() -> None:
    plan, documents, judgments = _setup()
    stage = RetrievalStageOutput(
        stage="graph_retrieval",
        hits=(
            RankedHit(entity_id="c", score=0.99),
            RankedHit(entity_id="a", score=0.8),
            RankedHit(entity_id="b", score=0.7),
        ),
    )

    metrics = evaluate_stage(
        stage,
        judgments,
        plan=plan,
        documents=documents,
        k=2,
    )

    assert metrics.precision_at_k == pytest.approx(0.5)
    assert metrics.recall_at_k == pytest.approx(0.5)
    assert metrics.mrr == pytest.approx(0.5)
    assert metrics.false_positive_count_at_k == 1
    ideal_dcg = 7 / math.log2(2) + 1 / math.log2(3)
    actual_dcg = 7 / math.log2(3)
    assert metrics.ndcg_at_k == pytest.approx(actual_dcg / ideal_dcg)


def test_criterion_linked_citations_distinguish_span_from_claim_support() -> None:
    plan, documents, judgments = _setup()
    content = documents[0].content
    stage = RetrievalStageOutput(
        stage="grounding",
        hits=(
            RankedHit(
                entity_id="a",
                score=1.0,
                evidence=(
                    CriterionEvidence(
                        criterion_id="graph",
                        citations=(
                            _citation("doc-a", content, "graph retrieval"),
                            Citation(
                                document_id="doc-a",
                                start=0,
                                end=5,
                                quote="wrong",
                            ),
                            _citation("doc-b", content, "graph retrieval"),
                            _citation("doc-a", content, "Built"),
                        ),
                    ),
                ),
            ),
        ),
    )

    metrics = evaluate_stage(
        stage,
        judgments,
        plan=plan,
        documents=documents,
        k=1,
    )

    assert metrics.citation_count == 4
    assert metrics.valid_citation_count == 2
    assert metrics.supported_citation_count == 1
    assert metrics.citation_span_validity == pytest.approx(0.5)
    assert metrics.citation_support_validity == pytest.approx(0.25)
    assert metrics.grounded_hit_coverage == 1.0


def test_unrelated_valid_span_does_not_ground_a_hit() -> None:
    plan, documents, judgments = _setup()
    stage = RetrievalStageOutput(
        stage="grounding",
        hits=(
            RankedHit(
                entity_id="a",
                score=1.0,
                evidence=(
                    CriterionEvidence(
                        criterion_id="graph",
                        citations=(
                            _citation("doc-a", documents[0].content, "Built"),
                        ),
                    ),
                ),
            ),
        ),
    )

    metrics = evaluate_stage(
        stage,
        judgments,
        plan=plan,
        documents=documents,
        k=1,
    )

    assert metrics.citation_span_validity == 1.0
    assert metrics.citation_support_validity == 0.0
    assert metrics.grounded_hit_coverage == 0.0


def test_false_positive_on_zero_truth_is_not_perfect_recall_or_ndcg() -> None:
    plan, documents, judgments = _setup()
    no_gold = tuple(
        row.model_copy(
            update={
                "eligibility": TruthValue.FALSE,
                "relevance": 0.0,
                "matched_criteria": (),
            }
        )
        for row in judgments
    )
    stage = RetrievalStageOutput(
        stage="final",
        hits=(RankedHit(entity_id="c", score=1.0),),
    )

    metrics = evaluate_stage(
        stage,
        no_gold,
        plan=plan,
        documents=documents,
        k=1,
    )

    assert metrics.recall_at_k == 0.0
    assert metrics.ndcg_at_k == 0.0
    assert metrics.false_positive_count_at_k == 1
    assert metrics.exact_empty_accuracy == 0.0


def test_exhaustive_empty_result_scores_exact_empty_correctly() -> None:
    plan, documents, judgments = _setup()
    no_gold = tuple(
        row.model_copy(
            update={"eligibility": TruthValue.FALSE, "relevance": 0.0}
        )
        for row in judgments
    )
    stage = RetrievalStageOutput(stage="final", hits=())

    metrics = evaluate_stage(
        stage,
        no_gold,
        plan=plan,
        documents=documents,
        k=1,
    )

    assert metrics.recall_at_k == 1.0
    assert metrics.ndcg_at_k == 1.0
    assert metrics.exact_empty_accuracy == 1.0


def test_metrics_refuse_to_score_indeterminate_open_world_truth() -> None:
    plan, documents, judgments = _setup()
    indeterminate = (
        *judgments[:2],
        judgments[2].model_copy(update={"eligibility": TruthValue.UNKNOWN}),
    )

    with pytest.raises(ValueError, match="indeterminate entities"):
        evaluate_stage(
            RetrievalStageOutput(stage="final", hits=()),
            indeterminate,
            plan=plan,
            documents=documents,
            k=1,
        )


def test_no_citations_is_not_reported_as_perfect_grounding() -> None:
    plan, documents, judgments = _setup()
    stage = RetrievalStageOutput(
        stage="grounding",
        hits=(RankedHit(entity_id="a", score=1.0),),
    )

    metrics = evaluate_stage(
        stage,
        judgments,
        plan=plan,
        documents=documents,
        k=1,
    )

    assert metrics.citation_count == 0
    assert metrics.citation_span_validity == 0.0
    assert metrics.citation_support_validity == 0.0
    assert metrics.grounded_hit_coverage == 0.0


def test_must_not_grounding_requires_an_explicit_verified_absent_claim() -> None:
    plan, documents, judgments = _setup()
    required = plan.criteria[0]
    forbidden = Criterion(
        id="training-only",
        predicate=Predicate(name="training_only"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="graph retrieval"),
        modality=Modality.MUST_NOT,
        evidence=EvidencePolicy(minimum_sources=1),
    )
    negative_plan = SearchPlan(
        query="Graph retrieval excluding training-only evidence",
        criteria=(required, forbidden),
        root=Expression.all(
            Expression.leaf(required.id),
            Expression.not_(Expression.leaf(forbidden.id)),
        ),
    )
    positive_oracle = judgments[0]
    negative_judgment = positive_oracle.model_copy(
        update={
            "criterion_judgments": (
                *positive_oracle.criterion_judgments,
                CriterionJudgment(
                    criterion_id=forbidden.id,
                    truth=TruthValue.FALSE,
                ),
            )
        }
    )
    citation = _citation("doc-a", documents[0].content, "graph retrieval")
    positive_evidence = (
        CriterionEvidence(criterion_id=required.id, citations=(citation,)),
    )

    unchecked = evaluate_stage(
        RetrievalStageOutput(
            stage="final",
            hits=(RankedHit(entity_id="a", score=1.0, evidence=positive_evidence),),
        ),
        (negative_judgment,),
        plan=negative_plan,
        documents=documents,
        k=1,
    )
    verified = evaluate_stage(
        RetrievalStageOutput(
            stage="final",
            hits=(
                RankedHit(
                    entity_id="a",
                    score=1.0,
                    evidence=positive_evidence,
                    verified_absent_criterion_ids=(forbidden.id,),
                ),
            ),
        ),
        (negative_judgment,),
        plan=negative_plan,
        documents=documents,
        k=1,
    )

    assert unchecked.grounded_hit_coverage == 0.0
    assert verified.grounded_hit_coverage == 1.0


def test_stage_contract_rejects_duplicate_entities() -> None:
    with pytest.raises(ValidationError, match="hit entity IDs must be unique"):
        RetrievalStageOutput(
            stage="retrieval",
            hits=(
                RankedHit(entity_id="a", score=1.0),
                RankedHit(entity_id="a", score=0.5),
            ),
        )
