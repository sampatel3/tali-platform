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


def test_grounding_deadline_fits_inside_chat_stream_idle_budget():
    assert tc.GROUND_BATCH_DEADLINE_S < 30


@pytest.fixture(autouse=True)
def _no_grounding_cache(monkeypatch):
    """Disable the Redis-backed grounding cache by default so tests are
    deterministic and never touch a real Redis. The cache-specific tests install
    their own fake handle via monkeypatch."""
    monkeypatch.setattr(ge, "_redis", lambda: None)


class _FakeRedis:
    """Minimal in-memory stand-in for the grounding cache (get / setex)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, _ttl, value):
        self.store[key] = value


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
# caching + retry + no-fallback (the Saurabh fix)
# --------------------------------------------------------------------------


def _met_response():
    return SimpleNamespace(content=[
        _text_block("[[C1]] MET — "),
        _text_block(
            "led the core banking migration",
            citations=[_cite("Led the core banking platform migration", 0, 38)],
        ),
    ])


class _CountingClient:
    """Fake Anthropic client whose `create` is driven by a supplied behaviour."""

    def __init__(self, behaviour):
        self.calls = 0
        self.requests = []
        outer = self

        class _M:
            def create(self, **kwargs):
                outer.calls += 1
                outer.requests.append(kwargs)
                return behaviour(outer.calls)

        self.messages = _M()


def test_cache_grounds_once_then_reuses(monkeypatch):
    """A second identical query reads the cached verdict — no second API call."""
    fake = _FakeRedis()
    monkeypatch.setattr(ge, "_redis", lambda: fake)
    client = _CountingClient(lambda _n: _met_response())

    kw = dict(
        cv_text="...led the core banking platform migration...",
        criteria=["banking domain experience"],
        client=client,
        organization_id=1,
        application_id=42,
    )
    first = ge.extract_cv_evidence(**kw)
    second = ge.extract_cv_evidence(**kw)

    assert client.calls == 1  # second served from cache
    assert first[0].status == second[0].status == "met"
    assert second[0].grounded is True
    assert fake.store  # the met verdict was cached


def test_failed_call_yields_error_not_missing(monkeypatch):
    """No fallback: a call that fails after retries becomes `error` (UI shows
    'couldn't verify'), never a fabricated `missing`, and is never cached."""
    fake = _FakeRedis()
    monkeypatch.setattr(ge, "_redis", lambda: fake)
    monkeypatch.setattr(ge.time, "sleep", lambda *_a, **_k: None)

    class _Boom(Exception):
        pass

    monkeypatch.setattr(ge, "_TRANSIENT_ERRORS", (_Boom,))

    def _always_fail(_n):
        raise _Boom("overloaded")

    client = _CountingClient(_always_fail)
    out = ge.extract_cv_evidence(
        cv_text="banking platform work",
        criteria=["banking domain experience"],
        client=client,
        organization_id=1,
        application_id=7,
    )
    assert client.calls == ge.GROUNDING_MAX_ATTEMPTS  # retried, didn't bail early
    assert out[0].status == "error"  # NOT "missing"
    assert out[0].grounded is False
    assert fake.store == {}  # errors are never cached


def test_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(ge.time, "sleep", lambda *_a, **_k: None)

    class _Boom(Exception):
        pass

    monkeypatch.setattr(ge, "_TRANSIENT_ERRORS", (_Boom,))

    def _fail_twice(n):
        if n < 3:
            raise _Boom("429")
        return _met_response()

    client = _CountingClient(_fail_twice)
    out = ge.extract_cv_evidence(
        cv_text="core banking platform",
        criteria=["banking domain experience"],
        client=client,
        organization_id=1,
        application_id=9,
    )
    assert client.calls == 3
    assert out[0].status == "met" and out[0].grounded is True
    reservation_refs = {
        call["metering"]["credit_reservation"]["external_ref"]
        for call in client.requests
    }
    assert len(reservation_refs) == 3


def test_grounding_threads_role_into_each_admitted_call(monkeypatch):
    captured = []

    def _admit(**kwargs):
        captured.append(kwargs)
        return {
            "feature": "candidate_grounding",
            "organization_id": kwargs["organization_id"],
            "role_id": kwargs["role_id"],
            "credit_reservation": {
                "organization_id": kwargs["organization_id"],
                "feature": "candidate_grounding",
                "amount": 5_000,
                "external_ref": "test-grounding-hold",
                "live": False,
            },
        }

    monkeypatch.setattr(ge, "admitted_search_metering", _admit)
    client = _CountingClient(lambda _n: _met_response())

    out = ge.extract_cv_evidence(
        cv_text="core banking platform",
        criteria=["banking domain experience"],
        client=client,
        organization_id=1,
        role_id=88,
        application_id=9,
    )

    assert out[0].status == "met"
    assert captured[0]["role_id"] == 88
    assert client.requests[0]["metering"]["role_id"] == 88


def test_non_transient_error_is_not_retried(monkeypatch):
    """A 400-class error won't be fixed by retrying — fail fast to a single call."""
    monkeypatch.setattr(ge, "_TRANSIENT_ERRORS", ())  # nothing counts as transient

    def _bad_request(_n):
        raise ValueError("malformed document")

    client = _CountingClient(_bad_request)
    out = ge.extract_cv_evidence(
        cv_text="x banking",
        criteria=["banking domain experience"],
        client=client,
        organization_id=1,
        application_id=1,
    )
    assert client.calls == 1
    assert out[0].status == "error"


def test_find_top_candidates_shows_error_not_hidden(monkeypatch):
    """Regression for the Saurabh bug: a grounding failure marks the criteria
    `error` and the candidate is STILL shown — not hidden, not blanked as
    'missing' (which read as a damning evidence gap)."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_search", lambda **kw: SearchOutput(
        application_ids=[1],
        parsed_filter=ParsedFilter(soft_criteria=["salary under 30k AED"]),
        warnings=[]))
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)
    monkeypatch.setattr(ge.time, "sleep", lambda *_a, **_k: None)

    class _Boom(Exception):
        pass

    monkeypatch.setattr(ge, "_TRANSIENT_ERRORS", (_Boom,))

    a = _fake_app(1, taali=80, name="A")
    a.cv_text = "some cv text"
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 1)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a])

    class _FailClient:
        class _M:
            def create(self, **kw):
                raise _Boom("overloaded")

        messages = _M()

    out = tc.find_top_candidates(
        db=MagicMock(), organization_id=1, query="top under 30k",
        base_query=MagicMock(), limit=5, evidence_client=_FailClient(),
    )
    ids = [c["application_id"] for c in out["candidates"]]
    assert ids == [1]  # not hidden by the failure
    assert out["candidates"][0]["criteria"][0]["status"] == "error"
    assert out["excluded"]["not_met_total"] == 0
    assert out["deep_checked"] == 1
    assert out["evidence_succeeded"] == 0
    assert any(w["code"] == "evidence_incomplete" for w in out["warnings"])


def test_qualified_is_unknown_when_requested_criteria_are_capped(monkeypatch):
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(tc, "MAX_CRITERIA", 1)
    monkeypatch.setattr(
        runner_mod,
        "run_search",
        lambda **_kw: SearchOutput(
            application_ids=[1],
            parsed_filter=ParsedFilter(
                soft_criteria=["banking experience", "team leadership"]
            ),
            warnings=[],
        ),
    )
    app = _fake_app(1, taali=80, name="A")
    app.cv_text = "Led banking platform delivery."
    monkeypatch.setattr(tc, "_pool_count", lambda _base: 1)
    monkeypatch.setattr(tc, "_load_candidates", lambda _base, **_kw: [app])
    monkeypatch.setattr(tc, "_notes_text", lambda _app: None)

    class _Client:
        class _Messages:
            def create(self, **_kw):
                return SimpleNamespace(
                    content=[
                        _text_block("[[C1]] MET — banking platform delivery"),
                        _text_block(
                            "banking",
                            citations=[_cite("Led banking platform delivery.")],
                        ),
                    ]
                )

        messages = _Messages()

    out = tc.find_top_candidates(
        db=MagicMock(),
        organization_id=1,
        query="banking experience and team leadership",
        base_query=MagicMock(),
        limit=5,
        evidence_client=_Client(),
    )

    assert out["criteria_checked"] == ["banking experience"]
    assert out["criteria_unchecked"] == ["team leadership"]
    assert out["qualified"] is None
    assert any(w["code"] == "criteria_capped" for w in out["warnings"])


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


def test_criteria_over_cap_are_reported_not_silently_dropped():
    requested = [f"criterion {index}" for index in range(tc.MAX_CRITERIA + 2)]
    parsed = ParsedFilter(soft_criteria=requested)

    all_criteria, checked, unchecked = tc._criteria_coverage(parsed)

    assert all_criteria == requested
    assert checked == requested[: tc.MAX_CRITERIA]
    assert unchecked == requested[tc.MAX_CRITERIA :]


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
    assert spec["criteria"] == [{
        "text": "banking domain experience",
        "kind": "qualitative",
        "requires_grounding": True,
    }]
    assert "grounded" not in spec["criteria"][0]


def test_short_label_truncates_long_criterion_on_word_boundary():
    long = ("preference for experience working at a company in Germany or UK or "
            "United States or France or Europe")
    out = tc._short_label(long)
    assert out.endswith("…")
    assert len(out) <= tc._ECHO_CRITERION_MAX + 1
    assert not out[:-1].endswith(" ")  # broke at a word boundary, no trailing space
    # short criteria are left untouched
    assert tc._short_label("salary expectation <= 30000 AED") == "salary expectation <= 30000 AED"


def test_build_spec_echo_shortens_but_keeps_full_criterion_text():
    longc = ("preference for experience working at a company in Germany or UK or "
             "United States or France or Europe")
    spec = tc._build_spec(ParsedFilter(), query="q", rank_by="taali", criteria=[longc])
    assert "…" in spec["echo"]                     # echo is tightened for scanning
    assert spec["criteria"][0]["text"] == longc    # full text preserved for the rows


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
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 3)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: list(apps))

    out = tc.find_top_candidates(
        db=db,
        organization_id=1,
        role_id=44,
        query="top data engineers",
        base_query=MagicMock(),
        limit=2,
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
    assert seen_kwargs.get("role_id") == 44
    # no qualitative criteria → no grounding spend, no evidence model, no filter
    assert out["evidence_model"] is None
    assert out["excluded"]["not_met_total"] == 0
    assert out["candidates"][0]["criteria"] == []


def test_bare_role_top_n_reuses_stored_scorecard_evidence(monkeypatch):
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "run_search",
        lambda **_kw: SearchOutput(
            application_ids=[1],
            parsed_filter=ParsedFilter(),
            warnings=[],
        ),
    )
    app = _fake_app(1, taali=88, name="Ada")
    app.cv_match_details = {
        "requirements_assessment": [
            {
                "requirement": "Own production platform delivery",
                "priority": "must_have",
                "status": "met",
                "evidence_quotes": ["Owned the platform migration into production."],
                "reasoning": "Direct ownership evidence.",
            },
            {
                "requirement": "Stakeholder leadership",
                "priority": "strong_preference",
                "status": "partially_met",
                "evidence_quotes": ["Presented the roadmap to product and operations."],
            },
        ],
    }
    monkeypatch.setattr(tc, "_pool_count", lambda _base: 1)
    monkeypatch.setattr(tc, "_load_candidates", lambda _base, **_kw: [app])

    out = tc.find_top_candidates(
        db=MagicMock(),
        organization_id=1,
        role_id=44,
        query="candidates",
        base_query=MagicMock(),
        limit=5,
    )

    assert out["evidence_basis"] == "stored_role_requirements"
    assert out["evidence_reused"] == 1
    assert out["deep_checked"] == 0  # reused evidence, no fresh model spend
    assert out["qualified"] is None
    criteria = out["candidates"][0]["criteria"]
    assert [row["criterion"] for row in criteria] == [
        "Own production platform delivery",
        "Stakeholder leadership",
    ]
    assert criteria[0]["grounded"] is True
    assert criteria[0]["source"] == "role_requirement"
    assert criteria[0]["evidence"][0]["quote"] == (
        "Owned the platform migration into production."
    )


def test_bare_role_evidence_prioritizes_hard_constraints():
    app = _fake_app(1, taali=88, name="Ada")
    app.cv_match_details = {
        "requirements_assessment": [
            {
                "requirement": "GraphQL familiarity",
                "priority": "nice_to_have",
                "status": "met",
                "evidence_quotes": ["Used GraphQL on an internal dashboard."],
            },
            {
                "requirement": "Work authorization",
                "priority": "constraint",
                "status": "met",
                "evidence_quotes": ["Authorized to work in the UK."],
            },
            {
                "requirement": "Production ownership",
                "priority": "must_have",
                "status": "met",
                "evidence_quotes": ["Owned the production release process."],
            },
        ]
    }

    verdicts = tc._stored_role_requirement_verdicts(app, limit=2)

    assert [row.criterion for row in verdicts] == [
        "Work authorization",
        "Production ownership",
    ]


def test_find_top_candidates_does_not_pad_zero_structural_matches(monkeypatch):
    """A failed role/skill prefilter must not turn into unrelated top scorers."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "run_search",
        lambda **kw: SearchOutput(
            application_ids=[],
            parsed_filter=ParsedFilter(
                skills_any=["project manager", "scrum master"],
                soft_criteria=["Treasury experience", "Data experience"],
            ),
            warnings=[],
        ),
    )
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 1_461)

    def _must_not_load(*_args, **_kwargs):
        raise AssertionError("unrelated candidates must not be loaded")

    monkeypatch.setattr(tc, "_load_candidates", _must_not_load)

    out = tc.find_top_candidates(
        db=MagicMock(),
        organization_id=1,
        query="project manager or scrum master with Treasury and Data",
        base_query=MagicMock(),
        limit=10,
        evidence_client=MagicMock(),
    )

    assert out["total_matched"] == 0
    assert out["pool_size"] == 1_461
    assert out["structural_matches"] == 0
    assert out["evaluated"] == 0
    assert out["shown"] == 0
    assert out["candidates"] == []
    assert out["warnings"][-1]["code"] == "no_structural_matches"


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
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 3)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a1, a2, a3])

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
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

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


