"""Unit tests for the holistic Sonnet scoring engine (cv_match holistic_v1).

Two-call design: call 1 = calibrated score (_LeanScore), call 2 = report
detail (_Report). The tests assert role_fit_score comes from call 1 and the
complete report (snapshot / dimensions / graded requirements with verbatim
evidence) comes from call 2.
"""

from types import SimpleNamespace

import pytest

from app.cv_matching import holistic
from app.cv_matching.holistic import (
    _Derivation,
    _Dims,
    _LeanScore,
    _ReqGrade,
    _ReqItem,
    _Report,
    _Snapshot,
    _TL,
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
        (False, "2", 2, False),
        (True, "", 2, False),
        (True, "2", 2, True),
        (True, "1,2,3", 2, True),
        (True, "1,3", 2, False),
        (True, "*", 2, True),
        (True, "*", None, True),
        (True, "2", None, False),
    ],
)
def test_holistic_enabled_for(monkeypatch, enabled, allow, org_id, expected):
    monkeypatch.setattr(orch.settings, "HOLISTIC_SCORING_ENABLED", enabled, raising=False)
    monkeypatch.setattr(orch.settings, "HOLISTIC_SCORING_ORG_IDS", allow, raising=False)
    app = SimpleNamespace(organization_id=org_id)
    assert orch._holistic_enabled_for(app) is expected


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _deriv():
    return _Derivation(
        core_capability="Data pipeline engineering",
        requirements=[
            _ReqItem(requirement="PySpark ETL", importance="critical", is_core=True),
            _ReqItem(requirement="AWS Glue", importance="important"),
            _ReqItem(requirement="Nice: Terraform", importance="peripheral"),
        ],
    )


def _lean():
    return _LeanScore(
        overall=72,
        core_capability_score=80,
        verdict="Solid fit",
        reasoning="Strong hands-on pipeline work.",
        matching_skills=["PySpark", "Delta Lake"],
        missing_skills=["Glue depth"],
        highlights=["10y pipelines"],
        concerns=["No financial-services domain"],
    )


def _report():
    return _Report(
        snapshot=_Snapshot(
            years_experience=8.0,
            top_skills=["PySpark", "Delta Lake", "AWS"],
            timeline=[_TL(company="Acme", role="Senior DE", start_year=2020, is_current=True)],
        ),
        dimensions=_Dims(
            skills_coverage=70, skills_depth=75, title_trajectory=68,
            seniority_alignment=72, industry_match=60, tenure_pattern=80,
        ),
        requirements=[
            _ReqGrade(index=0, status="met", score=88, evidence="Built Spark ETL pipelines at Acme", impact=""),
            _ReqGrade(index=1, status="partial", score=50, evidence="Some Glue exposure", impact="ramp needed"),
        ],
    )


# --------------------------------------------------------------------------
# output mapping — score from call 1, report from call 2
# --------------------------------------------------------------------------
def test_to_output_complete_report():
    out = _to_output(_lean(), _report(), _deriv(), "trace",
                     _fake_res(None, input_tokens=1500, output_tokens=300),
                     _fake_res(None, input_tokens=1500, output_tokens=1500))

    assert out.role_fit_score == 72.0            # from call 1 (lean)
    assert out.requirements_match_score == 72.0  # kept == overall so recompute can't override
    assert out.engine_version == "2.1.0"         # stamped provenance
    assert out.prompt_version == "holistic_v2"
    assert out.scoring_status == ScoringStatus.OK
    assert out.matching_skills == ["PySpark", "Delta Lake"]  # call 1
    assert out.summary.startswith("Solid fit — ")

    # report (call 2)
    assert out.candidate_snapshot is not None
    assert out.candidate_snapshot.years_experience == 8.0
    assert out.dimension_scores.skills_depth == 75.0
    assert len(out.requirements_assessment) == 3  # all derived requirements present
    r0 = out.requirements_assessment[0]
    assert r0.priority == Priority.MUST_HAVE and r0.status == Status.MET and r0.match_score == 88
    assert r0.evidence_quotes == ["Built Spark ETL pipelines at Acme"]
    # _to_output passes quotes through; offsets are set by the grounding pass
    # (run in run_holistic_match), not here.
    assert r0.evidence_start_char == -1
    assert out.requirements_assessment[2].status == Status.UNKNOWN  # ungraded by model

    # token usage sums both calls
    assert out.input_tokens == 3000 and out.output_tokens == 1800


