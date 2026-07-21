"""Contract tests for rank-preserving candidate evidence search.

Every Graphiti, async runner, and Neo4j interaction is fake.  These tests must
never reach an embedding or model provider.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.candidate_graph import search as graph_search
from app.services.metered_async_anthropic_client import (
    GraphProviderAdmissionError,
    graph_metering_ctx,
)


@dataclass
class _FakeDriver:
    records: list[dict]
    calls: list[tuple[str, dict]] = field(default_factory=list)
    error: Exception | None = None

    async def execute_query(self, cypher: str, **params):
        self.calls.append((cypher, params))
        if self.error is not None:
            raise self.error
        return self.records, None, None


@dataclass
class _FakeGraphiti:
    searches: dict[str, list]
    driver: _FakeDriver
    calls: list[dict] = field(default_factory=list)
    metering_contexts: list[object] = field(default_factory=list)
    error_for: str | None = None

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        self.metering_contexts.append(graph_metering_ctx.get())
        if kwargs["query"] == self.error_for:
            raise RuntimeError("semantic backend exploded")
        return self.searches.get(kwargs["query"], [])


def _run_async(coro, **_kwargs):
    return asyncio.run(coro)


def _edge(uuid: str, fact: str, *, episodes: list[str] | None = None):
    """Graphiti-like EntityEdge with intentionally unhydrated nodes."""
    return SimpleNamespace(
        uuid=uuid,
        name="HAS_SKILL",
        fact=fact,
        episodes=episodes or [],
        source_node_uuid=f"source-{uuid}",
        target_node_uuid=f"target-{uuid}",
        source_node=None,
        target_node=None,
        attributes={},
        valid_at=None,
        invalid_at=None,
    )


def _record(
    edge_uuid: str,
    *,
    episode_uuid: str | None,
    episode_name: str | None,
    content: str | None,
    fact: str | None = None,
    source_props: dict | None = None,
    target_props: dict | None = None,
):
    return {
        "edge_uuid": edge_uuid,
        "edge_fact": fact,
        "source_name": "Candidate entity",
        "source_props": source_props or {},
        "target_name": "Evidence entity",
        "target_props": target_props or {},
        "episode_uuid": episode_uuid,
        "episode_name": episode_name,
        "episode_content": content,
        "episode_source_description": "candidate.cv_text",
    }


def _search(graphiti: _FakeGraphiti, **kwargs):
    with (
        patch.object(graph_search.graph_client, "is_configured", return_value=True),
        patch.object(graph_search.graph_client, "get_graphiti", return_value=graphiti),
        patch.object(graph_search.graph_client, "run_async", side_effect=_run_async),
    ):
        return graph_search.search_candidate_evidence(**kwargs)


def test_search_preserves_query_and_edge_rank_while_hydrating_episode_sources():
    first = "built distributed systems"
    second = "worked in payments"
    driver = _FakeDriver(
        records=[
            # Deliberately reverse the edge order returned by hydration.  Search
            # order, not Cypher record order, owns rank.
            _record(
                "edge-low",
                episode_uuid="episode-low",
                episode_name="interview-9-transcript",
                content="Subject candidate: Ada (taali_id=42)\nBuilt payment rails.",
            ),
            _record(
                "edge-high",
                episode_uuid="episode-high",
                episode_name="candidate-7-cv",
                content="Subject candidate: Grace (taali_id=7)\nBuilt distributed systems.",
            ),
            _record(
                "edge-second",
                episode_uuid="episode-second",
                episode_name="candidate-99-profile",
                content="Subject candidate: Lin (taali_id=99)\nPayments engineer.",
            ),
        ]
    )
    graphiti = _FakeGraphiti(
        searches={
            first: [
                _edge("edge-high", "Grace knows distributed systems"),
                _edge("edge-low", "Ada built payment rails"),
            ],
            second: [_edge("edge-second", "Lin worked in payments")],
        },
        driver=driver,
    )

    result = _search(
        graphiti,
        organization_id=12,
        role_id=34,
        queries=[first, second],
        limit_per_query=10,
    )

    assert result.status == "ok"
    assert result.capped is False
    assert result.exhaustive is True
    observed = [
        (hit.query_index, hit.rank, hit.edge_uuid, hit.candidate_id)
        for hit in result.hits
    ]
    assert observed == [
        (0, 0, "edge-high", 7),
        (0, 1, "edge-low", 42),
        (1, 0, "edge-second", 99),
    ]
    assert result.hits[0].fact == "Grace knows distributed systems"
    assert result.hits[0].episodes[0].content.endswith("Built distributed systems.")
    assert result.hits[0].episodes[0].source_description == "candidate.cv_text"

    assert [call["query"] for call in graphiti.calls] == [first, second]
    assert all(call["group_ids"] == ["org-12"] for call in graphiti.calls)
    assert all(call["num_results"] == 10 for call in graphiti.calls)
    assert all(
        ctx.organization_id == 12 and ctx.role_id == 34
        for ctx in graphiti.metering_contexts
    )


def test_hydration_is_one_parameterized_query_and_never_interpolates_search_text():
    hostile_query = "Robert'); MATCH (n) DETACH DELETE n //"
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-1",
                episode_name="candidate-5-profile",
                content="Subject candidate: Rob (taali_id=5)",
            )
        ]
    )
    graphiti = _FakeGraphiti(
        searches={hostile_query: [_edge("edge-1", "retrieval context")]},
        driver=driver,
    )

    result = _search(
        graphiti,
        organization_id=8,
        queries=[hostile_query],
        limit_per_query=5,
    )

    assert result.status == "ok"
    assert len(driver.calls) == 1
    cypher, params = driver.calls[0]
    assert hostile_query not in cypher
    assert "$edge_uuids" in cypher
    assert "$group_id" in cypher
    assert "episode.group_id IS NULL" in cypher
    assert params["edge_uuids"] == ["edge-1"]
    assert params["group_id"] == "org-8"


def test_candidate_id_uses_episode_provenance_before_conflicting_node_attributes():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-1",
                episode_name="candidate-17-cv",
                content="Subject candidate: Correct Person (taali_id=17)",
                source_props={"taali_id": 999},
            )
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"python": [_edge("edge-1", "taali_id=888 knows Python")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=2, queries=["python"])

    assert [hit.candidate_id for hit in result.hits] == [17]


def test_candidate_id_never_falls_back_to_untyped_node_attributes():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-1",
                episode_name="legacy-profile",
                content="Legacy source without a subject header",
                source_props={"taali_id": "73"},
            )
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"rust": [_edge("edge-1", "Candidate knows Rust")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=2, queries=["rust"])

    assert result.hits == ()


def test_shared_edge_cannot_use_untyped_node_id_to_choose_a_candidate():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-17",
                episode_name="candidate-17-cv",
                content="Subject candidate: Seventeen (taali_id=17)",
                source_props={"taali_id": 17},
            ),
            _record(
                "edge-1",
                episode_uuid="episode-18",
                episode_name="candidate-18-cv",
                content="Subject candidate: Eighteen (taali_id=18)",
                source_props={"taali_id": 17},
            ),
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"shared fact": [_edge("edge-1", "shared context")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=2, queries=["shared fact"])

    assert result.hits == ()


def test_unowned_episode_is_not_attached_to_the_single_candidate_owner():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-owned",
                episode_name="candidate-17-cv",
                content="Subject candidate: Seventeen (taali_id=17)\nBuilt Python services.",
            ),
            _record(
                "edge-1",
                episode_uuid="episode-role",
                episode_name="role-17-intent",
                content="Role 17 requires COBOL.",
                source_props={"taali_id": 17},
            ),
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"python": [_edge("edge-1", "generated context")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=2, queries=["python"])

    assert [hit.candidate_id for hit in result.hits] == [17]
    assert [episode.uuid for episode in result.hits[0].episodes] == [
        "episode-owned"
    ]


def test_ambiguous_multi_candidate_edge_without_direct_subject_is_dropped():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-17",
                episode_name="candidate-17-cv",
                content="Subject candidate: Seventeen (taali_id=17)",
            ),
            _record(
                "edge-1",
                episode_uuid="episode-18",
                episode_name="candidate-18-cv",
                content="Subject candidate: Eighteen (taali_id=18)",
            ),
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"ambiguous": [_edge("edge-1", "shared context")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=2, queries=["ambiguous"])

    assert result.hits == ()


def test_graphiti_fact_is_context_but_episode_content_is_retained_for_citation():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="episode-1",
                episode_name="candidate-4-cv",
                content="Subject candidate: Pat (taali_id=4)\nDirect CV quote.",
                fact="Graphiti paraphrase",
            )
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"direct evidence": [_edge("edge-1", "Search-time paraphrase")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=2, queries=["direct evidence"])

    hit = result.hits[0]
    assert hit.fact == "Search-time paraphrase"
    assert hit.episodes[0].content.endswith("Direct CV quote.")
    assert hit.episodes[0].content != hit.fact


def test_unconfigured_backend_is_unavailable_not_an_empty_success():
    with (
        patch.object(graph_search.graph_client, "is_configured", return_value=False),
        patch.object(graph_search.graph_client, "get_graphiti") as get_graphiti,
    ):
        result = graph_search.search_candidate_evidence(
            organization_id=1,
            queries=["anything"],
        )

    assert result.status == "unavailable"
    assert result.hits == ()
    assert result.capped is False
    assert result.exhaustive is False
    get_graphiti.assert_not_called()


def test_autonomous_search_marks_graph_provider_context_as_role_authorized():
    graphiti = _FakeGraphiti(searches={"payments": []}, driver=_FakeDriver(records=[]))

    result = _search(
        graphiti,
        organization_id=2,
        role_id=7,
        queries=["payments"],
        require_role_authority=True,
    )

    assert result.status == "ok"
    assert len(graphiti.metering_contexts) == 1
    context = graphiti.metering_contexts[0]
    assert context is not None
    assert context.role_id == 7
    assert context.require_hard_admission is True
    assert context.require_role_admission is True


def test_graph_authority_denial_is_not_converted_to_a_cacheable_result():
    graphiti = _FakeGraphiti(searches={}, driver=_FakeDriver(records=[]))

    async def denied(**_kwargs):
        raise GraphProviderAdmissionError("role agent is paused")

    graphiti.search = denied
    with pytest.raises(GraphProviderAdmissionError, match="paused"):
        _search(
            graphiti,
            organization_id=2,
            role_id=7,
            queries=["payments"],
            require_role_authority=True,
        )


def test_zero_results_is_an_ok_exhaustive_search_and_skips_hydration():
    driver = _FakeDriver(records=[])
    graphiti = _FakeGraphiti(searches={"nothing": []}, driver=driver)

    result = _search(graphiti, organization_id=1, queries=["nothing"])

    assert result.status == "ok"
    assert result.hits == ()
    assert result.capped is False
    assert result.exhaustive is True
    assert driver.calls == []


def test_limit_saturation_is_disclosed_as_capped_and_not_exhaustive():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="ep-1",
                episode_name="candidate-1-profile",
                content="Subject candidate: One (taali_id=1)",
            ),
            _record(
                "edge-2",
                episode_uuid="ep-2",
                episode_name="candidate-2-profile",
                content="Subject candidate: Two (taali_id=2)",
            ),
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"broad": [_edge("edge-1", "one"), _edge("edge-2", "two")]},
        driver=driver,
    )

    result = _search(
        graphiti,
        organization_id=1,
        queries=["broad"],
        limit_per_query=2,
    )

    assert result.capped is True
    assert result.exhaustive is False


def test_search_failure_is_error_not_zero_and_does_not_discard_safe_partial_hits():
    driver = _FakeDriver(
        records=[
            _record(
                "edge-1",
                episode_uuid="ep-1",
                episode_name="candidate-1-profile",
                content="Subject candidate: One (taali_id=1)",
            )
        ]
    )
    graphiti = _FakeGraphiti(
        searches={"works": [_edge("edge-1", "one")]},
        driver=driver,
        error_for="fails",
    )

    result = _search(
        graphiti,
        organization_id=1,
        queries=["works", "fails"],
    )

    assert result.status == "error"
    assert [hit.candidate_id for hit in result.hits] == [1]
    assert result.exhaustive is False
    assert result.errors == ("query 1 failed",)


def test_hydration_failure_is_error_and_does_not_promote_uncited_graph_facts():
    driver = _FakeDriver(records=[], error=RuntimeError("neo4j unavailable"))
    graphiti = _FakeGraphiti(
        searches={"python": [_edge("edge-1", "Candidate knows Python")]},
        driver=driver,
    )

    result = _search(graphiti, organization_id=1, queries=["python"])

    assert result.status == "error"
    assert result.hits == ()
    assert result.exhaustive is False
    assert result.errors == ("evidence hydration failed",)


def test_result_normalization_includes_edge_uuid_episode_ids_and_node_uuids():
    normalized = list(graph_search._iter_facts([_edge("edge-9", "context", episodes=["ep-9"]) ]))

    assert normalized == [
        {
            "uuid": "edge-9",
            "name": "HAS_SKILL",
            "fact": "context",
            "edge_label": "HAS_SKILL",
            "episodes": ["ep-9"],
            "valid_at": None,
            "invalid_at": None,
            "source_uuid": "source-edge-9",
            "source_name": None,
            "source_labels": [],
            "source_attributes": {},
            "target_uuid": "target-edge-9",
            "target_name": None,
            "target_labels": [],
            "target_attributes": {},
            "attributes": {},
        }
    ]