def test_report_scrub_drops_structured_and_embedded_contact_pii():
    from app.domains.top_reports.service import _scrub

    snap = {
        "created_at": "2026-07-15T08:30:00Z",
        "candidates": [{
            "candidate_name": "X",
            "candidate_email": "x@y.com",
            "candidate_phone": "+971 50 123 4567",
            "candidate_summary": "Email x@y.com or call +971 (50) 123-4567.",
            "criteria": [{
                "evidence": [{
                    "quote": "Contact: x@y.com; token sk-testtoken1234567890; worked 2018-2024.",
                }],
            }],
            "taali_score": 90,
        }],
    }
    out = _scrub(snap)
    assert "candidate_email" not in out["candidates"][0]
    assert "candidate_phone" not in out["candidates"][0]
    assert out["candidates"][0]["candidate_name"] == "X"
    assert out["candidates"][0]["candidate_summary"] == (
        "Email [email redacted] or call [phone redacted]."
    )
    quote = out["candidates"][0]["criteria"][0]["evidence"][0]["quote"]
    assert "x@y.com" not in quote
    assert "sk-testtoken1234567890" not in quote
    assert "2018-2024" in quote
    assert out["created_at"] == "2026-07-15T08:30:00Z"
    # original is not mutated
    assert "candidate_email" in snap["candidates"][0]
    assert "x@y.com" in snap["candidates"][0]["candidate_summary"]


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
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

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


