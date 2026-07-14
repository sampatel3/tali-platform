"""Coverage and person-deduplication guarantees for the search runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.candidate_search import runner
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
    assert out.qualified == 2
    assert out.exhaustive is True


def test_deep_verification_reports_bounded_coverage_without_hiding_it(monkeypatch):
    parsed = ParsedFilter(soft_criteria=["banking domain"])
    rows = [(index, 1000 + index) for index in range(1, 61)]
    _wire_query(monkeypatch, parsed=parsed, rows=rows)

    from app.candidate_search import rerank as rerank_module

    monkeypatch.setattr(
        rerank_module,
        "rerank_application_ids",
        lambda **kw: [app_id for app_id in kw["application_ids"] if app_id % 2 == 0],
    )

    out = runner.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="banking domain",
        base_query=MagicMock(),
        rerank_enabled=True,
    )

    assert out.database_matches == 60
    assert out.deep_checked == 50
    assert out.qualified == 25
    assert out.application_ids == list(range(2, 51, 2))
    assert out.capped is True
    assert out.exhaustive is False
    assert out.warnings[-1].code == "verification_capped"
