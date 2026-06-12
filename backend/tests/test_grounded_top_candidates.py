"""Tests for the grounded "top N with X and Y" flow.

Covers the parts that must be correct for the answer to be *grounded*:
- citation-response parsing pairs each criterion with its verbatim quotes;
- a verdict with no quote is flagged ungrounded (the anti-hallucination gate);
- stored role-requirement evidence is reused only when it's a grounded match;
- the shortlist is ranked by score BEFORE truncation (the "top" fix).

All pure / mock-backed — no real Anthropic calls, no real DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.candidate_search import grounded_evidence as ge
from app.candidate_search import top_candidates as tc
from app.candidate_search.schemas import ParsedFilter, SearchOutput


# --------------------------------------------------------------------------
# citation parsing + grounding enforcement
# --------------------------------------------------------------------------


def _text_block(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


def _cite(quote, start=-1, end=-1, document_index=0):
    return SimpleNamespace(
        type="char_location",
        cited_text=quote,
        start_char_index=start,
        end_char_index=end,
        document_index=document_index,
    )


def test_parse_pairs_criteria_with_quotes_and_verdicts():
    criteria = ["banking domain experience", "kafka"]
    blocks = [
        _text_block("[[C1]] MET — "),
        _text_block(
            "Senior Data Engineer at JPMorgan",
            citations=[_cite("Senior Data Engineer, JPMorgan Chase (2019-2023)", 100, 145)],
        ),
        _text_block("\n[[C2]] MISSING — no evidence of Kafka."),
    ]

    verdicts = ge.parse_citation_response(blocks, criteria)

    assert verdicts[0].criterion == "banking domain experience"
    assert verdicts[0].status == "met"
    assert verdicts[0].grounded is True
    assert verdicts[0].source == "cv"
    assert verdicts[0].evidence[0].quote.startswith("Senior Data Engineer")
    assert verdicts[0].evidence[0].start_char == 100
    assert verdicts[0].evidence[0].source == "cv"

    assert verdicts[1].status == "missing"
    assert verdicts[1].grounded is False
    assert verdicts[1].evidence == []


def test_parse_met_without_citation_is_not_grounded():
    """The core anti-hallucination rule: a MET claim with no verbatim quote
    keeps its word but is flagged ungrounded so it never counts as satisfied."""
    verdicts = ge.parse_citation_response(
        [_text_block("[[C1]] MET — strong banking background")],
        ["banking domain experience"],
    )
    assert verdicts[0].status == "met"
    assert verdicts[0].grounded is False
    assert verdicts[0].source == "none"


def test_parse_partial_verdict_word():
    verdicts = ge.parse_citation_response(
        [_text_block(
            "[[C1]] PARTIAL — adjacent fintech work",
            citations=[_cite("Payments Engineer at a fintech", 5, 35)],
        )],
        ["banking domain experience"],
    )
    assert verdicts[0].status == "partially_met"
    assert verdicts[0].grounded is True


def test_extract_no_criteria_short_circuits():
    assert ge.extract_cv_evidence(
        cv_text="anything", criteria=[], client=None, organization_id=1, application_id=1
    ) == []


def test_extract_no_cv_text_returns_missing():
    out = ge.extract_cv_evidence(
        cv_text="   ", criteria=["banking"], client=None, organization_id=1, application_id=1
    )
    assert len(out) == 1
    assert out[0].status == "missing"
    assert out[0].grounded is False


def test_chunk_cv_breaks_runon_header_into_small_blocks():
    # A separator-laden CV header with no sentence punctuation — the plain-text
    # chunker would treat this as one giant citable blob. _chunk_cv breaks it up.
    cv = (
        "JANE DOE Senior Data Engineer | (306) 450-6919 | jane.doe.engineer@example.com | "
        "linkedin.com/in/janedoe | github.com/janedoe | Open to Abu Dhabi UAE and Riyadh KSA | "
        "PROFILE Senior data engineer with 12+ years of ETL and financial data delivery "
        "experience in a regulated financial institution including AWS Glue and PySpark"
    )
    chunks = ge._chunk_cv(cv)
    assert len(chunks) > 1
    assert all(len(c) <= ge.CV_CHUNK_MAX_LEN for c in chunks)
    assert any("financial institution" in c for c in chunks)
    # contact noise is separated out into its own block
    assert any("jane.doe.engineer@example.com" in c for c in chunks)


def test_chunk_cv_splits_on_lines_and_sentences():
    cv = "Line one.\n\nSkills: A · B · C\nWorked at Bank X. Built ETL pipelines."
    chunks = ge._chunk_cv(cv)
    assert "Line one." in chunks
    assert "Worked at Bank X." in chunks
    assert "Built ETL pipelines." in chunks


def test_extract_happy_path_through_fake_client():
    class _FakeClient:
        def __init__(self):
            self.calls = 0

            class _Messages:
                def create(inner_self, **kwargs):
                    self.calls += 1
                    # echo the document+criteria contract is honoured
                    assert kwargs["messages"][0]["content"][0]["citations"] == {"enabled": True}
                    return SimpleNamespace(
                        content=[
                            _text_block("[[C1]] MET — "),
                            _text_block(
                                "led the core banking migration",
                                citations=[_cite("Led the core banking platform migration", 0, 38)],
                            ),
                        ]
                    )

            self.messages = _Messages()

    client = _FakeClient()
    out = ge.extract_cv_evidence(
        cv_text="...led the core banking platform migration...",
        criteria=["banking domain experience"],
        client=client,
        organization_id=1,
        application_id=42,
    )
    assert client.calls == 1
    assert out[0].status == "met"
    assert out[0].grounded is True
    assert "banking" in out[0].evidence[0].quote.lower()


def test_parse_tags_quote_with_document_source():
    """A citation into document 1 (notes) is tagged source='notes'."""
    blocks = [
        _text_block("[[C1]] MET — "),
        _text_block(
            "states salary under 40k",
            citations=[_cite("Salary expectation: less than 40,000 AED", document_index=1)],
        ),
    ]
    verdicts = ge.parse_citation_response(blocks, ["salary cap"], doc_sources=["cv", "notes"])
    assert verdicts[0].evidence[0].source == "notes"
    assert verdicts[0].source == "notes"


def test_extract_grounds_salary_from_notes_when_cv_silent():
    """Salary lives in the notes, not the CV — grounding must use it and tag
    the quote source='notes' (the bug Sam hit)."""
    captured = {}

    class _FakeClient:
        def __init__(self):
            class _Messages:
                def create(inner_self, **kwargs):
                    captured["docs"] = [
                        b for b in kwargs["messages"][0]["content"] if b.get("type") == "document"
                    ]
                    return SimpleNamespace(
                        content=[
                            _text_block("[[C1]] PARTIAL — states <40k, above the 30k cap"),
                            _text_block(
                                "salary expectation under 40k",
                                citations=[
                                    _cite(
                                        "Salary expectation: less than 40,000 AED",
                                        document_index=1,
                                    )
                                ],
                            ),
                        ]
                    )

            self.messages = _Messages()

    out = ge.extract_cv_evidence(
        cv_text="AWS Glue ETL pipelines, PySpark, CDC.",  # no salary in the CV
        notes_text="Recruiter note: Salary expectation: less than 40,000 AED",
        criteria=["salary expectation less than 30000 AED"],
        client=_FakeClient(),
        organization_id=1,
        application_id=7,
    )
    # two documents were sent (CV + notes)
    assert len(captured["docs"]) == 2
    assert out[0].status == "partially_met"
    assert out[0].grounded is True
    assert out[0].evidence[0].source == "notes"


# --------------------------------------------------------------------------
# stored-evidence reuse matcher
# --------------------------------------------------------------------------


def test_reuse_grounded_positive():
    stored = [
        {
            "requirement": "Banking domain experience",
            "status": "met",
            "evidence_quotes": ["Vice President, Investment Banking at HSBC"],
            "evidence_start_char": 12,
            "evidence_end_char": 52,
        }
    ]
    v = tc._reuse_stored("banking domain experience", stored)
    assert v is not None
    assert v.status == "met"
    assert v.grounded is True
    assert v.source == "role_requirement"
    assert v.evidence[0].start_char == 12


def test_reuse_skips_positive_without_quotes():
    stored = [{"requirement": "Banking experience", "status": "met", "evidence_quotes": []}]
    assert tc._reuse_stored("banking", stored) is None


def test_reuse_missing_is_returned_ungrounded():
    stored = [{"requirement": "Kafka streaming", "status": "missing", "evidence_quotes": []}]
    v = tc._reuse_stored("kafka", stored)
    assert v is not None and v.status == "missing" and v.grounded is False


def test_reuse_no_token_overlap_returns_none():
    stored = [{"requirement": "Banking experience", "status": "met", "evidence_quotes": ["x"]}]
    assert tc._reuse_stored("kubernetes operations", stored) is None


def test_reuse_unknown_status_falls_through():
    stored = [{"requirement": "Banking experience", "status": "unknown", "evidence_quotes": ["x"]}]
    assert tc._reuse_stored("banking", stored) is None


# --------------------------------------------------------------------------
# criteria collection + spec echo
# --------------------------------------------------------------------------


def test_collect_criteria_dedupes_and_caps():
    parsed = ParsedFilter(
        soft_criteria=["banking", "Banking", "led a team"], keywords=["fintech"]
    )
    out = tc._collect_criteria(parsed)
    assert out == ["banking", "led a team", "fintech"]


def test_build_spec_echo_mentions_population_criteria_and_ranking():
    parsed = ParsedFilter(skills_all=["data engineer"])
    spec = tc._build_spec(parsed, query="top data engineers with banking", rank_by="taali",
                          criteria=["banking domain experience"])
    assert "data engineer" in spec["echo"]
    assert "banking domain experience" in spec["echo"]
    assert "Taali fit" in spec["echo"]
    assert spec["ranking_key"] == "taali"


# --------------------------------------------------------------------------
# rank-before-truncate (the "top isn't actually top" fix)
# --------------------------------------------------------------------------


def _fake_app(app_id, *, taali=None, name="Cand"):
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
        created_at=None, cv_match_details=None,
    )


def test_find_top_candidates_ranks_then_truncates(monkeypatch):
    # run_search returns ids in arbitrary order (as the real prefilter does).
    from app.candidate_search import runner as runner_mod

    seen_kwargs = {}

    def _fake_run_search(**kw):
        seen_kwargs.update(kw)
        return SearchOutput(
            application_ids=[1, 2, 3],
            parsed_filter=ParsedFilter(skills_all=["data engineer"]),  # no soft criteria
            warnings=[],
        )

    monkeypatch.setattr(runner_mod, "run_search", _fake_run_search)

    apps = [_fake_app(1, taali=50), _fake_app(2, taali=90), _fake_app(3, taali=70)]
    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.all.return_value = apps

    out = tc.find_top_candidates(
        db=db, organization_id=1, query="top data engineers", base_query=MagicMock(), limit=2
    )

    assert out["total_matched"] == 3
    assert out["shortlist_size"] == 2
    ranked_ids = [c["application_id"] for c in out["candidates"]]
    assert ranked_ids == [2, 3]  # 90, 70 — NOT DB order [1,2]
    assert out["candidates"][0]["rank"] == 1
    # the prefilter MUST be structural-only — qualitative criteria are grounded,
    # not ILIKE-matched into the pool (the "0 matched" bug).
    assert seen_kwargs.get("defer_qualitative") is True
    assert seen_kwargs.get("rerank_enabled") is False
    # no qualitative criteria → no grounding spend, no evidence model
    assert out["evidence_model"] is None
    assert out["candidates"][0]["criteria"] == []


def test_run_search_defer_qualitative_keeps_prefilter_structural(monkeypatch):
    """Regression for the "0 matched" bug: a qualitative phrase like "banking
    domain experience" must NOT be applied as a literal cv_text ILIKE in the
    prefilter (it phrase-matches ~zero CVs) — it is grounded downstream."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(runner_mod.cache_module, "get", lambda *a, **k: None)
    monkeypatch.setattr(runner_mod.cache_module, "set", lambda *a, **k: None)
    monkeypatch.setattr(
        runner_mod,
        "parse_nl_query",
        lambda *a, **k: ParsedFilter(
            soft_criteria=["banking domain experience"], keywords=["fintech"]
        ),
    )

    captured = {}

    def _fake_apply(base, parsed, *, soft_criteria_as_keywords):
        captured["soft_as_keywords"] = soft_criteria_as_keywords
        captured["sql_keywords"] = list(parsed.keywords)
        q = MagicMock()
        q.with_entities.return_value.all.return_value = []
        return q

    monkeypatch.setattr(runner_mod, "apply_parsed_filter", _fake_apply)

    out = runner_mod.run_search(
        db=MagicMock(),
        organization_id=1,
        nl_query="top with banking domain experience",
        base_query=MagicMock(),
        rerank_enabled=False,
        defer_qualitative=True,
    )

    # Neither soft criteria nor keywords hard-filter the pool in the SQL pass.
    assert captured["soft_as_keywords"] is False
    assert captured["sql_keywords"] == []
    # But the returned filter still carries them for the grounding step.
    assert out.parsed_filter.soft_criteria == ["banking domain experience"]
    assert out.parsed_filter.keywords == ["fintech"]
