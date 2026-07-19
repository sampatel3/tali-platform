from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.reconciliation_history import (
    MAX_RECONCILIATION_HISTORY_BYTES,
    MAX_RECONCILIATION_HISTORY_ENTRIES,
    MalformedReconciliationHistory,
    RECONCILIATION_HISTORY_SATURATION_KEY,
    ReconciliationHistoryFull,
    append_reconciliation_history,
    require_reconciliation_history_capacity,
)


_HISTORY_KEY = "reconciliation_observation_history"


def test_hundredth_entry_is_retained_and_next_append_is_fenced() -> None:
    receipt = {
        _HISTORY_KEY: [
            {"observation_id": f"preserved-{index}"}
            for index in range(MAX_RECONCILIATION_HISTORY_ENTRIES - 1)
        ]
    }

    appended = append_reconciliation_history(
        receipt,
        history_key=_HISTORY_KEY,
        entry={"observation_id": "preserved-99"},
        saturated_at="2026-07-17T10:00:00+00:00",
    )

    assert appended.appended is True
    assert len(receipt[_HISTORY_KEY]) == MAX_RECONCILIATION_HISTORY_ENTRIES
    retained = deepcopy(receipt[_HISTORY_KEY])
    with pytest.raises(ReconciliationHistoryFull):
        require_reconciliation_history_capacity(receipt, _HISTORY_KEY)
    assert receipt[_HISTORY_KEY] == retained


def test_oversized_new_entry_preserves_prior_history_and_marks_saturation() -> None:
    prior = [{"observation_id": "keep-exactly", "evidence": {"answer": "yes"}}]
    receipt = {_HISTORY_KEY: deepcopy(prior)}

    appended = append_reconciliation_history(
        receipt,
        history_key=_HISTORY_KEY,
        entry={"evidence": {"raw": "x" * MAX_RECONCILIATION_HISTORY_BYTES}},
        saturated_at="2026-07-17T10:00:00+00:00",
    )

    assert appended.appended is False
    assert receipt[_HISTORY_KEY] == prior
    marker = receipt[RECONCILIATION_HISTORY_SATURATION_KEY][_HISTORY_KEY]
    assert marker["reason"] == "candidate_exceeds_byte_limit"
    assert marker["max_bytes"] == MAX_RECONCILIATION_HISTORY_BYTES
    with pytest.raises(ReconciliationHistoryFull):
        require_reconciliation_history_capacity(receipt, _HISTORY_KEY)
    stable_marker = deepcopy(marker)
    repeated = append_reconciliation_history(
        receipt,
        history_key=_HISTORY_KEY,
        entry={"evidence": {"raw": "new-result-after-a-race"}},
        saturated_at="2026-07-17T11:00:00+00:00",
    )
    assert repeated.appended is False
    assert receipt[_HISTORY_KEY] == prior
    assert receipt[RECONCILIATION_HISTORY_SATURATION_KEY][_HISTORY_KEY] == stable_marker


def test_existing_byte_exhaustion_fails_without_rewriting() -> None:
    receipt = {
        _HISTORY_KEY: [
            {"observation_id": "kept", "raw": "x" * MAX_RECONCILIATION_HISTORY_BYTES}
        ]
    }
    original = deepcopy(receipt)

    with pytest.raises(ReconciliationHistoryFull):
        require_reconciliation_history_capacity(receipt, _HISTORY_KEY)

    assert receipt == original


@pytest.mark.parametrize(
    "receipt",
    [
        {_HISTORY_KEY: [{"valid": True}, "must-not-be-filtered"]},
        {_HISTORY_KEY: [], RECONCILIATION_HISTORY_SATURATION_KEY: "broken"},
    ],
)
def test_malformed_history_or_marker_fails_without_rewriting(receipt) -> None:
    original = deepcopy(receipt)

    with pytest.raises(MalformedReconciliationHistory):
        require_reconciliation_history_capacity(receipt, _HISTORY_KEY)

    assert receipt == original
