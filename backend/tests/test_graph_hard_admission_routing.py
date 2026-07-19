"""Every Graphiti provider-bearing route must enter an explicit hard meter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.candidate_graph import agent_episodes


def test_recruiter_action_without_role_is_explicit_workspace_spend():
    captured: dict = {}

    def _dispatch(episodes, **kwargs):
        captured.update(kwargs)
        return len(list(episodes))

    with patch.object(
        agent_episodes.graph_client,
        "group_id_for_org",
        side_effect=lambda value: f"org-{value}",
    ), patch.object(agent_episodes, "dispatch", side_effect=_dispatch):
        ok = agent_episodes.emit_recruiter_action_event(
            organization_id=42,
            decision_id=99,
            recruiter_id=3,
            action="teach",
            reason="Correction",
            happened_at=datetime.now(timezone.utc),
        )

    assert ok is True
    assert captured["bill_organization_id"] == 42
    assert captured["bill_role_id"] is None
    assert captured["require_hard_admission"] is True
    assert captured["require_role_admission"] is False
    assert captured["raise_on_error"] is True