def test_to_output_empty_report_degrades_gracefully():
    # call 2 failed → empty _Report; score still valid, report fields absent
    out = _to_output(_lean(), _Report(), _deriv(), "t", _fake_res(None), None)
    assert out.role_fit_score == 72.0
    assert out.scoring_status == ScoringStatus.OK
    assert out.candidate_snapshot is None
    # requirements still listed (from derivation), just ungraded
    assert len(out.requirements_assessment) == 3
    assert out.requirements_assessment[0].status == Status.UNKNOWN


# --------------------------------------------------------------------------
# run_holistic_match
# --------------------------------------------------------------------------
@pytest.fixture
def _nocache(monkeypatch):
    monkeypatch.setattr(holistic, "_redis", lambda: None)
    monkeypatch.setattr(holistic, "_cache_get", lambda k: None)
    monkeypatch.setattr(holistic, "_cache_set", lambda k, o: None)


def test_run_holistic_missing_inputs():
    out = run_holistic_match("", "jd", client=object())
    assert out.scoring_status == ScoringStatus.FAILED
    out2 = run_holistic_match("cv", "", client=object())
    assert out2.scoring_status == ScoringStatus.FAILED


def test_run_holistic_happy_path(monkeypatch, _nocache):
    deriv, lean, report = _deriv(), _lean(), _report()

    def fake_gen(client, *, output_model, **kw):
        if output_model is _Derivation:
            return _fake_res(deriv)
        if output_model is _LeanScore:
            return _fake_res(lean, input_tokens=1500, output_tokens=300)
        return _fake_res(report, input_tokens=1500, output_tokens=1800)

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)
    out = run_holistic_match(
        # CV contains both requirement evidences so the grounding pass keeps them
        "Built Spark ETL pipelines at Acme. Some Glue exposure too.",
        "a real jd body",
        client=object(),
        metering_context={"organization_id": 2, "role_id": 26, "entity_id": "application:1"},
    )
    assert out.scoring_status == ScoringStatus.OK
    assert out.role_fit_score == 72.0
    assert out.engine_version == "2.1.0"
    assert out.candidate_snapshot is not None
    assert out.dimension_scores is not None
    assert len(out.requirements_assessment) == 3
    assert out.output_tokens == 2100  # 300 + 1800
    # grounded: the verbatim quote was located → offsets set
    assert out.requirements_assessment[0].status == Status.MET
    assert out.requirements_assessment[0].evidence_start_char >= 0


def test_run_holistic_drops_fabricated_evidence(monkeypatch, _nocache):
    # P1-A regression: a quote NOT in the CV must be dropped and the
    # requirement downgraded — never surfaced as grounded evidence.
    deriv, lean = _deriv(), _lean()
    report = _Report(
        requirements=[
            _ReqGrade(index=0, status="met", score=90,
                      evidence="THIS QUOTE IS NOT ANYWHERE IN THE CANDIDATE CV", impact=""),
        ],
    )

    def fake_gen(client, *, output_model, **kw):
        if output_model is _Derivation:
            return _fake_res(deriv)
        if output_model is _LeanScore:
            return _fake_res(lean)
        return _fake_res(report)

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)
    out = run_holistic_match("a totally unrelated cv body", "jd body", client=object())
    assert out.scoring_status == ScoringStatus.OK
    r0 = out.requirements_assessment[0]
    assert r0.status == Status.UNKNOWN        # downgraded
    assert r0.evidence_quotes == []           # fabricated quote dropped
    assert r0.match_tier == "missing"


def test_run_holistic_report_failure_still_scores(monkeypatch, _nocache):
    deriv, lean = _deriv(), _lean()

    def fake_gen(client, *, output_model, **kw):
        if output_model is _Derivation:
            return _fake_res(deriv)
        if output_model is _LeanScore:
            return _fake_res(lean)
        return _fake_res(None)  # report call fails

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)
    out = run_holistic_match("cv body", "jd body", client=object())
    assert out.scoring_status == ScoringStatus.OK  # score survives
    assert out.role_fit_score == 72.0
    assert out.candidate_snapshot is None  # report absent, but no crash


def test_run_holistic_score_failure(monkeypatch, _nocache):
    def fake_gen(client, *, output_model, **kw):
        if output_model is _Derivation:
            return _fake_res(_Derivation(core_capability="x", requirements=[]))
        if output_model is _LeanScore:
            return _fake_res(None)  # score call fails
        return _fake_res(_Report())

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)
    out = run_holistic_match("cv", "jd", client=object())
    assert out.scoring_status == ScoringStatus.FAILED
    assert "holistic_score_failed" in out.error_reason
