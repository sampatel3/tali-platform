"""Unit tests for the rubric-driven scoring engine.

These exercise the grader logic + aggregation + error resilience without
hitting Anthropic. The Claude client is patched to return canned JSON;
metering is patched to no-op.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.components.assessments.rubric_scoring import (
    DimensionGrade,
    RubricResult,
    RubricScorer,
    ScoringArtifacts,
    _DISCERNMENT_LENS_PROMPT,
    _DILIGENCE_LENS_PROMPT,
    _DELIVERABLE_LENS_PROMPT,
    _DECISION_LENS_PROMPT,
    _PRACTICE_LENS_PROMPT,
    _system_prompt_for_lens,
    _build_user_prompt,
    fluency_axis_for_dimension,
    summarize_fluency_4d,
)


@pytest.fixture
def sample_rubric():
    """Mirror of the canonical task-spec shape (per
    ``data_eng_data_quality_contract_framework`` today)."""
    return {
        "framework_assessment": {
            "weight": 0.22,
            "criteria": {
                "excellent": "Reads spec + diagnostics before coding.",
                "good": "Identifies the issue but misses one layer.",
                "poor": "Edits without reading the spec.",
            },
        },
        "contract_validation": {
            "weight": 0.20,
            "criteria": {
                "excellent": "Validates required columns + types.",
                "good": "One of columns/types only.",
                "poor": "Leaves stub returning True.",
            },
        },
        "quality_checks": {
            "weight": 0.20,
            "criteria": {
                "excellent": "All 4 checks correct with failing rows.",
                "good": "Most correct; one incorrect.",
                "poor": "Checks still rubber-stamp.",
            },
        },
        "severity_gating": {
            "weight": 0.20,
            "criteria": {
                "excellent": "Blocks on ERROR only; names blockers.",
                "good": "Blocks but ignores severity.",
                "poor": "Gate still passes everything.",
            },
        },
        "communication_clarity": {
            "weight": 0.18,
            "criteria": {
                "excellent": "Platform-Lead-facing summary.",
                "good": "Engineering summary, light on gaps.",
                "poor": "Cannot explain what was fixed.",
            },
        },
    }


@pytest.fixture
def sample_artifacts():
    return ScoringArtifacts(
        repo_files={
            "dq/gate.py": "def promotion_gate(results):\n    return {'passed': True}\n",
            "dq/checks.py": "def not_null_check(records, col):\n    return {'passed': True}\n",
        },
        design_doc="# LIBRARY_DESIGN\nI chose dict-shape because Airflow wanted bool.",
        prompt_transcript=[
            {"message": "fix it", "response": "I'll read the files and fix them."},
            {"message": "all done?", "response": "Yes, all tests pass."},
        ],
        test_results_summary="9 of 9 tests passed",
        task_scenario="Implement DQ framework primitives.",
        candidate_role="data_engineer",
    )


def _grader_response(score, rating, reasoning="ok", citations=None):
    """Build a Claude messages.create-shaped response object."""
    payload = {
        "score": score, "rating": rating, "reasoning": reasoning,
        "evidence_citations": citations or [],
    }
    return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])


@pytest.fixture
def patched_metered_client():
    """Patch ``MeteredAnthropicClient`` so we can inject grader responses
    without touching Anthropic. Returns a holder dict — tests populate
    ``responses_to_yield`` IN ORDER (one per dimension graded)."""
    holder = {
        "responses_to_yield": [],
        "calls": [],
    }

    def factory(*args, **kwargs):
        instance = MagicMock()

        def messages_create(**call_kwargs):
            holder["calls"].append(call_kwargs)
            if not holder["responses_to_yield"]:
                raise RuntimeError("No more canned responses queued")
            return holder["responses_to_yield"].pop(0)

        instance.messages = MagicMock()
        instance.messages.create = messages_create
        return instance

    with patch(
        "app.components.assessments.rubric_scoring.MeteredAnthropicClient",
        side_effect=factory,
    ), patch(
        "app.components.assessments.rubric_scoring.Anthropic",
        MagicMock(),
    ):
        yield holder


# ---- ScoringArtifacts ------------------------------------------------------


def test_artifacts_repo_files_excerpt_respects_caps(sample_artifacts):
    # Build artifacts with way more files than the cap
    many_files = {f"file_{i:03d}.py": f"content_{i}" for i in range(50)}
    art = ScoringArtifacts(repo_files=many_files)
    excerpt = art.repo_files_excerpt()
    # Should mention the omission count
    assert "more files omitted" in excerpt
    # First file should be present, far-tail file should not
    assert "file_000.py" in excerpt
    assert "file_049.py" not in excerpt


def test_artifacts_empty_excerpts_are_human_readable():
    art = ScoringArtifacts()
    assert "no repo files" in art.repo_files_excerpt()
    assert "no DESIGN.md" in art.design_doc_excerpt()
    assert "no prompts" in art.prompt_transcript_excerpt()


def test_artifacts_design_doc_truncates_long_docs():
    huge = "x" * 50_000
    art = ScoringArtifacts(design_doc=huge)
    excerpt = art.design_doc_excerpt()
    assert "truncated" in excerpt
    assert len(excerpt) < len(huge)


# ---- PR-2: process-visible grading (trace + git diff) ----------------------


def _trace_transcript():
    return [
        {
            "message": "run the tests then fix the gate",
            "response": "The gate hardcodes passed=True; fixed.",
            "tool_calls_made": [
                {"name": "mcp__sandbox__Bash", "input": {"command": "pytest -q"},
                 "result": "2 failed, 7 passed", "is_error": False},
                {"name": "mcp__sandbox__Edit", "input": {"path": "dq/gate.py"},
                 "result": "could not find exact match", "is_error": True},
            ],
        },
    ]


def test_transcript_excerpt_includes_tool_trace_by_default():
    """Process trace is the default now: each turn interleaves the agent's
    tool calls + results (and an [error] flag) so the grader sees verification."""
    art = ScoringArtifacts(prompt_transcript=_trace_transcript())
    excerpt = art.prompt_transcript_excerpt()
    assert "[Candidate]: run the tests" in excerpt
    assert "[Claude]: The gate hardcodes" in excerpt
    assert "[Agent actions]" in excerpt
    assert "Bash(pytest -q)" in excerpt
    assert "→ 2 failed, 7 passed" in excerpt
    assert "Edit(dq/gate.py)" in excerpt
    assert "[error]" in excerpt  # the failed Edit


def test_transcript_excerpt_force_off_omits_trace():
    """The include_process_trace knob can still force message/response-only —
    used by scripts/shadow_rescore_assessments.py for a before/after compare."""
    art = ScoringArtifacts(prompt_transcript=_trace_transcript(), include_process_trace=False)
    excerpt = art.prompt_transcript_excerpt()
    assert "[Candidate]: run the tests" in excerpt
    assert "[Agent actions]" not in excerpt
    assert "pytest -q" not in excerpt


def test_transcript_tool_result_excerpt_is_bounded():
    huge = "z" * 5000
    art = ScoringArtifacts(
        prompt_transcript=[{
            "message": "read it", "response": "ok",
            "tool_calls_made": [{"name": "mcp__sandbox__Read", "input": {"path": "big.py"},
                                 "result": huge, "is_error": False}],
        }],
        include_process_trace=True,
    )
    excerpt = art.prompt_transcript_excerpt()
    # The 5000-char result is truncated to the per-line excerpt cap.
    assert huge not in excerpt
    assert "Read(big.py)" in excerpt


def test_git_evidence_excerpt_rendered_by_default_and_bounded():
    ge = {"commits": "abc123 fix the gate", "diff_main": "d" * 10_000}
    # Default (process trace on): commits + diff present, diff bounded.
    ex = ScoringArtifacts(git_evidence=ge).git_evidence_excerpt()
    assert "abc123 fix the gate" in ex
    assert "diff truncated" in ex
    assert len(ex) < 10_000
    # The force-off knob still suppresses it (shadow harness before/after).
    assert ScoringArtifacts(git_evidence=ge, include_process_trace=False).git_evidence_excerpt() == ""
    # No evidence captured → empty.
    assert ScoringArtifacts().git_evidence_excerpt() == ""


# ---- PR-5/PR-6: discernment/diligence lenses + 4-D fluency rollup -----------


def test_system_prompt_for_lens_routes_all_lenses():
    assert _system_prompt_for_lens("deliverable") is _DELIVERABLE_LENS_PROMPT
    assert _system_prompt_for_lens("discernment") is _DISCERNMENT_LENS_PROMPT
    assert _system_prompt_for_lens("diligence") is _DILIGENCE_LENS_PROMPT
    assert _system_prompt_for_lens("practice") is _PRACTICE_LENS_PROMPT
    assert _system_prompt_for_lens("decision") is _DECISION_LENS_PROMPT
    # Unknown / unset → decision-leaning back-compat default.
    assert _system_prompt_for_lens(None) is _DECISION_LENS_PROMPT
    assert _system_prompt_for_lens("nonsense") is _DECISION_LENS_PROMPT


def test_fluency_axis_for_dimension_mapping():
    # interrogation_outcome grader → delegation (decision ownership)
    assert fluency_axis_for_dimension({"grader": "interrogation_outcome"}) == "delegation"
    # lens routing
    assert fluency_axis_for_dimension({"lens": "deliverable"}) == "deliverable"
    assert fluency_axis_for_dimension({"lens": "discernment"}) == "discernment"
    assert fluency_axis_for_dimension({"lens": "diligence"}) == "diligence"
    assert fluency_axis_for_dimension({"lens": "decision"}) == "delegation"
    # explicit fluency tag wins over lens
    assert fluency_axis_for_dimension({"lens": "decision", "fluency": "description"}) == "description"
    # unset / junk → delegation default
    assert fluency_axis_for_dimension({}) == "delegation"
    assert fluency_axis_for_dimension("notadict") == "delegation"


def test_summarize_fluency_4d_weighted_rollup():
    rubric = {
        "design_decisions_articulated": {"grader": "interrogation_outcome", "weight": 0.4},
        "contract_correctness": {"lens": "deliverable", "weight": 0.3},
        "verify": {"lens": "discernment", "weight": 0.3},
    }
    dims = [
        DimensionGrade(dimension_id="design_decisions_articulated", score=8.0, rating="good", reasoning="ok", weight=0.4),
        DimensionGrade(dimension_id="contract_correctness", score=6.0, rating="good", reasoning="ok", weight=0.3),
        DimensionGrade(dimension_id="verify", score=9.0, rating="excellent", reasoning="ok", weight=0.3),
    ]
    out = summarize_fluency_4d(rubric, dims)
    assert out["delegation"] == 80.0
    assert out["deliverable"] == 60.0
    assert out["discernment"] == 90.0
    # Axes with no contributing dimension → None (no signal).
    assert out["description"] is None
    assert out["diligence"] is None


def test_summarize_fluency_4d_skips_errored_dimension():
    rubric = {"verify": {"lens": "discernment", "weight": 1.0}}
    dims = [
        DimensionGrade(dimension_id="verify", score=0.0, rating="poor", reasoning="grader failed",
                       weight=1.0, error="network blip"),
    ]
    out = summarize_fluency_4d(rubric, dims)
    # The only discernment dim errored → axis is None, not a misleading 0.
    assert out["discernment"] is None


# ---- PR-9: planted-trap excerpt + prompt inclusion -------------------------


def test_traps_excerpt_renders_planted_tell_where():
    art = ScoringArtifacts(traps=[
        {"id": "t1", "planted": "agent proposes silencing the failing check", "tell": "candidate rejects the shortcut", "where": "dq/checks.py"},
    ])
    ex = art.traps_excerpt()
    assert "agent proposes silencing the failing check" in ex
    assert "CAUGHT IF: candidate rejects the shortcut" in ex
    assert "where: dq/checks.py" in ex
    # No traps → empty.
    assert ScoringArtifacts().traps_excerpt() == ""


def test_build_user_prompt_includes_traps_when_present():
    criteria = {"excellent": "x", "good": "y", "poor": "z"}
    with_traps = ScoringArtifacts(
        task_scenario="s", candidate_role="data_engineer",
        traps=[{"id": "t1", "planted": "agent papers over the contradiction", "tell": "candidate surfaces it"}],
    )
    prompt = _build_user_prompt("verify", criteria, with_traps)
    assert "Planted traps for this task" in prompt
    assert "agent papers over the contradiction" in prompt
    # Absent when the task declares no traps.
    without = ScoringArtifacts(task_scenario="s", candidate_role="data_engineer")
    assert "Planted traps for this task" not in _build_user_prompt("verify", criteria, without)


# ---- RubricScorer.grade_dimension ------------------------------------------


def test_grade_dimension_returns_typed_result(
    patched_metered_client, sample_artifacts,
):
    patched_metered_client["responses_to_yield"] = [
        _grader_response(
            7.5, "good",
            reasoning="Code is correct but design doc is thin.",
            citations=["dq/gate.py:5", "transcript turn 1"],
        ),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=42, assessment_id=99)
    grade = scorer.grade_dimension(
        dimension_id="quality_checks",
        criteria={"excellent": "x", "good": "y", "poor": "z"},
        artifacts=sample_artifacts,
        weight=0.20,
    )
    assert isinstance(grade, DimensionGrade)
    assert grade.dimension_id == "quality_checks"
    assert grade.score == 7.5
    assert grade.rating == "good"
    assert "thin" in grade.reasoning
    assert grade.evidence_citations == ["dq/gate.py:5", "transcript turn 1"]
    assert grade.weight == 0.20
    assert grade.error is None


def test_grade_dimension_clamps_out_of_range_scores(
    patched_metered_client, sample_artifacts,
):
    patched_metered_client["responses_to_yield"] = [
        _grader_response(99, "excellent", reasoning="ok"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    grade = scorer.grade_dimension("d", {}, sample_artifacts)
    assert grade.score == 10.0


def test_grade_dimension_handles_invalid_rating(
    patched_metered_client, sample_artifacts,
):
    patched_metered_client["responses_to_yield"] = [
        _grader_response(5, "mediocre", reasoning="x"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    grade = scorer.grade_dimension("d", {}, sample_artifacts)
    # Unknown ratings collapse to ``poor`` — safer floor than letting bad
    # ratings leak into recruiter-facing UI.
    assert grade.rating == "poor"


def test_grade_dimension_tolerates_markdown_fenced_json(
    patched_metered_client, sample_artifacts,
):
    """Graders occasionally wrap their JSON in ```json fences despite
    the system prompt. The parser must tolerate it."""
    fenced = SimpleNamespace(content=[SimpleNamespace(
        text='```json\n{"score": 6, "rating": "good", "reasoning": "x", "evidence_citations": []}\n```'
    )])
    patched_metered_client["responses_to_yield"] = [fenced]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    grade = scorer.grade_dimension("d", {}, sample_artifacts)
    assert grade.score == 6.0
    assert grade.rating == "good"
    assert grade.error is None


def test_grade_dimension_error_returns_zero_with_error_set(
    patched_metered_client, sample_artifacts,
):
    """A grader call exception MUST NOT raise out of grade_dimension —
    must return a typed result so the aggregator can flag a gap rather
    than failing the whole submit flow."""
    # No responses queued → factory raises on the call
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    grade = scorer.grade_dimension("d", {}, sample_artifacts)
    assert grade.score == 0.0
    assert grade.rating == "poor"
    assert grade.error is not None
    assert "No more canned responses" in grade.error


def test_grade_dimension_threads_metering_kwargs(
    patched_metered_client, sample_artifacts,
):
    """Per the metering invariant, every Anthropic call must pass
    ``metering={feature, organization_id, sub_feature, ...}`` through
    to the wrapper so a ``UsageEvent`` lands. ``dimension`` MUST be
    tagged so we can attribute per-dimension spend later."""
    patched_metered_client["responses_to_yield"] = [
        _grader_response(8, "good"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=42, assessment_id=99)
    scorer.grade_dimension("framework_assessment", {}, sample_artifacts)

    # Metering shape: MeteredAnthropicClient only persists keys from
    # metering["metadata"] onto the UsageEvent row. ``sub_feature`` /
    # ``dimension`` / ``assessment_id`` MUST ride inside the nested
    # metadata dict (was top-level; fixed 2026-06-01).
    call = patched_metered_client["calls"][0]
    assert "metering" in call
    meta = call["metering"]
    assert meta["feature"] == "assessment"
    assert meta["organization_id"] == 42
    assert meta["entity_id"] == "assessment:99"
    assert meta["metadata"]["sub_feature"] == "rubric_scoring"
    assert meta["metadata"]["dimension"] == "framework_assessment"


# ---- RubricScorer.grade_rubric (aggregation) -------------------------------


def test_grade_rubric_aggregates_with_weights(
    patched_metered_client, sample_rubric, sample_artifacts,
):
    # 5 dimensions, scores [8, 6, 7, 5, 9] with weights [0.22, 0.20, 0.20, 0.20, 0.18]
    # weighted_sum = 8*.22 + 6*.20 + 7*.20 + 5*.20 + 9*.18 = 1.76 + 1.20 + 1.40 + 1.00 + 1.62 = 6.98
    # weights sum to 1.00 → score_10 = 6.98 → score_100 = 69.8
    patched_metered_client["responses_to_yield"] = [
        _grader_response(8, "good"),
        _grader_response(6, "good"),
        _grader_response(7, "good"),
        _grader_response(5, "good"),
        _grader_response(9, "excellent"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    result = scorer.grade_rubric(sample_rubric, sample_artifacts)

    assert isinstance(result, RubricResult)
    assert len(result.dimensions) == 5
    assert result.weighted_score_100 == pytest.approx(69.8, abs=0.05)
    assert result.fully_graded
    assert result.failed_dimension_ids == []


def test_grade_rubric_normalizes_when_weights_dont_sum_to_one(
    patched_metered_client, sample_artifacts,
):
    """If a future task spec lands with weights that don't sum to 1.0
    (e.g. 0.95 from rounding), the aggregator should defensively
    normalize so the final score isn't off."""
    rubric = {
        "a": {"weight": 0.45, "criteria": {}},
        "b": {"weight": 0.50, "criteria": {}},
    }
    patched_metered_client["responses_to_yield"] = [
        _grader_response(10, "excellent"),
        _grader_response(10, "excellent"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    result = scorer.grade_rubric(rubric, sample_artifacts)
    # Two 10/10 scores should yield 100/100 regardless of total weight
    assert result.weighted_score_100 == pytest.approx(100.0, abs=0.05)


def test_grade_rubric_continues_after_single_dimension_failure(
    patched_metered_client, sample_rubric, sample_artifacts,
):
    """A grader exception on ONE dimension must NOT block scoring the
    rest. The failed dimension records score=0 + error; the others
    grade normally. Failure list is surfaced via
    ``failed_dimension_ids`` so the recruiter UI can flag the gap."""
    # 5 dimensions; queue 5 responses but make the 3rd one un-parseable
    patched_metered_client["responses_to_yield"] = [
        _grader_response(8, "good"),
        _grader_response(7, "good"),
        SimpleNamespace(content=[SimpleNamespace(text="not even close to JSON")]),
        _grader_response(6, "good"),
        _grader_response(5, "good"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    result = scorer.grade_rubric(sample_rubric, sample_artifacts)
    assert len(result.dimensions) == 5
    assert not result.fully_graded
    assert "quality_checks" in result.failed_dimension_ids
    # 4 dimensions should be graded normally
    successful = [d for d in result.dimensions if d.error is None]
    assert len(successful) == 4


def test_grade_rubric_handles_empty_rubric(
    patched_metered_client, sample_artifacts,
):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    result = scorer.grade_rubric({}, sample_artifacts)
    assert result.dimensions == []
    assert result.weighted_score_100 == 0.0


def test_grade_rubric_zero_weights_falls_back_to_equal_weighting(
    patched_metered_client, sample_artifacts,
):
    """Defensive: if every dimension has weight 0 (misconfigured task),
    treat them as equal-weighted rather than dividing by zero."""
    rubric = {
        "a": {"weight": 0.0, "criteria": {}},
        "b": {"weight": 0.0, "criteria": {}},
    }
    patched_metered_client["responses_to_yield"] = [
        _grader_response(10, "excellent"),
        _grader_response(0, "poor"),
    ]
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    result = scorer.grade_rubric(rubric, sample_artifacts)
    # Average of 10 and 0 = 5/10 = 50/100
    assert result.weighted_score_100 == pytest.approx(50.0, abs=0.05)


# ---- Practice proficiency (practice_outcome grader + practice lens) ---------


def _pa(**kw):
    """Build ScoringArtifacts for practice-grader tests."""
    return ScoringArtifacts(**kw)


def test_practice_outcome_context_setup_strong(patched_metered_client):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    repo = {"AGENTS.md": "# Agents\n- run: pytest -q\n- style: black\n- gotcha: venv in .venv\n"}
    grade = scorer.grade_dimension_via_practice_outcome(
        "context_and_direction", _pa(repo_files=repo), weight=0.4, probe="context_setup",
    )
    # Presence + structure caps at the GOOD band by design (excellent = LLM lens).
    assert grade.rating == "good"
    assert grade.score == 7.5
    assert any("AGENTS.md" in c for c in grade.evidence_citations)


def test_practice_outcome_context_bloat_scores_poor(patched_metered_client):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    repo = {"CLAUDE.md": "\n".join(f"line {i}" for i in range(250))}
    grade = scorer.grade_dimension_via_practice_outcome(
        "ctx", _pa(repo_files=repo), probe="context_setup",
    )
    # Over-long memory file is the documented anti-pattern → poor.
    assert grade.rating == "poor"
    assert grade.score == 4.0


def test_practice_outcome_context_absent_scores_poor(patched_metered_client):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    grade = scorer.grade_dimension_via_practice_outcome(
        "ctx", _pa(repo_files={"main.py": "print(1)"}), probe="context_setup",
    )
    assert grade.rating == "poor"
    assert grade.score == 1.5


def test_practice_outcome_plan_first_present_and_absent(patched_metered_client):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    repo = {"PLAN.md": "1. read spec\n2. implement checks\n3. verify gate\n"}
    present = scorer.grade_dimension_via_practice_outcome("plan", _pa(repo_files=repo), probe="plan_first")
    assert present.rating == "good" and present.score == 7.5
    absent = scorer.grade_dimension_via_practice_outcome("plan", _pa(repo_files={}), probe="plan_first")
    assert absent.rating == "poor" and absent.score == 1.5


def test_practice_outcome_verification_from_trace(patched_metered_client):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    ran = [{"message": "run it", "response": "ok", "tool_calls_made": [
        {"name": "mcp__sandbox__Bash", "input": {"command": "pytest -q"}},
    ]}]
    g_ran = scorer.grade_dimension_via_practice_outcome("verify", _pa(prompt_transcript=ran), probe="verification")
    assert g_ran.rating == "good" and g_ran.score == 7.5
    none = [{"message": "x", "response": "y"}]
    g_none = scorer.grade_dimension_via_practice_outcome("verify", _pa(prompt_transcript=none), probe="verification")
    assert g_none.rating == "poor" and g_none.score == 1.5


def test_practice_outcome_composite_without_probe(patched_metered_client):
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    repo = {
        "AGENTS.md": "- run: pytest\n- style: black\n- note: keep lean\n",  # strong 7.5
        "PLAN.md": "1\n2\n3\n",                                              # strong 7.5
        # no reusable asset                                                  -> weak 1.5
    }
    transcript = [{"message": "go", "response": "ok", "tool_calls_made": [
        {"name": "Bash", "input": {"command": "pytest"}},                    # verify 7.5
    ]}]
    grade = scorer.grade_dimension_via_practice_outcome(
        "practice", _pa(repo_files=repo, prompt_transcript=transcript),
    )
    # (7.5 + 7.5 + 1.5 + 7.5) / 4 = 6.0
    assert grade.score == 6.0
    assert grade.rating == "good"


def test_grade_rubric_dispatches_practice_outcome_without_llm_call(patched_metered_client):
    # practice_outcome is deterministic: grade_rubric must NOT make an Anthropic call.
    scorer = RubricScorer(api_key="sk-fake", organization_id=1)
    rubric = {
        "verification_habit": {
            "weight": 1.0, "grader": "practice_outcome",
            "probe": "verification", "fluency": "diligence",
        },
    }
    transcript = [{"message": "go", "response": "ok", "tool_calls_made": [
        {"name": "Bash", "input": {"command": "pytest -q"}},
    ]}]
    result = scorer.grade_rubric(rubric, _pa(prompt_transcript=transcript))
    assert result.dimensions[0].dimension_id == "verification_habit"
    assert result.dimensions[0].score == 7.5
    assert result.weighted_score_100 == 75.0
    assert patched_metered_client["calls"] == []  # no LLM call for a deterministic dim


def test_practice_dimension_rolls_up_to_tagged_fluency_axis():
    # A practice dim with an explicit fluency tag rolls to that axis (no new axis).
    dims = [DimensionGrade(dimension_id="verification_habit", score=8.0, rating="good", reasoning="", weight=1.0)]
    rubric = {"verification_habit": {"grader": "practice_outcome", "fluency": "diligence"}}
    axes = summarize_fluency_4d(rubric, dims)
    assert axes["diligence"] == 80.0
    assert axes["deliverable"] is None


# ---- Two-stage part scoring (Part 1 Practice / Part 2 Applied) --------------

from app.components.assessments.rubric_scoring import (  # noqa: E402
    part_for_dimension,
    summarize_part_scores,
)


def test_part_for_dimension_mapping():
    assert part_for_dimension({"grader": "practice_outcome"}) == "practice"
    assert part_for_dimension({"lens": "practice"}) == "practice"
    assert part_for_dimension({"part": "practice"}) == "practice"
    assert part_for_dimension({"lens": "deliverable"}) == "applied"
    assert part_for_dimension({"grader": "interrogation_outcome"}) == "applied"
    assert part_for_dimension({}) == "applied"


def _dg(dim_id, score, weight=1.0, error=None):
    return DimensionGrade(dimension_id=dim_id, score=score, rating="good", reasoning="", weight=weight, error=error)


def test_summarize_part_scores_blends_two_stages():
    rubric = {
        "ctx": {"grader": "practice_outcome", "part": "practice"},
        "verify": {"grader": "practice_outcome", "part": "practice"},
        "impl": {"lens": "deliverable"},
    }
    dims = [_dg("ctx", 7.5, 0.5), _dg("verify", 1.5, 0.5), _dg("impl", 8.0, 1.0)]
    out = summarize_part_scores(rubric, dims, {"practice": 0.3, "applied": 0.7})
    assert out["practice"] == 45.0   # (7.5*.5 + 1.5*.5) * 10
    assert out["applied"] == 80.0    # 8.0 * 10
    # blend: 0.3*45 + 0.7*80 = 13.5 + 56 = 69.5
    assert out["blended_100"] == 69.5
    assert out["part_weights_used"] == {"practice": 0.3, "applied": 0.7}


def test_summarize_part_scores_single_part_collapses_to_that_part():
    # No practice dim -> practice None, blend == applied (back-compat: identical
    # to the ordinary weighted score for existing tasks).
    rubric = {"a": {"lens": "decision"}, "b": {"lens": "deliverable"}}
    dims = [_dg("a", 6.0, 0.4), _dg("b", 9.0, 0.6)]
    out = summarize_part_scores(rubric, dims)
    assert out["practice"] is None
    assert out["applied"] == round((6.0 * 0.4 + 9.0 * 0.6) / 1.0 * 10, 2)  # 78.0
    assert out["blended_100"] == out["applied"]


def test_summarize_part_scores_default_weights_when_unset():
    rubric = {"ctx": {"grader": "practice_outcome"}, "impl": {"lens": "deliverable"}}
    dims = [_dg("ctx", 10.0), _dg("impl", 5.0)]
    out = summarize_part_scores(rubric, dims, None)
    assert out["part_weights_used"] == {"practice": 0.3, "applied": 0.7}
    # 0.3*100 + 0.7*50 = 30 + 35 = 65
    assert out["blended_100"] == 65.0


def test_summarize_part_scores_skips_errored_dim():
    rubric = {"ctx": {"grader": "practice_outcome"}, "impl": {"lens": "deliverable"}}
    dims = [_dg("ctx", 9.0), _dg("impl", 0.0, error="boom")]
    out = summarize_part_scores(rubric, dims)
    assert out["practice"] == 90.0
    assert out["applied"] is None
    assert out["blended_100"] == 90.0  # only practice present
