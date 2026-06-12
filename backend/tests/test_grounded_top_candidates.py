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


def test_parse_not_met_constraint_cites_the_violating_value():
    """A stated-but-violating constraint (salary over cap) is NOT_MET and still
    carries the cited value — the fix for "shows missing though salary stated"."""
    blocks = [
        _text_block("[[C1]] NOT_MET — states 40,000 AED, above the 30k cap"),
        _text_block(
            "salary 40000",
            citations=[_cite("A: 40000", document_index=1)],
        ),
    ]
    verdicts = ge.parse_citation_response(
        blocks, ["salary expectation less than 30000 AED"], doc_sources=["cv", "notes"]
    )
    assert verdicts[0].status == "not_met"
    assert verdicts[0].grounded is True
    assert verdicts[0].evidence[0].source == "notes"
    assert "40000" in verdicts[0].evidence[0].quote


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


def test_collect_criteria_collapses_near_duplicate_phrasings():
    # The parser often emits a generic AND a specific phrasing of the same ask;
    # the generic (token-subset) is dropped so its evidence isn't shown twice.
    parsed = ParsedFilter(
        soft_criteria=["Western company", "Western enterprise company"], keywords=[]
    )
    assert tc._collect_criteria(parsed) == ["Western enterprise company"]


def test_collect_criteria_keeps_genuinely_distinct_criteria():
    parsed = ParsedFilter(
        soft_criteria=["banking domain", "real-time data"], keywords=[]
    )
    assert tc._collect_criteria(parsed) == ["banking domain", "real-time data"]


def test_collect_criteria_drops_count_and_filler_fragments():
    # "top 5" / "candidates" leak from the query text and must never become a
    # criterion everyone is judged MISSING on.
    parsed = ParsedFilter(
        soft_criteria=["banking domain"], keywords=["top 5", "candidates"]
    )
    assert tc._collect_criteria(parsed) == ["banking domain"]


def test_is_junk_criterion():
    for junk in ["top 5", "best 3", "candidates", "5 candidates", "first 5",
                 "show me 10 candidates", "the top 3 profiles"]:
        assert tc._is_junk_criterion(junk), junk
    for real in ["data engineer", "salary expectation <= 30000 AED",
                 "Western company", "5 years", "react", "led a team"]:
        assert not tc._is_junk_criterion(real), real


def test_collect_criteria_reassembles_split_salary_constraint():
    # The parser fragmented "salary less than 30000 AED" into a bare label and
    # a bare value, dropping the operator. We rebuild one clean cap line.
    parsed = ParsedFilter(
        soft_criteria=["salary", "30000 AED"],
        keywords=[],
        free_text="data engineers asking for salary less than 30000 AED",
    )
    out = tc._collect_criteria(parsed)
    assert out == ["salary <= 30000 AED"]
    assert tc._is_constraint(out[0])


def test_merge_fragments_takes_operator_from_value_then_query():
    # operator stated on the value fragment itself
    assert tc._merge_constraint_fragments(
        ["compensation", "under 40k"], None
    ) == ["compensation <= 40k"]
    # operator only in the query → ">=" for an "at least" phrasing
    assert tc._merge_constraint_fragments(
        ["salary", "30000 AED"], "salary at least 30000 AED"
    ) == ["salary >= 30000 AED"]
    # ambiguous → defaults to a cap
    assert tc._merge_constraint_fragments(["salary", "30000 AED"], None) == [
        "salary <= 30000 AED"
    ]


def test_merge_fragments_noop_without_a_pair():
    # already-clean single phrase is left alone
    assert tc._merge_constraint_fragments(
        ["salary expectation <= 30000 AED", "react"], "…"
    ) == ["salary expectation <= 30000 AED", "react"]
    # a value with no label sibling is not merged
    assert tc._merge_constraint_fragments(["react", "5 years"], "…") == [
        "react",
        "5 years",
    ]
    # a label with no value sibling is not merged
    assert tc._merge_constraint_fragments(["salary", "react"], "…") == [
        "salary",
        "react",
    ]


def test_years_experience_from_snapshot():
    app = SimpleNamespace(cv_match_details={"candidate_snapshot": {"years_experience": 7.5}})
    assert tc._years_experience(app) == 7.5
    app2 = SimpleNamespace(cv_match_details={"candidate_snapshot": {"years_experience": 8}})
    assert tc._years_experience(app2) == 8.0


def test_years_experience_absent_or_zero_is_none():
    for details in (
        None,
        {},
        {"candidate_snapshot": {}},
        {"candidate_snapshot": {"years_experience": 0}},
        {"candidate_snapshot": {"years_experience": None}},
        {"candidate_snapshot": "garbage"},
    ):
        assert tc._years_experience(SimpleNamespace(cv_match_details=details)) is None


