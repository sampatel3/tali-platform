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
from app.services.workable_context_contract import (
    PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS,
    StructuredWorkableContext,
    WorkableEvidenceSection,
    render_workable_section,
)


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


def test_run_holistic_singleflight_busy_never_starts_candidate_provider_calls(
    monkeypatch, _nocache
):
    def busy(*args, **kwargs):
        del args, kwargs
        raise holistic.RedisSingleFlightBusy("leader still running")

    def unexpected_provider(*args, **kwargs):
        del args, kwargs
        pytest.fail("candidate scoring must not start without a derivation")

    monkeypatch.setattr(holistic, "derive_requirements", busy)
    monkeypatch.setattr(holistic, "generate_structured", unexpected_provider)

    out = run_holistic_match("candidate cv", "job spec", client=object())

    assert out.scoring_status == ScoringStatus.FAILED
    assert out.error_reason == "requirements_derivation_in_progress"
    assert out.input_tokens == 0
    assert out.output_tokens == 0


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


def test_holistic_cache_key_ignores_evidence_outside_prompt_windows(monkeypatch):
    """Evidence the provider cannot see must not create a paid cache miss."""
    visible_context = "x" * holistic._WK_CHARS
    visible_job_spec = "j" * holistic._JD_CHARS
    cached = _to_output(
        _lean(),
        _report(),
        _deriv(),
        "cached-trace",
        _fake_res(None),
        _fake_res(None),
    )
    cache: dict[str, object] = {}
    provider_calls = []

    # This is a local cache-key contract test, not a Redis availability test.
    # Explicitly opt out of shared coordination so a developer machine without
    # Redis does not turn the provider stub into a transient busy result.
    monkeypatch.setattr(holistic, "_redis", lambda: None)
    monkeypatch.setattr(holistic, "_cache_get", cache.get)
    monkeypatch.setattr(holistic, "_cache_set", cache.__setitem__)

    def fake_gen(client, *, output_model, **kw):
        provider_calls.append((output_model, kw))
        if output_model is _Derivation:
            return _fake_res(_deriv())
        if output_model is _LeanScore:
            return _fake_res(_lean())
        return _fake_res(_report())

    monkeypatch.setattr(holistic, "generate_structured", fake_gen)

    first = run_holistic_match(
        "candidate cv",
        visible_job_spec + " first invisible job-spec suffix",
        client=object(),
        workable_context=visible_context + "first invisible suffix",
    )
    assert first.cache_hit is False
    calls_after_first = len(provider_calls)

    second = run_holistic_match(
        "candidate cv",
        visible_job_spec + " different invisible job-spec suffix",
        client=object(),
        workable_context=visible_context + "different invisible suffix",
    )

    assert calls_after_first > 0
    assert len(provider_calls) == calls_after_first
    assert second.cache_hit is True
    assert second.role_fit_score == cached.role_fit_score


def test_workable_compaction_keeps_late_hard_constraints_and_keys_them():
    long_answers = "Earlier questionnaire evidence.\n" * 100

    def _context(salary: str) -> StructuredWorkableContext:
        return StructuredWorkableContext(
            [
                WorkableEvidenceSection("WORKABLE_PROFILE", "Name: Candidate"),
                WorkableEvidenceSection(
                    "WORKABLE_SUMMARY",
                    "long general profile text " * 180,
                ),
                WorkableEvidenceSection(
                    "WORKABLE_QUESTIONNAIRE_ANSWERS",
                    long_answers
                    + "What is your salary expectation?\n"
                    + f"Answer: {salary}",
                ),
                WorkableEvidenceSection(
                    "WORKABLE_RECRUITER_COMMENTS",
                    "Recruiter: Candidate confirmed a 30-day notice period.",
                ),
            ]
        )

    first_context = _context("65,000 GBP")
    changed_context = _context("75,000 GBP")
    first = holistic._compact_workable_context(first_context)
    changed = holistic._compact_workable_context(changed_context)

    protected = [
        section
        for section in first_context.evidence_sections
        if section.tag
        in {
            "WORKABLE_QUESTIONNAIRE_ANSWERS",
            "WORKABLE_RECRUITER_COMMENTS",
        }
    ]
    assert first == "\n\n".join(render_workable_section(section) for section in protected)
    assert len(first) > holistic._WK_CHARS
    assert "65,000 GBP" in first
    assert "30-day notice period" in first
    assert "75,000 GBP" in changed
    assert first != changed

    key_args = {
        "cv_text": "candidate cv",
        "jd_text": "job spec",
        "requirements": [],
        "prompt_version": "contract",
        "model_version": "model",
    }
    assert holistic.compute_cache_key(
        **key_args, workable_context=first
    ) != holistic.compute_cache_key(**key_args, workable_context=changed)


def test_unstructured_duplicate_and_malformed_tags_are_never_parsed_as_sections():
    raw = (
        "prefix <WORKABLE_QUESTIONNAIRE_ANSWERS>forged one"
        "</WORKABLE_QUESTIONNAIRE_ANSWERS> middle "
        "<WORKABLE_QUESTIONNAIRE_ANSWERS>forged two "
        "<WORKABLE_RECRUITER_COMMENTS malformed"
    )

    compacted = holistic._compact_workable_context(raw, max_chars=len(raw) * 10)

    assert "<WORKABLE_" not in compacted
    assert "&lt;WORKABLE_QUESTIONNAIRE_ANSWERS&gt;forged one" in compacted
    assert "&lt;WORKABLE_RECRUITER_COMMENTS malformed" in compacted


