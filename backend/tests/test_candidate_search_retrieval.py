"""Behavioral tests for deterministic candidate retrieval fusion."""

import pytest

from app.candidate_search.retrieval import (
    BackendHit,
    BackendResult,
    BackendStatus,
    EvidenceHit,
    RetrievalMode,
    fuse_retrieval_results,
)


def test_hybrid_fusion_preserves_graph_order_and_maps_scoped_applications():
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(
            BackendHit(
                candidate_id=7,
                evidence=(EvidenceHit(source="graph_fact", reference="fact-7"),),
            ),
            BackendHit(
                candidate_id=2,
                evidence=(EvidenceHit(source="graph_fact", reference="fact-2"),),
            ),
        ),
    )
    postgres = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=7), BackendHit(candidate_id=2)),
    )

    result = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=graph,
        postgres=postgres,
        allowed_applications={7: 107, 2: 102},
    )

    assert [(hit.candidate_id, hit.application_id) for hit in result.hits] == [
        (7, 107),
        (2, 102),
    ]
    assert result.application_ids == (107, 102)
    assert result.hits[0].sources == ("graph", "postgres")
    assert result.hits[0].evidence == (
        EvidenceHit(source="graph_fact", reference="fact-7"),
    )


def test_fusion_drops_unscoped_graph_hits_and_rescues_postgres_only_candidates():
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(
            BackendHit(candidate_id=99),  # stale or belongs to another tenant
            BackendHit(candidate_id=1),
        ),
    )
    postgres = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=2), BackendHit(candidate_id=1)),
    )

    result = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=graph,
        postgres=postgres,
        allowed_applications={1: 101, 2: 202},
    )

    assert [hit.candidate_id for hit in result.hits] == [1, 2]
    assert result.application_ids == (101, 202)
    assert (result.hits[0].graph_rank, result.hits[0].postgres_rank) == (2, 2)
    assert (result.hits[1].graph_rank, result.hits[1].postgres_rank) == (None, 1)


def test_weighted_rrf_can_promote_overlap_without_losing_backend_ranks():
    result = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=BackendResult(
            backend="graph",
            status=BackendStatus.OK,
            hits=(BackendHit(candidate_id=1), BackendHit(candidate_id=2)),
        ),
        postgres=BackendResult(
            backend="postgres",
            status=BackendStatus.OK,
            hits=(BackendHit(candidate_id=2), BackendHit(candidate_id=3)),
        ),
        allowed_applications={1: 101, 2: 102, 3: 103},
    )

    assert result.application_ids == (102, 101, 103)
    assert (result.hits[0].graph_rank, result.hits[0].postgres_rank) == (2, 1)
    assert result.hits[0].score == pytest.approx(0.0486515071)


@pytest.mark.parametrize("status", [BackendStatus.UNAVAILABLE, BackendStatus.ERROR])
def test_hybrid_graph_failure_falls_back_without_claiming_an_exact_result(status):
    graph = BackendResult(
        backend="graph",
        status=status,
        exhaustive=False,
        error_code="graph_down",
    )
    postgres = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=3), BackendHit(candidate_id=4)),
    )

    result = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=graph,
        postgres=postgres,
        allowed_applications={3: 303, 4: 404},
    )

    assert result.application_ids == (303, 404)
    assert result.graph is graph
    assert result.graph.status is status
    assert result.exhaustive is False
    assert result.is_exact_empty is False


def test_exact_empty_is_distinct_from_unavailable_empty_and_capped_empty():
    postgres = BackendResult(backend="postgres", status=BackendStatus.OK)
    exact = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=BackendResult(backend="graph", status=BackendStatus.OK),
        postgres=postgres,
        allowed_applications={1: 101},
    )
    unavailable = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=BackendResult(
            backend="graph",
            status=BackendStatus.UNAVAILABLE,
            exhaustive=False,
        ),
        postgres=postgres,
        allowed_applications={1: 101},
    )
    capped = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=BackendResult(
            backend="graph",
            status=BackendStatus.OK,
            capped=True,
            exhaustive=False,
        ),
        postgres=postgres,
        allowed_applications={1: 101},
    )

    assert exact.hits == ()
    assert exact.is_exact_empty is True
    assert exact.exhaustive is True
    assert unavailable.hits == capped.hits == ()
    assert unavailable.is_exact_empty is False
    assert capped.is_exact_empty is False
    assert capped.capped is True