def test_candidate_payload_includes_years_and_headline():
    app = _fake_app(1, taali=80)
    app.cv_match_details = {
        "summary": "Strong fit. Solid backend depth and ownership.",
        "candidate_snapshot": {"years_experience": 9},
    }
    out = tc._candidate_payload(app, rank=1, verdicts=[], has_criteria=False)
    assert out["candidate_years"] == 9.0
    assert out["candidate_headline"] == "Strong fit."


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
    assert out["shown"] == 2
    ranked_ids = [c["application_id"] for c in out["candidates"]]
    assert ranked_ids == [2, 3]  # 90, 70 — NOT DB order [1,2]
    assert out["candidates"][0]["rank"] == 1
    # the prefilter MUST be structural-only — qualitative criteria are grounded,
    # not ILIKE-matched into the pool (the "0 matched" bug).
    assert seen_kwargs.get("defer_qualitative") is True
    assert seen_kwargs.get("rerank_enabled") is False
    # no qualitative criteria → no grounding spend, no evidence model, no filter
    assert out["evidence_model"] is None
    assert out["excluded"]["not_met_total"] == 0
    assert out["candidates"][0]["criteria"] == []


def test_find_top_candidates_hides_not_met(monkeypatch):
    """A candidate who clearly FAILS a requirement (salary over cap → not_met)
    is hidden, not shown with a 'not met' label — the recruiter asked for a
    filter, not a list of failures."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "run_search",
        lambda **kw: SearchOutput(
            application_ids=[1, 2, 3],
            parsed_filter=ParsedFilter(soft_criteria=["salary under 30k AED"]),
            warnings=[],
        ),
    )
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a1 = _fake_app(1, taali=80, name="A"); a1.cv_text = "ok under cap"
    a2 = _fake_app(2, taali=95, name="B"); a2.cv_text = "OVERCAP salary 40k"
    a3 = _fake_app(3, taali=70, name="C"); a3.cv_text = "ok under cap"
    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.all.return_value = [a1, a2, a3]

    class _FakeClient:
        class _M:
            def create(self, **kw):
                docs = [b for b in kw["messages"][0]["content"] if b.get("type") == "document"]
                cvtext = " ".join(ch["text"] for d in docs for ch in d["source"]["content"])
                if "OVERCAP" in cvtext:
                    return SimpleNamespace(content=[
                        _text_block("[[C1]] NOT_MET — states 40k, above the cap"),
                        _text_block("40k", citations=[_cite("salary 40k", document_index=0)]),
                    ])
                return SimpleNamespace(content=[
                    _text_block("[[C1]] MET — under cap"),
                    _text_block("ok", citations=[_cite("ok under cap", document_index=0)]),
                ])

        messages = _M()

    out = tc.find_top_candidates(
        db=db, organization_id=1, query="top under 30k", base_query=MagicMock(),
        limit=2, evidence_client=_FakeClient(),
    )
    ids = [c["application_id"] for c in out["candidates"]]
    assert 2 not in ids  # B (over cap) is hidden
    assert ids == [1, 3]  # A (80) then C (70), ranked by fit among those who pass
    assert out["shown"] == 2
    assert out["excluded"]["not_met_total"] == 1
    assert out["excluded"]["by_criterion"][0]["count"] == 1


def test_find_top_candidates_ranks_clear_signal_above_missing(monkeypatch):
    """Among candidates who pass the filter, those with clear evidence (met)
    rank ABOVE those whose data is unknown/missing — even at lower fit."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "run_search",
        lambda **kw: SearchOutput(
            application_ids=[1, 2],
            parsed_filter=ParsedFilter(soft_criteria=["led a large team"]),
            warnings=[],
        ),
    )
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=70, name="A"); a.cv_text = "HASIT led a 40-person team"
    b = _fake_app(2, taali=95, name="B"); b.cv_text = "nothing relevant here"
    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.all.return_value = [a, b]

    class _FakeClient:
        class _M:
            def create(self, **kw):
                docs = [x for x in kw["messages"][0]["content"] if x.get("type") == "document"]
                cv = " ".join(ch["text"] for d in docs for ch in d["source"]["content"])
                if "HASIT" in cv:
                    return SimpleNamespace(content=[
                        _text_block("[[C1]] MET — led a 40-person team"),
                        _text_block("team", citations=[_cite("led a 40-person team", document_index=0)]),
                    ])
                return SimpleNamespace(content=[_text_block("[[C1]] MISSING — no evidence")])

        messages = _M()

    out = tc.find_top_candidates(
        db=db, organization_id=1, query="best who led a large team",
        base_query=MagicMock(), limit=2, evidence_client=_FakeClient(),
    )
    ids = [c["application_id"] for c in out["candidates"]]
    # A (met, fit 70) ranks ABOVE B (missing, fit 95) — clear signal first.
    assert ids == [1, 2]
    assert out["candidates"][0]["criteria"][0]["status"] == "met"


