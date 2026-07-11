"""Deterministic process features (submit-time telemetry counts).

Pure-function tests: compute_process_features over synthetic ai_prompts +
timeline shapes (new dict tool calls, legacy string tool calls, missing
timestamps), and the grader-prompt integration via ScoringArtifacts.
"""

from app.components.assessments.process_features import (
    compute_process_features,
    render_process_features,
)
from app.components.assessments.rubric_scoring import ScoringArtifacts, _build_user_prompt


def _ts(minute: int, second: int = 0) -> str:
    return f"2026-07-10T10:{minute:02d}:{second:02d}+00:00"


def test_empty_inputs_yield_zero_features():
    features = compute_process_features(None, None)
    assert features["candidate_turns"] == 0
    assert features["test_runs"] == 0
    assert features["edits_after_last_test"] is None
    assert features["median_inter_turn_seconds"] is None
    assert features["single_mega_prompt"] is False


def test_opener_turn_is_not_a_candidate_turn():
    prompts = [
        {"message": "", "response": "opener text", "opener": True, "timestamp": _ts(0)},
        {"message": "run the tests", "response": "ok", "timestamp": _ts(1)},
    ]
    features = compute_process_features(prompts, [])
    assert features["candidate_turns"] == 1
    assert features["single_mega_prompt"] is True


def test_test_runs_counted_from_timeline_and_agent_bash():
    timeline = [
        {"event_type": "code_execute", "tests_total": 9, "timestamp": _ts(5)},
        {"event_type": "code_execute", "tests_total": None, "timestamp": _ts(6)},
        {"event_type": "ai_prompt", "timestamp": _ts(7)},
    ]
    prompts = [
        {
            "message": "verify it",
            "response": "done",
            "timestamp": _ts(8),
            "tool_calls_made": [
                {"name": "run_command", "input": "./.venv/bin/python -m pytest -q", "result": "9 passed"},
                {"name": "run_command", "input": "ls -la", "result": "files"},
            ],
        },
    ]
    features = compute_process_features(prompts, timeline)
    assert features["test_runs_with_results"] == 1
    assert features["agent_test_runs"] == 1
    assert features["test_runs"] == 2


def test_edits_after_last_test_flags_unverified_ship():
    # Last edit AFTER last test run → shipped unverified.
    timeline = [
        {"event_type": "code_execute", "tests_total": 5, "timestamp": _ts(10)},
        {"event_type": "repo_file_save", "timestamp": _ts(12)},
    ]
    features = compute_process_features([], timeline)
    assert features["edits_after_last_test"] is True

    # Test run after the last edit → verified before done.
    timeline = [
        {"event_type": "repo_file_save", "timestamp": _ts(10)},
        {"event_type": "code_execute", "tests_total": 5, "timestamp": _ts(12)},
    ]
    features = compute_process_features([], timeline)
    assert features["edits_after_last_test"] is False


def test_edit_tool_calls_count_as_edits():
    timeline = [{"event_type": "code_execute", "tests_total": 3, "timestamp": _ts(10)}]
    prompts = [
        {
            "message": "now tweak the config",
            "response": "done",
            "timestamp": _ts(15),
            "tool_calls_made": [{"name": "apply_edit", "input": "dag/config.py", "result": "ok"}],
        },
    ]
    features = compute_process_features(prompts, timeline)
    assert features["edits_after_last_test"] is True


def test_challenge_markers_and_tool_errors():
    prompts = [
        {"message": "why did you change the gate? revert that", "response": "ok", "timestamp": _ts(1)},
        {"message": "looks good", "response": "ok", "timestamp": _ts(2)},
        {
            "message": "run it",
            "response": "failed",
            "timestamp": _ts(3),
            "tool_calls_made": [{"name": "run_command", "input": "pytest", "result": "boom", "is_error": True}],
        },
    ]
    features = compute_process_features(prompts, [])
    assert features["challenge_marker_turns"] == 1
    assert features["tool_errors"] == 1


def test_cadence_from_server_timestamps():
    prompts = [
        {"message": "first prompt with a longer question about the brief", "timestamp": _ts(0)},
        {"message": "ok do that", "timestamp": _ts(1)},  # 60s later, 3 words → quick follow-up
        {"message": "and the next module too", "timestamp": _ts(11)},  # 600s later
    ]
    features = compute_process_features(prompts, [])
    assert features["candidate_turns"] == 3
    assert features["quick_follow_up_turns"] == 1
    assert features["median_inter_turn_seconds"] == 330.0
    assert features["max_idle_seconds"] == 600.0
    assert features["single_mega_prompt"] is False


def test_legacy_string_tool_calls_do_not_crash():
    prompts = [
        {"message": "go", "response": "ok", "timestamp": _ts(0), "tool_calls_made": ["Read", "Edit"]},
    ]
    features = compute_process_features(prompts, [])
    assert features["candidate_turns"] == 1
    assert features["tool_errors"] == 0


def test_render_empty_and_populated():
    assert render_process_features({}) == ""
    assert render_process_features(None) == ""
    text = render_process_features({"candidate_turns": 4, "test_runs": 2, "edits_after_last_test": False})
    assert "candidate turns: 4" in text
    assert "test/verification runs" in text
    assert "shipped unverified" in text


def test_grader_prompt_includes_counts_and_antiverbosity():
    artifacts = ScoringArtifacts(
        prompt_transcript=[{"message": "hi", "response": "hello"}],
        process_features={"candidate_turns": 3, "test_runs": 1},
    )
    prompt = _build_user_prompt("verification_before_done", {}, artifacts)
    assert "Deterministic process counts" in prompt
    assert "candidate turns: 3" in prompt
    assert "do not reward verbosity" in prompt

    bare = ScoringArtifacts(prompt_transcript=[{"message": "hi", "response": "hello"}])
    prompt = _build_user_prompt("verification_before_done", {}, bare)
    assert "Deterministic process counts" not in prompt