def test_structural_match_is_a_strict_population_filter(monkeypatch):
    """A requested skill/title is a hard population constraint; unrelated
    high scorers must never pad the result."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_search", lambda **kw: SearchOutput(
        application_ids=[3],  # structural filter matched ONLY app 3
        parsed_filter=ParsedFilter(skills_all=["react"], soft_criteria=["fintech domain"]),
        warnings=[]))
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 3)

    a1 = _fake_app(1, taali=90, name="A"); a1.cv_text = "fintech platform work"
    a2 = _fake_app(2, taali=85, name="B"); a2.cv_text = "fintech and banking"
    a3 = _fake_app(3, taali=40, name="C"); a3.cv_text = "fintech startup"
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a3])

    class _FakeClient:
        class _M:
            def create(self, **kw):
                return SimpleNamespace(content=[
                    _text_block("[[C1]] MET — fintech"),
                    _text_block("f", citations=[_cite("fintech", document_index=0)]),
                ])
        messages = _M()

    out = tc.find_top_candidates(
        db=MagicMock(), organization_id=1, query="top react engineers in fintech",
        base_query=MagicMock(), limit=3, evidence_client=_FakeClient())
    ids = {c["application_id"] for c in out["candidates"]}
    assert ids == {3}
    assert out["total_matched"] == 1
    assert out["pool_size"] == 3
    assert out["structural_matches"] == 1


def test_currency_cap_verdict_recomputed_from_cited_value():
    from app.candidate_search.grounded_evidence import CriterionVerdict, Evidence

    # The Seema case: 18,000 vs a 30,000 cap was mislabelled PARTIAL → MET.
    v = CriterionVerdict(criterion="salary expectation <= 30000 AED", status="partially_met",
                         grounded=True, evidence=[Evidence(quote="A: 18000", source="notes")])
    tc._recompute_currency_cap_verdict(v)
    assert v.status == "met"
    # 35,000 vs 30,000 (within 1.25x) → PARTIAL even if the model said met
    v2 = CriterionVerdict(criterion="salary <= 30000 AED", status="met", grounded=True,
                          evidence=[Evidence(quote="A: 35,000 AED", source="notes")])
    tc._recompute_currency_cap_verdict(v2)
    assert v2.status == "partially_met"
    # 45,000 (beyond 1.25x) → NOT_MET
    v3 = CriterionVerdict(criterion="salary expectation <= 30000 AED", status="met", grounded=True,
                          evidence=[Evidence(quote="states 45000 monthly", source="notes")])
    tc._recompute_currency_cap_verdict(v3)
    assert v3.status == "not_met"
    # "27k" shorthand under cap → MET
    v4 = CriterionVerdict(criterion="salary <= 30000 AED", status="partially_met", grounded=True,
                          evidence=[Evidence(quote="A: 27k AED", source="notes")])
    tc._recompute_currency_cap_verdict(v4)
    assert v4.status == "met"


def test_currency_cap_verdict_noop_when_ambiguous_or_nonconstraint():
    from app.candidate_search.grounded_evidence import CriterionVerdict, Evidence

    # not a currency/salary cap → untouched
    v = CriterionVerdict(criterion="Western company", status="partially_met", grounded=True,
                         evidence=[Evidence(quote="Emirates NBD", source="cv")])
    tc._recompute_currency_cap_verdict(v)
    assert v.status == "partially_met"
    # no stated value (model said missing, no evidence) → stays missing
    v2 = CriterionVerdict(criterion="salary expectation <= 30000 AED", status="missing",
                          grounded=False, evidence=[])
    tc._recompute_currency_cap_verdict(v2)
    assert v2.status == "missing"
    # a wrong citation with only an out-of-band number (a year) → trust the model
    v3 = CriterionVerdict(criterion="salary expectation <= 30000 AED", status="met", grounded=True,
                          evidence=[Evidence(quote="Engineer at LSEG since 2024", source="cv")])
    tc._recompute_currency_cap_verdict(v3)
    assert v3.status == "met"
    # two different stated values → ambiguous → untouched
    v4 = CriterionVerdict(criterion="salary <= 30000 AED", status="met", grounded=True,
                          evidence=[Evidence(quote="18000", source="notes"),
                                    Evidence(quote="25000", source="notes")])
    tc._recompute_currency_cap_verdict(v4)
    assert v4.status == "met"


def test_is_self_score_criterion_classifies():
    for crit in [
        "Taali score >= 60",
        "Taali score of at least 60",
        "minimum Taali score 55",
        "Taali fit >= 70",
        "taali score 60+",
    ]:
        assert tc._is_self_score_criterion(crit), crit
    # NOT self-score: no "taali" anchor, or no number, or unrelated.
    for crit in [
        "experience with scoring models",
        "credit score modelling",
        "Taali platform experience",  # "taali" but no score/fit token
        "banking domain experience",
        "salary expectation <= 30000 AED",
    ]:
        assert not tc._is_self_score_criterion(crit), crit


def test_parse_score_threshold():
    assert tc._parse_score_threshold("Taali score >= 60") == ("geq", 60.0)
    assert tc._parse_score_threshold("Taali score at least 55") == ("geq", 55.0)
    assert tc._parse_score_threshold("Taali score 70") == ("geq", 70.0)  # bare → floor
    assert tc._parse_score_threshold("Taali score <= 40") == ("leq", 40.0)
    assert tc._parse_score_threshold("Taali score under 40") == ("leq", 40.0)
    assert tc._parse_score_threshold("Taali score") is None  # no number


def test_self_score_verdict_recomputed_from_taali_score():
    from app.candidate_search.grounded_evidence import CriterionVerdict

    # The reported bug: "Taali score >= 60" was MISSING even though the candidate
    # scored 62. It's self-referential — decided against the score, not the CV.
    v = CriterionVerdict(criterion="Taali score >= 60", status="missing", grounded=False)
    tc._recompute_self_score_verdict(v, _fake_app(1, taali=62))
    assert v.status == "met"
    assert v.grounded is True
    assert v.source == "taali_score"
    assert "62" in v.evidence[0].quote

    # Below the floor → not_met (still grounded against the real score).
    v2 = CriterionVerdict(criterion="Taali score >= 60", status="missing", grounded=False)
    tc._recompute_self_score_verdict(v2, _fake_app(2, taali=55))
    assert v2.status == "not_met"
    assert v2.grounded is True

    # A cap variant ("<= 40"): 55 exceeds it → not_met.
    v3 = CriterionVerdict(criterion="Taali score <= 40", status="missing", grounded=False)
    tc._recompute_self_score_verdict(v3, _fake_app(3, taali=55))
    assert v3.status == "not_met"


def test_self_score_verdict_noop_when_not_applicable():
    from app.candidate_search.grounded_evidence import CriterionVerdict

    # Not a self-score criterion → untouched.
    v = CriterionVerdict(criterion="banking domain experience", status="missing", grounded=False)
    tc._recompute_self_score_verdict(v, _fake_app(1, taali=80))
    assert v.status == "missing"
    # No score yet → leave the honest "couldn't find it" rather than assert pass/fail.
    v2 = CriterionVerdict(criterion="Taali score >= 60", status="missing", grounded=False)
    tc._recompute_self_score_verdict(v2, _fake_app(2, taali=None))
    assert v2.status == "missing"
    assert v2.grounded is False


def test_find_top_candidates_decides_self_score_criterion(monkeypatch):
    """End-to-end: a "Taali score >= 60" criterion reads as MET for a candidate
    who scored 62, even though the grounder (CV/notes only) returns MISSING — and
    the candidate is shown, not hidden (a score gate is a preference, not a
    hard constraint)."""
    from app.candidate_search import runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_search", lambda **kw: SearchOutput(
        application_ids=[1, 2],
        parsed_filter=ParsedFilter(soft_criteria=["Taali score >= 60"]),
        warnings=[]))
    monkeypatch.setattr(tc, "_notes_text", lambda app: None)

    a = _fake_app(1, taali=62, name="A"); a.cv_text = "data engineer"
    b = _fake_app(2, taali=55, name="B"); b.cv_text = "data engineer"
    monkeypatch.setattr(tc, "_pool_count", lambda bq: 2)
    monkeypatch.setattr(tc, "_load_candidates", lambda bq, **kw: [a, b])

    class _FakeClient:
        class _M:
            def create(self, **kw):
                # The grounder can't find "Taali score" in the CV — MISSING.
                return SimpleNamespace(content=[_text_block("[[C1]] MISSING — not in CV")])
        messages = _M()

    out = tc.find_top_candidates(db=MagicMock(), organization_id=1, query="top with Taali 60+",
                                base_query=MagicMock(), limit=5, evidence_client=_FakeClient())
    by_id = {c["application_id"]: c for c in out["candidates"]}
    assert set(by_id) == {1, 2}  # neither hidden — score gate is a preference
    assert by_id[1]["criteria"][0]["status"] == "met"
    assert by_id[1]["criteria"][0]["grounded"] is True
    assert by_id[1]["criteria"][0]["source"] == "taali_score"
    assert by_id[1]["meets_all_criteria"] is True
    assert by_id[2]["criteria"][0]["status"] == "not_met"
    assert out["excluded"]["not_met_total"] == 0


def test_has_structural_classifies():
    assert tc._has_structural(ParsedFilter(skills_all=["react"]))
    assert tc._has_structural(ParsedFilter(locations_country=["United Arab Emirates"]))
    assert tc._has_structural(ParsedFilter(min_years_experience=5))
    assert not tc._has_structural(ParsedFilter(soft_criteria=["western company"]))
    assert not tc._has_structural(ParsedFilter(keywords=["fintech"]))
