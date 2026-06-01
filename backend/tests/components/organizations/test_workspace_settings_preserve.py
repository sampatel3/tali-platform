"""Operator-only workspace_settings keys must survive read/save.

Regression for the silent-drop bug: the GET/PATCH org handlers persist
``resolved_workspace_settings(org)`` / ``merge_workspace_settings(...)`` back
onto the org, and those used to round-trip through the strict ``WorkspaceSettings``
schema — which dropped any key it didn't declare (decision_policy_auto_apply,
decision_policy_autoresearch, decision_policy_min_signals_for_retune). That made
the autoresearch / auto-apply opt-ins reset on the next settings read or save.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.domains.identity_access.organization_serialization import (
    merge_workspace_settings,
    resolved_workspace_settings,
)
from app.schemas.organization import (
    OrgUpdate,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)


def _org(ws: dict):
    return SimpleNamespace(workspace_settings=ws)


def test_resolved_preserves_operator_only_flags():
    org = _org({
        "locale": "English (UK)",
        "decision_policy_autoresearch": "agentic",
        "decision_policy_auto_apply": True,
        "decision_policy_min_signals_for_retune": 12,
    })
    out = resolved_workspace_settings(org)
    assert out["decision_policy_autoresearch"] == "agentic"
    assert out["decision_policy_auto_apply"] is True
    assert out["decision_policy_min_signals_for_retune"] == 12
    assert out["locale"] == "English (UK)"  # known field still normalized through


def test_merge_keeps_operator_flags_when_user_saves_normal_settings():
    org = _org({"decision_policy_autoresearch": "grid", "locale": "English (US)"})
    incoming = OrgUpdate(
        workspace_settings=WorkspaceSettingsUpdate(candidate_facing_brand="Acme")
    )
    out = merge_workspace_settings(org, incoming)
    assert out["decision_policy_autoresearch"] == "grid"  # survived the save
    assert out["candidate_facing_brand"] == "Acme"


def test_api_response_schema_excludes_operator_flags():
    # The response builder re-applies the strict schema, so internal flags
    # never leak into the API response even though they're persisted.
    resolved = resolved_workspace_settings(_org({"decision_policy_autoresearch": "agentic"}))
    response = WorkspaceSettings(**resolved).model_dump()
    assert "decision_policy_autoresearch" not in response
