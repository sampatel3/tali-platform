"""Tests for talent-pool rediscovery — screen the scored pool against a NEW
requirement (``top_candidates.screen_pool_against_requirement``).

The inverse of grounded top-N: cast a new requirement across the scored history
and rank by fit to THAT requirement (grounded met/partial), not the stale role
score. A bounded window is deep-checked via the (cache-backed) Citations pass.
Covers the properties that make it correct + cost-safe:
- ranks by new-requirement fit, NOT the stale score;
- caps the grounding window and discloses it (`screened` / `capped`);
- hard-constraint failures hide; grounding-unavailable degrades honestly;
- a re-score shortlist is handed back for the opt-in Sonnet step.

All pure / mock-backed — no real Anthropic calls, no real DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.candidate_search import grounded_evidence as ge
from app.candidate_search import top_candidates as tc
from app.candidate_search.schemas import ParsedFilter, SearchOutput


@pytest.fixture(autouse=True)
def _routing_seam(monkeypatch):
    """Keep these search-behavior tests independent of durable router storage."""

    monkeypatch.setattr(ge, "_redis", lambda: None)

    class _Execution:
        selected_model_id = "test-grounding-model"
        last_attempt_model_id = "test-grounding-model"
        decision = SimpleNamespace(
            behavior_fingerprint="test-grounding-behavior",
        )

        def finish_workflow(self, *, succeeded: bool) -> None:
            self.succeeded = succeeded

    monkeypatch.setattr(ge, "prepare_route", lambda *_args, **_kwargs: _Execution())


def _text_block(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


def _cite(quote, start=-1, end=-1, document_index=0):
    return SimpleNamespace(
        type="char_location", cited_text=quote,
        start_char_index=start, end_char_index=end, document_index=document_index,
    )


def _fake_app(app_id, *, taali=None, name="Cand", cv_text=None):
    cand = SimpleNamespace(
        full_name=name, email=f"{name}@x.com", position="Engineer",
        location_city="Dubai", location_country="UAE",
        cv_text=None, cv_sections=None, skills=[],
    )
    role = SimpleNamespace(name="Data Engineer")
    return SimpleNamespace(
        id=app_id, candidate_id=app_id, role_id=10, candidate=cand, role=role,
        pipeline_stage="applied", application_outcome="open",
        pipeline_stage_updated_at=None, workable_stage=None, external_stage_normalized=None,
        taali_score_cache_100=taali, pre_screen_score_100=None, rank_score=None,
        cv_match_score=None, workable_score=None, auto_reject_state=None,
        created_at=None, cv_match_details=None, cv_text=cv_text,
    )


def _run_search(parsed, *, ids=None, database_matches=None):
    application_ids = [1, 2] if ids is None else list(ids)
    return lambda **kw: SearchOutput(
        application_ids=application_ids,
        parsed_filter=parsed,
        warnings=[],
        database_matches=database_matches,
    )


def _route_factory(client):
    return lambda _execution: client


def _no_grounding_client(monkeypatch):
    """Make the central routed transport unavailable after route planning."""

    def _unavailable(_execution):
        raise RuntimeError("transport unavailable")

    monkeypatch.setattr(ge, "routed_messages_client", _unavailable)


def _verdict_client(marker_to_blocks):
    """Fake Anthropic client whose grounding verdict depends on a marker in the
    candidate's CV text — so different candidates get different verdicts. Any CV
    without a known marker comes back MISSING."""
    class _Client:
        class _M:
            def create(self, **kw):
                docs = [b for b in kw["messages"][0]["content"] if b.get("type") == "document"]
                cv = " ".join(ch["text"] for d in docs for ch in d["source"]["content"])
                for marker, blocks in marker_to_blocks.items():
                    if marker in cv:
                        return SimpleNamespace(content=blocks)
                return SimpleNamespace(content=[_text_block("[[C1]] MISSING — no evidence")])
        messages = _M()
    return _Client()


def test_screen_ranks_by_new_requirement_fit_not_stale_score(monkeypatch):
    """The crux of rediscovery: a low-prior-score candidate who clearly MEETS the
    new requirement ranks ABOVE a high-prior-score candidate it isn't evidenced
    for — fit to THIS requirement, not the stale role score."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_search", _run_search(ParsedFilter(soft_criteria=["banking domain"]))
    )
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=40, name="A", cv_text="HITAB led the core banking platform migration")
    b = _fake_app(2, taali=95, name="B", cv_text="nothing relevant here")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates_by_ids", lambda bq, ids: [a, b])

    client = _verdict_client({
        "HITAB": [
            _text_block("[[C1]] MET — led the core banking migration"),
            _text_block("x", citations=[_cite("led the core banking platform migration", 0, 38)]),
        ],
    })

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="banking domain",
        base_query=MagicMock(), evidence_route_client_factory=_route_factory(client),
        deep_verify=True,
    )

    assert out["mode"] == "rediscovery"
    ids = [c["application_id"] for c in out["candidates"]]
    assert ids == [1]  # B is missing required evidence, so its stale score cannot rescue it.
    assert out["candidates"][0]["criteria"][0]["status"] == "met"
    assert out["evidence_model"]  # grounded
    assert out["rescore_candidate_ids"] == [1]  # only verified matches are re-scored
    assert out["excluded"]["missing_total"] == 1


