"""Unit contracts for durable ATS outcome receipt reconstruction."""

from types import SimpleNamespace

from app.services.ats_writeback_state import (
    OUTCOME_WRITEBACK_KEY,
    replace_sync_state_preserving_writeback,
    set_outcome_writeback_state,
)


def _application():
    return SimpleNamespace(id=17, integration_sync_state={"sync_cursor": "keep"})


def _reconciliation_fields() -> dict:
    return {
        "reconciliation_status": "resolved",
        "reconciliation_resolved_at": "2026-07-17T10:00:00+00:00",
        "provider_reconciled_at": "2026-07-17T10:00:00+00:00",
        "resolved_operation_id": "manual:17:one",
        "resolved_receipt_key": OUTCOME_WRITEBACK_KEY,
        "reconciliation_resolved_by_actor_id": 9,
        "reconciliation_resolved_by_actor_type": "recruiter",
        "reconciliation_evidence": {"observation_id": "obs-1"},
        "reconciliation_observation_id": "obs-1",
        "reconciliation_disposition": "confirm_provider_matches_local",
        "reconciliation_observation": {"observation_id": "obs-1"},
        "reconciliation_observation_history": [{"observation_id": "obs-1"}],
        "reconciliation_resolution_history": [{"observation_id": "obs-1"}],
        "reconciliation_last_checked_at": "2026-07-17T09:59:00+00:00",
    }


def _seed_reconciled_receipt(app) -> dict:
    set_outcome_writeback_state(
        app,
        provider="workable",
        status="provider_call_started",
        target_outcome="rejected",
        operation_id="manual:17:one",
        provider_target_id="candidate-17",
    )
    state = dict(app.integration_sync_state)
    receipt = dict(state[OUTCOME_WRITEBACK_KEY])
    receipt.update(_reconciliation_fields())
    state[OUTCOME_WRITEBACK_KEY] = receipt
    app.integration_sync_state = state
    return receipt


def test_same_exact_operation_preserves_append_only_reconciliation_evidence():
    app = _application()
    previous = _seed_reconciled_receipt(app)

    receipt = set_outcome_writeback_state(
        app,
        provider="workable",
        status="confirmed",
        target_outcome="rejected",
        operation_id="manual:17:one",
        provider_target_id="candidate-17",
    )

    for key in _reconciliation_fields():
        assert receipt[key] == previous[key]
    assert app.integration_sync_state["sync_cursor"] == "keep"


def test_different_operation_does_not_inherit_reconciliation_evidence():
    app = _application()
    _seed_reconciled_receipt(app)

    receipt = set_outcome_writeback_state(
        app,
        provider="workable",
        status="provider_call_started",
        target_outcome="open",
        operation_id="manual:17:two",
        provider_target_id="candidate-17",
    )

    assert not set(_reconciliation_fields()).intersection(receipt)
    assert receipt["operation_id"] == "manual:17:two"
    assert app.integration_sync_state["sync_cursor"] == "keep"


def test_sync_replacement_preserves_note_current_and_append_only_history():
    app = _application()
    current = {"operation_id": "note-B", "status": "provider_succeeded"}
    history = [{"operation_id": "note-A", "status": "confirmed"}]
    app.integration_sync_state.update(
        ats_note_writeback=current,
        ats_note_writeback_history=history,
    )

    replace_sync_state_preserving_writeback(app, {"sync_cursor": "new"})

    assert app.integration_sync_state["sync_cursor"] == "new"
    assert app.integration_sync_state["ats_note_writeback"] == current
    assert app.integration_sync_state["ats_note_writeback_history"] == history
