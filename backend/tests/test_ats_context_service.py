from types import SimpleNamespace

from app.services.ats_context_service import application_ats_context


def _app(**overrides):
    values = {
        "bullhorn_job_submission_id": None,
        "bullhorn_status": None,
        "workable_candidate_id": None,
        "workable_stage": None,
        "external_stage_raw": None,
        "external_stage_normalized": None,
        "pipeline_stage": "applied",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_unmapped_bullhorn_status_is_explicitly_unknown_and_fails_closed():
    context = application_ats_context(
        _app(bullhorn_job_submission_id="42", bullhorn_status="Client Bespoke")
    )
    assert context == {
        "provider": "bullhorn",
        "raw_stage": "Client Bespoke",
        "normalized_stage": None,
        "needs_mapping": True,
        "post_handover": False,
        "writeback_linked": True,
    }


def test_mapped_bullhorn_advance_is_a_post_handover_signal():
    context = application_ats_context(
        _app(
            bullhorn_job_submission_id="42",
            bullhorn_status="Interview Scheduled",
            external_stage_normalized="advanced",
        )
    )
    assert context["needs_mapping"] is False
    assert context["normalized_stage"] == "advanced"
    assert context["post_handover"] is True


def test_workable_stage_uses_the_same_provider_neutral_contract():
    context = application_ats_context(
        _app(workable_candidate_id="abc", workable_stage="Technical Interview")
    )
    assert context["provider"] == "workable"
    assert context["raw_stage"] == "Technical Interview"
    assert context["post_handover"] is True
    assert context["needs_mapping"] is False


def test_native_application_has_no_external_writeback_claim():
    context = application_ats_context(_app(pipeline_stage="review"))
    assert context["provider"] == "native"
    assert context["writeback_linked"] is False
    assert context["needs_mapping"] is False