def test_equal_rrf_scores_break_ties_by_graph_rank_deterministically():
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=20), BackendHit(candidate_id=10)),
    )
    postgres = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=10), BackendHit(candidate_id=20)),
    )

    first = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=graph,
        postgres=postgres,
        allowed_applications={10: 110, 20: 120},
        graph_weight=1.0,
        postgres_weight=1.0,
    )
    second = fuse_retrieval_results(
        mode=RetrievalMode.HYBRID,
        graph=graph,
        postgres=postgres,
        allowed_applications={20: 120, 10: 110},
        graph_weight=1.0,
        postgres_weight=1.0,
    )

    assert first.application_ids == second.application_ids == (120, 110)
    assert first.hits[0].score == pytest.approx(first.hits[1].score)


def test_retrieval_modes_use_only_the_selected_backend():
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=1),),
    )
    postgres = BackendResult(
        backend="postgres",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=2),),
    )

    graph_only = fuse_retrieval_results(
        mode=RetrievalMode.GRAPH_ONLY,
        graph=graph,
        postgres=postgres,
        allowed_applications={1: 101, 2: 102},
    )
    postgres_only = fuse_retrieval_results(
        mode=RetrievalMode.POSTGRES_ONLY,
        graph=graph,
        postgres=postgres,
        allowed_applications={1: 101, 2: 102},
    )

    assert graph_only.application_ids == (101,)
    assert postgres_only.application_ids == (102,)
    assert graph_only.hits[0].sources == ("graph",)
    assert postgres_only.hits[0].sources == ("postgres",)


def test_backend_selection_uses_mode_not_result_value_equality():
    same_value = BackendResult(
        backend="retriever",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=1),),
    )

    result = fuse_retrieval_results(
        mode=RetrievalMode.POSTGRES_ONLY,
        graph=same_value,
        postgres=same_value,
        allowed_applications={1: 101},
    )

    assert result.hits[0].sources == ("retriever",)
    assert result.hits[0].graph_rank is None
    assert result.hits[0].postgres_rank == 1
    assert result.hits[0].score == pytest.approx(1 / 61)


def test_duplicate_backend_candidates_keep_their_first_authoritative_rank():
    graph = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(
            BackendHit(candidate_id=1),
            BackendHit(candidate_id=1),
            BackendHit(candidate_id=2),
        ),
    )

    result = fuse_retrieval_results(
        mode=RetrievalMode.GRAPH_ONLY,
        graph=graph,
        allowed_applications={1: 101, 2: 102},
    )

    assert [(hit.candidate_id, hit.graph_rank) for hit in result.hits] == [
        (1, 1),
        (2, 3),
    ]


def test_contracts_reject_ambiguous_coverage_and_invalid_authority_mapping():
    with pytest.raises(ValueError, match="candidate_id"):
        BackendHit(candidate_id=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot be exhaustive"):
        BackendResult(backend="graph", status=BackendStatus.UNAVAILABLE)
    with pytest.raises(ValueError, match="capped backend"):
        BackendResult(backend="graph", status=BackendStatus.OK, capped=True)
    with pytest.raises(ValueError, match="application IDs"):
        fuse_retrieval_results(
            mode=RetrievalMode.GRAPH_ONLY,
            graph=BackendResult(backend="graph", status=BackendStatus.OK),
            allowed_applications={1: 0},
        )
    with pytest.raises(ValueError, match="RetrievalMode"):
        fuse_retrieval_results(
            mode="hybrid",  # type: ignore[arg-type]
            graph=BackendResult(backend="graph", status=BackendStatus.OK),
            postgres=BackendResult(backend="postgres", status=BackendStatus.OK),
            allowed_applications={},
        )
