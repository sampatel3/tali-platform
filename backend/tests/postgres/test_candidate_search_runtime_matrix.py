"""Real PostgreSQL regressions for the unified candidate-search runtime.

Only external boundaries are replaced: structured parsing, usage admission,
and GraphDB evidence retrieval. SQL compilation/execution, role/tenant scope,
rank fusion, handler hydration, and every product-surface adapter remain real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agent_chat import tools as role_agent_tools
from app.agent_runtime import tool_registry as runtime_tools
from app.candidate_graph.search import (
    GraphCandidateEvidenceHit,
    GraphEpisodeEvidence,
    GraphEvidenceSearchResult,
)
from app.candidate_search import cache as search_cache
from app.candidate_search import hybrid, parser, rerank, runner
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
from app.candidate_search.schemas import ParsedFilter
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
from app.candidate_search.tool_failure_contract import candidate_search_result_failed
from app.deps import get_current_user
from app.llm import StructuredResult
from app.main import app
from app.mcp import handlers
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User
from app.platform.database import get_db
from app.taali_chat import tool_registry as taali_tools


AGENTFORCE_QUERY = "Agentforce experience"
KEYWORD_QUERY = "ledger reconciliation incident leadership"
SEARCH_INDEXES = {
    "ix_candidates_search_skills_trgm",
    "ix_candidates_search_experience_trgm",
    "ix_candidates_search_profile_trgm",
    "ix_candidate_applications_cv_fts",
    "ix_candidates_cv_fts",
}


@dataclass(frozen=True)
class SearchWorld:
    user: User
    role: Role
    candidate_ids: dict[str, int]
    application_ids: dict[str, int]
    documents: dict[str, str]


@dataclass
class BoundaryFakes:
    parser_results: list[StructuredResult[ParsedFilter]] = field(default_factory=list)
    graph_results: dict[str, GraphEvidenceSearchResult] = field(default_factory=dict)
    parser_calls: list[dict[str, Any]] = field(default_factory=list)
    graph_calls: list[dict[str, Any]] = field(default_factory=list)


def _seed_world(db) -> SearchWorld:
    stamp = str(id(db))
    organization = Organization(name=f"Search truth {stamp}", slug=f"search-truth-{stamp}")
    other_organization = Organization(
        name=f"Other search truth {stamp}", slug=f"other-search-truth-{stamp}"
    )
    db.add_all([organization, other_organization])
    db.flush()
    role = Role(organization_id=organization.id, name="AI Engineer", source="manual")
    other_role = Role(
        organization_id=organization.id, name="Other Role", source="manual"
    )
    other_org_role = Role(
        organization_id=other_organization.id,
        name="Other Tenant Role",
        source="manual",
    )
    user = User(
        email=f"search-{stamp}@example.test",
        hashed_password="not-used",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="Search Tester",
        organization_id=organization.id,
        role="owner",
    )
    db.add_all([role, other_role, other_org_role, user])
    db.flush()

    candidate_ids: dict[str, int] = {}
    application_ids: dict[str, int] = {}
    documents: dict[str, str] = {}

    def add_candidate(
        key: str,
        *,
        position: str,
        skills: list[str],
        country: str,
        cv_text: str,
        selected_role: Role = role,
        selected_org: Organization = organization,
        candidate_deleted: bool = False,
        application_deleted: bool = False,
    ) -> None:
        deleted_at = datetime.now(timezone.utc)
        candidate = Candidate(
            organization_id=selected_org.id,
            email=f"{key}-{stamp}@ground-truth.test",
            full_name=key,
            position=position,
            headline=position,
            location_country=country,
            skills=skills,
            experience_entries=[
                {
                    "title": position,
                    "company": "Ground Truth Ltd",
                    "country": country,
                    "start_date": "2019-01-01",
                }
            ],
            cv_text=cv_text,
            deleted_at=deleted_at if candidate_deleted else None,
        )
        db.add(candidate)
        db.flush()
        application = CandidateApplication(
            organization_id=selected_org.id,
            candidate_id=candidate.id,
            role_id=selected_role.id,
            source="manual",
            status="applied",
            pipeline_stage="review",
            pipeline_stage_source="recruiter",
            application_outcome="open",
            cv_text=cv_text,
            deleted_at=deleted_at if application_deleted else None,
        )
        db.add(application)
        db.flush()
        candidate_ids[key] = int(candidate.id)
        application_ids[key] = int(application.id)
        documents[key] = cv_text

    add_candidate(
        "structured-match",
        position="Data Engineer",
        skills=["PySpark", "Python (Programming Language)"],
        country="United Arab Emirates",
        cv_text=(
            "Led ledger reconciliation incident leadership for resilient "
            "settlement operations."
        ),
    )
    add_candidate(
        "pyspark-near-title",
        position="Software Engineer",
        skills=["PySpark"],
        country="United Arab Emirates",
        cv_text="Built streaming services with PySpark.",
    )
    add_candidate(
        "project-manager",
        position="Project Manager",
        skills=["Project Management"],
        country="United Kingdom",
        cv_text="Delivered a multi-year treasury transformation.",
    )
    add_candidate(
        "python-wrong-location",
        position="Data Engineer",
        skills=["Python"],
        country="United Kingdom",
        cv_text="Built Python data services in London.",
    )
    add_candidate(
        "uae-wrong-skill",
        position="Data Engineer",
        skills=["Go"],
        country="United Arab Emirates",
        cv_text="Built Go services in Dubai.",
    )

    agentforce_documents = {
        "agentforce-applied": (
            "Built and deployed Agentforce actions for customer service."
        ),
        "agentforce-mention": "Interested in Agentforce and learning the platform.",
        "salesforce-adjacent": "Administered Salesforce Sales Cloud for five years.",
        "agentforce-team": (
            "Ben's team used Agentforce; Ben did not operate it directly."
        ),
        "agentforce-negated": "Did not build or deploy Agentforce in any role.",
    }
    for key, content in agentforce_documents.items():
        add_candidate(
            key,
            position="AI Engineer",
            skills=["Salesforce"] if key == "salesforce-adjacent" else [],
            country="United Arab Emirates",
            cv_text=content,
        )

    scope_text = (
        "Built and deployed Agentforce actions. Led ledger reconciliation "
        "incident leadership."
    )
    scope_kwargs = {
        "position": "Data Engineer",
        "skills": ["PySpark", "Python"],
        "country": "United Arab Emirates",
        "cv_text": scope_text,
    }
    add_candidate("scope-other-role", selected_role=other_role, **scope_kwargs)
    add_candidate(
        "scope-other-tenant",
        selected_role=other_org_role,
        selected_org=other_organization,
        **scope_kwargs,
    )
    add_candidate("scope-deleted-application", application_deleted=True, **scope_kwargs)
    add_candidate("scope-deleted-candidate", candidate_deleted=True, **scope_kwargs)

    return SearchWorld(
        user=user,
        role=role,
        candidate_ids=candidate_ids,
        application_ids=application_ids,
        documents=documents,
    )


@pytest.fixture
def search_world(postgres_search_db) -> SearchWorld:
    return _seed_world(postgres_search_db)


@pytest.fixture
def boundaries(monkeypatch) -> BoundaryFakes:
    fakes = BoundaryFakes()
    search_cache.clear()

    monkeypatch.setattr(parser, "_resolve_anthropic_client", lambda **_kwargs: object())

    def admit(**kwargs):
        meter = {
            **dict(kwargs.get("base_metering") or {}),
            "feature": kwargs["feature"].value,
            "organization_id": int(kwargs["organization_id"]),
            "trace_id": "postgres-search-test",
        }
        if kwargs.get("role_id") is not None:
            meter["role_id"] = int(kwargs["role_id"])
        return meter

    def generate(*args, **kwargs):
        fakes.parser_calls.append({"args": args, "kwargs": kwargs})
        if not fakes.parser_results:
            raise AssertionError("unexpected model parser call")
        return fakes.parser_results.pop(0)

    def retrieve_graph(**kwargs):
        query = str(kwargs["query"])
        fakes.graph_calls.append(dict(kwargs))
        if query not in fakes.graph_results:
            raise AssertionError(f"unexpected GraphDB retrieval for {query!r}")
        raw_result = fakes.graph_results[query]
        return hybrid.retrieve_graph_backend(
            **kwargs,
            graph_search_fn=lambda **_graph_kwargs: raw_result,
        )

    def external_call_forbidden(*_args, **_kwargs):
        raise AssertionError("external provider boundary was not faked")

    monkeypatch.setattr(parser, "admitted_search_metering", admit)
    monkeypatch.setattr(parser, "generate_structured", generate)
    monkeypatch.setattr(runner, "retrieve_graph_backend", retrieve_graph)
    monkeypatch.setattr(
        "app.candidate_graph.search.search_candidate_evidence",
        external_call_forbidden,
    )
    monkeypatch.setattr(rerank, "rerank_application_ids", external_call_forbidden)
    yield fakes
    search_cache.clear()


def _names(payload: dict[str, Any]) -> set[str]:
    return {str(row["candidate_name"]) for row in payload["applications"]}


def _document(document_id: str, entity_id: str, content: str) -> Document:
    return Document(
        id=document_id,
        entity_id=entity_id,
        source_type="cv",
        content=content,
        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
    )


def _agentforce_oracle(world: SearchWorld) -> set[str]:
    entity_ids = (
        "agentforce-applied",
        "agentforce-mention",
        "salesforce-adjacent",
        "agentforce-team",
        "agentforce-negated",
    )
    documents = tuple(
        _document(f"cv-{entity_id}", entity_id, world.documents[entity_id])
        for entity_id in entity_ids
    )
    applied = documents[0]
    start = applied.content.index("Agentforce")
    constructed = ConstructedWorld(
        id="postgres-agentforce-world",
        entities=tuple(WorldEntity(id=value, kind="person") for value in entity_ids),
        documents=documents,
        facts=(
            Fact(
                id="fact-applied-agentforce",
                subject_id="agentforce-applied",
                predicate="demonstrated",
                object=SearchObject(kind="capability", value="Agentforce"),
                direct_subject=True,
                provenance=(
                    Citation(
                        document_id=applied.id,
                        start=start,
                        end=start + len("Agentforce"),
                        quote="Agentforce",
                    ),
                ),
            ),
        ),
        closed_world_predicates=("demonstrated",),
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
    intent = QueryIntent(
        id="applied-agentforce",
        plan=SearchPlan(
            query=AGENTFORCE_QUERY,
            criteria=(criterion,),
            root=Expression.leaf(criterion.id),
        ),
    )
    return {
        item.entity_id
        for item in derive_judgments(constructed, intent)
        if item.eligibility is TruthValue.TRUE
    }


def _agentforce_graph_result(
    world: SearchWorld, *, capped: bool = False
) -> GraphEvidenceSearchResult:
    ordered = (
        "agentforce-mention",
        "salesforce-adjacent",
        "agentforce-team",
        "agentforce-negated",
        "scope-other-role",
        "scope-other-tenant",
        "scope-deleted-application",
        "scope-deleted-candidate",
        "agentforce-applied",
    )
    hits = []
    for rank, entity_id in enumerate(ordered):
        content = world.documents[entity_id]
        hits.append(
            GraphCandidateEvidenceHit(
                candidate_id=world.candidate_ids[entity_id],
                query=AGENTFORCE_QUERY,
                query_index=0,
                rank=rank,
                edge_uuid=f"edge-{entity_id}",
                fact="Generated graph text is not evidence.",
                source_name=entity_id,
                target_name="Agentforce",
                episodes=(
                    GraphEpisodeEvidence(
                        uuid=f"cv-{entity_id}",
                        name=f"cv-{entity_id}",
                        content=content,
                        source_description="cv",
                    ),
                ),
            )
        )
    return GraphEvidenceSearchResult(
        status="ok",
        hits=tuple(hits),
        capped=capped,
        exhaustive=not capped,
    )


def test_migrated_postgres_schema_has_candidate_search_indexes(
    postgres_search_engine,
) -> None:
    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())
    with postgres_search_engine.connect() as connection:
        actual_heads = {
            str(row[0])
            for row in connection.execute(text("SELECT version_num FROM alembic_version"))
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema()"
                )
            )
        }
    assert actual_heads == expected_heads
    assert SEARCH_INDEXES <= indexes


@pytest.mark.parametrize(
    ("query", "expected", "parsed_field", "parsed_value", "exact_empty"),
    [
        (
            "all candidates with PySpark",
            {"structured-match", "pyspark-near-title"},
            "skills_all",
            ["PySpark"],
            False,
        ),
        (
            "all candidates with project manager",
            {"project-manager"},
            "titles_all",
            ["project manager"],
            False,
        ),
        (
            "candidates with Python based in United Arab Emirates",
            {"structured-match"},
            "locations_country",
            ["United Arab Emirates"],
            False,
        ),
        (
            "candidates with data engineer, PySpark based in United Arab Emirates",
            {"structured-match"},
            "titles_all",
            ["data engineer"],
            False,
        ),
        (
            "all candidates with Rust",
            set(),
            "skills_all",
            ["Rust"],
            True,
        ),
    ],
    ids=("known-skill", "title", "location", "mixed", "exact-empty"),
)
def test_real_postgres_structured_search_matrix(
    postgres_search_db,
    search_world: SearchWorld,
    boundaries: BoundaryFakes,
    query: str,
    expected: set[str],
    parsed_field: str,
    parsed_value: list[str],
    exact_empty: bool,
) -> None:
    result = handlers.nl_search_candidates(
        postgres_search_db,
        search_world.user,
        query=query,
        role_id=int(search_world.role.id),
        limit=100,
    )

    assert _names(result) == expected
    assert result["parsed_filter"][parsed_field] == parsed_value
    assert result["retrieval"]["mode"] == "postgres_only"
    assert result["retrieval"]["graph_status"] == "not_selected"
    assert result["database_matches"] == len(expected)
    assert result["retrieval_matches"] == len(expected)
    assert result["is_exact_empty"] is exact_empty
    assert result["exhaustive"] is True
    assert result["warnings"] == []
    assert boundaries.parser_calls == []
    assert boundaries.graph_calls == []


def test_parser_failure_executes_real_postgres_keyword_fallback_without_false_zero(
    postgres_search_db,
    search_world: SearchWorld,
    boundaries: BoundaryFakes,
) -> None:
    boundaries.parser_results.append(
        StructuredResult(value=None, ok=False, error_reason="local parser failure")
    )
    boundaries.graph_results[KEYWORD_QUERY] = GraphEvidenceSearchResult(
        status="ok", exhaustive=True
    )

    result = handlers.nl_search_candidates(
        postgres_search_db,
        search_world.user,
        query=KEYWORD_QUERY,
        role_id=int(search_world.role.id),
        limit=100,
    )

    assert _names(result) == {"structured-match"}
    assert result["parsed_filter"]["keywords"] == [KEYWORD_QUERY]
    assert result["parsed_filter"]["parse_degraded"] is True
    assert result["database_matches"] == 1
    assert result["is_exact_empty"] is False
    warning_codes = {item["code"] for item in result["warnings"]}
    assert {"parser_failed", "graph_coverage_partial"} <= warning_codes
    assert candidate_search_result_failed("nl_search_candidates", result) is True
    assert len(boundaries.parser_calls) == 1
    assert len(boundaries.graph_calls) == 1


def test_agentforce_grounded_result_matches_independent_oracle_on_real_postgres(
    postgres_search_db,
    search_world: SearchWorld,
    boundaries: BoundaryFakes,
) -> None:
    expected = _agentforce_oracle(search_world)
    boundaries.graph_results[AGENTFORCE_QUERY] = _agentforce_graph_result(search_world)

    result = handlers.nl_search_candidates(
        postgres_search_db,
        search_world.user,
        query=AGENTFORCE_QUERY,
        role_id=int(search_world.role.id),
        limit=100,
    )

    assert expected == {"agentforce-applied"}
    assert _names(result) == expected
    assert result["database_matches"] == 0
    assert result["retrieval_matches"] == 1
    assert result["retrieval"]["hits"][0]["sources"] == ["graph"]
    assert result["retrieval"]["hits"][0]["evidence"]
    assert result["is_exact_empty"] is False
    assert candidate_search_result_failed("nl_search_candidates", result) is False
    assert boundaries.parser_calls == []
    assert len(boundaries.graph_calls) == 1


@pytest.mark.parametrize("graph_state", ["partial", "error"])
def test_graph_partial_or_error_never_becomes_a_false_exact_zero(
    postgres_search_db,
    search_world: SearchWorld,
    boundaries: BoundaryFakes,
    graph_state: str,
) -> None:
    if graph_state == "partial":
        boundaries.graph_results[AGENTFORCE_QUERY] = _agentforce_graph_result(
            search_world, capped=True
        )
    else:
        boundaries.graph_results[AGENTFORCE_QUERY] = GraphEvidenceSearchResult(
            status="error",
            exhaustive=False,
            errors=("local fake graph failure",),
        )

    result = handlers.nl_search_candidates(
        postgres_search_db,
        search_world.user,
        query=AGENTFORCE_QUERY,
        role_id=int(search_world.role.id),
        limit=100,
    )

    assert result["is_exact_empty"] is False
    assert result["exhaustive"] is False
    warning_codes = {item["code"] for item in result["warnings"]}
    if graph_state == "partial":
        assert _names(result) == {"agentforce-applied"}
        assert result["capped"] is True
        assert "graph_coverage_partial" in warning_codes
        assert candidate_search_result_failed("nl_search_candidates", result) is False
    else:
        assert result["applications"] == []
        assert "graph_retrieval_failed" in warning_codes
        assert candidate_search_result_failed("nl_search_candidates", result) is True


def test_real_postgres_search_is_identical_across_all_product_surfaces(
    postgres_search_db,
    search_world: SearchWorld,
    boundaries: BoundaryFakes,
) -> None:
    query = "all candidates with PySpark"
    expected = {"structured-match", "pyspark-near-title"}
    shared = handlers.nl_search_candidates(
        postgres_search_db,
        search_world.user,
        query=query,
        role_id=int(search_world.role.id),
        limit=100,
    )
    taali = taali_tools.dispatch_tool(
        "nl_search_candidates",
        {"query": query, "role_id": int(search_world.role.id), "limit": 100},
        db=postgres_search_db,
        user=search_world.user,
    )
    role_agent = role_agent_tools.dispatch_tool(
        "search_candidates",
        {"query": query},
        db=postgres_search_db,
        role=search_world.role,
        user=search_world.user,
    )
    runtime = runtime_tools.dispatch(
        "nl_search_candidates",
        {"query": query, "limit": 100},
        db=postgres_search_db,
        agent_run=SimpleNamespace(decisions_emitted=0),
        role=search_world.role,
    )

    def override_db():
        yield postgres_search_db

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: search_world.user
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/applications",
                params={
                    "role_id": int(search_world.role.id),
                    "nl_query": query,
                    "application_outcome": "open",
                    "include_stage_counts": "false",
                    "limit": 100,
                },
            )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    assert response.status_code == 200, response.text
    http_names = {str(item["candidate_name"]) for item in response.json()["items"]}
    assert http_names == expected
    for payload in (shared, taali, role_agent, runtime):
        assert _names(payload) == expected
        assert payload["retrieval"]["mode"] == "postgres_only"
        assert payload["is_exact_empty"] is False
    assert boundaries.parser_calls == []
    assert boundaries.graph_calls == []
