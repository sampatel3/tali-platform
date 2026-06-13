"""Unit tests for the holistic Sonnet scoring engine (cv_match holistic_v1).

Covers the deterministic surface (org gating + output mapping) and a
stubbed-client happy path — no network. The whole point of the engine is
that the Sonnet ``overall`` becomes ``role_fit_score`` directly, so that
mapping is asserted explicitly.
"""

from types import SimpleNamespace

import pytest

from app.cv_matching import holistic
from app.cv_matching.holistic import (
    _Derivation,
    _HolisticScore,
    _ReqItem,
    _to_output,
    run_holistic_match,
)
from app.cv_matching.schemas import Priority, ScoringStatus, Status
from app.services import cv_score_orchestrator as orch


def _fake_res(value, **usage):
    return SimpleNamespace(
        value=value,
        ok=value is not None,
        error_reason="" if value is not None else "stub_error",
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 1000),
            output_tokens=usage.get("output_tokens", 500),
            cache_read_tokens=0,
            cache_creation_tokens=0,
        ),
        trace_id="t",
        cache_hit=False,
        retry_count=0,
        validation_failures=0,
    )


# --------------------------------------------------------------------------
# org gating
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "enabled,allow,org_id,expected",
    [
        (False, "2", 2, False),   # master switch off
        (True, "", 2, False),     # empty allowlist
        (True, "2", 2, True),     # exact match
        (True, "1,2,3", 2, True), # in list
        (True, "1,3", 2, False),  # not in list
        (True, "*", 2, True),     # wildcard
        (True, "*", None, True),  # wildcard ignores missing org
        (True, "2", None, False), # no org, specific list
    ],
)
def test_holistic_enabled_for(monkeypatch, enabled, allow, org_id, expected):
    monkeypatch.setattr(orch.settings, "HOLISTIC_SCORING_ENABLED", enabled, raising=False)
    monkeypatch.setattr(orch.settings, "HOLISTIC_SCORING_ORG_IDS", allow, raising=False)
    app = SimpleNamespace(organization_id=org_id)
    assert orch._holistic_enabled_for(app) is expected


# --------------------------------------------------------------------------
# output mapping — the validated score must land on role_fit_score
# --------------------------------------------------------------------------
def test_to_output_overall_becomes_role_fit():
    deriv = _Derivation(
        core_capability="Data pipeline engineering",
        requirements=[
            _ReqItem(requirement="PySpark ETL", importance="critical", is_core=True),
            _ReqItem(requirement="AWS", importance="important"),
        ],
    )
    s = _HolisticScore(
        overall=72,
        core_capability_score=80,
        verdict="Solid fit",
        reasoning="Strong hands-on pipeline work.",
        strengths=["10y PySpark", "Delta Lake"],
        gaps=["No Glue"],
    )
    out = _to_output(s, deriv, "trace", _fake_res(s, input_tokens=3000, output_tokens=1500))

    assert out.role_fit_score == 72.0           # the holistic overall, verbatim
    assert out.cv_fit_score == 72.0
    assert out.requirements_match_score == 72.0  # display fallback = overall
    assert out.scoring_status == ScoringStatus.OK
    assert out.model_version == holistic.HOLISTIC_MODEL
    assert out.prompt_version == "holistic_v1"
    assert out.dimension_scores is None         # no per-dimension breakdown
    # requirements come from the DERIVATION, listed but not separately graded
    assert len(out.requirements_assessment) == 2
    a0 = out.requirements_assessment[0]
    assert a0.requirement == "PySpark ETL"
    assert a0.priority == Priority.MUST_HAVE    # is_core / critical → must_have
    assert a0.match_score == -1                 # ungraded
    assert a0.status == Status.UNKNOWN
    assert a0.assessable is False
    assert out.requirements_assessment[1].priority == Priority.STRONG_PREFERENCE
    assert out.summary.startswith("Solid fit — ")
    assert out.matching_skills == ["10y PySpark", "Delta Lake"]
    assert out.missing_skills == ["No Glue"]
    assert out.input_tokens == 3000 and out.output_tokens == 1500


def test_to_output_handles_empty_reqs():
    deriv = _Derivation(core_capability="X", requirements=[])
    s = _HolisticScore(overall=100, reasoning="r")
    out = _to_output(s, deriv, "t", _fake_res(s))
    assert out.role_fit_score == 100.0
    assert out.requirements_match_score == 100.0
    assert out.requirements_assessment == []


def test_to_output_clamps_out_of_range_score():
    # A raw namespace bypasses the LLM-schema's 0-100 validator; _to_output
    # must still clamp defensively before it reaches CVMatchOutput's bounds.
    deriv = _Derivation(core_capability="X", requirements=[])
    s = SimpleNamespace(
        overall=150, core_capability_score=0, verdict="", reasoning="r",
        strengths=[], gaps=[],
    )
    out = _to_output(s, deriv, "t", _fake_res(None))
    assert out.role_fit_score == 100.0


# --------------------------------------------------------------------------
# run_holistic_match
# --------------------------------------------------------------------------
def test_run_holistic_missing_inputs():
    out = run_holistic_match("", "jd", client=object())
    assert out.scoring_status == ScoringStatus.FAILED
    assert out.role_fit_score == 0.0
    out2 = run_holistic_match("cv", "", client=object())
    assert out2.scoring_status == ScoringStatus.FAILED


def test_run_holistic_happy_path(monkeypatch):
    monkeypatch.setattr(holistic, "_redis", lambda: None)  # skip cache

    deriv = _Derivation(
        core_capability="Data eng",
        requirements=[_ReqItem(requirement="Spark", importance="critical", is_core=True)],
    )
    score = _HolisticScore(
        overall=64,
        core_capability_score=70,
        verdict="Fit",
        reasoning="ok",
        strengths=["a"],
        gaps=["b"],
    )

    def fake_generate_structured(client, *, output_model, **kw):
        if output_model is _Derivation:
            return _fake_res(deriv)
        return _fake_res(score, input_tokens=4000, output_tokens=1800)

    monkeypatch.setattr(holistic, "generate_structured", fake_generate_structured)

    out = run_holistic_match(
        "a real cv body",
        "a real jd body",
        client=object(),
        metering_context={"organization_id": 2, "role_id": 26, "entity_id": "application:1"},
        workable_context="recruiter says strong",
    )
    assert out.scoring_status == ScoringStatus.OK
    assert out.role_fit_score == 64.0
    assert out.model_version == "claude-sonnet-4-6"
    assert len(out.requirements_assessment) == 1  # from the derivation
    assert out.requirements_assessment[0].requirement == "Spark"
    assert out.output_tokens == 1800


def test_run_holistic_score_failure(monkeypatch):
    monkeypatch.setattr(holistic, "_redis", lambda: None)

    def fake_gen(client, *, output_model, **kw):
        if output_model is _Derivation:
            return _fake_res(_Derivation(core_capability="x", requirements=[]))
        return _fake_res(None)  # score call fails

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)
    out = run_holistic_match("cv", "jd", client=object())
    assert out.scoring_status == ScoringStatus.FAILED
    assert "holistic_score_failed" in out.error_reason
