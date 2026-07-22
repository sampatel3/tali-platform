"""Multi-user controls at the role-agent chat mutation boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.agent_chat import engine as chat_engine
from app.agent_chat import tools
from app.agent_chat.service import ensure_conversation
from app.domains.agent_chat import routes as chat_routes
from app.models.agent_conversation import AgentConversationMessage
from app.models.job_hiring_team import JobHiringTeam
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_change_event import RoleChangeEvent
from app.models.role_criterion import RoleCriterion
from app.models.task import Task
from app.models.user import User
from tests.conftest import TestingSessionLocal


def _routed_transport_stub():
    return SimpleNamespace(
        messages=object(),
        ai_routing_metered_transport=True,
        ai_routing_sdk_max_retries=0,
        organization_id=None,
    )


def _user(db, org: Organization, local: str) -> User:
    user = User(
        email=f"{local}-{id(db)}@chat-controls.test",
        hashed_password="x",
        full_name=local.title(),
        organization_id=org.id,
        role="member",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    db.flush()
    return user


def _subjects(db):
    org = Organization(
        name="Chat Collaboration Controls",
        slug=f"chat-collaboration-{id(db)}",
    )
    db.add(org)
    db.flush()
    recruiter = _user(db, org, "recruiter")
    interviewer = _user(db, org, "interviewer")
    role = Role(
        organization_id=org.id,
        name="Platform Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5_000,
        job_spec_text="Original role specification.",
    )
    db.add(role)
    db.flush()
    db.add_all(
        [
            JobHiringTeam(
                organization_id=org.id,
                role_id=role.id,
                user_id=recruiter.id,
                team_role="recruiter",
            ),
            JobHiringTeam(
                organization_id=org.id,
                role_id=role.id,
                user_id=interviewer.id,
                team_role="interviewer",
            ),
        ]
    )
    db.commit()
    return org, role, recruiter, interviewer


def _other_role_draft(db, org: Organization) -> tuple[Role, Task]:
    role = Role(
        organization_id=org.id,
        name="Data Engineer",
        source="manual",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5_000,
    )
    task = Task(
        organization_id=org.id,
        name="Data pipeline assessment",
        is_active=False,
        extra_data={"generated": True, "needs_review": True},
    )
    db.add_all([role, task])
    db.flush()
    role.tasks.append(task)
    db.commit()
    return role, task


def _draft_for_role(db, org: Organization, role: Role) -> Task:
    task = Task(
        organization_id=org.id,
        name="Platform reliability exercise",
        task_type="platform",
        difficulty="medium",
        duration_minutes=30,
        is_template=False,
        is_active=False,
        task_key=f"platform-{role.id}-{id(db)}",
        role="platform_engineer",
        description="Original scenario.",
        scenario="Original scenario.",
        calibration_prompt="Warm up.",
        repo_structure={"name": "platform", "files": {"README.md": "start"}},
        evaluation_rubric={"delivery": {"weight": 1.0}},
        extra_data={
            "generated": True,
            "needs_review": True,
            "battle_test": {"verdict": "pass"},
            "deliverable": {"kind": "repository"},
        },
    )
    db.add(task)
    db.flush()
    role.tasks.append(task)
    db.commit()
    return task


def _activate_draft(db, task, *, user_id):
    extra = dict(task.extra_data or {})
    extra["needs_review"] = False
    extra["approved_by_user_id"] = int(user_id)
    task.extra_data = extra
    task.is_active = True
    db.add(task)
    db.flush()
    return task


def _scripted_response(*blocks, stop_reason: str):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason)


def _tool_block(name: str, arguments: dict):
    return SimpleNamespace(type="tool_use", id=f"tool-{name}", name=name, input=arguments)


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _persist_turn_message(db, org, role, user):
    conversation = ensure_conversation(
        db,
        organization_id=int(org.id),
        role=role,
    )
    chat_engine.persist_user_message(
        db=db,
        conversation=conversation,
        user=user,
        user_message="Please update this job.",
    )
    db.commit()
    return conversation


def _run_scripted_turn(
    db,
    *,
    org,
    role,
    user,
    conversation,
    accepted_role_version: int,
    scripted,
):
    with (
        patch.object(
            chat_engine,
            "routed_messages_client",
            return_value=_routed_transport_stub(),
        ),
        patch.object(chat_engine, "reserve"),
        patch.object(chat_engine, "one_call", side_effect=scripted),
    ):
        result = chat_engine.run_agent_response(
            db=db,
            role=role,
            user=user,
            organization=org,
            conversation=conversation,
            accepted_role_version=accepted_role_version,
        )
    db.commit()
    return result


def _tool_result_payloads(db, conversation):
    rows = (
        db.query(AgentConversationMessage)
        .filter(AgentConversationMessage.conversation_id == conversation.id)
        .order_by(AgentConversationMessage.id.asc())
        .all()
    )
    return [
        json.loads(block["content"])
        for row in rows
        for block in (row.content or [])
        if block.get("type") == "tool_result"
    ]


def test_turn_rejects_mutation_when_ui_changed_after_message_acceptance(db):
    org, role, recruiter, _interviewer = _subjects(db)
    conversation = _persist_turn_message(db, org, role, recruiter)
    accepted_version = int(role.version or 1)

    # A direct UI save lands while the durable user message waits for its
    # asynchronous worker. The old model intent must not overwrite it.
    role.monthly_usd_budget_cents = 6_200
    role.version = accepted_version + 1
    db.commit()

    _run_scripted_turn(
        db,
        org=org,
        role=role,
        user=recruiter,
        conversation=conversation,
        accepted_role_version=accepted_version,
        scripted=[
            _scripted_response(
                _tool_block(
                    "adjust_agent_settings",
                    {"monthly_budget_cents": 9_000},
                ),
                stop_reason="tool_use",
            ),
            _scripted_response(
                _text_block("The job changed, so I did not overwrite it."),
                stop_reason="end_turn",
            ),
        ],
    )

    db.refresh(role)
    assert role.version == accepted_version + 1
    assert role.monthly_usd_budget_cents == 6_200
    payload = _tool_result_payloads(db, conversation)[-1]
    assert payload["status_code"] == 409
    assert payload["error"]["code"] == "ROLE_VERSION_CONFLICT"
    assert payload["error"]["current_version"] == accepted_version + 1


def test_async_worker_carries_enqueued_version_into_engine(db):
    org, role, recruiter, _interviewer = _subjects(db)
    conversation = _persist_turn_message(db, org, role, recruiter)
    accepted_version = int(role.version or 1)
    role.version = accepted_version + 1
    role.name = "Changed before worker start"
    db.commit()
    seen = {}

    def _capture_worker_boundary(**kwargs):
        seen["accepted_role_version"] = kwargs["accepted_role_version"]
        seen["loaded_role_version"] = int(kwargs["role"].version or 1)

    with (
        patch("app.platform.database.SessionLocal", TestingSessionLocal),
        patch(
            "app.agent_chat.engine.run_agent_response",
            side_effect=_capture_worker_boundary,
        ),
    ):
        from app.tasks.agent_chat_tasks import run_agent_chat_turn

        result = run_agent_chat_turn(
            conversation_id=int(conversation.id),
            role_id=int(role.id),
            user_id=int(recruiter.id),
            organization_id=int(org.id),
            accepted_role_version=accepted_version,
        )

    assert result["status"] == "replied"
    assert seen["accepted_role_version"] == accepted_version
    assert seen["loaded_role_version"] == accepted_version + 1


def test_turn_rechecks_version_after_model_call_before_mutating(db):
    org, role, recruiter, _interviewer = _subjects(db)
    conversation = _persist_turn_message(db, org, role, recruiter)
    accepted_version = int(role.version or 1)
    calls = {"count": 0}

    def _model_call(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            # Simulate a UI commit while the external model call is in flight.
            role.name = "Platform Engineer — UI revision"
            role.monthly_usd_budget_cents = 6_800
            role.version = accepted_version + 1
            db.commit()
            return _scripted_response(
                _tool_block(
                    "adjust_agent_settings",
                    {"monthly_budget_cents": 9_000},
                ),
                stop_reason="tool_use",
            )
        return _scripted_response(
            _text_block("I preserved the newer UI revision."),
            stop_reason="end_turn",
        )

    _run_scripted_turn(
        db,
        org=org,
        role=role,
        user=recruiter,
        conversation=conversation,
        accepted_role_version=accepted_version,
        scripted=_model_call,
    )

    db.refresh(role)
    assert role.version == accepted_version + 1
    assert role.name == "Platform Engineer — UI revision"
    assert role.monthly_usd_budget_cents == 6_800
    payload = _tool_result_payloads(db, conversation)[-1]
    assert payload["error"]["code"] == "ROLE_VERSION_CONFLICT"


def test_stale_turn_can_still_run_read_only_tools(db):
    org, role, recruiter, _interviewer = _subjects(db)
    conversation = _persist_turn_message(db, org, role, recruiter)
    accepted_version = int(role.version or 1)
    role.monthly_usd_budget_cents = 7_400
    role.version = accepted_version + 1
    db.commit()

    final = _run_scripted_turn(
        db,
        org=org,
        role=role,
        user=recruiter,
        conversation=conversation,
        accepted_role_version=accepted_version,
        scripted=[
            _scripted_response(
                _tool_block("get_role_overview", {}),
                stop_reason="tool_use",
            ),
            _scripted_response(
                _text_block("The current monthly cap is 7,400 cents."),
                stop_reason="end_turn",
            ),
        ],
    )

    assert final.text == "The current monthly cap is 7,400 cents."
    payload = _tool_result_payloads(db, conversation)[-1]
    assert "error" not in payload
    assert payload["agent"]["monthly_budget_cents"] == 7_400


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("adjust_agent_settings", {"monthly_budget_cents": 9_000}),
        ("set_agent_state", {"action": "pause"}),
        (
            "update_job_spec",
            {
                "job_spec_text": (
                    "A complete platform engineering job description covering "
                    "distributed systems, reliability, delivery, and mentoring."
                )
            },
        ),
    ],
)
def test_configured_team_exclusion_blocks_chat_role_mutations(
    db, tool_name, arguments
):
    _org, role, _recruiter, interviewer = _subjects(db)

    with pytest.raises(HTTPException) as exc_info:
        tools.dispatch_tool(
            tool_name,
            arguments,
            db=db,
            role=role,
            user=interviewer,
        )

    assert exc_info.value.status_code == 403
    db.refresh(role)
    assert role.version == 1
    assert role.monthly_usd_budget_cents == 5_000
    assert role.agent_paused_at is None
    assert role.job_spec_text == "Original role specification."
    assert db.query(RoleChangeEvent).count() == 0


def test_allowed_chat_settings_advance_version_once_and_audit_actor(db):
    _org, role, recruiter, _interviewer = _subjects(db)

    first = tools.dispatch_tool(
        "adjust_agent_settings",
        {"monthly_budget_cents": 7_000, "auto_advance": True},
        db=db,
        role=role,
        user=recruiter,
    )
    assert first["ok"] is True
    assert first["agent"]["version"] == 2

    # A repeated model tool call for the already-current values is a no-op: it
    # neither fabricates another version nor another audit row.
    tools.dispatch_tool(
        "adjust_agent_settings",
        {"monthly_budget_cents": 7_000, "auto_advance": True},
        db=db,
        role=role,
        user=recruiter,
    )
    db.refresh(role)
    assert role.version == 2

    # A stale Role object supplied by a long-running chat turn is ignored. The
    # dispatcher locks and refreshes the persisted row, then advances from 2.
    stale_role = Role(
        id=role.id,
        organization_id=role.organization_id,
        name=role.name,
        version=1,
    )
    tools.dispatch_tool(
        "adjust_agent_settings",
        {"monthly_budget_cents": 8_000},
        db=db,
        role=stale_role,
        user=recruiter,
    )

    db.refresh(role)
    assert role.version == 3
    assert role.monthly_usd_budget_cents == 8_000
    events = db.query(RoleChangeEvent).order_by(RoleChangeEvent.id.asc()).all()
    assert [(event.from_version, event.to_version) for event in events] == [
        (1, 2),
        (2, 3),
    ]
    assert all(event.actor_user_id == recruiter.id for event in events)
    assert all(event.reason == "agent chat" for event in events)


def test_chat_job_spec_edit_is_versioned_redacted_and_transactional(db):
    _org, role, recruiter, _interviewer = _subjects(db)
    new_spec = (
        "Lead the platform engineering roadmap across distributed systems, "
        "production reliability, incident response, observability, delivery, "
        "security, and mentoring for a growing engineering organization."
    )

    with patch("app.services.role_criteria_service.sync_derived_criteria"):
        result = tools.dispatch_tool(
            "update_job_spec",
            {"job_spec_text": new_spec},
            db=db,
            role=role,
            user=recruiter,
        )

    assert result["applied"] is True
    db.refresh(role)
    assert role.job_spec_text == new_spec
    assert role.version == 2
    event = db.query(RoleChangeEvent).one()
    assert event.action == "job_spec_updated"
    assert event.actor_user_id == recruiter.id
    assert (event.from_version, event.to_version) == (1, 2)
    assert "job_spec_text" in event.changes
    assert new_spec not in json.dumps(event.changes)


def test_audit_failure_rolls_back_chat_role_mutation(db):
    _org, role, recruiter, _interviewer = _subjects(db)

    with patch(
        "app.agent_chat.controls.add_role_change_event",
        side_effect=RuntimeError("audit unavailable"),
    ):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            tools.dispatch_tool(
                "adjust_agent_settings",
                {"monthly_budget_cents": 9_000},
                db=db,
                role=role,
                user=recruiter,
            )

    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.monthly_usd_budget_cents == 5_000
    assert persisted.version == 1
    assert db.query(RoleChangeEvent).count() == 0


def test_chat_constraint_mutations_advance_related_configuration_revision(db):
    _org, role, recruiter, _interviewer = _subjects(db)

    added = tools.dispatch_tool(
        "add_or_update_constraint",
        {"text": "Production Python", "bucket": "preferred"},
        db=db,
        role=role,
        user=recruiter,
    )
    criterion_id = int(added["criterion"]["id"])
    db.refresh(role)
    assert role.version == 2

    # Identical adjacent model tool calls are true no-ops: no related row,
    # Role revision, or audit boundary changes.
    repeated = tools.dispatch_tool(
        "add_or_update_constraint",
        {
            "criterion_id": criterion_id,
            "text": "Production Python",
            "bucket": "preferred",
        },
        db=db,
        role=role,
        user=recruiter,
    )
    assert repeated["action"] == "updated"
    assert repeated["invalidates_scores"] is False
    db.refresh(role)
    assert role.version == 2
    assert db.query(RoleChangeEvent).count() == 1

    tools.dispatch_tool(
        "add_or_update_constraint",
        {
            "criterion_id": criterion_id,
            "text": "Production Python and SQL",
            "bucket": "preferred",
        },
        db=db,
        role=role,
        user=recruiter,
    )
    tools.dispatch_tool(
        "remove_constraint",
        {"criterion_id": criterion_id},
        db=db,
        role=role,
        user=recruiter,
    )

    db.refresh(role)
    assert role.version == 4
    criterion = db.query(RoleCriterion).filter(RoleCriterion.id == criterion_id).one()
    assert criterion.deleted_at is not None
    events = db.query(RoleChangeEvent).order_by(RoleChangeEvent.id.asc()).all()
    assert [(event.from_version, event.to_version) for event in events] == [
        (1, 2),
        (2, 3),
        (3, 4),
    ]
    assert all(event.action == "role_criteria_updated" for event in events)
    assert all(event.actor_user_id == recruiter.id for event in events)
    assert all(event.changes == {} for event in events)
    assert "criterion added" in (events[0].reason or "")
    assert "criterion updated" in (events[1].reason or "")
    assert "criterion removed" in (events[2].reason or "")


def test_constraint_audit_failure_rolls_back_related_row_and_version(db):
    _org, role, recruiter, _interviewer = _subjects(db)

    with patch(
        "app.agent_chat.tools.add_role_change_event",
        side_effect=RuntimeError("criterion audit unavailable"),
    ):
        with pytest.raises(RuntimeError, match="criterion audit unavailable"):
            tools.dispatch_tool(
                "add_or_update_constraint",
                {"text": "Kubernetes", "bucket": "preferred"},
                db=db,
                role=role,
                user=recruiter,
            )

    persisted = db.query(Role).filter(Role.id == role.id).one()
    assert persisted.version == 1
    assert (
        db.query(RoleCriterion)
        .filter(RoleCriterion.role_id == role.id)
        .count()
        == 0
    )
    assert db.query(RoleChangeEvent).count() == 0


def test_draft_task_mutation_route_uses_agent_control_policy(db):
    _org, role, _recruiter, interviewer = _subjects(db)

    with patch.object(chat_routes, "approve_draft") as approve:
        with pytest.raises(HTTPException) as exc_info:
            chat_routes.approve_draft_task(
                role_id=int(role.id),
                task_id=123,
                body=chat_routes.ApproveDraftRequest(
                    expected_version=int(role.version or 1),
                ),
                db=db,
                current_user=interviewer,
            )

    assert exc_info.value.status_code == 403
    approve.assert_not_called()


def test_recruiter_cannot_approve_another_roles_draft(db):
    org, authorized_role, recruiter, _interviewer = _subjects(db)
    _other_role, other_task = _other_role_draft(db, org)

    with patch(
        "app.services.task_approval_service.approve_task_for_use"
    ) as approve_task:
        with pytest.raises(HTTPException) as exc_info:
            chat_routes.approve_draft_task(
                role_id=int(authorized_role.id),
                task_id=int(other_task.id),
                body=chat_routes.ApproveDraftRequest(
                    expected_version=int(authorized_role.version or 1),
                ),
                db=db,
                current_user=recruiter,
            )

    assert exc_info.value.status_code == 400
    assert "draft not found" in str(exc_info.value.detail).lower()
    approve_task.assert_not_called()
    db.refresh(other_task)
    assert other_task.is_active is False
    assert other_task.extra_data.get("approved_by_user_id") is None


def test_recruiter_cannot_revise_another_roles_draft(db):
    org, authorized_role, recruiter, _interviewer = _subjects(db)
    _other_role, other_task = _other_role_draft(db, org)
    original_name = other_task.name

    with (
        patch.object(chat_routes.settings, "ANTHROPIC_API_KEY", "sk-test"),
        patch("app.services.task_spec_generator.revise_task_spec") as revise_spec,
    ):
        result = chat_routes.revise_draft_task(
            role_id=int(authorized_role.id),
            task_id=int(other_task.id),
            body=chat_routes.ReviseDraftRequest(
                expected_version=int(authorized_role.version or 1),
                answers={"issues": ["scope"], "direction": "targeted"},
                note="Keep it concise.",
            ),
            db=db,
            current_user=recruiter,
        )

    assert result["ok"] is False
    assert "draft not found" in str(result["error"]).lower()
    revise_spec.assert_not_called()
    db.refresh(other_task)
    assert other_task.is_active is False
    assert other_task.name == original_name


def test_draft_approve_bumps_once_and_audits_actor_atomically(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)

    with patch(
        "app.services.task_approval_service.approve_task_for_use",
        side_effect=_activate_draft,
    ):
        result = chat_routes.approve_draft_task(
            role_id=int(role.id),
            task_id=int(task.id),
            body=chat_routes.ApproveDraftRequest(expected_version=1),
            db=db,
            current_user=recruiter,
        )

    db.refresh(role)
    db.refresh(task)
    event = db.query(RoleChangeEvent).one()
    assert result["role_version"] == 2
    assert role.version == 2
    assert task.is_active is True
    assert event.action == "role_draft_task_approved"
    assert (event.from_version, event.to_version) == (1, 2)
    assert event.actor_user_id == recruiter.id
    assert event.changes == {}


def test_stale_draft_approve_uses_standard_conflict_and_does_not_mutate(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)
    role.version = 2
    db.commit()

    with patch(
        "app.services.task_approval_service.approve_task_for_use"
    ) as approve_task:
        with pytest.raises(HTTPException) as exc_info:
            chat_routes.approve_draft_task(
                role_id=int(role.id),
                task_id=int(task.id),
                body=chat_routes.ApproveDraftRequest(expected_version=1),
                db=db,
                current_user=recruiter,
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "ROLE_VERSION_CONFLICT"
    assert exc_info.value.detail["current_version"] == 2
    approve_task.assert_not_called()
    db.refresh(task)
    assert task.is_active is False
    assert db.query(RoleChangeEvent).count() == 0


def test_draft_approve_audit_failure_rolls_back_task_and_version(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)

    with (
        patch(
            "app.services.task_approval_service.approve_task_for_use",
            side_effect=_activate_draft,
        ),
        patch.object(
            chat_routes,
            "add_role_change_event",
            side_effect=RuntimeError("draft audit unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError, match="draft audit unavailable"):
            chat_routes.approve_draft_task(
                role_id=int(role.id),
                task_id=int(task.id),
                body=chat_routes.ApproveDraftRequest(expected_version=1),
                db=db,
                current_user=recruiter,
            )

    db.refresh(role)
    db.refresh(task)
    assert role.version == 1
    assert task.is_active is False
    assert db.query(RoleChangeEvent).count() == 0


def test_draft_revision_model_call_has_no_transaction_and_race_conflicts(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)
    original_name = task.name

    def concurrent_change(**kwargs):
        # The preflight read transaction is closed before paid model work; no
        # transaction means this call cannot still own a Role row lock.
        assert db.in_transaction() is False
        with TestingSessionLocal() as concurrent_db:
            concurrent_role = (
                concurrent_db.query(Role).filter(Role.id == role.id).one()
            )
            concurrent_role.version = 2
            concurrent_db.commit()
        spec = dict(kwargs["prior_spec"])
        spec["name"] = "Stale generated revision"
        return SimpleNamespace(valid=True, spec=spec, errors=[])

    with (
        patch.object(chat_routes.settings, "ANTHROPIC_API_KEY", "sk-test"),
        patch(
            "app.services.task_spec_generator.revise_task_spec",
            side_effect=concurrent_change,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            chat_routes.revise_draft_task(
                role_id=int(role.id),
                task_id=int(task.id),
                body=chat_routes.ReviseDraftRequest(
                    expected_version=1,
                    answers={"issues": ["scope"], "direction": "targeted"},
                ),
                db=db,
                current_user=recruiter,
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "ROLE_VERSION_CONFLICT"
    assert exc_info.value.detail["current_version"] == 2
    db.refresh(task)
    assert task.name == original_name
    assert db.query(RoleChangeEvent).count() == 0


def test_material_draft_revision_bumps_once_and_audits_actor(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)

    def revised(**kwargs):
        assert db.in_transaction() is False
        spec = dict(kwargs["prior_spec"])
        spec["name"] = "Platform reliability exercise v2"
        spec["scenario"] = "A materially revised incident scenario."
        return SimpleNamespace(valid=True, spec=spec, errors=[])

    with (
        patch.object(chat_routes.settings, "ANTHROPIC_API_KEY", "sk-test"),
        patch(
            "app.services.task_spec_generator.revise_task_spec",
            side_effect=revised,
        ),
    ):
        result = chat_routes.revise_draft_task(
            role_id=int(role.id),
            task_id=int(task.id),
            body=chat_routes.ReviseDraftRequest(
                expected_version=1,
                answers={"issues": ["scenario"], "direction": "targeted"},
            ),
            db=db,
            current_user=recruiter,
        )

    db.refresh(role)
    db.refresh(task)
    event = db.query(RoleChangeEvent).one()
    assert result["material"] is True
    assert result["role_version"] == 2
    assert role.version == 2
    assert task.name.endswith("v2")
    assert event.action == "role_draft_task_revised"
    assert (event.from_version, event.to_version) == (1, 2)
    assert event.actor_user_id == recruiter.id


def test_identical_draft_revision_is_noop_without_version_or_audit(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)
    original_extra = dict(task.extra_data)

    def echo(**kwargs):
        assert db.in_transaction() is False
        return SimpleNamespace(
            valid=True,
            spec=kwargs["prior_spec"],
            errors=[],
        )

    with (
        patch.object(chat_routes.settings, "ANTHROPIC_API_KEY", "sk-test"),
        patch("app.services.task_spec_generator.revise_task_spec", side_effect=echo),
    ):
        result = chat_routes.revise_draft_task(
            role_id=int(role.id),
            task_id=int(task.id),
            body=chat_routes.ReviseDraftRequest(
                expected_version=1,
                answers={"issues": ["scope"], "direction": "targeted"},
            ),
            db=db,
            current_user=recruiter,
        )

    db.refresh(role)
    db.refresh(task)
    assert result["material"] is False
    assert result["role_version"] == 1
    assert role.version == 1
    assert task.extra_data == original_extra
    assert db.query(RoleChangeEvent).count() == 0


def test_draft_revision_audit_failure_rolls_back_task_and_version(db):
    org, role, recruiter, _interviewer = _subjects(db)
    task = _draft_for_role(db, org, role)
    original_name = task.name

    def revised(**kwargs):
        spec = dict(kwargs["prior_spec"])
        spec["name"] = "Must roll back"
        return SimpleNamespace(valid=True, spec=spec, errors=[])

    with (
        patch.object(chat_routes.settings, "ANTHROPIC_API_KEY", "sk-test"),
        patch(
            "app.services.task_spec_generator.revise_task_spec",
            side_effect=revised,
        ),
        patch.object(
            chat_routes,
            "add_role_change_event",
            side_effect=RuntimeError("revision audit unavailable"),
        ),
    ):
        with pytest.raises(RuntimeError, match="revision audit unavailable"):
            chat_routes.revise_draft_task(
                role_id=int(role.id),
                task_id=int(task.id),
                body=chat_routes.ReviseDraftRequest(
                    expected_version=1,
                    answers={"issues": ["scope"], "direction": "targeted"},
                ),
                db=db,
                current_user=recruiter,
            )

    db.refresh(role)
    db.refresh(task)
    assert role.version == 1
    assert task.name == original_name
    assert db.query(RoleChangeEvent).count() == 0
