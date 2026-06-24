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

from app.candidate_search import top_candidates as tc
from app.candidate_search.schemas import ParsedFilter, SearchOutput


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


def _run_search(parsed):
    return lambda **kw: SearchOutput(application_ids=[], parsed_filter=parsed, warnings=[])


def _no_grounding_client(monkeypatch):
    """Force the resolved grounding client to None (grounding unavailable)."""
    monkeypatch.setattr(
        "app.services.claude_client_resolver.get_metered_client", lambda **kw: None
    )


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
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

    client = _verdict_client({
        "HITAB": [
            _text_block("[[C1]] MET — led the core banking migration"),
            _text_block("x", citations=[_cite("led the core banking platform migration", 0, 38)]),
        ],
    })

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="banking domain",
        base_query=MagicMock(), evidence_client=client,
    )

    assert out["mode"] == "rediscovery"
    ids = [c["application_id"] for c in out["candidates"]]
    assert ids == [1, 2]  # A (met, prior 40) ABOVE B (missing, prior 95)
    assert out["candidates"][0]["criteria"][0]["status"] == "met"
    assert out["evidence_model"]  # grounded
    assert out["rescore_candidate_ids"] == [1, 2]  # the shortlist to re-score


def test_screen_caps_window_and_says_so(monkeypatch):
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_search", _run_search(ParsedFilter(soft_criteria=["banking domain"]))
    )
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=80, name="A", cv_text="banking platform work")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 5000)  # huge scored pool
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a])

    client = _verdict_client({"banking": [
        _text_block("[[C1]] MET — banking"),
        _text_block("b", citations=[_cite("banking platform work", 0, 20)])]})

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="banking domain",
        base_query=MagicMock(), evidence_client=client,
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
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

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
        base_query=MagicMock(), evidence_client=client,
    )
    ids = [c["application_id"] for c in out["candidates"]]
    assert ids == [1]  # B (over cap) hidden
    assert out["excluded"]["not_met_total"] == 1


def test_screen_grounding_unavailable_degrades(monkeypatch):
    """No grounding client → degrade to a ranked-by-fit list with an honest
    warning, rather than an empty or falsely-screened result."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod, "run_search", _run_search(ParsedFilter(soft_criteria=["kafka streaming"]))
    )
    _no_grounding_client(monkeypatch)

    a = _fake_app(1, taali=60, name="A")
    b = _fake_app(2, taali=90, name="B")
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="kafka streaming", base_query=MagicMock(),
    )
    assert [c["application_id"] for c in out["candidates"]] == [2, 1]  # by score
    assert out["evidence_model"] is None
    assert out["screened"] == 0
    assert any(w["code"] == "rerank_skipped" for w in out["warnings"])


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
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

    out = tc.screen_pool_against_requirement(
        db=MagicMock(), organization_id=1, requirement="python", base_query=MagicMock(),
    )
    assert [c["application_id"] for c in out["candidates"]] == [2, 1]
    assert any(w["code"] == "no_criteria" for w in out["warnings"])


# ---------------------------------------------------------------------------
# chat-tool wiring
# ---------------------------------------------------------------------------


def test_rediscovery_tool_registered():
    from app.taali_chat import tool_registry as tr

    names = {t["name"] for t in tr.TAALI_CHAT_TOOLS}
    assert "screen_pool_against_requirement" in names
    assert tr._HANDLER_BY_NAME.get("screen_pool_against_requirement") is not None
