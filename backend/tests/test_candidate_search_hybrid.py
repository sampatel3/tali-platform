"""Hybrid candidate retrieval tests.

All graph calls in this module are in-memory fakes.  These tests must never
reach Graphiti, an embedding provider, or a model provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.candidate_graph.search import (
    GraphCandidateEvidenceHit,
    GraphEpisodeEvidence,
    GraphEvidenceSearchResult,
)
from app.candidate_search.hybrid import (
    GraphEvidenceClause,
    GraphEvidenceRequirement,
    graph_backend_result,
    run_hybrid_retrieval,
)
from app.candidate_search.plan_adapter import parsed_filter_to_search_plan
from app.candidate_search.plan_evidence import graph_evidence_requirements
from app.candidate_search.retrieval import (
    BackendHit,
    BackendResult,
    BackendStatus,
    RetrievalMode,
)
from app.candidate_search.schemas import ParsedFilter


def _episode(
    uuid: str,
    content: str = "Original candidate source",
    source: str | None = "candidate.cv_text",
) -> GraphEpisodeEvidence:
    return GraphEpisodeEvidence(
        uuid=uuid,
        name=f"candidate-source-{uuid}",
        content=content,
        source_description=source,
    )


def _graph_hit(
    candidate_id: int,
    *,
    rank: int,
    episodes: tuple[GraphEpisodeEvidence, ...] = (),
    fact: str = "Graph-generated paraphrase",
) -> GraphCandidateEvidenceHit:
    return GraphCandidateEvidenceHit(
        candidate_id=candidate_id,
        query="ignored adapter echo",
        query_index=0,
        rank=rank,
        edge_uuid=f"edge-{candidate_id}-{rank}",
        fact=fact,
        source_name="Candidate",
        target_name="Skill",
        episodes=episodes,
    )


@dataclass
class _FakeGraphSearch:
    result: GraphEvidenceSearchResult
    calls: list[dict] = field(default_factory=list)

    def __call__(self, **kwargs) -> GraphEvidenceSearchResult:
        self.calls.append(kwargs)
        return self.result


def test_graph_semantic_hit_rescues_postgres_miss_with_original_source_provenance():
    raw_query = "people who solved unusual payment reconciliation failures"
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(1, rank=0, episodes=(_episode("note-17"),)),
            ),
            exhaustive=True,
        )
    )

    result = run_hybrid_retrieval(
        query=raw_query,
        organization_id=11,
        role_id=23,
        allowed_applications={1: 101, 2: 102},
        postgres=(BackendHit(candidate_id=2),),
        graph_search_fn=graph,
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
    )

    assert result.application_ids == (101, 102)
    rescued = result.hits[0]
    assert rescued.sources == ("graph",)
    assert rescued.graph_rank == 1
    assert rescued.postgres_rank is None
    assert [(item.source, item.reference) for item in rescued.evidence] == [
        ("candidate.cv_text", "episode:note-17")
    ]
    assert graph.calls == [
        {
            "organization_id": 11,
            "role_id": 23,
            "queries": (raw_query,),
            "limit_per_query": 50,
        }
    ]


def test_postgres_authority_drops_stale_or_cross_tenant_graph_candidate():
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(999, rank=0, episodes=(_episode("stale"),)),
                _graph_hit(1, rank=1, episodes=(_episode("scoped"),)),
            ),
            exhaustive=True,
        )
    )

    result = run_hybrid_retrieval(
        query="deep graph evidence",
        organization_id=5,
        role_id=None,
        allowed_applications={1: 41},
        postgres=(),
        graph_search_fn=graph,
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
    )

    assert result.application_ids == (41,)
    assert [hit.candidate_id for hit in result.hits] == [1]


@pytest.mark.parametrize("status", ["unavailable", "error"])
def test_graph_failure_falls_back_to_postgres_without_claiming_exactness(status: str):
    # The graph adapter may retain partial hits for diagnostics.  An incomplete
    # backend must not contribute those candidates to user-visible retrieval.
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(
            status=status,  # type: ignore[arg-type]
            hits=(_graph_hit(1, rank=0, episodes=(_episode("partial"),)),),
            capped=status == "error",
            errors=("one query failed",) if status == "error" else (),
        )
    )

    result = run_hybrid_retrieval(
        query="anything",
        organization_id=5,
        allowed_applications={1: 41, 2: 42},
        postgres=(BackendHit(candidate_id=2),),
        graph_search_fn=graph,
    )

    assert result.application_ids == (42,)
    assert result.graph is not None
    assert result.graph.status.value == status
    assert result.graph.hits == ()
    assert result.exhaustive is False
    assert result.is_exact_empty is False
    assert result.capped is (status == "error")


def test_graph_exception_is_a_typed_error_and_postgres_results_survive():
    calls: list[dict] = []

    def exploding_graph(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("graph connection broke")

    result = run_hybrid_retrieval(
        query="query",
        organization_id=9,
        allowed_applications={3: 303},
        postgres=(BackendHit(candidate_id=3),),
        graph_search_fn=exploding_graph,
    )

    assert len(calls) == 1
    assert result.application_ids == (303,)
    assert result.graph is not None
    assert result.graph.status is BackendStatus.ERROR
    assert result.graph.error_code == "graph_search_error"


def test_unknown_or_partial_graph_coverage_prevents_false_exact_empty():
    successful_empty = _FakeGraphSearch(
        GraphEvidenceSearchResult(status="ok", exhaustive=True)
    )

    unknown = run_hybrid_retrieval(
        query="no hits",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(),
        graph_search_fn=successful_empty,
    )
    partial = run_hybrid_retrieval(
        query="no hits",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(),
        graph_search_fn=successful_empty,
        graph_coverage=0.7,
    )
    complete = run_hybrid_retrieval(
        query="no hits",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(),
        graph_search_fn=successful_empty,
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
    )
    reported_complete_but_not_authoritative = run_hybrid_retrieval(
        query="no hits",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(),
        graph_search_fn=successful_empty,
        graph_coverage=1.0,
    )

    assert unknown.is_exact_empty is False
    assert partial.is_exact_empty is False
    assert complete.is_exact_empty is True
    assert reported_complete_but_not_authoritative.is_exact_empty is False
    assert unknown.graph is not None and unknown.graph.exhaustive is False
    assert complete.graph is not None and complete.graph.exhaustive is True


def test_capped_graph_search_stays_non_exhaustive_even_with_full_pool_coverage():
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(status="ok", capped=True, exhaustive=False)
    )

    result = run_hybrid_retrieval(
        query="broad query",
        organization_id=1,
        allowed_applications={},
        postgres=(),
        graph_search_fn=graph,
        graph_coverage=1.0,
        graph_coverage_authoritative=True,
    )

    assert result.capped is True
    assert result.exhaustive is False
    assert result.is_exact_empty is False


def test_graph_fact_and_empty_episode_are_never_exposed_as_citations():
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(
                    1,
                    rank=0,
                    fact="This is generated retrieval context, not a source",
                    episodes=(
                        _episode("blank", content="  "),
                        _episode(
                            "real", content="Candidate-authored evidence", source=None
                        ),
                    ),
                ),
            ),
        )
    )

    result = run_hybrid_retrieval(
        query="evidence",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(),
        graph_search_fn=graph,
    )

    assert [(item.source, item.reference) for item in result.hits[0].evidence] == [
        ("graph_episode", "episode:real")
    ]
    assert all("edge" not in item.reference for item in result.hits[0].evidence)


def test_graph_hit_without_nonempty_original_episode_does_not_qualify():
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(1, rank=0, fact="Generated fact only"),
                _graph_hit(
                    2,
                    rank=1,
                    episodes=(_episode("blank-only", content="  "),),
                ),
            ),
        )
    )

    result = run_hybrid_retrieval(
        query="evidence",
        organization_id=1,
        allowed_applications={1: 10, 2: 20},
        postgres=(),
        graph_search_fn=graph,
    )

    assert result.graph is not None
    assert result.graph.hits == ()
    assert result.hits == ()


def test_duplicate_graph_hits_merge_episode_evidence_at_first_candidate_rank():
    graph = _FakeGraphSearch(
        GraphEvidenceSearchResult(
            status="ok",
            hits=(
                _graph_hit(2, rank=0, episodes=(_episode("two-a"),)),
                _graph_hit(2, rank=1, episodes=(_episode("two-b"),)),
                _graph_hit(1, rank=2, episodes=(_episode("one"),)),
            ),
        )
    )

    result = run_hybrid_retrieval(
        query="evidence",
        organization_id=1,
        allowed_applications={1: 10, 2: 20},
        postgres=(),
        graph_search_fn=graph,
    )

    assert [hit.candidate_id for hit in result.hits] == [2, 1]
    assert result.hits[0].graph_rank == 1
    assert result.hits[1].graph_rank == 3
    assert [item.reference for item in result.hits[0].evidence] == [
        "episode:two-a",
        "episode:two-b",
    ]


def test_ranked_postgres_rows_and_backend_results_are_both_accepted():
    class Row:
        def __init__(self, candidate_id: int):
            self.candidate_id = candidate_id

    no_graph = _FakeGraphSearch(GraphEvidenceSearchResult(status="ok"))
    from_rows = run_hybrid_retrieval(
        query="query",
        organization_id=1,
        allowed_applications={1: 10, 2: 20},
        postgres=(Row(2), {"candidate_id": 1}),
        graph_search_fn=no_graph,
    )
    from_result = run_hybrid_retrieval(
        query="query",
        organization_id=1,
        allowed_applications={1: 10, 2: 20},
        postgres=BackendResult(
            backend="postgres",
            status=BackendStatus.OK,
            hits=(BackendHit(candidate_id=1), BackendHit(candidate_id=2)),
        ),
        graph_search_fn=no_graph,
    )

    assert [hit.postgres_rank for hit in from_rows.hits] == [1, 2]
    assert from_rows.application_ids == (20, 10)
    assert from_result.application_ids == (10, 20)


def test_postgres_only_mode_never_calls_graph():
    graph = _FakeGraphSearch(GraphEvidenceSearchResult(status="ok"))

    result = run_hybrid_retrieval(
        query="query",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(BackendHit(candidate_id=1),),
        graph_search_fn=graph,
        mode=RetrievalMode.POSTGRES_ONLY,
    )

    assert result.application_ids == (10,)
    assert result.graph is None
    assert graph.calls == []


def test_graph_coverage_is_validated():
    graph = _FakeGraphSearch(GraphEvidenceSearchResult(status="ok"))

    with pytest.raises(ValueError, match="graph_coverage"):
        run_hybrid_retrieval(
            query="query",
            organization_id=1,
            allowed_applications={},
            postgres=(),
            graph_search_fn=graph,
            graph_coverage=1.01,
        )


def test_graph_requirement_drops_semantic_hit_without_source_support():
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(_episode("unrelated", content="Built Kubernetes systems"),),
            ),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=(
            GraphEvidenceRequirement(
                operator="all",
                clauses=(GraphEvidenceClause("skill-agentforce", "Agentforce"),),
            ),
        ),
    )

    assert result.hits == ()


def test_short_grounding_term_does_not_match_inside_a_different_word():
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(_episode("google", content="Built systems on Google Cloud"),),
            ),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=(
            GraphEvidenceRequirement(
                operator="all",
                clauses=(GraphEvidenceClause("skill-go", "Go", "demonstrated"),),
            ),
        ),
    )

    assert result.hits == ()


def test_graph_requirement_matches_meaningful_terms_not_query_scaffolding():
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(
                    _episode(
                        "agentforce",
                        content="Built and deployed Agentforce actions for service workflows.",
                    ),
                ),
            ),
            _graph_hit(
                2,
                rank=1,
                episodes=(_episode("salesforce-only", content="Salesforce administrator"),),
            ),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=(
            GraphEvidenceRequirement(
                operator="all",
                clauses=(
                    GraphEvidenceClause(
                        "agentforce-experience",
                        "hands-on Agentforce experience",
                    ),
                ),
            ),
        ),
    )

    assert [hit.candidate_id for hit in result.hits] == [1]
    assert result.hits[0].evidence[0].clause_ids == ("agentforce-experience",)


@pytest.mark.parametrize(
    "content",
    [
        "Interested in Agentforce",
        "Familiar with Agentforce",
        "Interested in Agentforce experience opportunities",
    ],
)
def test_experience_claim_rejects_non_applied_mentions(content: str):
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(_episode("agentforce-mention", content=content),),
            ),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=(
            GraphEvidenceRequirement(
                operator="all",
                clauses=(
                    GraphEvidenceClause(
                        "agentforce-experience",
                        "hands-on Agentforce experience",
                        "matches_claim",
                    ),
                ),
            ),
        ),
    )

    assert result.hits == ()


def test_planned_disjunction_promotes_only_a_source_backed_experience_branch():
    parsed = ParsedFilter(
        skills_any=["Python", "Kubernetes"],
        soft_criteria=["Python or Kubernetes experience"],
        free_text="Python or Kubernetes experience",
    )
    plan = parsed_filter_to_search_plan(parsed)
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(
                    _episode(
                        "kubernetes-applied",
                        content="Built and operated Kubernetes production platforms.",
                    ),
                ),
            ),
            _graph_hit(
                2,
                rank=1,
                episodes=(
                    _episode("python-interest", content="Interested in Python"),
                ),
            ),
            _graph_hit(
                3,
                rank=2,
                episodes=(_episode("unrelated", content="Built Go services"),),
            ),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=graph_evidence_requirements(parsed, plan),
    )

    assert [hit.candidate_id for hit in result.hits] == [1]


@pytest.mark.parametrize(
    ("predicate", "content"),
    [
        ("worked_at", "Studied at Google University"),
        ("worked_at", "Studied at Google University. Worked at Meta"),
        ("worked_at", "Worked at Googleplex"),
        ("colleague_of", "Candidate 42 is mentioned in this CV"),
        ("n_hop_from", "Candidate 42 is mentioned in this CV"),
    ],
)
def test_relationship_clause_requires_relationship_evidence(predicate: str, content: str):
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(_episode("source", content=content),),
            ),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=(
            GraphEvidenceRequirement(
                operator="all",
                clauses=(
                    GraphEvidenceClause(
                        "relationship",
                        "Google" if predicate == "worked_at" else "42",
                        predicate,
                    ),
                ),
            ),
        ),
    )

    assert result.hits == ()


def test_graph_requirement_enforces_all_and_links_evidence_to_clause_ids():
    raw = GraphEvidenceSearchResult(
        status="ok",
        hits=(
            _graph_hit(
                1,
                rank=0,
                episodes=(_episode("google", content="Worked at Google"),),
            ),
            _graph_hit(
                1,
                rank=1,
                episodes=(_episode("mit", content="Studied at MIT"),),
            ),
            _graph_hit(
                2,
                rank=2,
                episodes=(_episode("google-only", content="Worked at Google"),),
            ),
        ),
    )
    requirement = GraphEvidenceRequirement(
        operator="all",
        clauses=(
            GraphEvidenceClause("worked-google", "Google"),
            GraphEvidenceClause("studied-mit", "MIT"),
        ),
    )

    result = graph_backend_result(
        raw,
        graph_coverage=None,
        requirements=(requirement,),
    )

    assert [hit.candidate_id for hit in result.hits] == [1]
    by_reference = {
        evidence.reference: evidence.clause_ids
        for evidence in result.hits[0].evidence
    }
    assert by_reference == {
        "episode:google": ("worked-google",),
        "episode:mit": ("studied-mit",),
    }


def test_prebuilt_graph_result_skips_graph_execution():
    graph = _FakeGraphSearch(GraphEvidenceSearchResult(status="ok"))
    prebuilt = BackendResult(
        backend="graph",
        status=BackendStatus.OK,
        hits=(BackendHit(candidate_id=1),),
        exhaustive=False,
    )

    result = run_hybrid_retrieval(
        query="query",
        organization_id=1,
        allowed_applications={1: 10},
        postgres=(),
        graph_result=prebuilt,
        graph_search_fn=graph,
    )

    assert result.application_ids == (10,)
    assert graph.calls == []