def test_report_scrub_drops_contact_pii():
    from app.domains.top_reports.service import _scrub

    snap = {"candidates": [{"candidate_name": "X", "candidate_email": "x@y.com", "taali_score": 90}]}
    out = _scrub(snap)
    assert "candidate_email" not in out["candidates"][0]
    assert out["candidates"][0]["candidate_name"] == "X"
    # original is not mutated
    assert "candidate_email" in snap["candidates"][0]


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


def test_candidate_blurb_skips_cover_note_and_synthesises():
    cand = SimpleNamespace(
        summary="Dear Hiring Manager, I hope this message finds you well...",
        headline="Sr. MLOps and AiOps",
        cv_sections={"summary": "", "experience": [{"title": "SRE", "company": "McKinsey"}],
                     "skills": ["AWS", "Azure", "Kubernetes"]},
        skills=["AWS", "Azure"],
        experience_entries=[],
    )
    b = tc._candidate_blurb(cand)
    assert "Dear Hiring Manager" not in (b or "")
    assert "Sr. MLOps" in b and "McKinsey" in b and "AWS" in b


def test_candidate_blurb_prefers_real_cv_summary():
    cand = SimpleNamespace(
        summary="Hi, I came across the role...",
        headline="",
        cv_sections={"summary": "Seasoned DevOps engineer with 10 years scaling cloud platforms for fintechs."},
        skills=[], experience_entries=[],
    )
    assert tc._candidate_blurb(cand).startswith("Seasoned DevOps")


def test_scoring_summary_splits_headline_and_body():
    app = SimpleNamespace(cv_match_details={
        "summary": "Partial fit: strong DevOps depth but gaps in banking. Candidate has 15 years cloud experience and led teams."
    })
    headline, body = tc._scoring_summary(app)
    assert headline == "Partial fit: strong DevOps depth but gaps in banking."
    assert body.startswith("Candidate has 15 years")


def test_scoring_summary_empty_returns_none():
    assert tc._scoring_summary(SimpleNamespace(cv_match_details={})) == (None, None)
    assert tc._scoring_summary(SimpleNamespace(cv_match_details={"summary": ""})) == (None, None)


def test_is_constraint_classifies():
    assert tc._is_constraint("salary expectation less than 30000 AED")
    assert tc._is_constraint("30000 AED")
    assert tc._is_constraint("at least 5 years experience")
    assert tc._is_constraint("based in UAE")
    assert not tc._is_constraint("Western company")
    assert not tc._is_constraint("Western enterprises")
    assert not tc._is_constraint("banking domain experience")


def test_find_top_candidates_keeps_failed_preference(monkeypatch):
    """A failed PREFERENCE (not a Western company) must NOT hide the candidate —
    only a failed hard constraint does. The candidate is shown, ranked lower."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_search", lambda **kw: SearchOutput(
        application_ids=[1, 2],
        parsed_filter=ParsedFilter(soft_criteria=["Western company"]),
        warnings=[]))
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=80, name="A"); a.cv_text = "WESTERN worked at McKinsey"
    b = _fake_app(2, taali=95, name="B"); b.cv_text = "worked at Emirates NBD Dubai"
    db = MagicMock()
    db.query.return_value.options.return_value.filter.return_value.all.return_value = [a, b]

    class _FakeClient:
        class _M:
            def create(self, **kw):
                docs = [x for x in kw["messages"][0]["content"] if x.get("type") == "document"]
                cv = " ".join(ch["text"] for d in docs for ch in d["source"]["content"])
                if "WESTERN" in cv:
                    return SimpleNamespace(content=[_text_block("[[C1]] MET — McKinsey"),
                                                   _text_block("m", citations=[_cite("McKinsey", document_index=0)])])
                return SimpleNamespace(content=[_text_block("[[C1]] NOT_MET — Emirates NBD is not Western"),
                                               _text_block("e", citations=[_cite("Emirates NBD", document_index=0)])])
        messages = _M()

    out = tc.find_top_candidates(db=db, organization_id=1, query="top with Western company",
                                 base_query=MagicMock(), limit=5, evidence_client=_FakeClient())
    ids = [c["application_id"] for c in out["candidates"]]
    assert ids == [1, 2]  # B (not_met Western) shown, ranked below A (met)
    assert out["excluded"]["not_met_total"] == 0
