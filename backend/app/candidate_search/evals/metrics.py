"""Pure ranking, empty-result, and criterion-grounding metrics."""

from __future__ import annotations

import math

from ..search_plan import Modality, SearchPlan
from .contracts import (
    Citation,
    Document,
    OracleJudgment,
    RankedHit,
    RetrievalStageOutput,
    ResultStatus,
    StageMetrics,
    TruthValue,
)
from .oracle import evaluate_expression_truth


def _citation_span_is_valid(
    citation: Citation,
    documents: dict[str, Document],
    *,
    entity_id: str,
) -> bool:
    document = documents.get(citation.document_id)
    return bool(
        document is not None
        and document.entity_id == entity_id
        and citation.end <= len(document.content)
        and document.content[citation.start : citation.end] == citation.quote
    )


def _citation_key(citation: Citation) -> tuple[str, int, int, str]:
    return (
        citation.document_id,
        citation.start,
        citation.end,
        citation.quote,
    )


def _dcg(relevances: list[float]) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
    )


def _grounding_for_hit(
    hit: RankedHit,
    judgment: OracleJudgment | None,
    *,
    plan: SearchPlan,
    documents: dict[str, Document],
) -> tuple[int, int, int, bool]:
    """Return citation counts and whether evidence proves plan eligibility."""

    criterion_truth = {
        row.criterion_id: row
        for row in (judgment.criterion_judgments if judgment else ())
    }
    supported_ids: set[str] = set()
    citation_count = 0
    span_valid_count = 0
    support_valid_count = 0

    for evidence in hit.evidence:
        criterion = plan.criteria_by_id.get(evidence.criterion_id)
        oracle = criterion_truth.get(evidence.criterion_id)
        accepted = {
            _citation_key(citation)
            for citation in (oracle.supporting_citations if oracle else ())
        }
        supported_for_criterion: list[Citation] = []
        for citation in evidence.citations:
            citation_count += 1
            span_valid = _citation_span_is_valid(
                citation,
                documents,
                entity_id=hit.entity_id,
            )
            span_valid_count += int(span_valid)
            support_valid = bool(
                span_valid
                and oracle is not None
                and oracle.truth is TruthValue.TRUE
                and _citation_key(citation) in accepted
            )
            support_valid_count += int(support_valid)
            if support_valid:
                supported_for_criterion.append(citation)

        if criterion is None or oracle is None:
            continue
        source_ids = {
            documents[citation.document_id].independent_source_id
            for citation in supported_for_criterion
        }
        if (
            len(source_ids) >= criterion.evidence.minimum_sources
            and (
                bool(supported_for_criterion)
                or not criterion.evidence.require_citation_span
            )
        ):
            supported_ids.add(criterion.id)

    if judgment is None or not judgment.eligible:
        return citation_count, span_valid_count, support_valid_count, False

    evidence_truth: dict[str, TruthValue] = {}
    oracle_by_id = {
        row.criterion_id: row.truth for row in judgment.criterion_judgments
    }
    verified_absent = set(hit.verified_absent_criterion_ids)
    for criterion in plan.criteria:
        if criterion.modality is Modality.MUST_NOT:
            # Negative eligibility is a backend claim, not evidence the metric
            # may borrow from hidden oracle truth.  Validate an explicit
            # verified-absent claim against the oracle; otherwise keep the
            # criterion indeterminate so NOT(UNKNOWN) cannot score grounded.
            evidence_truth[criterion.id] = (
                TruthValue.FALSE
                if criterion.id in verified_absent
                and oracle_by_id[criterion.id] is TruthValue.FALSE
                else TruthValue.UNKNOWN
            )
        elif (
            not criterion.evidence.require_citation_span
            and criterion.evidence.minimum_sources == 0
            and oracle_by_id[criterion.id] is TruthValue.TRUE
        ):
            evidence_truth[criterion.id] = TruthValue.TRUE
        else:
            evidence_truth[criterion.id] = (
                TruthValue.TRUE
                if criterion.id in supported_ids
                else TruthValue.FALSE
            )
    grounded = evaluate_expression_truth(plan.root, evidence_truth) is TruthValue.TRUE
    return citation_count, span_valid_count, support_valid_count, grounded


