"""Completion-policy regressions for durable ATS move operations."""

from unittest.mock import MagicMock, patch

from app.services.ats_move_result_policy import terminalize_skipped_move_result


def test_non_move_or_non_skipped_result_is_left_for_normal_completion():
    db = MagicMock()

    assert (
        terminalize_skipped_move_result(
            db,
            11,
            "post_note",
            {"application_id": 22},
            {"status": "skipped", "reason": "not_linked"},
            33,
        )
        is None
    )
    assert (
        terminalize_skipped_move_result(
            db,
            11,
            "move_stage",
            {"application_id": 22},
            {"status": "ok"},
            33,
        )
        is None
    )


def test_skipped_move_is_surfaced_and_terminalized_as_failure():
    db = MagicMock()
    payload = {"application_id": 22}
    with (
        patch("app.services.workable_op_runner.surface_op_failure") as surface,
        patch("app.services.background_job_runs.update_run") as update_run,
    ):
        result = terminalize_skipped_move_result(
            db,
            11,
            "move_stage",
            payload,
            {"status": "skipped", "reason": "not_linked", "application_id": 22},
            33,
        )

    assert result == {
        "status": "failed",
        "reason": "not_linked",
        "application_id": 22,
        "op_type": "move_stage",
        "code": "not_linked",
    }
    error = surface.call_args.kwargs["error"]
    assert error.code == "not_linked"
    assert error.retriable is False
    surface.assert_called_once_with(
        db,
        organization_id=11,
        op_type="move_stage",
        payload=payload,
        error=error,
    )
    update_run.assert_called_once_with(
        33,
        status="failed",
        counters=result,
        error="The ATS move could not run because its application changed",
        finished=True,
    )
