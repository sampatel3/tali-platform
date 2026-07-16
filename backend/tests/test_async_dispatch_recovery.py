"""Lost/ambiguous broker publish, duplicate delivery, and lease recovery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.agent_conversation import (
    AUTHOR_ROLE_ASSISTANT,
    AgentConversation,
    AgentConversationMessage,
)
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.outreach_campaign import OutreachCampaign, OutreachMessage
from app.models.pool_rescore_job import PoolRescoreJob
from app.models.role import Role
from app.models.user import User
from tests.conftest import auth_headers


def _org_id(db, email: str) -> int:
    return int(db.query(User).filter(User.email == email).one().organization_id)


def _role(db, org_id: int, *, name: str = "Recovery role") -> Role:
    row = Role(
        organization_id=org_id,
        name=name,
        source="manual",
        agentic_mode_enabled=True,
        job_spec_text="Hire a reliable engineer",
    )
    db.add(row)
    db.flush()
    return row


def test_outreach_generate_publish_failure_is_durable_and_recovered(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    campaign = OutreachCampaign(organization_id=org_id, name="Recovery wave")
    db.add(campaign)
    db.flush()
    db.add(
        OutreachMessage(
            campaign_id=campaign.id,
            organization_id=org_id,
            email="draft-recover@example.com",
            status="pending",
        )
    )
    db.commit()

    with patch(
        "app.tasks.outreach_tasks.generate_campaign_drafts.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/outreach/campaigns/{campaign.id}/generate",
            headers=headers,
            json={"confirm": True},
        )
    assert response.status_code == 200, response.text
    assert response.json()["dispatch_pending"] is True
    db.refresh(campaign)
    assert campaign.status == "generating"

    from app.tasks.outreach_tasks import recover_outreach_campaign_work

    with patch("app.tasks.outreach_tasks.generate_campaign_drafts.delay") as delay:
        summary = recover_outreach_campaign_work.run(limit=10)
    delay.assert_called_once_with(int(campaign.id))
    assert summary["kicked"] == 1


def test_outreach_duplicate_send_uses_one_stable_provider_request(db):
    org = Organization(name="Send recovery", slug=f"send-recovery-{id(db)}")
    db.add(org)
    db.flush()
    campaign = OutreachCampaign(
        organization_id=org.id,
        name="Wave",
        status="sending",
    )
    db.add(campaign)
    db.flush()
    message = OutreachMessage(
        campaign_id=campaign.id,
        organization_id=org.id,
        email="once@example.com",
        subject="Hi",
        body="Interested? {{cta_url}}",
        status="queued",
    )
    db.add(message)
    db.commit()

    from app.tasks import outreach_tasks

    email = MagicMock()
    email.send_outreach_email.return_value = {"success": True, "email_id": "resend-1"}
    with patch(
        "app.components.notifications.email_client.EmailService", return_value=email
    ), patch.object(outreach_tasks.time, "sleep", return_value=None):
        first = outreach_tasks.send_campaign_messages.run(int(campaign.id))
        second = outreach_tasks.send_campaign_messages.run(int(campaign.id))

    assert first["sent"] == 1
    assert second["sent"] == 0
    email.send_outreach_email.assert_called_once()
    assert email.send_outreach_email.call_args.kwargs["idempotency_key"] == (
        f"outreach-message/{int(message.id)}"
    )


def test_outreach_transient_failure_stays_recoverable_without_leaking_provider_error(db):
    org = Organization(name="Transient send", slug=f"transient-send-{id(db)}")
    db.add(org)
    db.flush()
    campaign = OutreachCampaign(
        organization_id=org.id,
        name="Retry wave",
        status="sending",
    )
    db.add(campaign)
    db.flush()
    message = OutreachMessage(
        campaign_id=campaign.id,
        organization_id=org.id,
        email="retry@example.com",
        subject="Hi",
        body="Interested? {{cta_url}}",
        status="queued",
    )
    db.add(message)
    db.commit()

    from app.tasks import outreach_tasks

    provider_secret = "https://api-key@provider.invalid/internal"
    email = MagicMock()
    email.send_outreach_email.return_value = {
        "success": False,
        "error": provider_secret,
        "retryable": True,
    }
    with patch(
        "app.components.notifications.email_client.EmailService", return_value=email
    ), patch.object(outreach_tasks.time, "sleep", return_value=None):
        result = outreach_tasks.send_campaign_messages.run(int(campaign.id))

    db.refresh(message)
    db.refresh(campaign)
    assert result["failed"] == 0
    assert message.status == "queued"
    assert message.delivery_next_attempt_at is not None
    assert "temporarily unavailable" in (message.error or "").lower()
    assert provider_secret not in (message.error or "")
    assert campaign.status == "sending"


def test_outreach_idempotency_key_reaches_resend_wire():
    from app.components.notifications.email_client import EmailService

    service = EmailService(api_key="test", from_email="sender@example.com")
    with patch(
        "app.components.notifications.email_client._send_resend_email",
        return_value={"id": "provider-id"},
    ) as send:
        result = service.send_outreach_email(
            to_email="candidate@example.com",
            subject="Hello",
            text_body="Text",
            html_body="<p>Text</p>",
            reply_to="recruiter@example.com",
            unsubscribe_url="https://example.com/unsubscribe",
            idempotency_key="outreach-message/42",
        )
    assert result["success"] is True
    assert send.call_args.kwargs["idempotency_key"] == "outreach-message/42"


def test_pool_rescore_publish_failure_and_stale_lease_recover(client, db):
    headers, email = auth_headers(client)
    _org_id(db, email)
    with patch(
        "app.tasks.pool_rescore_tasks.rescore_pool_against_requirement.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            "/api/v1/candidates/pool-rescore",
            headers=headers,
            json={"requirement_text": "Python", "application_ids": [123]},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["dispatch_pending"] is True
    job = db.get(PoolRescoreJob, int(payload["job_id"]))
    assert job.status == "pending"

    job.status = "running"
    job.lease_until = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    from app.tasks.pool_rescore_tasks import recover_pool_rescore_jobs

    with patch("app.tasks.pool_rescore_tasks.rescore_pool_against_requirement.delay") as delay:
        summary = recover_pool_rescore_jobs.run(limit=10)
    delay.assert_called_once_with(int(job.id))
    assert summary["stale_recovered"] == 1


def test_pool_rescore_duplicate_worker_reuses_per_candidate_receipt(db):
    org = Organization(name="Pool recovery", slug=f"pool-recovery-{id(db)}")
    db.add(org)
    db.flush()
    role = _role(db, int(org.id))
    candidate = Candidate(organization_id=org.id, email="pool@example.com", full_name="Pool")
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
        cv_text="Python engineer",
    )
    db.add(application)
    db.flush()
    job = PoolRescoreJob(
        organization_id=org.id,
        requirement_text="Python",
        requirement_hash="hash",
        application_ids=[application.id],
        status="pending",
    )
    db.add(job)
    db.commit()

    output = SimpleNamespace(
        scoring_status=SimpleNamespace(value="ok"),
        role_fit_score=88,
        summary="Strong",
        cache_hit=False,
    )
    from app.tasks.pool_rescore_tasks import rescore_pool_against_requirement

    with patch(
        "app.services.claude_client_resolver.get_metered_client", return_value=object()
    ), patch("app.cv_matching.holistic.run_holistic_match", return_value=output) as score:
        first = rescore_pool_against_requirement.run(int(job.id))
        second = rescore_pool_against_requirement.run(int(job.id))
    assert first["scored"] == 1
    assert second["skipped"] is True
    score.assert_called_once()


def test_agent_chat_publish_failure_stays_pending_then_recovers(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Chat recovery")
    db.commit()
    with patch(
        "app.tasks.agent_chat_tasks.run_agent_chat_turn.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Who should I review?"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["dispatch_pending"] is True
    conversation = db.query(AgentConversation).filter_by(role_id=role.id).one()
    assert conversation.turn_status == "pending"

    from app.tasks.agent_chat_tasks import recover_agent_chat_turns

    user_id = int(db.query(User).filter(User.email == email).one().id)
    turn_message_id = int(conversation.turn_message_id)
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay") as delay:
        summary = recover_agent_chat_turns.run(limit=10)
    assert summary["kicked"] == 1
    delay.assert_called_once_with(
        conversation_id=int(conversation.id),
        role_id=int(role.id),
        user_id=user_id,
        organization_id=org_id,
        turn_message_id=turn_message_id,
    )
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay") as delay:
        second = recover_agent_chat_turns.run(limit=10)
    assert second["kicked"] == 0
    delay.assert_not_called()


def test_agent_chat_duplicate_delivery_claims_turn_once(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Chat once")
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Review once"},
        )
    payload = response.json()
    conversation = db.get(AgentConversation, int(payload["conversation_id"]))

    from app.agent_chat.service import post_agent_message
    from app.tasks.agent_chat_tasks import run_agent_chat_turn

    def _reply(*, db, conversation, **_kwargs):
        return post_agent_message(db, conversation=conversation, text="Done")

    with patch("app.agent_chat.engine.run_agent_response", side_effect=_reply) as run:
        first = run_agent_chat_turn.run(
            int(conversation.id),
            int(role.id),
            int(db.query(User).filter(User.email == email).one().id),
            org_id,
            int(conversation.turn_message_id),
        )
        second = run_agent_chat_turn.run(
            int(conversation.id),
            int(role.id),
            int(db.query(User).filter(User.email == email).one().id),
            org_id,
            int(conversation.turn_message_id),
        )
    assert first["status"] == "replied"
    assert second["reason"] == "turn_already_closed"
    run.assert_called_once()


def test_agent_chat_recovered_exact_payload_cannot_execute_a_newer_turn(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Chat exact recovery")
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        first = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Old turn"},
        )
    conversation = db.get(AgentConversation, int(first.json()["conversation_id"]))
    old_message_id = int(conversation.turn_message_id)
    user_id = int(db.query(User).filter(User.email == email).one().id)

    # Simulate the old turn closing after recovery captured its payload, then a
    # newer recruiter turn taking ownership of the same role conversation.
    from app.agent_chat.service import post_agent_message

    post_agent_message(db, conversation=conversation, text="Old turn complete")
    conversation.turn_status = "done"
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        second = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "New turn"},
        )
    assert second.status_code == 200
    db.refresh(conversation)
    new_message_id = int(conversation.turn_message_id)
    assert new_message_id != old_message_id

    from app.tasks.agent_chat_tasks import run_agent_chat_turn

    with patch("app.agent_chat.engine.run_agent_response") as run:
        replay = run_agent_chat_turn.run(
            conversation_id=int(conversation.id),
            role_id=int(role.id),
            user_id=user_id,
            organization_id=org_id,
            turn_message_id=old_message_id,
        )
    assert replay == {
        "status": "skipped",
        "reason": "superseded_turn",
        "role_id": int(role.id),
    }
    run.assert_not_called()
    db.refresh(conversation)
    assert conversation.turn_status == "pending"
    assert int(conversation.turn_message_id) == new_message_id


def test_agent_chat_legacy_four_argument_payload_runs_on_new_worker(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Legacy chat payload")
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Queued by the old API"},
        )
    conversation = db.get(AgentConversation, int(response.json()["conversation_id"]))
    user_id = int(db.query(User).filter(User.email == email).one().id)

    from app.agent_chat.service import post_agent_message
    from app.tasks.agent_chat_tasks import run_agent_chat_turn

    def _reply(*, db, conversation, **_kwargs):
        return post_agent_message(db, conversation=conversation, text="Legacy delivered")

    with patch("app.agent_chat.engine.run_agent_response", side_effect=_reply) as run:
        result = run_agent_chat_turn.run(
            conversation_id=int(conversation.id),
            role_id=int(role.id),
            user_id=user_id,
            organization_id=org_id,
        )
    assert result["status"] == "replied"
    run.assert_called_once()


def test_agent_chat_legacy_payload_for_another_user_cannot_close_current_turn(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Legacy wrong user")
    owner = db.query(User).filter(User.email == email).one()
    other = User(
        email=f"legacy-other-{id(db)}@example.com",
        hashed_password="x",
        full_name="Other",
        organization_id=org_id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(other)
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Owner's current turn"},
        )
    conversation = db.get(AgentConversation, int(response.json()["conversation_id"]))

    from app.tasks.agent_chat_tasks import run_agent_chat_turn

    with patch("app.agent_chat.engine.run_agent_response") as run:
        result = run_agent_chat_turn.run(
            conversation_id=int(conversation.id),
            role_id=int(role.id),
            user_id=int(other.id),
            organization_id=org_id,
        )
    assert result["reason"] == "legacy_turn_context_mismatch"
    run.assert_not_called()
    db.refresh(conversation)
    assert conversation.turn_status == "pending"
    message = db.get(AgentConversationMessage, int(conversation.turn_message_id))
    assert int(message.author_user_id) == int(owner.id)


def test_agent_chat_exact_context_mismatch_closes_once_and_is_not_recovered(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Exact mismatch")
    other = User(
        email=f"exact-other-{id(db)}@example.com",
        hashed_password="x",
        full_name="Other",
        organization_id=org_id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(other)
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Bound to the original author"},
        )
    conversation = db.get(AgentConversation, int(response.json()["conversation_id"]))

    from app.tasks.agent_chat_tasks import recover_agent_chat_turns, run_agent_chat_turn

    result = run_agent_chat_turn.run(
        conversation_id=int(conversation.id),
        role_id=int(role.id),
        user_id=int(other.id),
        organization_id=org_id,
        turn_message_id=int(conversation.turn_message_id),
    )
    assert result["reason"] == "turn_context_mismatch"
    db.refresh(conversation)
    assert conversation.turn_status == "done"
    assert conversation.turn_error == "turn_context_mismatch"
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay") as delay:
        recovered = recover_agent_chat_turns.run(limit=10)
    assert recovered["kicked"] == 0
    delay.assert_not_called()


def test_agent_chat_expired_owner_cannot_commit_staged_reply(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Lost lease")
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        response = client.post(
            f"/api/v1/agent-chat/conversations/{role.id}/messages",
            headers=headers,
            json={"message": "Do not duplicate this"},
        )
    conversation = db.get(AgentConversation, int(response.json()["conversation_id"]))
    user_id = int(db.query(User).filter(User.email == email).one().id)

    from app.agent_chat.service import post_agent_message
    from app.tasks.agent_chat_tasks import run_agent_chat_turn

    def _stale_reply(*, db, conversation, **_kwargs):
        # Simulate recovery assigning a newer owner generation before this
        # worker's final commit. The task's CAS must roll this staged row back.
        conversation.turn_attempts = int(conversation.turn_attempts) + 1
        return post_agent_message(db, conversation=conversation, text="stale reply")

    with patch("app.agent_chat.engine.run_agent_response", side_effect=_stale_reply):
        result = run_agent_chat_turn.run(
            conversation_id=int(conversation.id),
            role_id=int(role.id),
            user_id=user_id,
            organization_id=org_id,
            turn_message_id=int(conversation.turn_message_id),
        )
    assert result["reason"] == "turn_lease_lost"
    assert (
        db.query(AgentConversationMessage)
        .filter(
            AgentConversationMessage.conversation_id == int(conversation.id),
            AgentConversationMessage.author_role == AUTHOR_ROLE_ASSISTANT,
            AgentConversationMessage.text == "stale reply",
        )
        .count()
        == 0
    )


def test_agent_chat_lease_boundary_and_task_limits_are_non_overlapping(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    equal_role = _role(db, org_id, name="Lease equal")
    future_role = _role(db, org_id, name="Lease future")
    db.commit()
    with patch("app.tasks.agent_chat_tasks.run_agent_chat_turn.delay"):
        equal_response = client.post(
            f"/api/v1/agent-chat/conversations/{equal_role.id}/messages",
            headers=headers,
            json={"message": "Equal lease"},
        )
        future_response = client.post(
            f"/api/v1/agent-chat/conversations/{future_role.id}/messages",
            headers=headers,
            json={"message": "Future lease"},
        )
    equal = db.get(AgentConversation, int(equal_response.json()["conversation_id"]))
    future = db.get(AgentConversation, int(future_response.json()["conversation_id"]))
    fixed = datetime.now(timezone.utc).replace(microsecond=0)
    equal.turn_status = future.turn_status = "running"
    equal.turn_lease_until = fixed
    future.turn_lease_until = fixed + timedelta(seconds=1)
    db.commit()

    from app.tasks import agent_chat_tasks

    with patch.object(agent_chat_tasks, "_now", return_value=fixed), patch.object(
        agent_chat_tasks.run_agent_chat_turn, "delay"
    ) as delay:
        summary = agent_chat_tasks.recover_agent_chat_turns.run(limit=10)
    assert summary["stale_recovered"] == 1
    assert delay.call_count == 1
    assert delay.call_args.kwargs["conversation_id"] == int(equal.id)
    db.refresh(future)
    assert future.turn_status == "running"
    assert agent_chat_tasks.run_agent_chat_turn.soft_time_limit == 42 * 60
    assert agent_chat_tasks.run_agent_chat_turn.time_limit == 45 * 60
    assert agent_chat_tasks._TURN_LEASE > timedelta(
        seconds=agent_chat_tasks.run_agent_chat_turn.time_limit
    )


def test_agent_reevaluation_duplicate_delivery_has_one_focused_cycle(db):
    org = Organization(name="Re-eval recovery", slug=f"reeval-recovery-{id(db)}")
    db.add(org)
    db.flush()
    role = _role(db, int(org.id))
    candidate = Candidate(organization_id=org.id, email="reeval@example.com", full_name="R")
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=application.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="discarded",
        reasoning="stale",
        model_version="m",
        prompt_version="p",
        idempotency_key=f"recovery:{application.id}",
        reevaluation_status="pending",
    )
    db.add(decision)
    db.commit()

    from app.tasks.reevaluation_tasks import run_agent_re_evaluation

    completed = {
        "status": "ok",
        "run_status": "succeeded",
        "agent_run_id": 42,
    }
    with patch("app.tasks.agent_tasks.agent_manual_run.run", return_value=completed) as run:
        first = run_agent_re_evaluation.run(int(decision.id))
        second = run_agent_re_evaluation.run(int(decision.id))
    assert first["status"] == "done"
    assert second["reason"] == "already_closed"
    run.assert_called_once_with(
        role_id=int(role.id),
        application_id=int(application.id),
        dispatch_key=f"agent-reevaluation/{int(decision.id)}",
    )


def test_agent_reevaluation_duplicate_delivery_respects_backoff_due_time(db):
    org = Organization(name="Re-eval backoff", slug=f"reeval-backoff-{id(db)}")
    db.add(org)
    db.flush()
    role = _role(db, int(org.id))
    candidate = Candidate(
        organization_id=org.id,
        email="reeval-backoff@example.com",
        full_name="R",
    )
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=application.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="discarded",
        reasoning="stale",
        model_version="m",
        prompt_version="p",
        idempotency_key=f"backoff:{application.id}",
        reevaluation_status="pending",
        reevaluation_next_attempt_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(decision)
    db.commit()

    from app.tasks.reevaluation_tasks import run_agent_re_evaluation

    with patch("app.tasks.agent_tasks.agent_manual_run.run") as run:
        result = run_agent_re_evaluation.run(int(decision.id))
    assert result == {"status": "skipped", "reason": "not_due"}
    run.assert_not_called()
    db.refresh(decision)
    assert decision.reevaluation_status == "pending"
    assert int(decision.reevaluation_attempts or 0) == 0


def test_dispatch_migration_backfills_only_sending_campaign_approvals():
    from pathlib import Path

    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "174_async_dispatch_recovery.py"
    ).read_text(encoding="utf-8")
    assert "SET status = 'queued'" in source
    assert "WHERE status = 'approved'" in source
    assert "WHERE status = 'sending'" in source


def test_agent_reevaluation_route_publish_failure_is_recovered(client, db):
    headers, email = auth_headers(client)
    org_id = _org_id(db, email)
    role = _role(db, org_id, name="Re-eval route recovery")
    candidate = Candidate(organization_id=org_id, email="route-r@example.com", full_name="R")
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org_id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=org_id,
        role_id=role.id,
        application_id=application.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="pending",
        reasoning="old inputs",
        model_version="m",
        prompt_version="p",
        idempotency_key=f"route-recovery:{application.id}",
    )
    db.add(decision)
    db.commit()

    with patch(
        "app.tasks.reevaluation_tasks.run_agent_re_evaluation.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        response = client.post(
            f"/api/v1/agent-decisions/{decision.id}/re-evaluate",
            headers=headers,
        )
    assert response.status_code == 200, response.text
    assert response.json()["queued"] is False
    assert "automatic retry" in response.json()["detail"]
    db.refresh(decision)
    assert decision.status == "discarded"
    assert decision.reevaluation_status == "pending"

    from app.tasks.reevaluation_tasks import recover_agent_re_evaluations

    with patch("app.tasks.reevaluation_tasks.run_agent_re_evaluation.delay") as delay:
        summary = recover_agent_re_evaluations.run(limit=10)
    delay.assert_called_once_with(int(decision.id))
    assert summary["kicked"] == 1


def test_agent_cycle_dispatch_key_replays_original_paid_run(db):
    org = Organization(name="Agent receipt", slug=f"agent-receipt-{id(db)}")
    db.add(org)
    db.flush()
    role = _role(db, int(org.id), name="Receipt role")
    original = AgentRun(
        id=9_900_001,
        organization_id=org.id,
        role_id=role.id,
        trigger="manual",
        dispatch_key="agent-reevaluation/receipt-test",
        status="succeeded",
        model_version="m",
        prompt_version="p",
        finished_at=datetime.now(timezone.utc),
    )
    db.add(original)
    db.commit()

    from app.agent_runtime.orchestrator import run_cycle

    replay = run_cycle(
        db,
        role=role,
        trigger="manual",
        application_id=123,
        dispatch_key="agent-reevaluation/receipt-test",
    )
    assert int(replay.id) == int(original.id)
    assert db.query(AgentRun).filter_by(dispatch_key=original.dispatch_key).count() == 1


def test_confirmed_manual_run_intent_is_durable_and_redispatch_is_bounded(db):
    org = Organization(name="Manual intent", slug=f"manual-intent-{id(db)}")
    db.add(org)
    db.flush()
    user = User(
        email=f"manual-intent-{id(db)}@example.com",
        hashed_password="x",
        full_name="Recruiter",
        organization_id=org.id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    role = _role(db, int(org.id), name="Manual intent role")
    # Keep execution on the free readiness gate; this test is about dispatch
    # ownership and must never need a model/provider client.
    role.job_spec_text = None
    role.description = None
    db.add(user)
    db.commit()

    from app.agent_chat.application_commands import enqueue_manual_run

    dispatch_key = "chat-command/manual-intent-test"
    with patch(
        "app.tasks.agent_tasks.agent_manual_run.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        result = enqueue_manual_run(
            db,
            role,
            user,
            dispatch_key=dispatch_key,
        )
    assert result["dispatch_pending"] is True
    intent = db.query(AgentRun).filter_by(dispatch_key=dispatch_key).one()
    assert intent.status == "dispatching"
    snapshot = dict(intent.agent_state_snapshot or {})
    assert int(snapshot["dispatch_attempts"]) == 1
    snapshot["dispatch_next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    intent.agent_state_snapshot = snapshot
    db.commit()

    from app.tasks.agent_tasks import (
        agent_manual_run,
        recover_dispatching_manual_agent_runs,
    )

    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        first = recover_dispatching_manual_agent_runs.run(limit=10)
    assert first["kicked"] == 1
    delay.assert_called_once_with(
        role_id=int(role.id),
        application_id=None,
        dispatch_key=dispatch_key,
    )
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        second = recover_dispatching_manual_agent_runs.run(limit=10)
    assert second["kicked"] == 0
    delay.assert_not_called()

    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org",
        side_effect=AssertionError("readiness gate must avoid paid model access"),
    ):
        executed = agent_manual_run.run(
            role_id=int(role.id),
            application_id=None,
            dispatch_key=dispatch_key,
        )
        replay = agent_manual_run.run(
            role_id=int(role.id),
            application_id=None,
            dispatch_key=dispatch_key,
        )
    assert executed["run_status"] == "aborted"
    assert replay["agent_run_id"] == executed["agent_run_id"]
    assert db.query(AgentRun).filter_by(dispatch_key=dispatch_key).count() == 1
    with patch("app.tasks.agent_tasks.agent_manual_run.delay") as delay:
        fulfilled = enqueue_manual_run(
            db,
            role,
            user,
            dispatch_key=dispatch_key,
        )
    assert fulfilled["status"] == "aborted"
    assert fulfilled["replayed"] is True
    delay.assert_not_called()


def test_reevaluation_wrapper_recovers_crash_after_paid_run_commit(db):
    org = Organization(name="Agent crash receipt", slug=f"agent-crash-{id(db)}")
    db.add(org)
    db.flush()
    role = _role(db, int(org.id), name="Crash receipt role")
    candidate = Candidate(organization_id=org.id, email="crash@example.com", full_name="C")
    db.add(candidate)
    db.flush()
    application = CandidateApplication(
        organization_id=org.id,
        candidate_id=candidate.id,
        role_id=role.id,
        status="applied",
        pipeline_stage="review",
        pipeline_stage_source="recruiter",
        application_outcome="open",
        source="manual",
    )
    db.add(application)
    db.flush()
    decision = AgentDecision(
        organization_id=org.id,
        role_id=role.id,
        application_id=application.id,
        decision_type="advance_to_interview",
        recommendation="advance_to_interview",
        status="discarded",
        reasoning="stale",
        model_version="m",
        prompt_version="p",
        idempotency_key=f"crash-recovery:{application.id}",
        reevaluation_status="pending",
    )
    db.add(decision)
    db.flush()
    dispatch_key = f"agent-reevaluation/{int(decision.id)}"
    db.add(
        AgentRun(
            id=9_900_002,
            organization_id=org.id,
            role_id=role.id,
            trigger="manual",
            dispatch_key=dispatch_key,
            status="succeeded",
            decisions_emitted=1,
            model_version="m",
            prompt_version="p",
            finished_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    from app.tasks.reevaluation_tasks import run_agent_re_evaluation

    # Simulates: paid run committed, wrapper died before closing its receipt.
    # Replay must find the keyed AgentRun before resolving any provider client.
    with patch(
        "app.agent_runtime.orchestrator.get_client_for_org",
        side_effect=AssertionError("must not spend twice"),
    ):
        result = run_agent_re_evaluation.run(int(decision.id))
    assert result["status"] == "done"
    db.refresh(decision)
    assert decision.reevaluation_status == "done"
    assert db.query(AgentRun).filter_by(dispatch_key=dispatch_key).count() == 1
