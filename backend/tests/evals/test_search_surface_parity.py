"""Cross-surface contract for the production candidate-search entry points.

The retrieval engine's evidence semantics are exercised in
``test_runtime_ground_truth.py``.  This module keeps the second layer honest:
every unified product surface must preserve that oracle-derived result and the
selected role scope.  Provider and graph calls are replaced with local values.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent_chat import tools as role_agent_tools
from app.agent_runtime import tool_registry as autonomous_tools
from app.candidate_search.role_projection import OWNER_ROLE_JUDGMENT_FIELDS
from app.candidate_search.grounded_evidence import CriterionVerdict, Evidence
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
from app.mcp import server as mcp_server
from app.mcp.catalog import (
    CANDIDATE_QUALITATIVE_EVIDENCE,
    CANDIDATE_QUALITATIVE_EXACT_EMPTY,
)
from app.mcp.shared_reads import capabilities_for_successful_read
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.role import ROLE_KIND_SISTER, Role
from app.models.sister_role_evaluation import SisterRoleEvaluation
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


def _sister_role_truth_case(db, client) -> dict:
    """Construct a role-local oracle where owner and sister rankings invert."""

    headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    owner = Role(
        organization_id=user.organization_id,
        name="Canonical ATS role",
        source="manual",
    )
    db.add(owner)
    db.flush()
    sister = Role(
        organization_id=user.organization_id,
        name="Related PySpark role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(sister)
    db.flush()
    specs = (
        # key, owner score, owner verdict/stage, sister score/stage
        ("best", 12.0, "Below threshold", "review", 96.0, "applied"),
        ("second", 92.0, "Advance recommended", "applied", 61.0, "review"),
        ("local_advanced", 99.0, "Advance recommended", "applied", 30.0, "advanced"),
        # Shared ATS advancement restricts writes but cannot hide or override
        # this related role's explicit membership and local stage.
        ("globally_advanced", 8.0, None, "advanced", 98.0, "applied"),
    )
    applications: dict[str, CandidateApplication] = {}
    for (
        key,
        owner_score,
        owner_verdict,
        owner_stage,
        sister_score,
        sister_stage,
    ) in specs:
        application = _seed_candidate(
            db,
            role=owner,
            entity_id=key.replace("_", "-"),
        )
        application.taali_score_cache_100 = owner_score
        application.pre_screen_score_100 = owner_score
        application.pre_screen_recommendation = owner_verdict
        application.pipeline_stage = owner_stage
        applications[key] = application
        db.add(
            SisterRoleEvaluation(
                organization_id=user.organization_id,
                role_id=sister.id,
                source_application_id=application.id,
                status="done",
                pipeline_stage=sister_stage,
                spec_fingerprint="related-spec",
                role_fit_score=sister_score,
                details={"summary": f"{key} related-role evidence."},
            )
        )
    db.commit()
    return {
        "headers": headers,
        "user": user,
        "owner": owner,
        "sister": sister,
        **applications,
    }


def _qualitative_full_pool_case(db, client) -> dict:
    """Create low/unscored matches plus an owner-only related distractor."""

    _headers, email = auth_headers(client)
    user = db.query(User).filter(User.email == email).one()
    ordinary = Role(
        organization_id=user.organization_id,
        name="Ordinary qualitative role",
        source="manual",
    )
    owner = Role(
        organization_id=user.organization_id,
        name="ATS transport owner",
        source="manual",
    )
    db.add_all([ordinary, owner])
    db.flush()
    related = Role(
        organization_id=user.organization_id,
        name="Independent qualitative role",
        source="sister",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
    )
    db.add(related)
    db.flush()

    ordinary_low = _seed_candidate(
        db,
        role=ordinary,
        entity_id="ordinary-low-score-match",
    )
    ordinary_low.taali_score_cache_100 = 3.0
    ordinary_low.pre_screen_recommendation = "Below threshold"
    ordinary_low.candidate.cv_text = (
        "Built and deployed Agentforce actions for customer support."
    )
    ordinary_unscored = _seed_candidate(
        db,
        role=ordinary,
        entity_id="ordinary-unscored-match",
    )
    ordinary_unscored.taali_score_cache_100 = None
    ordinary_unscored.candidate.cv_text = (
        "Implemented Agentforce workflows with cited production ownership."
    )
    ordinary_high = _seed_candidate(
        db,
        role=ordinary,
        entity_id="ordinary-high-score-nonmatch",
    )
    ordinary_high.taali_score_cache_100 = 99.0
    ordinary_high.candidate.cv_text = "Built conventional Python services."

    related_unscored = _seed_candidate(
        db,
        role=owner,
        entity_id="related-unscored-match",
    )
    related_unscored.taali_score_cache_100 = None
    related_unscored.candidate.cv_text = (
        "Delivered Agentforce agents and actions in production."
    )
    db.add(
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=related.id,
            candidate_id=related_unscored.candidate_id,
            source_application_id=related_unscored.id,
            status="pending",
            pipeline_stage="applied",
            application_outcome="open",
            spec_fingerprint="qualitative-related-spec",
            role_fit_score=None,
        )
    )
    related_high = _seed_candidate(
        db,
        role=owner,
        entity_id="related-high-score-nonmatch",
    )
    related_high.candidate.cv_text = "Built conventional Java services."
    db.add(
        SisterRoleEvaluation(
            organization_id=user.organization_id,
            role_id=related.id,
            candidate_id=related_high.candidate_id,
            source_application_id=related_high.id,
            status="done",
            pipeline_stage="review",
            application_outcome="open",
            spec_fingerprint="qualitative-related-spec",
            role_fit_score=99.0,
        )
    )
    owner_only = _seed_candidate(
        db,
        role=owner,
        entity_id="owner-only-agentforce-match",
    )
    owner_only.taali_score_cache_100 = 100.0
    owner_only.candidate.cv_text = (
        "Built and deployed Agentforce actions for another requisition."
    )
    db.commit()
    return {
        "user": user,
        "ordinary": ordinary,
        "owner": owner,
        "related": related,
        "ordinary_low": ordinary_low,
        "ordinary_unscored": ordinary_unscored,
        "ordinary_high": ordinary_high,
        "related_unscored": related_unscored,
        "related_high": related_high,
        "owner_only": owner_only,
    }


def _local_qualitative_grounding(
    monkeypatch, *, criterion: str, degraded: bool
) -> None:
    def local_runner(**_kwargs):
        return SearchOutput(
            application_ids=[],
            parsed_filter=ParsedFilter(
                soft_criteria=[criterion],
                free_text=criterion,
            ),
            warnings=[],
            capped=False,
            exhaustive=True,
            is_exact_empty=False,
        )

    def local_ground(applications, *, criteria, **_kwargs):
        [requested] = criteria
        grounded = []
        for application in applications:
            if degraded:
                verdict = CriterionVerdict(
                    requested,
                    status="error",
                    note="Synthetic evidence service unavailable.",
                )
            else:
                cv = str(application.candidate.cv_text or "")
                if "agentforce" in cv.lower() and "agentforce" in requested.lower():
                    verdict = CriterionVerdict(
                        requested,
                        status="met",
                        grounded=True,
                        source="cv_citation",
                        evidence=[Evidence(quote=cv, source="cv")],
                    )
                else:
                    verdict = CriterionVerdict(requested, status="missing")
            grounded.append((application, [verdict]))
        return grounded

    monkeypatch.setattr("app.candidate_search.runner.run_search", local_runner)
    monkeypatch.setattr(
        "app.candidate_search.top_candidates._ground_window",
        local_ground,
    )
    monkeypatch.setattr(
        handlers,
        "_attach_shareable_candidate_report",
        lambda _db, _user, **kwargs: kwargs["snapshot"],
    )


def _grounded_search_on_every_surface(db, *, user, role, query: str) -> dict[str, dict]:
    args = {"query": query, "limit": 10, "rank_by": "taali"}

    @contextmanager
    def borrowed_session(_ctx, _scopes):
        yield db, user

    with patch.object(mcp_server, "_open_session", borrowed_session):
        public = mcp_server.find_top_candidates(
            object(),
            role_id=int(role.id),
            **args,
        )
    taali = taali_tools.dispatch_tool(
        "find_top_candidates",
        args,
        db=db,
        user=user,
        conversation=SimpleNamespace(role_id=int(role.id)),
    )
    agent_chat = role_agent_tools.dispatch_tool(
        "find_top_candidates",
        args,
        db=db,
        role=role,
        user=user,
    )
    with patch.object(autonomous_tools, "_governance_block_reason", return_value=None):
        autonomous = autonomous_tools.dispatch(
            "find_top_candidates",
            args,
            db=db,
            agent_run=MagicMock(decisions_emitted=0),
            role=role,
        )
    assert agent_chat["type"] == "candidate_evidence"
    return {
        "public_mcp": public,
        "taali_chat": taali,
        "agent_chat": {
            key: value for key, value in agent_chat.items() if key != "type"
        },
        "autonomous_agent": autonomous,
    }


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
    assert all(
        "candidate_applications.role_id" in str(call["base_query"]) for call in calls
    )


def test_related_role_top_candidates_preserve_local_truth_across_agent_surfaces(
    db, client, monkeypatch
) -> None:
    """Both chats and the autonomous agent must use the sister-role projection.

    This regression is deterministic and free: the parser result is local, the
    query has no qualitative criterion, and neither graph nor evidence clients
    can be reached.
    """

    case = _sister_role_truth_case(db, client)
    calls: list[dict] = []

    def local_runner(**kwargs):
        calls.append(kwargs)
        return SearchOutput(
            application_ids=[],
            parsed_filter=ParsedFilter(),
            warnings=[],
            rerank_applied=False,
            exhaustive=True,
            is_exact_empty=False,
        )

    monkeypatch.setattr("app.candidate_search.runner.run_search", local_runner)
    monkeypatch.setattr(
        handlers,
        "_attach_shareable_candidate_report",
        lambda _db, _user, **kwargs: kwargs["snapshot"],
    )
    args = {"query": "candidates", "limit": 10, "rank_by": "taali"}

    shared = handlers.find_top_candidates(
        db,
        case["user"],
        role_id=int(case["sister"].id),
        **args,
    )
    taali = taali_tools.dispatch_tool(
        "find_top_candidates",
        _arguments_with_role_scope(
            "find_top_candidates",
            args,
            conversation_role_id=int(case["sister"].id),
        ),
        db=db,
        user=case["user"],
    )
    role_agent = role_agent_tools.dispatch_tool(
        "find_top_candidates",
        args,
        db=db,
        role=case["sister"],
        user=case["user"],
    )
    with patch.object(autonomous_tools, "_governance_block_reason", return_value=None):
        autonomous = autonomous_tools.dispatch(
            "find_top_candidates",
            args,
            db=db,
            agent_run=MagicMock(decisions_emitted=0),
            role=case["sister"],
        )

    expected_ids = [
        int(case["globally_advanced"].id),
        int(case["best"].id),
        int(case["second"].id),
    ]
    for payload in (shared, taali, role_agent, autonomous):
        rows = payload["candidates"]
        assert payload["pool_size"] == 3
        assert [int(row["application_id"]) for row in rows] == expected_ids
        assert [row["taali_score"] for row in rows] == [98.0, 96.0, 61.0]
        assert [row["pipeline_stage"] for row in rows] == [
            "applied",
            "applied",
            "review",
        ]
        assert all(int(row["role_id"]) == int(case["sister"].id) for row in rows)
        assert all(row["role_name"] == case["sister"].name for row in rows)
        assert all(row["score_mode"] == "sister_role" for row in rows)
        # The source application contributes shared CV evidence and ATS
        # restrictions only. Owner-role scores and judgments are never part of
        # the independent related role's agent-facing projection.
        assert all(OWNER_ROLE_JUDGMENT_FIELDS.isdisjoint(row) for row in rows)

        returned = {int(row["application_id"]) for row in rows}
        assert int(case["local_advanced"].id) not in returned
        assert int(case["globally_advanced"].id) in returned

    assert case["best"].pre_screen_recommendation == "Below threshold"
    assert len(calls) == 4
    assert {int(call["role_id"]) for call in calls} == {int(case["sister"].id)}
    assert all(call["include_subgraph"] is False for call in calls)


def test_grounded_qualitative_search_uses_full_active_pool_on_every_surface(
    db,
    client,
    monkeypatch,
) -> None:
    """Low/unscored matches remain visible; an ATS owner is never membership."""

    case = _qualitative_full_pool_case(db, client)
    query = "hands-on Agentforce experience"
    _local_qualitative_grounding(
        monkeypatch,
        criterion=query,
        degraded=False,
    )

    ordinary_results = _grounded_search_on_every_surface(
        db,
        user=case["user"],
        role=case["ordinary"],
        query=query,
    )
    expected_ordinary = [
        int(case["ordinary_low"].id),
        int(case["ordinary_unscored"].id),
    ]
    for payload in ordinary_results.values():
        assert payload["pool_size"] == payload["role_roster_size"] == 3
        assert [
            int(candidate["application_id"]) for candidate in payload["candidates"]
        ] == expected_ordinary
        assert payload["candidates"][0]["taali_score"] == 3.0
        assert payload["candidates"][1]["taali_score"] is None
        capabilities = capabilities_for_successful_read(
            "find_top_candidates",
            payload,
            arguments={"query": query},
            request_text=f"Show me candidates with {query}",
        )
        assert CANDIDATE_QUALITATIVE_EVIDENCE in capabilities

    related_results = _grounded_search_on_every_surface(
        db,
        user=case["user"],
        role=case["related"],
        query=query,
    )
    related_match_id = int(case["related_unscored"].id)
    owner_only_id = int(case["owner_only"].id)
    for payload in related_results.values():
        assert payload["pool_size"] == payload["role_roster_size"] == 2
        assert [
            int(candidate["application_id"]) for candidate in payload["candidates"]
        ] == [related_match_id]
        assert payload["candidates"][0]["taali_score"] is None
        assert payload["candidates"][0]["role_id"] == int(case["related"].id)
        assert owner_only_id not in {
            int(candidate["application_id"]) for candidate in payload["candidates"]
        }


def test_grounded_qualitative_exact_empty_is_identical_on_every_surface(
    db,
    client,
    monkeypatch,
) -> None:
    case = _qualitative_full_pool_case(db, client)
    query = "hands-on PySpark experience"
    _local_qualitative_grounding(
        monkeypatch,
        criterion=query,
        degraded=False,
    )

    results = _grounded_search_on_every_surface(
        db,
        user=case["user"],
        role=case["ordinary"],
        query=query,
    )
    for payload in results.values():
        assert payload["candidates"] == []
        assert payload["pool_size"] == payload["role_roster_size"] == 3
        assert payload["deep_checked"] == 3
        assert payload["evidence_succeeded"] == 3
        assert payload["qualified_total"] == 0
        assert payload["exhaustive"] is True
        assert payload["is_exact_empty"] is True
        capabilities = capabilities_for_successful_read(
            "find_top_candidates",
            payload,
            arguments={"query": query},
            request_text=f"Do we have anyone with {query}?",
        )
        assert CANDIDATE_QUALITATIVE_EXACT_EMPTY in capabilities


def test_grounded_qualitative_degraded_zero_is_never_exact_on_any_surface(
    db,
    client,
    monkeypatch,
) -> None:
    case = _qualitative_full_pool_case(db, client)
    query = "hands-on Agentforce experience"
    _local_qualitative_grounding(
        monkeypatch,
        criterion=query,
        degraded=True,
    )

    results = _grounded_search_on_every_surface(
        db,
        user=case["user"],
        role=case["ordinary"],
        query=query,
    )
    for payload in results.values():
        assert payload["candidates"] == []
        assert payload["deep_checked"] == 3
        assert payload["evidence_succeeded"] == 0
        assert payload["qualified_total"] is None
        assert payload["capped"] is True
        assert payload["exhaustive"] is False
        assert payload["is_exact_empty"] is False
        capabilities = capabilities_for_successful_read(
            "find_top_candidates",
            payload,
            arguments={"query": query},
            request_text=f"Do we have anyone with {query}?",
        )
        assert CANDIDATE_QUALITATIVE_EVIDENCE not in capabilities
        assert CANDIDATE_QUALITATIVE_EXACT_EMPTY not in capabilities


def test_related_role_application_filter_preserves_local_truth_across_surfaces(
    db, client
) -> None:
    """Application filtering shares sister truth in Chat and autonomous runs.

    Agent Chat's candidate-search tool delegates to the already-covered
    natural-language path; ``search_applications`` itself is exposed by Taali
    Chat and the autonomous agent runtime.
    """

    case = _sister_role_truth_case(db, client)
    args = {
        "min_score": 70,
        "score_type": "taali",
        "pipeline_stage": "applied",
        "sort_by": "taali_score",
        "limit": 10,
    }
    shared = handlers.search_applications(
        db,
        case["user"],
        role_id=int(case["sister"].id),
        **args,
    )
    taali = taali_tools.dispatch_tool(
        "search_applications",
        _arguments_with_role_scope(
            "search_applications",
            args,
            conversation_role_id=int(case["sister"].id),
        ),
        db=db,
        user=case["user"],
    )
    with patch.object(autonomous_tools, "_governance_block_reason", return_value=None):
        autonomous = autonomous_tools.dispatch(
            "search_applications",
            {**args, "role_id": int(case["owner"].id)},
            db=db,
            agent_run=MagicMock(decisions_emitted=0),
            role=case["sister"],
        )

    for rows in (shared, taali, autonomous):
        assert [int(row["application_id"]) for row in rows] == [
            int(case["globally_advanced"].id),
            int(case["best"].id),
        ]
        for row in rows:
            assert int(row["role_id"]) == int(case["sister"].id)
            assert row["role_name"] == case["sister"].name
            assert row["pipeline_stage"] == "applied"
            assert row["assessment_score"] is None
            assert row["score_mode"] == "sister_role"
        assert [row["taali_score"] for row in rows] == [98.0, 96.0]
        assert [row["pre_screen_score"] for row in rows] == [98.0, 96.0]

    assert case["best"].taali_score_cache_100 == 12.0
    assert case["best"].pipeline_stage == "review"
    assert case["best"].pre_screen_recommendation == "Below threshold"


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
