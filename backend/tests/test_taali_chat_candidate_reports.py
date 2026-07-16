"""Explicit, confirmed report publishing through global Taali Chat."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.job_hiring_team import JobHiringTeam, TEAM_ROLE_RECRUITER
from app.models.organization import Organization
from app.models.role import Role
from app.models.taali_chat_conversation import TaaliChatConversation
from app.models.taali_chat_message import ROLE_USER, TaaliChatMessage
from app.models.top_candidates_report import TopCandidatesReport
from app.models.user import User
from app.taali_chat.tool_registry import dispatch_tool


def _world(db, *, suffix: str = "main"):
    org = Organization(
        name=f"Taali report {suffix}",
        slug=f"taali-report-{suffix}-{id(db)}",
    )
    db.add(org)
    db.flush()
    user = User(
        email=f"taali-report-{suffix}-{id(db)}@example.test",
        hashed_password="x",
        organization_id=int(org.id),
        role="member",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = Role(organization_id=int(org.id), name="Backend", source="manual")
    db.add_all([user, role])
    db.flush()
    db.add(
        JobHiringTeam(
            organization_id=int(org.id),
            role_id=int(role.id),
            user_id=int(user.id),
            team_role=TEAM_ROLE_RECRUITER,
        )
    )
    conversation = TaaliChatConversation(
        organization_id=int(org.id),
        user_id=int(user.id),
        role_id=int(role.id),
        title="Candidate report",
    )
    db.add(conversation)
    db.flush()
    return org, user, role, conversation


def _top_snapshot(application_id: int = 41) -> dict:
    return {
        "spec": {"query": "banking experience", "rank_by": "taali"},
        "shown": 1,
        "total_matched": 1,
        "candidates": [
            {
                "application_id": application_id,
                "candidate_id": 77,
                "role_id": 9,
                "candidate_name": "Ada Lovelace",
                "candidate_email": "ada@example.test",
                "candidate_phone": "+971500000000",
                "frontend_url": "https://app.example.test/jobs/9/candidates/41",
                "workable_profile_url": "https://private.example.test/77",
                "pipeline_stage": "review",
                "criteria": [
                    {
                        "criterion": "banking experience",
                        "status": "met",
                        "grounded": True,
                        "evidence": [
                            {"quote": "Led a retail banking migration.", "source": "cv"}
                        ],
                    }
                ],
            }
        ],
    }


def _screen_snapshot() -> dict:
    return {
        "mode": "rediscovery",
        "database_matches": 18,
        "deep_checked": 1,
        "evidence_succeeded": 1,
        "qualified": 1,
        "returned": 1,
        "capped": True,
        "warnings": [{"code": "verification_capped"}],
        "candidates": [
            {
                "application_id": 51,
                "candidate_id": 81,
                "candidate_name": "Grace Hopper",
                "candidate_email": "grace@example.test",
                "candidate_phone": "+971500000001",
                "frontend_url": "https://app.example.test/jobs/9/candidates/51",
                "criteria": [
                    {
                        "criterion": "payments platform experience",
                        "status": "met",
                        "grounded": True,
                        "evidence": [{"quote": "Built payment rails.", "source": "cv"}],
                    }
                ],
            }
        ],
    }


def _persist_preview(db, *, conversation, preview) -> None:
    db.add(
        TaaliChatMessage(
            conversation_id=int(conversation.id),
            organization_id=int(conversation.organization_id),
            role=ROLE_USER,
            content=[
                {
                    "type": "tool_result",
                    "tool_use_id": "candidate-report-preview",
                    "content": json.dumps(preview),
                    "is_error": False,
                }
            ],
        )
    )
    db.flush()


def _confirm(db, *, conversation) -> None:
    db.add(
        TaaliChatMessage(
            conversation_id=int(conversation.id),
            organization_id=int(conversation.organization_id),
            role=ROLE_USER,
            content=[{"type": "text", "text": "Yes, share that exact result."}],
        )
    )
    db.flush()


def test_top_report_requires_later_confirmation_is_scrubbed_and_idempotent(db):
    org, user, role, conversation = _world(db)
    arguments = {
        "role_id": int(role.id),
        "query": "banking experience",
        "limit": 5,
        "rank_by": "taali",
    }
    with patch(
        "app.mcp.handlers.find_top_candidates", return_value=_top_snapshot()
    ) as search:
        preview = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )
        assert preview["type"] == "candidate_evidence"
        assert preview["needs_confirmation"] is True
        assert preview["share_preview"] is True
        assert db.query(TopCandidatesReport).count() == 0

        _persist_preview(db, conversation=conversation, preview=preview)
        _confirm(db, conversation=conversation)
        result = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )
        replayed = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )

    assert search.call_count == 2
    report = db.query(TopCandidatesReport).one()
    assert report.organization_id == org.id
    assert report.role_id == role.id
    assert report.created_by_user_id == user.id
    assert result == replayed
    assert result["type"] == "candidate_report"
    assert result["report_url"].endswith(f"/report/{report.token}")
    assert result["_confirmation_consumed"]
    candidate = report.snapshot["candidates"][0]
    for private_field in (
        "application_id",
        "candidate_id",
        "candidate_email",
        "candidate_phone",
        "frontend_url",
        "pipeline_stage",
        "role_id",
        "workable_profile_url",
    ):
        assert private_field not in candidate
    expires_at = report.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    assert timedelta(days=29) < expires_at - datetime.now(timezone.utc) <= timedelta(
        days=30
    )


def test_screen_report_preserves_exact_coverage_after_confirmation(db):
    _org, user, role, conversation = _world(db, suffix="screen")
    arguments = {
        "role_id": int(role.id),
        "requirement_text": "payments platform experience",
        "limit": 20,
        "offset": 0,
        "deep_verify": True,
    }
    with patch(
        "app.mcp.handlers.screen_pool_against_requirement",
        return_value=_screen_snapshot(),
    ) as search:
        preview = dispatch_tool(
            "create_screen_pool_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )
        _persist_preview(db, conversation=conversation, preview=preview)
        _confirm(db, conversation=conversation)
        result = dispatch_tool(
            "create_screen_pool_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )

    assert search.call_count == 2
    report = db.query(TopCandidatesReport).one()
    assert result["report_kind"] == "screen_pool"
    assert report.snapshot["database_matches"] == 18
    assert report.snapshot["deep_checked"] == 1
    assert report.snapshot["evidence_succeeded"] == 1
    assert report.snapshot["qualified"] == 1
    assert report.snapshot["capped"] is True
    assert report.snapshot["warnings"] == [{"code": "verification_capped"}]
    assert "candidate_email" not in report.snapshot["candidates"][0]


def test_taali_report_reauthorizes_and_blocks_revoked_recruiter(db):
    _org, user, role, conversation = _world(db, suffix="revoked")
    arguments = {
        "role_id": int(role.id),
        "query": "banking experience",
        "limit": 5,
    }
    with patch("app.mcp.handlers.find_top_candidates", return_value=_top_snapshot()):
        preview = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )
        _persist_preview(db, conversation=conversation, preview=preview)
        _confirm(db, conversation=conversation)
        db.query(JobHiringTeam).filter(
            JobHiringTeam.role_id == role.id,
            JobHiringTeam.user_id == user.id,
        ).delete(synchronize_session=False)
        db.flush()

        with pytest.raises(HTTPException) as exc_info:
            dispatch_tool(
                "create_top_candidates_report",
                arguments,
                db=db,
                user=user,
                conversation=conversation,
            )

    assert exc_info.value.status_code == 403
    assert db.query(TopCandidatesReport).count() == 0


def test_taali_report_detects_snapshot_drift_and_requires_fresh_confirmation(db):
    _org, user, role, conversation = _world(db, suffix="drift")
    arguments = {
        "role_id": int(role.id),
        "query": "banking experience",
        "limit": 5,
    }
    with patch(
        "app.mcp.handlers.find_top_candidates",
        side_effect=[_top_snapshot(41), _top_snapshot(42)],
    ):
        preview = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )
        _persist_preview(db, conversation=conversation, preview=preview)
        _confirm(db, conversation=conversation)
        refreshed = dispatch_tool(
            "create_top_candidates_report",
            arguments,
            db=db,
            user=user,
            conversation=conversation,
        )

    assert refreshed["needs_confirmation"] is True
    assert refreshed["candidates"][0]["application_id"] == 42
    assert "changed" in refreshed["message"].lower()
    assert db.query(TopCandidatesReport).count() == 0
