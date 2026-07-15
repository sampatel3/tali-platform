"""Role-scoped, recruiter-safe autonomous run history in Agent Chat."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agent_chat import system_prompt, tools
from app.models.agent_run import AgentRun
from app.models.organization import Organization
from app.models.role import Role
from app.models.user import User


_RUN_IDS = itertools.count(8_000_000)


def _org_role(db, label: str):
    org = Organization(name=f"{label} org", slug=f"run-history-{label}-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name=f"{label} role",
        source="manual",
    )
    user = User(
        email=f"run-history-{label}-{id(db)}@example.test",
        hashed_password="x",
        full_name=f"{label} recruiter",
        organization_id=int(org.id),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add_all([role, user])
    db.flush()
    return org, role, user


def _run(
    db,
    *,
    org,
    role,
    status="failed",
    trigger="cron",
    error=None,
    started_at=None,
):
    row = AgentRun(
        id=next(_RUN_IDS),
        organization_id=int(org.id),
        role_id=int(role.id),
        trigger=trigger,
        status=status,
        error=error,
        model_version="private-provider-model",
        prompt_version="private-prompt-version",
        started_at=started_at or datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        rounds_executed=2,
        decisions_emitted=1,
        total_cost_micro_usd=12_345,
        tools_called=[{"name": "get_application", "count": 2}],
    )
    db.add(row)
    db.flush()
    return row


def test_run_history_dispatch_redacts_raw_failures_and_private_diagnostics(db):
    org, role, user = _org_role(db, "safe")
    run = _run(
        db,
        org=org,
        role=role,
        error=(
            "anthropic call failed: Authorization: Bearer sk-ant-SECRET; "
            "provider request body and internal hostname"
        ),
    )

    result = tools.dispatch_tool(
        "list_recent_agent_runs", {}, db=db, role=role, user=user
    )

    assert result["role_id"] == int(role.id)
    assert result["count"] == 1
    item = result["runs"][0]
    assert item["run_id"] == int(run.id)
    assert item["failure_type"] == "model_provider"
    assert item["failure_summary"] == "The model provider call did not complete."
    assert "error" not in item
    assert "model_version" not in item
    assert "prompt_version" not in item
    serialized = json.dumps(result)
    assert "sk-ant-SECRET" not in serialized
    assert "Authorization" not in serialized
    assert "internal hostname" not in serialized
    assert "private-provider-model" not in serialized
    assert "private-prompt-version" not in serialized


def test_run_history_dispatch_is_locked_to_the_injected_role_and_org(db):
    org, role, user = _org_role(db, "target")
    sibling = Role(
        organization_id=int(org.id), name="Sibling role", source="manual"
    )
    db.add(sibling)
    db.flush()
    foreign_org, foreign_role, _foreign_user = _org_role(db, "foreign")

    target_run = _run(db, org=org, role=role, error="watchdog: worker timeout")
    _run(db, org=org, role=sibling, error="sibling secret")
    _run(db, org=foreign_org, role=foreign_role, error="foreign secret")

    # Even an out-of-schema role_id argument cannot redirect the dispatcher;
    # the authenticated conversation's injected role is authoritative.
    result = tools.dispatch_tool(
        "list_recent_agent_runs",
        {"role_id": int(sibling.id), "limit": 20},
        db=db,
        role=role,
        user=user,
    )

    assert result["role_id"] == int(role.id)
    assert [item["run_id"] for item in result["runs"]] == [int(target_run.id)]
    assert "sibling secret" not in json.dumps(result)
    assert "foreign secret" not in json.dumps(result)


def test_run_history_validates_filters_and_prompt_teaches_safe_followup(db):
    org, role, user = _org_role(db, "filters")
    now = datetime.now(timezone.utc)
    failed = _run(
        db,
        org=org,
        role=role,
        status="failed",
        trigger="manual",
        error="no-progress circuit breaker",
        started_at=now,
    )
    _run(
        db,
        org=org,
        role=role,
        status="succeeded",
        trigger="cron",
        started_at=now - timedelta(hours=1),
    )

    result = tools.dispatch_tool(
        "list_recent_agent_runs",
        {"status": "failed", "trigger": "manual", "limit": 1},
        db=db,
        role=role,
        user=user,
    )
    assert [item["run_id"] for item in result["runs"]] == [int(failed.id)]
    with pytest.raises(ValueError, match="unknown agent run status"):
        tools.dispatch_tool(
            "list_recent_agent_runs",
            {"status": "not-a-status"},
            db=db,
            role=role,
            user=user,
        )

    assert "`list_recent_agent_runs`" in system_prompt.SYSTEM_PROMPT
    assert "never invent, request, or quote raw provider diagnostics" in (
        system_prompt.SYSTEM_PROMPT
    )
