"""Confirmed, tenant-safe publishing for grounded top-candidate reports."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.agent_chat.tools import dispatch_tool
from app.models.agent_conversation import (
    AUTHOR_ROLE_USER,
    MESSAGE_KIND_CHAT,
    MESSAGE_KIND_TOOL,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.organization import Organization
from app.models.role import Role
from app.models.top_candidates_report import TopCandidatesReport
from app.models.user import User


def _world(db, *, suffix: str = "main"):
    org = Organization(name=f"Report {suffix}", slug=f"report-{suffix}-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"report-{suffix}-{id(db)}@example.test",
        hashed_password="x",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(organization_id=int(org.id), name="Backend", source="manual")
    db.add_all([user, role])
    db.flush()
    conversation = AgentConversation(
        organization_id=int(org.id), role_id=int(role.id)
    )
    db.add(conversation)
    db.flush()
    return org, user, role, conversation


def _snapshot(application_id: int = 41):
    return {
        "spec": {"query": "banking experience", "rank_by": "taali"},
        "shown": 1,
        "total_matched": 3,
        "candidates": [
            {
                "application_id": application_id,
                "candidate_id": 77,
                "role_id": 9,
                "candidate_name": "Ada Lovelace",
                "candidate_email": "ada@example.test",
                "candidate_phone": "+971500000000",
                "frontend_url": "https://app.example.test/jobs/9/candidates/41",
                "workable_profile_url": "https://private.workable.test/candidate/77",
                "ats_context": {"provider": "workable", "candidate_id": "secret"},
                "pipeline_stage": "review",
                "taali_score": 91,
                "criteria": [
                    {
                        "criterion": "banking experience",
                        "status": "met",
                        "grounded": True,
                        "evidence": [{"quote": "Led a retail banking migration."}],
                    }
                ],
            }
        ],
        "excluded": {"not_met_total": 0, "by_criterion": []},
    }


def _persist_preview(db, *, conversation, preview):
    db.add(
        AgentConversationMessage(
            conversation_id=int(conversation.id),
            organization_id=int(conversation.organization_id),
            role_id=int(conversation.role_id),
            author_role=AUTHOR_ROLE_USER,
            kind=MESSAGE_KIND_TOOL,
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "share-preview",
                    "content": json.dumps(preview),
                    "is_error": False,
                }
            ],
        )
    )
    db.flush()


def _confirm(db, *, conversation, user):
    db.add(
        AgentConversationMessage(
            conversation_id=int(conversation.id),
            organization_id=int(conversation.organization_id),
            role_id=int(conversation.role_id),
            author_role=AUTHOR_ROLE_USER,
            author_user_id=int(user.id),
            kind=MESSAGE_KIND_CHAT,
            content=[{"type": "text", "text": "Yes, share that exact shortlist."}],
            text="Yes, share that exact shortlist.",
        )
    )
    db.flush()


def test_report_requires_later_confirmation_and_publishes_scrubbed_snapshot(db):
    org, user, role, conversation = _world(db)
    arguments = {"query": "banking experience", "limit": 5, "rank_by": "taali"}
    with patch(
        "app.mcp.handlers.find_top_candidates", return_value=_snapshot()
    ) as search:
        preview = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "candidate_evidence"
        assert preview["needs_confirmation"] is True
        assert preview["share_preview"] is True
        assert db.query(TopCandidatesReport).count() == 0

        _persist_preview(db, conversation=conversation, preview=preview)
        _confirm(db, conversation=conversation, user=user)
        result = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            role=role,
            user=user,
            conversation=conversation,
        )

    assert search.call_count == 2  # fresh server recomputation at confirmation
    report = db.query(TopCandidatesReport).one()
    assert report.organization_id == org.id
    assert report.role_id == role.id
    assert report.created_by_user_id == user.id
    candidate = report.snapshot["candidates"][0]
    for private_field in (
        "application_id", "application_outcome", "ats_context", "auto_reject_state",
        "bullhorn_status", "candidate_id", "candidate_email", "candidate_phone",
        "created_at", "external_stage_normalized", "frontend_url", "pipeline_stage",
        "pipeline_stage_updated_at", "role_id", "workable_stage", "workable_profile_url",
    ):
        assert private_field not in candidate
    assert result["type"] == "candidate_report"
    assert result["report_url"].endswith(f"/report/{report.token}")
    assert result["_confirmation_consumed"]
    assert "candidate_email" not in result["candidates"][0]


def test_changed_shortlist_requires_a_fresh_confirmation(db):
    _org, user, role, conversation = _world(db, suffix="changed")
    arguments = {"query": "banking experience", "limit": 5}
    with patch(
        "app.mcp.handlers.find_top_candidates",
        side_effect=[_snapshot(41), _snapshot(42)],
    ):
        preview = dispatch_tool(
            "create_top_candidates_report", arguments, db=db, role=role,
            user=user, conversation=conversation,
        )
        _persist_preview(db, conversation=conversation, preview=preview)
        _confirm(db, conversation=conversation, user=user)
        refreshed = dispatch_tool(
            "create_top_candidates_report", arguments, db=db, role=role,
            user=user, conversation=conversation,
        )

    assert refreshed["needs_confirmation"] is True
    assert refreshed["candidates"][0]["application_id"] == 42
    assert "changed" in refreshed["message"].lower()
    assert db.query(TopCandidatesReport).count() == 0


def test_report_action_rejects_cross_tenant_role(db):
    _org, user, _role, conversation = _world(db, suffix="caller")
    _foreign_org, _foreign_user, foreign_role, _foreign_conversation = _world(
        db, suffix="foreign"
    )
    with patch("app.mcp.handlers.find_top_candidates") as search:
        with pytest.raises(ValueError, match="not found"):
            dispatch_tool(
                "create_top_candidates_report",
                {"query": "banking experience"},
                db=db,
                role=foreign_role,
                user=user,
                conversation=conversation,
            )
    search.assert_not_called()
