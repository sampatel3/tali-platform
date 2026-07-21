"""Cross-surface contract for the production candidate-search entry points.

The retrieval engine's evidence semantics are exercised in
``test_runtime_ground_truth.py``.  This module keeps the second layer honest:
every unified product surface must preserve that oracle-derived result and the
selected role scope.  Provider and graph calls are replaced with local values.
"""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import MagicMock, patch

from app.agent_chat import tools as role_agent_tools
from app.agent_runtime import tool_registry as autonomous_tools
from app.candidate_search.evals.contracts import (
    Citation,
    ConstructedWorld,
    Document,
    Fact,
    QueryIntent,
    TruthValue,
    WorldEntity,
)
from app.candidate_search.evals.oracle import derive_judgments
from app.candidate_search.schemas import (
    GraphEdge,
    GraphNode,
    GraphPayload,
    ParsedFilter,
    SearchOutput,
    SearchRetrievalSummary,
    SearchRetrievalTrace,
)
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
)
from app.mcp import handlers
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import Role
from app.models.user import User
from app.taali_chat import tool_registry as taali_tools
from app.taali_chat.service import _arguments_with_role_scope
from tests.conftest import auth_headers


QUERY = "People with hands-on Agentforce experience"


def _document(document_id: str, entity_id: str, content: str) -> Document:
    return Document(
        id=document_id,
        entity_id=entity_id,
        source_type="cv",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def _constructed_truth() -> tuple[ConstructedWorld, QueryIntent]:
    documents = (
        _document(
            "cv-role-applied",
            "role-applied",
            "Built and deployed Agentforce actions for customer service.",
        ),
        _document(
            "cv-role-mention",
            "role-mention",
            "Interested in Agentforce and learning the platform.",
        ),
        _document(
            "cv-other-role-applied",
            "other-role-applied",
            "Implemented Agentforce workflows for a support operation.",
        ),
    )
    facts = []
    for document in (documents[0], documents[2]):
        start = document.content.index("Agentforce")
        facts.append(
            Fact(
                id=f"fact-{document.entity_id}",
                subject_id=document.entity_id,
                predicate="demonstrated",
                object=SearchObject(kind="capability", value="Agentforce"),
                confidence=1.0,
                direct_subject=True,
                provenance=(
                    Citation(
                        document_id=document.id,
                        start=start,
                        end=start + len("Agentforce"),
                        quote="Agentforce",
                    ),
                ),
            )
        )
    criterion = Criterion(
        id="applied-agentforce",
        predicate=Predicate(name="demonstrated"),
        subject=SearchObject(kind="person"),
        object=SearchObject(kind="capability", value="Agentforce"),
        comparison=Comparison(operator=ComparisonOperator.EXISTS),
        modality=Modality.MUST,
        evidence=EvidencePolicy(
            require_direct_subject=True,
            require_citation_span=True,
            minimum_sources=1,
        ),
    )
    return (
        ConstructedWorld(
            id="cross-surface-agentforce-world",
            entities=tuple(
                WorldEntity(id=document.entity_id, kind="person")
                for document in documents
            ),
            documents=documents,
            facts=tuple(facts),
            closed_world_predicates=("demonstrated",),
        ),
        QueryIntent(
            id="applied-agentforce",
            plan=SearchPlan(
                query=QUERY,
                criteria=(criterion,),
                root=Expression.leaf(criterion.id),
            ),
        ),
    )


def _seed_candidate(db, *, role: Role, entity_id: str) -> CandidateApplication:
    candidate = Candidate(
        organization_id=role.organization_id,
        email=f"{entity_id}@ground-truth.test",
        full_name=entity_id,
        position="AI Engineer",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=role.organization_id,
        candidate_id=candidate.id,
        role_id=role.id,
        source="manual",
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        taali_score_cache_100=80.0,
    )
    db.add(application)
    db.flush()
    return application


def _oracle_case(db, client):
    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    role = Role(organization_id=user.organization_id, name="Target", source="manual")
    other_role = Role(
        organization_id=user.organization_id,
        name="Other",
        source="manual",
    )
    db.add_all([role, other_role])
    db.flush()
    role_by_entity = {
        "role-applied": role,
        "role-mention": role,
        "other-role-applied": other_role,
    }
    app_by_entity = {
        entity_id: _seed_candidate(db, role=entity_role, entity_id=entity_id)
        for entity_id, entity_role in role_by_entity.items()
    }
    db.commit()

    world, intent = _constructed_truth()
    true_entities = {
        judgment.entity_id
        for judgment in derive_judgments(world, intent)
        if judgment.eligibility is TruthValue.TRUE
    }
    expected_role_application_ids = sorted(
        int(app_by_entity[entity_id].id)
        for entity_id in true_entities
        if int(app_by_entity[entity_id].role_id) == int(role.id)
    )
    return {
        "headers": headers,
        "user": user,
        "role": role,
        "world": world,
        "intent": intent,
        "app_by_entity": app_by_entity,
        "true_entities": true_entities,
        "expected_ids": expected_role_application_ids,
    }


def _search_output(case, *, include_graph: bool = False) -> SearchOutput:
    expected_ids = case["expected_ids"]
    app_by_id = {
        int(application.id): application
        for application in case["app_by_entity"].values()
    }
    return SearchOutput(
        application_ids=expected_ids,
        parsed_filter=ParsedFilter(
            soft_criteria=["hands-on Agentforce experience"],
            free_text=QUERY,
        ),
        database_matches=0,
        retrieval_matches=len(expected_ids),
        retrieval=SearchRetrievalSummary(
            mode="hybrid",
            graph_status="ok",
            exhaustive=False,
            hits=[
                SearchRetrievalTrace(
                    application_id=application_id,
                    candidate_id=int(app_by_id[application_id].candidate_id),
                    score=1.0,
                    sources=["graph"],
                    graph_rank=index,
                    evidence=[
                        {
                            "source": "cv",
                            "reference": "oracle-constructed-document",
                            "clause_ids": ["applied-agentforce"],
                        }
                    ],
                )
                for index, application_id in enumerate(expected_ids, start=1)
            ],
        ),
        subgraph=(
            GraphPayload(
                nodes=[
                    GraphNode(id="person:oracle", label="Person", name="Oracle match"),
                    GraphNode(id="skill:agentforce", label="Skill", name="Agentforce"),
                ],
                edges=[
                    GraphEdge(
                        source="person:oracle",
                        target="skill:agentforce",
                        label="HAS_SKILL",
                        extra={"fact": "Built and deployed Agentforce actions."},
                    )
                ],
            )
            if include_graph
            else None
        ),
        exhaustive=False,
        is_exact_empty=False,
    )


def _application_ids(payload: dict, *, http: bool = False) -> set[int]:
    rows = payload["items"] if http else payload["applications"]
    key = "id" if http else "application_id"
    return {int(row[key]) for row in rows}


def test_oracle_truth_and_role_scope_are_identical_across_unified_search_surfaces(
    db, client, monkeypatch
) -> None:
    """Applications, shared MCP, both chats, and runtime share one contract."""

    case = _oracle_case(db, client)
    expected = set(case["expected_ids"])
    calls: list[dict] = []

    def local_runner(**kwargs):
        calls.append(kwargs)
        return _search_output(case, include_graph=bool(kwargs["include_subgraph"]))

    monkeypatch.setattr("app.candidate_search.runner.run_search", local_runner)
    http_response = client.get(
        "/api/v1/applications",
        params={"nl_query": QUERY, "role_id": int(case["role"].id)},
        headers=case["headers"],
    )
    assert http_response.status_code == 200, http_response.text
    http_payload = http_response.json()

    shared_payload = handlers.nl_search_candidates(
        db,
        case["user"],
        query=QUERY,
        role_id=int(case["role"].id),
    )
    taali_args = _arguments_with_role_scope(
        "nl_search_candidates",
        {"query": QUERY},
        conversation_role_id=int(case["role"].id),
    )
    taali_payload = taali_tools.dispatch_tool(
        "nl_search_candidates",
        taali_args,
        db=db,
        user=case["user"],
    )
    role_agent_payload = role_agent_tools.dispatch_tool(
        "search_candidates",
        {"query": QUERY},
        db=db,
        role=case["role"],
        user=case["user"],
    )
    with patch.object(autonomous_tools, "_governance_block_reason", return_value=None):
        autonomous_payload = autonomous_tools.dispatch(
            "nl_search_candidates",
            {"query": QUERY, "role_id": 999999},
            db=db,
            agent_run=MagicMock(decisions_emitted=0),
            role=case["role"],
        )

    assert _application_ids(http_payload, http=True) == expected
    for payload in (
        shared_payload,
        taali_payload,
        role_agent_payload,
        autonomous_payload,
    ):
        assert _application_ids(payload) == expected
        assert payload["retrieval_matches"] == len(expected)
        assert payload["database_matches"] == 0
        assert payload["is_exact_empty"] is False
        assert payload["retrieval"]["hits"][0]["evidence"][0]["clause_ids"] == [
            "applied-agentforce"
        ]

    assert http_payload["nl_coverage"]["retrieval_matches"] == len(expected)
    assert len(calls) == 5
    assert {int(call["role_id"]) for call in calls} == {int(case["role"].id)}
    assert all("candidate_applications.role_id" in str(call["base_query"]) for call in calls)


def test_graph_compatibility_surfaces_delegate_to_role_scoped_grounded_search(
    db, client, monkeypatch
) -> None:
    """The graph-shaped view is a wrapper, not an independent search path."""

    case = _oracle_case(db, client)
    expected = set(case["expected_ids"])
    calls: list[dict] = []

    def local_runner(**kwargs):
        calls.append(kwargs)
        return _search_output(case, include_graph=bool(kwargs["include_subgraph"]))

    monkeypatch.setattr("app.candidate_search.runner.run_search", local_runner)
    taali_args = _arguments_with_role_scope(
        "graph_search_candidates",
        {"query": QUERY},
        conversation_role_id=int(case["role"].id),
    )
    with patch.object(
        handlers,
        "nl_search_candidates",
        wraps=handlers.nl_search_candidates,
    ) as shared_search:
        shared = handlers.graph_search_candidates(
            db,
            case["user"],
            query=QUERY,
            role_id=int(case["role"].id),
        )
        taali = taali_tools.dispatch_tool(
            "graph_search_candidates",
            taali_args,
            db=db,
            user=case["user"],
        )
        with patch.object(
            autonomous_tools,
            "_governance_block_reason",
            return_value=None,
        ):
            autonomous = autonomous_tools.dispatch(
                "graph_search_candidates",
                {"query": QUERY},
                db=db,
                agent_run=MagicMock(decisions_emitted=0),
                role=case["role"],
            )

    for payload in (shared, taali, autonomous):
        assert _application_ids(payload) == expected
        assert payload["retrieval_matches"] == len(expected)
        assert payload["is_exact_empty"] is False
        assert payload["evidence"][0]["clause_ids"] == ["applied-agentforce"]
        assert payload["graph_facts"][0]["fact"] == (
            "Built and deployed Agentforce actions."
        )
        assert payload["graph_facts"][0]["is_citation"] is False
        assert payload["graph_facts_are_evidence"] is False
        assert payload["graph"]["edges"][0]["label"] == "HAS_SKILL"

    assert shared_search.call_count == 3
    for delegated in shared_search.call_args_list:
        assert delegated.kwargs["role_id"] == int(case["role"].id)
        assert delegated.kwargs["deep_verify"] is False
        assert delegated.kwargs["include_graph"] is True
    assert len(calls) == 3
    assert {int(call["role_id"]) for call in calls} == {int(case["role"].id)}
    assert all(call["include_subgraph"] is True for call in calls)
