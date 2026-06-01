"""graph_sync per-org attribution: the live interview/event sync paths must
thread the org to dispatch so the metered wrapper writes a per-org graph_sync
usage_event (instead of an unattributed org=NULL call_log row).

Regression guard for the residual NULL-org leaks the reconciliation audit
surfaced after PR #477 (which only covered the outbox drain + backfill).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.candidate_graph import client as graph_client
from app.candidate_graph import episodes as episode_module
from app.candidate_graph import sync as sync_module


def _capture_dispatch(captured):
    def _fake(eps, **kwargs):
        captured.update(kwargs)
        return 1

    return _fake


def test_sync_event_attributes_explicit_org_and_db():
    ev = MagicMock()
    ev.organization_id = 99
    captured: dict = {}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_event_episode", return_value=MagicMock()
    ), patch.object(
        episode_module, "dispatch", side_effect=_capture_dispatch(captured)
    ):
        sync_module.sync_event(ev, db="DB_SENTINEL", bill_organization_id=99)
    assert captured["bill_organization_id"] == 99
    assert captured["db"] == "DB_SENTINEL"


def test_sync_event_falls_back_to_event_org():
    """No explicit org → use the event's own (non-nullable) organization_id."""
    ev = MagicMock()
    ev.organization_id = 77
    captured: dict = {}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_event_episode", return_value=MagicMock()
    ), patch.object(
        episode_module, "dispatch", side_effect=_capture_dispatch(captured)
    ):
        sync_module.sync_event(ev, db="DB")
    assert captured["bill_organization_id"] == 77
    assert captured["db"] == "DB"


def test_sync_interview_attributes_explicit_org():
    iv = MagicMock()
    iv.organization_id = 55
    captured: dict = {}
    with patch.object(graph_client, "is_configured", return_value=True), patch.object(
        episode_module, "build_interview_episodes", return_value=[MagicMock()]
    ), patch.object(
        episode_module, "dispatch", side_effect=_capture_dispatch(captured)
    ):
        sync_module.sync_interview(iv, db="DB", bill_organization_id=55)
    assert captured["bill_organization_id"] == 55
    assert captured["db"] == "DB"