def test_screen_caps_window_and_says_so(monkeypatch):
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "run_search",
        _run_search(ParsedFilter(soft_criteria=["banking domain"]), database_matches=5000),
    )
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=80, name="A", cv_text="banking platform work")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 5000)  # huge scored pool
    monkeypatch.setattr(tc, "_load_candidates_by_ids", lambda bq, ids: [a])

    client = _verdict_client({"banking": [
        _text_block("[[C1]] MET — banking"),
        _text_block("b", citations=[_cite("banking platform work", 0, 20)])]})

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="banking domain",
        base_query=MagicMock(), evidence_route_client_factory=_route_factory(client),
        deep_verify=True,
    )
    assert out["capped"] is True
    assert out["screened"] == 1
    assert out["screen_cap"] == tc.SCREEN_GROUND_WINDOW
    assert out["total_matched"] == 5000


def test_screen_hides_failed_hard_constraint(monkeypatch):
    """A grounded NOT_MET on a hard constraint (salary over cap) hides the
    candidate — same filtering rule as find_top_candidates."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_search", _run_search(ParsedFilter(soft_criteria=["salary under 30k AED"]))
    )
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=80, name="A", cv_text="ok under cap")
    b = _fake_app(2, taali=95, name="B", cv_text="OVERCAP salary 40k")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates_by_ids", lambda bq, ids: [a, b])

    client = _verdict_client({
        "OVERCAP": [
            _text_block("[[C1]] NOT_MET — states 40k, above the cap"),
            _text_block("40k", citations=[_cite("salary 40k", document_index=0)])],
        "ok under cap": [
            _text_block("[[C1]] MET — under cap"),
            _text_block("ok", citations=[_cite("ok under cap", document_index=0)])],
    })

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="salary under 30k AED",
        base_query=MagicMock(), evidence_route_client_factory=_route_factory(client),
        deep_verify=True,
    )
    ids = [c["application_id"] for c in out["candidates"]]
    assert ids == [1]  # B (over cap) hidden
    assert out["excluded"]["not_met_total"] == 1


def test_screen_grounding_unavailable_fails_closed_for_required_evidence(monkeypatch):
    """No grounding client means an unhedged qualitative requirement cannot be
    verified, so the tool returns no candidates instead of false matches."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_search", _run_search(ParsedFilter(soft_criteria=["kafka streaming"]))
    )
    _no_grounding_client(monkeypatch)

    a = _fake_app(1, taali=60, name="A", cv_text="Kafka streaming")
    b = _fake_app(2, taali=90, name="B", cv_text="Kafka streaming")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates_by_ids", lambda bq, ids: [a, b])

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="kafka streaming", base_query=MagicMock(),
        deep_verify=True,
    )
    assert out["candidates"] == []
    assert out["evidence_model"] == "test-grounding-model"
    assert out["screened"] == 2
    assert out["evidence_succeeded"] == 0
    assert out["search_status"] == "no_verified_matches"
    assert any(w["code"] == "evidence_incomplete" for w in out["warnings"])


def test_screen_no_criteria_ranks_by_recall(monkeypatch):
    """A requirement with no qualitative criteria still returns a useful ranked
    list (by fit) with a warning rather than an empty result."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_search", _run_search(ParsedFilter(skills_all=["python"]))  # structural only
    )
    _no_grounding_client(monkeypatch)

    a = _fake_app(1, taali=50, name="A")
    b = _fake_app(2, taali=90, name="B")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates_by_ids", lambda bq, ids: [a, b])

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="python", base_query=MagicMock(),
        deep_verify=True,
    )
    assert [c["application_id"] for c in out["candidates"]] == [1, 2]
    assert any(w["code"] == "no_criteria" for w in out["warnings"])


# ---------------------------------------------------------------------------
# chat-tool wiring
# ---------------------------------------------------------------------------


def test_rediscovery_tool_registered():
    from app.taali_chat import tool_registry as tr

    names = {t["name"] for t in tr.TAALI_CHAT_TOOLS}
    assert "screen_pool_against_requirement" in names
    assert tr._HANDLER_BY_NAME.get("screen_pool_against_requirement") is not None
