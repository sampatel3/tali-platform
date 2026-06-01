"""GraphRAG prior parity — mainspring's vendored GraphitiBackend == tali's original.

ADR-0010 KG cutover STRUCTURAL PROOF. Tali's ``graph_priors`` sub-agent now routes
its GraphRAG prior through mainspring's vendored
``vendor.mainspring_kg.graphiti.GraphitiBackend.get_priors``, which ports tali's
``app/candidate_graph/graphrag_queries`` (the multi-hop Cypher + ``synthesise_prior``).

The priors feed LIVE candidate decisions and cannot be verified against the live
Neo4j/Voyage graph offline, so the safety here is STRUCTURAL: the ported Cypher
query strings and the synthesis math are CHARACTER-IDENTICAL to tali's, so over
the same graph they return identical priors by construction. This test proves
both halves of that claim:

1. ``test_cypher_query_source_is_char_identical`` / ``test_synthesise_prior_source_*``
   — the vendored ``graphrag_queries`` module source is byte-identical to tali's
   ``app/candidate_graph/graphrag_queries.py`` (whitespace-normalised diff is
   empty). Same query STRINGS + same synthesis CODE.

2. ``test_synthesis_identical_over_corpus`` — feed IDENTICAL fabricated query-row
   inputs to BOTH tali's ``synthesise_prior`` and the vendored one and assert
   identical ``p_advance`` / ``confidence`` / ``components``. The synthesis is
   deterministic given rows, so: same graph rows -> same prior.

3. ``test_priors_mapping_is_faithful`` — ``GraphitiBackend._priors_from_synthesis``
   preserves tali's exact ``p_advance`` + ``confidence`` and maps the no-signal
   sentinel onto ``Priors.empty`` (so the sub-agent still falls through to the
   legacy heuristic), with ``p_positive == p_advance`` and
   ``neighbour_count``/``examples`` carried through unchanged.

Any future drift in the vendored port fails CI here.
"""
from __future__ import annotations

import re

import pytest

# tali's ORIGINAL working synthesis (the reference).
from app.candidate_graph import graphrag_queries as tali_q

# mainspring's vendored port (what the sub-agent now calls under the hood).
from vendor.mainspring_kg.graphrag import graphrag_queries as ms_q
from vendor.mainspring_kg.graphiti import GraphitiBackend
from vendor.mainspring_kg.base import Priors


# ---------------------------------------------------------------------------
# 1. Source char-identity: the vendored Cypher + synthesis code IS tali's.
# ---------------------------------------------------------------------------

def _source_without_logger_line(module) -> str:
    """Module source with the (cosmetic) logger-name line dropped.

    The ONLY intended difference between tali's and the vendored module is the
    ``logging.getLogger("<name>")`` line — neither a Cypher query STRING nor any
    synthesis MATH. Everything else (every query string, every weight, the
    p_advance / confidence formulas) must be character-identical.
    """
    import inspect

    src = inspect.getsource(module)
    return "".join(
        line for line in src.splitlines(keepends=True)
        if "logging.getLogger(" not in line
    )


