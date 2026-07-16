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
    assert out.engine_version == holistic.HOLISTIC_ENGINE_VERSION  # stamped provenance
    assert out.prompt_version == holistic.HOLISTIC_PROMPT_VERSION
    assert out.scoring_status == ScoringStatus.OK
    assert out.matching_skills == ["PySpark", "Delta Lake"]  # call 1
    assert out.summary == "Solid fit — Strong hands-on pipeline work."

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


def test_holistic_summary_contract_is_model_authored_and_lossless():
    system = holistic._SCORE_SYS
    assert "2-3 concise plain-English sentences" in system
    assert "aiming for about 75 words" in system
    assert "not a hard word cutoff" in system
    assert "one or two most material gaps or uncertainties" in system
    assert "structured candidate report below carries that detail" in system

    schema = _LeanScore.model_json_schema()["properties"]
    assert schema["verdict"]["maxLength"] == 60
    assert schema["reasoning"]["maxLength"] == 1000

    reasoning = (
        "Production-scale Lakehouse ownership and dimensional-modelling depth are well evidenced "
        "across complex data programmes. The material uncertainty is direct knowledge-graph and "
        "ontology delivery, which the CV does not demonstrate."
    )
    lean = _lean().model_copy(update={"verdict": "Partial fit", "reasoning": reasoning})
    out = _to_output(lean, _report(), _deriv(), "trace", _fake_res(None), _fake_res(None))
    assert out.summary == f"Partial fit — {reasoning}"


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


def test_holistic_authority_callback_blocks_report_after_main_score(
    monkeypatch, _nocache
):
    """The two-call engine must not start its report after Pause commits."""

    generated = []
    phases: list[str] = []

    def fake_gen(client, *, output_model, **kw):
        if kw.get("before_provider_call") is not None:
            kw["before_provider_call"](0)
        generated.append(output_model)
        if output_model is _Derivation:
            return _fake_res(_deriv())
        if output_model is _LeanScore:
            return _fake_res(_lean())
        return _fake_res(_report())

    def authorize(phase: str) -> None:
        phases.append(phase)
        if phase == "full_score.report":
            raise RuntimeError("workspace paused")

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)
    with pytest.raises(RuntimeError, match="workspace paused"):
        run_holistic_match(
            "Built Spark ETL pipelines at Acme.",
            "a real jd body",
            client=object(),
            before_provider_call=authorize,
        )

    assert phases == [
        "full_score.requirements",
        "full_score.main",
        "full_score.report",
    ]
    assert generated == [_Derivation, _LeanScore]


def test_run_holistic_drops_fabricated_evidence(monkeypatch, _nocache):
    # P1-A regression: a quote NOT in the CV must be DROPPED so it can never be
    # shown as a verbatim citation — but the model's per-requirement judgment
    # (status/score) is KEPT (it's an independent grade, not derived from the
    # quote). A quote-less requirement reads as ungrounded downstream.
    deriv, lean = _deriv(), _lean()
    report = _Report(
        requirements=[
            _ReqGrade(index=0, status="met", score=90,
                      evidence="THIS QUOTE IS NOT ANYWHERE IN THE CANDIDATE CV", impact="x"),
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
    assert r0.evidence_quotes == []            # fabricated quote dropped
    assert r0.evidence_start_char == -1        # no verbatim location
    assert r0.status == Status.MET             # judgment kept (independent grade)
    assert r0.match_score == 90


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


def test_holistic_caches_stable_prefix(monkeypatch, _nocache):
    """Cost-efficiency (no model/methodology change): the per-role rubric +
    requirements ride a ``cache_control``'d SYSTEM block — byte-identical across
    candidates so Anthropic serves it from the prefix cache — while only the CV +
    Workable context vary per candidate in the (uncached) user message.
    """
    deriv, lean, report = _deriv(), _lean(), _report()
    calls = []

    def fake_gen(client, *, output_model, **kw):
        calls.append((output_model, kw))
        if output_model is _Derivation:
            return _fake_res(deriv)
        if output_model is _LeanScore:
            return _fake_res(lean)
        return _fake_res(report)

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)

    def _run(cv):
        return run_holistic_match(
            cv, "a real jd body", client=object(),
            metering_context={"organization_id": 2, "role_id": 26, "entity_id": "application:1"},
        )

    _run("CANDIDATE ONE distinctive cv alpha")
    _run("CANDIDATE TWO distinctive cv beta")

    # Both scoring calls (score + report) cache the stable prefix in the system
    # param; the derivation call is left untouched (already Redis-cached).
    scored = [kw for (om, kw) in calls if om in (_LeanScore, _Report)]
    assert len(scored) == 4  # 2 candidates x (score + report)
    for kw in scored:
        system = kw["system"]
        assert isinstance(system, list) and len(system) == 1
        blk = system[0]
        assert blk["type"] == "text"
        assert blk["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        # rubric + derived requirements live in the cached prefix...
        assert "CORE CAPABILITY" in blk["text"] and "PySpark ETL" in blk["text"]
        # ...and the per-candidate CV does NOT leak into it (would break caching).
        assert "distinctive cv" not in blk["text"]
        # the CV rides the uncached user message
        assert "CANDIDATE CV:" in kw["messages"][0]["content"]

    # The cached prefix is byte-identical across candidates (cache-eligible),
    # while each candidate's CV is distinct in the user turn.
    score_calls = [kw for (om, kw) in calls if om is _LeanScore]
    assert score_calls[0]["system"][0]["text"] == score_calls[1]["system"][0]["text"]
    assert "alpha" in score_calls[0]["messages"][0]["content"]
    assert "beta" in score_calls[1]["messages"][0]["content"]


def test_lean_score_overall_is_required():
    """A7 regression: ``overall`` maps DIRECTLY to role_fit_score (see
    ``_to_output``), so it must be REQUIRED. A ``default=0`` previously let a
    degraded-but-schema-valid tool emission that omitted ``overall`` validate as
    ok=True with overall=0 → the orchestrator persisted cv_match_score=0 with
    status OK = a silent 0-score auto-reject of a real candidate (live on the
    holistic org). Required means an absent field raises in the structured
    layer's ``model_validate`` → ValidationFailure → retry → only-then FAILED
    (cv_match_score=None, retried later), never a 0 auto-reject."""
    from pydantic import ValidationError

    # An emission that omits ``overall`` must NOT silently validate to 0.
    with pytest.raises(ValidationError):
        _LeanScore(core_capability_score=80, verdict="degraded emission")

    # The synthetic tool's input_schema must list ``overall`` as required so the
    # model is constrained to emit it (input_schema == model_json_schema()).
    assert "overall" in (_LeanScore.model_json_schema().get("required") or [])

    # A genuine model-emitted ``overall=0`` (a real clear-misfit verdict) is a
    # valid int and still passes — the fix targets ABSENCE, not low scores.
    assert _LeanScore(overall=0, core_capability_score=0).overall == 0