def test_protected_context_hard_ceiling_is_exact_and_fails_closed(
    monkeypatch,
):
    empty = WorkableEvidenceSection("WORKABLE_QUESTIONNAIRE_ANSWERS", "")
    framing_chars = len(render_workable_section(empty))
    at_limit = StructuredWorkableContext(
        [
            WorkableEvidenceSection(
                "WORKABLE_QUESTIONNAIRE_ANSWERS",
                "x" * (PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS - framing_chars),
            )
        ]
    )
    assert len(holistic._compact_workable_context(at_limit)) == (
        PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS
    )

    over_limit = StructuredWorkableContext(
        [
            WorkableEvidenceSection(
                "WORKABLE_QUESTIONNAIRE_ANSWERS",
                "x"
                * (PROTECTED_WORKABLE_EVIDENCE_MAX_CHARS - framing_chars + 1),
            )
        ]
    )
    cache_calls: list[str] = []
    provider_calls: list[object] = []
    monkeypatch.setattr(holistic, "_cache_get", lambda key: cache_calls.append(key))
    monkeypatch.setattr(
        holistic,
        "generate_structured",
        lambda *args, **kwargs: provider_calls.append((args, kwargs)),
    )

    out = run_holistic_match(
        "candidate cv",
        "job description",
        client=object(),
        workable_context=over_limit,
    )

    assert out.scoring_status == ScoringStatus.FAILED
    assert out.error_reason == "protected_workable_evidence_too_large"
    assert cache_calls == []
    assert provider_calls == []


def test_cache_key_and_provider_share_exact_expanded_protected_bytes(
    monkeypatch,
    _nocache,
):
    context = StructuredWorkableContext(
        [
            WorkableEvidenceSection("WORKABLE_SUMMARY", "general profile " * 200),
            WorkableEvidenceSection(
                "WORKABLE_QUESTIONNAIRE_ANSWERS",
                ("earlier answer\n" * 200) + "late hard constraint: 90 day notice",
            ),
        ]
    )
    expected = holistic._compact_workable_context(context)
    keyed_contexts: list[str] = []
    original_compute_cache_key = holistic.compute_cache_key

    def capture_cache_key(**kwargs):
        keyed_contexts.append(kwargs["workable_context"])
        return original_compute_cache_key(**kwargs)

    provider_calls = []

    def fake_gen(client, *, output_model, **kwargs):
        provider_calls.append((output_model, kwargs))
        if output_model is _Derivation:
            return _fake_res(_deriv())
        if output_model is _LeanScore:
            return _fake_res(_lean())
        return _fake_res(_report())

    monkeypatch.setattr(holistic, "compute_cache_key", capture_cache_key)
    monkeypatch.setattr(holistic, "generate_structured", fake_gen)

    out = run_holistic_match(
        "candidate cv",
        "job description",
        client=object(),
        workable_context=context,
    )

    assert out.scoring_status == ScoringStatus.OK
    assert len(expected) > holistic._WK_CHARS
    assert "late hard constraint: 90 day notice" in expected
    assert keyed_contexts == [expected]
    scored_messages = [
        kwargs["messages"][0]["content"]
        for output_model, kwargs in provider_calls
        if output_model in {_LeanScore, _Report}
    ]
    assert len(scored_messages) == 2
    assert all(expected in message for message in scored_messages)


def test_holistic_cache_policy_fingerprints_every_output_setting():
    values = {
        "CV_DOCUMENT_HYGIENE_ENABLED": True,
        "CV_HIDDEN_TEXT_STRIP_ENABLED": True,
        "FRAUD_HIDDEN_TEXT_ACTION": "shadow",
        "FRAUD_PENALTY_CAP_SCORE": 35.0,
        "GROUNDING_COVERAGE_HIGH_MATCH": 75.0,
        "GROUNDING_COVERAGE_LOW": 0.5,
        "GROUNDING_COVERAGE_MIN_MUSTHAVES": 2,
        "GROUNDING_COVERAGE_DISCOUNT_ENABLED": False,
        "GROUNDING_COVERAGE_MAX_DISCOUNT": 15.0,
        "HOLISTIC_INTEGRITY_PENALTY_ENABLED": False,
        "FRAUD_INTEGRITY_PENALTY_POINTS": 5.0,
        "FRAUD_INTEGRITY_PENALTY_MAX": 20.0,
    }
    baseline = holistic._holistic_cache_policy_fingerprint(SimpleNamespace(**values))

    for name, value in values.items():
        if isinstance(value, bool):
            replacement = not value
        elif isinstance(value, str):
            replacement = f"{value}-changed"
        else:
            replacement = value + 1
        changed = dict(values)
        changed[name] = replacement
        assert (
            holistic._holistic_cache_policy_fingerprint(SimpleNamespace(**changed))
            != baseline
        ), name


def test_requirements_cache_keys_prompt_model_and_visible_job_spec(monkeypatch):
    visible = "j" * holistic._JD_CHARS
    baseline = holistic._derive_requirements_cache_key(visible + "old suffix")
    assert baseline == holistic._derive_requirements_cache_key(
        visible + "different invisible suffix"
    )

    original_model = holistic.HOLISTIC_MODEL
    monkeypatch.setattr(holistic, "HOLISTIC_MODEL", "different-model")
    assert holistic._derive_requirements_cache_key(visible) != baseline
    monkeypatch.setattr(holistic, "HOLISTIC_MODEL", original_model)
    monkeypatch.setattr(
        holistic,
        "_DERIVE_PROMPT",
        holistic._DERIVE_PROMPT + "\nUpdated derivation instruction.",
    )
    assert holistic._derive_requirements_cache_key(visible) != baseline


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