def _normalise_ws(text: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def test_cypher_and_synthesis_source_is_char_identical():
    tali_src = _normalise_ws(_source_without_logger_line(tali_q))
    ms_src = _normalise_ws(_source_without_logger_line(ms_q))
    assert ms_src == tali_src, (
        "vendored graphrag_queries source diverged from tali's — the Cypher "
        "and/or synthesis math is no longer character-identical"
    )


def test_every_query_function_string_is_identical():
    """Belt-and-braces: compare each query function's source individually so a
    diff localises to a function, and assert the literal Cypher strings match."""
    import inspect

    fn_names = [
        "role_must_haves",
        "candidate_claimed_skills",
        "role_requirements_weighted",
        "successful_skill_patterns",
        "referrer_signal",
        "company_overlap_with_top_performers",
        "similar_past_candidates",
        "skill_to_outcome_paths",
        "synthesise_prior",
    ]
    for name in fn_names:
        tali_fn = getattr(tali_q, name)
        ms_fn = getattr(ms_q, name)
        assert inspect.getsource(ms_fn) == inspect.getsource(tali_fn), (
            f"{name}() source diverged between tali and the vendored port"
        )


# ---------------------------------------------------------------------------
# 2. Behavioural parity: identical rows -> identical prior.
# ---------------------------------------------------------------------------

def _ref(total=0, hires=0, top=0):
    return {"total_referrals": total, "hires": hires, "top_performers": top}


def _overlap(company, n):
    return {"company": company, "overlap_top_performers": n, "avg_quality": 0.8}


def _similar(outcome, shared_skills=2, shared_companies=1):
    return {
        "candidate_id": "x",
        "outcome": outcome,
        "shared_skills": shared_skills,
        "shared_companies": shared_companies,
        "quality_signal": 0.6,
    }


def _skillrow(hire_rate, n):
    return {"skill": "py", "candidates_with_skill": n, "hire_rate": hire_rate,
            "avg_quality_signal": 0.5}


# A corpus exercising every synthesis branch + edge cases (empty sources, the
# referrer top-performer weighting, overlap saturation clamp at 5, the 3-hired
# similar-candidate clamp, the weighted skill-outcome denominator, missing
# fields defaulting, and the all-empty -> p_advance=None sentinel).
CORPUS: list[dict] = [
    # 0. All empty -> no components -> p_advance=None sentinel.
    {"referrer": None, "overlap_rows": [], "similar_rows": [], "skill_outcome_rows": []},
    # 1. Referrer only, strong top-performer fraction.
    {"referrer": _ref(10, 6, 4), "overlap_rows": [], "similar_rows": [], "skill_outcome_rows": []},
    # 2. Referrer with zero volume -> ignored (total_referrals==0).
    {"referrer": _ref(0, 0, 0), "overlap_rows": [], "similar_rows": [], "skill_outcome_rows": []},
    # 3. Company overlap below saturation.
    {"referrer": None, "overlap_rows": [_overlap("Acme", 2), _overlap("Globex", 1)],
     "similar_rows": [], "skill_outcome_rows": []},
    # 4. Company overlap above saturation (clamp to 1.0).
    {"referrer": None, "overlap_rows": [_overlap("Acme", 9)], "similar_rows": [],
     "skill_outcome_rows": []},
    # 5. Similar candidates, mixed outcomes (case-insensitive 'Hired').
    {"referrer": None, "overlap_rows": [],
     "similar_rows": [_similar("Hired"), _similar("rejected"), _similar("hired"),
                      _similar(None)],
     "skill_outcome_rows": []},
    # 6. Similar candidates exceeding the 3-hired clamp.
    {"referrer": None, "overlap_rows": [],
     "similar_rows": [_similar("hired")] * 5, "skill_outcome_rows": []},
    # 7. Skill->outcome weighted average.
    {"referrer": None, "overlap_rows": [], "similar_rows": [],
     "skill_outcome_rows": [_skillrow(0.8, 10), _skillrow(0.2, 5)]},
    # 8. Skill->outcome with zero denominator -> score 0.
    {"referrer": None, "overlap_rows": [], "similar_rows": [],
     "skill_outcome_rows": [_skillrow(0.9, 0)]},
    # 9. All four sources present (full weighted average + confidence=1.0).
    {"referrer": _ref(8, 5, 3),
     "overlap_rows": [_overlap("Acme", 3), _overlap("Globex", 1)],
     "similar_rows": [_similar("hired"), _similar("hired"), _similar("rejected")],
     "skill_outcome_rows": [_skillrow(0.7, 12), _skillrow(0.4, 6)]},
    # 10. Three sources (confidence = 3/4 = 0.75).
    {"referrer": _ref(4, 1, 0),
     "overlap_rows": [_overlap("Acme", 1)],
     "similar_rows": [_similar("hired")],
     "skill_outcome_rows": []},
    # 11. Missing optional fields (None hires/top, missing overlap count).
    {"referrer": {"total_referrals": 3}, "overlap_rows": [{"company": "X"}],
     "similar_rows": [{"outcome": "HIRED"}], "skill_outcome_rows": [{"candidates_with_skill": 4}]},
]


@pytest.mark.parametrize("rows", CORPUS, ids=[f"corpus{i}" for i in range(len(CORPUS))])
def test_synthesis_identical_over_corpus(rows):
    tali_out = tali_q.synthesise_prior(**rows)
    ms_out = ms_q.synthesise_prior(**rows)
    # Exact dict equality: p_advance, confidence, components (names/scores/
    # weights/summaries), and the synthesis_note sentinel all identical.
    assert ms_out == tali_out


def test_synthesis_p_advance_and_confidence_bitwise():
    """Belt-and-braces over the corpus: the two numeric outputs the policy
    engine actually consumes are equal under ``repr`` (catches any float drift)."""
    for rows in CORPUS:
        t = tali_q.synthesise_prior(**rows)
        m = ms_q.synthesise_prior(**rows)
        assert repr(m.get("p_advance")) == repr(t.get("p_advance"))
        assert repr(m.get("confidence")) == repr(t.get("confidence"))


# ---------------------------------------------------------------------------
# 3. Priors-shape mapping: the backend preserves tali's prior values exactly.
# ---------------------------------------------------------------------------

def test_priors_mapping_is_faithful():
    backend = GraphitiBackend()
    for rows in CORPUS:
        synthesis = tali_q.synthesise_prior(**rows)
        neighbour_count = len(rows["similar_rows"]) + len(rows["overlap_rows"])
        priors = backend._priors_from_synthesis(
            case_id=123, synthesis=synthesis, neighbour_count=neighbour_count,
        )
        if synthesis["p_advance"] is None:
            # No-signal sentinel -> Priors.empty so the sub-agent falls through.
            assert priors == Priors.empty(123)
        else:
            assert priors.p_advance == float(synthesis["p_advance"])
            assert priors.confidence == float(synthesis["confidence"])
            # tali uses p_advance as the p_hired/p_positive proxy.
            assert priors.p_positive == priors.p_advance
            assert priors.neighbour_count == neighbour_count
            assert priors.examples == list(synthesis["components"])


def test_empty_sentinel_round_trips_to_fall_through():
    """The all-empty corpus case (p_advance=None) MUST map to Priors.empty so the
    sub-agent's ``examples == []`` check sends it to the legacy heuristic — the
    graceful-degradation contract."""
    backend = GraphitiBackend()
    synthesis = tali_q.synthesise_prior(
        referrer=None, overlap_rows=[], similar_rows=[], skill_outcome_rows=[],
    )
    assert synthesis["p_advance"] is None
    priors = backend._priors_from_synthesis(case_id=7, synthesis=synthesis, neighbour_count=0)
    assert priors == Priors.empty(7)
    assert priors.examples == []
