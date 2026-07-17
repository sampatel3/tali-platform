"""Coverage and person-deduplication guarantees for the search runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.candidate_search import runner
from app.candidate_search.rerank import CandidateRerankOutcome, RerankBatchResult
from app.candidate_search.schemas import ParsedFilter


def _wire_query(monkeypatch, *, parsed: ParsedFilter, rows: list[tuple[int, int]]):
    query = MagicMock()
    query.with_entities.return_value.all.return_value = rows
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *a, **k: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda q, _parsed: q)
    monkeypatch.setattr(runner, "_execute_graph_predicates", lambda **kw: None)
    return query


def test_search_deduplicates_people_before_count_and_limit(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"])
    _wire_query(
        monkeypatch,
        parsed=parsed,
        rows=[(10, 100), (11, 100), (20, 200)],
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="Python",
        base_query=MagicMock(),
        rerank_enabled=False,
    )

    assert out.application_ids == [10, 20]
    assert out.database_matches == 2
    assert out.qualified is None
    assert out.deep_checked == 0
    assert out.exhaustive is True


def test_failed_verifier_does_not_claim_deep_checked_or_qualified(monkeypatch):
    parsed = ParsedFilter(soft_criteria=["banking domain"])
    _wire_query(monkeypatch, parsed=parsed, rows=[(10, 100), (20, 200)])

    from app.candidate_search import rerank as rerank_module

    def _unavailable(**_kwargs):
        raise rerank_module.RerankUnavailable("no verifier")

    monkeypatch.setattr(rerank_module, "rerank_application_ids", _unavailable)

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="banking domain",
        base_query=MagicMock(),
        rerank_enabled=True,
    )

    assert out.application_ids == [10, 20]
    assert out.database_matches == 2
    assert out.rerank_applied is False
    assert out.deep_checked == 0
    assert out.evidence_succeeded == 0
    assert out.evidence_failed == 0
    assert out.qualified is None
    assert out.capped is True
    assert out.exhaustive is False
    assert out.warnings[-1].code == "rerank_skipped"


def test_deep_verification_reports_bounded_coverage_without_hiding_it(monkeypatch):
    parsed = ParsedFilter(soft_criteria=["banking domain"])
    rows = [(index, 1000 + index) for index in range(1, 61)]
    _wire_query(monkeypatch, parsed=parsed, rows=rows)

    from app.candidate_search import rerank as rerank_module

    def _verified(**kwargs):
        outcomes = [
            CandidateRerankOutcome(
                application_id=app_id,
                status="qualified" if app_id % 2 == 0 else "not_qualified",
                reason="test",
            )
            for app_id in kwargs["application_ids"]
        ]
        return RerankBatchResult(
            application_ids=[
                item.application_id for item in outcomes if item.status == "qualified"
            ],
            outcomes=outcomes,
        )

    monkeypatch.setattr(rerank_module, "rerank_application_ids", _verified)

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="banking domain",
        base_query=MagicMock(),
        rerank_enabled=True,
    )

    assert out.database_matches == 60
    assert out.deep_checked == 50
    assert out.evidence_succeeded == 50
    assert out.evidence_failed == 0
    assert out.qualified == 25
    assert out.application_ids == list(range(2, 51, 2))
    assert out.capped is True
    assert out.exhaustive is False
    assert out.warnings[-1].code == "verification_capped"


def test_mixed_verifier_errors_are_retained_and_not_counted_negative(monkeypatch):
    parsed = ParsedFilter(soft_criteria=["banking domain"])
    _wire_query(
        monkeypatch,
        parsed=parsed,
        rows=[(10, 100), (20, 200), (30, 300)],
    )

    from app.candidate_search import rerank as rerank_module

    monkeypatch.setattr(
        rerank_module,
        "rerank_application_ids",
        lambda **_kwargs: RerankBatchResult(
            # Qualified + errored candidates remain visible; the definitive
            # negative is the only row filtered out.
            application_ids=[10, 30],
            outcomes=[
                CandidateRerankOutcome(10, "qualified", reason="clear evidence"),
                CandidateRerankOutcome(20, "not_qualified", reason="no evidence"),
                CandidateRerankOutcome(30, "error", error_code="model_call_failed"),
            ],
        ),
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="banking domain",
        base_query=MagicMock(),
        rerank_enabled=True,
    )

    assert out.application_ids == [10, 30]
    assert out.deep_checked == 3
    assert out.evidence_succeeded == 2
    assert out.evidence_failed == 1
    assert out.qualified == 1
    assert out.rerank_applied is True
    assert out.capped is True
    assert out.exhaustive is False
    assert [item.status for item in out.verification_results] == [
        "qualified",
        "not_qualified",
        "error",
    ]
    assert out.warnings[-1].code == "rerank_partial"


def test_all_verifier_errors_leave_qualified_unknown(monkeypatch):
    parsed = ParsedFilter(soft_criteria=["banking domain"])
    _wire_query(monkeypatch, parsed=parsed, rows=[(10, 100), (20, 200)])

    from app.candidate_search import rerank as rerank_module

    monkeypatch.setattr(
        rerank_module,
        "rerank_application_ids",
        lambda **_kwargs: RerankBatchResult(
            application_ids=[10, 20],
            outcomes=[
                CandidateRerankOutcome(10, "error", error_code="invalid_model_response"),
                CandidateRerankOutcome(20, "error", error_code="model_call_failed"),
            ],
        ),
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="banking domain",
        base_query=MagicMock(),
        rerank_enabled=True,
    )

    assert out.application_ids == [10, 20]
    assert out.deep_checked == 2
    assert out.evidence_succeeded == 0
    assert out.evidence_failed == 2
    assert out.qualified is None
    assert out.rerank_applied is False
    assert out.capped is True
    assert out.exhaustive is False
    assert out.warnings[-1].code == "rerank_partial"


def test_role_id_reaches_graph_predicate_execution(monkeypatch):
    parsed = ParsedFilter(
        graph_predicates=[{"type": "worked_at", "value": "Acme"}]
    )
    query = MagicMock()
    query.with_entities.return_value.all.return_value = []
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: parsed)
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *a, **k: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda q, _parsed: q)
    captured = {}

    def _execute(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(runner, "_execute_graph_predicates", _execute)

    runner.run_search(
        db=MagicMock(),
        organization_id=3,
        role_id=17,
        nl_query="people who worked at Acme",
        base_query=MagicMock(),
    )

    assert captured["organization_id"] == 3
    assert captured["role_id"] == 17


def test_parser_failure_warning_does_not_expose_exception_details(monkeypatch):
    query = MagicMock()
    query.with_entities.return_value.all.return_value = []
    monkeypatch.setattr(runner.cache_module, "get", lambda _key: None)
    monkeypatch.setattr(runner.cache_module, "set", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "parse_nl_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private-parser.internal api_key=tenant-secret")
        ),
    )
    monkeypatch.setattr(runner, "apply_parsed_filter", lambda *a, **k: query)
    monkeypatch.setattr(runner, "apply_relevance_order", lambda q, _parsed: q)
    monkeypatch.setattr(runner, "_execute_graph_predicates", lambda **kw: None)

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="Python",
        base_query=MagicMock(),
    )

    assert out.warnings[0].code == "parser_failed"
    assert "tenant-secret" not in out.warnings[0].message
    assert "private-parser" not in out.warnings[0].message


def test_optional_search_failures_return_stable_public_warnings(monkeypatch):
    parsed = ParsedFilter(soft_criteria=["banking domain"])
    _wire_query(monkeypatch, parsed=parsed, rows=[(10, 100)])

    from app.candidate_search import rerank as rerank_module

    monkeypatch.setattr(
        rerank_module,
        "rerank_application_ids",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("https://private-rerank.internal?token=tenant-secret")
        ),
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="banking domain",
        base_query=MagicMock(),
        rerank_enabled=True,
    )

    warning = next(item for item in out.warnings if item.code == "rerank_skipped")
    assert warning.message == (
        "Deep verification was unavailable; showing database matches instead."
    )
    assert "tenant-secret" not in str(out.warnings)


def test_graph_failures_return_stable_public_warnings(monkeypatch):
    from app.candidate_graph import client as graph_client
    from app.candidate_graph import search as graph_search

    warnings = []
    parsed = ParsedFilter(
        graph_predicates=[{"type": "worked_at", "value": "Acme"}]
    )
    monkeypatch.setattr(graph_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        graph_search,
        "candidate_ids_matching_all",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("neo4j://private-graph.internal?token=tenant-secret")
        ),
    )

    assert (
        runner._execute_graph_predicates(
            organization_id=1,
            role_id=2,
            parsed=parsed,
            warnings=warnings,
        )
        is None
    )

    assert warnings[0].code == "graph_predicate_dropped"
    assert warnings[0].message == (
        "Graph predicates were unavailable and were ignored for this search."
    )
    assert "tenant-secret" not in str(warnings)


def test_subgraph_failure_warning_does_not_expose_exception_details(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"])
    _wire_query(monkeypatch, parsed=parsed, rows=[(10, 100)])

    from app.candidate_graph import search as graph_search

    monkeypatch.setattr(
        runner,
        "_candidate_ids_for_application_ids",
        lambda *_args, **_kwargs: [100],
    )
    monkeypatch.setattr(
        graph_search,
        "subgraph_for_candidates",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("neo4j://private-graph.internal?token=tenant-secret")
        ),
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="Python",
        base_query=MagicMock(),
        include_subgraph=True,
    )

    warning = next(item for item in out.warnings if item.code == "neo4j_unavailable")
    assert warning.message == "Graph view is temporarily unavailable."
    assert "tenant-secret" not in str(out.warnings)


def test_empty_scoped_subgraph_never_falls_back_to_unrelated_candidates(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"])
    _wire_query(monkeypatch, parsed=parsed, rows=[(10, 100)])

    from app.candidate_graph import search as graph_search
    from app.candidate_search.schemas import GraphPayload

    monkeypatch.setattr(
        runner,
        "_candidate_ids_for_application_ids",
        lambda *_args, **_kwargs: [100],
    )
    monkeypatch.setattr(
        graph_search,
        "episode_selectors_for_candidates",
        lambda *_args, **_kwargs: ["candidate-100-"],
    )
    monkeypatch.setattr(
        graph_search,
        "subgraph_for_candidates",
        lambda **_kwargs: GraphPayload(),
    )
    monkeypatch.setattr(
        graph_search,
        "subgraph_for_query",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("broad fallback must not run")
        ),
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        role_id=77,
        nl_query="Python",
        base_query=MagicMock(),
        include_subgraph=True,
    )

    assert out.subgraph == GraphPayload()
    assert any(warning.code == "graph_data_missing" for warning in out.warnings)


def test_subgraph_provider_runs_after_sql_episode_snapshot_is_released(monkeypatch):
    parsed = ParsedFilter(skills_all=["Python"])
    _wire_query(monkeypatch, parsed=parsed, rows=[(10, 100)])

    from app.candidate_graph import search as graph_search
    from app.candidate_search.schemas import GraphNode, GraphPayload

    class _BoundarySession:
        def __init__(self):
            self.transaction_open = True
            self.rollbacks = 0

        def rollback(self):
            self.transaction_open = False
            self.rollbacks += 1

    db = _BoundarySession()

    def _candidate_ids(session, _application_ids):
        assert session is db
        session.transaction_open = True
        return [100]

    def _selectors(session, candidate_ids):
        assert session is db
        assert session.transaction_open is True
        assert candidate_ids == [100]
        return ["candidate-100-", "interview-7-", "event-9"]

    def _subgraph(**kwargs):
        assert db.transaction_open is False
        assert "db" not in kwargs
        assert kwargs["episode_selectors"] == [
            "candidate-100-",
            "interview-7-",
            "event-9",
        ]
        return GraphPayload(
            nodes=[GraphNode(id="person:100", label="Person", name="Candidate")]
        )

    monkeypatch.setattr(runner, "_candidate_ids_for_application_ids", _candidate_ids)
    monkeypatch.setattr(graph_search, "episode_selectors_for_candidates", _selectors)
    monkeypatch.setattr(graph_search, "subgraph_for_candidates", _subgraph)
    monkeypatch.setattr(runner, "_enrich_graph_scores", lambda *_args: None)

    output = runner.run_search(
        db=db,
        organization_id=1,
        nl_query="Python",
        base_query=MagicMock(),
        include_subgraph=True,
    )

    assert output.subgraph is not None
    assert output.subgraph.nodes[0].id == "person:100"
    assert db.rollbacks == 3