def evaluate_stage(
    stage: RetrievalStageOutput,
    judgments: tuple[OracleJudgment, ...],
    *,
    plan: SearchPlan,
    documents: tuple[Document, ...],
    k: int,
) -> StageMetrics:
    if k < 1:
        raise ValueError("k must be at least one")
    indeterminate = [
        row.entity_id
        for row in judgments
        if row.eligibility is TruthValue.UNKNOWN
    ]
    if indeterminate:
        raise ValueError(
            "oracle contains indeterminate entities; declare predicate completeness: "
            f"{indeterminate}"
        )
    expected_criteria = set(plan.criteria_by_id)
    incomplete_judgments = [
        row.entity_id
        for row in judgments
        if {item.criterion_id for item in row.criterion_judgments}
        != expected_criteria
    ]
    if incomplete_judgments:
        raise ValueError(
            "oracle criterion judgments do not match the plan for entities: "
            f"{incomplete_judgments}"
        )

    by_id = {judgment.entity_id: judgment for judgment in judgments}
    relevant = {row.entity_id for row in judgments if row.eligible}
    top = stage.hits[:k]
    relevant_in_top = sum(hit.entity_id in relevant for hit in top)
    false_positives = sum(hit.entity_id not in relevant for hit in top)
    precision = relevant_in_top / k
    if relevant:
        recall = relevant_in_top / len(relevant)
    else:
        recall = 1.0 if stage.is_exact_empty else 0.0

    first_relevant_rank = next(
        (
            rank
            for rank, hit in enumerate(top, start=1)
            if hit.entity_id in relevant
        ),
        None,
    )
    mrr = 1.0 / first_relevant_rank if first_relevant_rank else 0.0

    actual_relevances = [
        by_id.get(hit.entity_id).relevance if hit.entity_id in by_id else 0.0
        for hit in top
    ]
    ideal_relevances = sorted(
        (row.relevance for row in judgments if row.eligible), reverse=True
    )[:k]
    ideal_dcg = _dcg(ideal_relevances)
    if ideal_dcg:
        ndcg = _dcg(actual_relevances) / ideal_dcg
    else:
        ndcg = 1.0 if stage.is_exact_empty else 0.0

    if stage.status is not ResultStatus.OK:
        exact_empty_accuracy = 0.0
    elif relevant:
        exact_empty_accuracy = float(bool(stage.hits))
    else:
        exact_empty_accuracy = float(stage.is_exact_empty)

    documents_by_id = {document.id: document for document in documents}
    citation_count = 0
    valid_citation_count = 0
    supported_citation_count = 0
    grounded_hit_count = 0
    for hit in top:
        citations, valid, supported, grounded = _grounding_for_hit(
            hit,
            by_id.get(hit.entity_id),
            plan=plan,
            documents=documents_by_id,
        )
        citation_count += citations
        valid_citation_count += valid
        supported_citation_count += supported
        grounded_hit_count += int(grounded)

    citation_span_validity = (
        valid_citation_count / citation_count if citation_count else 0.0
    )
    citation_support_validity = (
        supported_citation_count / citation_count if citation_count else 0.0
    )
    grounded_hit_coverage = grounded_hit_count / len(top) if top else 0.0

    return StageMetrics(
        k=k,
        retrieved_count=len(stage.hits),
        relevant_count=len(relevant),
        precision_at_k=precision,
        recall_at_k=recall,
        mrr=mrr,
        ndcg_at_k=ndcg,
        false_positive_count_at_k=false_positives,
        exact_empty_accuracy=exact_empty_accuracy,
        citation_span_validity=citation_span_validity,
        citation_support_validity=citation_support_validity,
        citation_count=citation_count,
        valid_citation_count=valid_citation_count,
        supported_citation_count=supported_citation_count,
        grounded_hit_coverage=grounded_hit_coverage,
        grounded_hit_count=grounded_hit_count,
    )


__all__ = ["evaluate_stage"]
