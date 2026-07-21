"""Pre-screen sub-agent: cache-fast-path + ok/error contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.sub_agents.base import SubAgentRequest
from app.sub_agents.pre_screen import PRE_SCREEN_SUB_AGENT
from app.models.role_criterion import RoleCriterion
from app.services.provider_usage_admission import AutomaticProviderAuthorityError

from .conftest import make_full_application


def test_cached_pre_screen_score_is_returned_without_claude_call(db):
    org, role, _candidate, app = make_full_application(
        db, pre_screen_score=85.0
    )
    app.pre_screen_recommendation = "Strong fit"
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    with patch("app.sub_agents.pre_screen.run_pre_screen") as runner:
        result = PRE_SCREEN_SUB_AGENT.run(req, db=db)
    runner.assert_not_called()
    assert result.ok is True
    assert result.cache_hit is True
    assert result.output["score"] == 85.0
    assert result.output["decision"] == "yes"


def test_missing_cv_text_returns_error(db):
    org, role, _, app = make_full_application(db, cv_text="")
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )
    result = PRE_SCREEN_SUB_AGENT.run(req, db=db)
    assert result.ok is False
    assert "cv_text" in (result.error or "")


def test_runner_invoked_when_no_cached_score(db):
    org, role, _, app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
    )

    class _FakePreScreenResult:
        decision = "yes"
        reason = "matches all must-haves"
        score = 78.0
        cache_hit = False
        input_tokens = 200
        output_tokens = 50
        cache_read_tokens = 0
        cache_creation_tokens = 0

    with patch(
        "app.sub_agents.pre_screen.run_pre_screen",
        return_value=_FakePreScreenResult(),
    ) as runner:
        result = PRE_SCREEN_SUB_AGENT.run(req, db=db)
    runner.assert_called_once()
    assert result.ok is True
    assert result.output["score"] == 78.0
    assert result.tokens_used == 250


def test_runner_receives_structured_role_requirements(db):
    org, role, _, app = make_full_application(db)
    db.add(
        RoleCriterion(
            role_id=int(role.id),
            source="recruiter",
            ordering=0,
            weight=1.0,
            must_have=True,
            bucket="must",
            text="Must have production Kubernetes experience",
        )
    )
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id), application_id=int(app.id), role_id=int(role.id)
    )

    class _FakePreScreenResult:
        decision = "yes"
        reason = "ok"
        score = 80.0
        cache_hit = False
        input_tokens = 1
        output_tokens = 1
        cache_read_tokens = 0
        cache_creation_tokens = 0

    with patch(
        "app.sub_agents.pre_screen.run_pre_screen", return_value=_FakePreScreenResult()
    ) as runner:
        PRE_SCREEN_SUB_AGENT.run(req, db=db)

    requirements = runner.call_args.args[2]
    assert len(requirements) == 1
    assert requirements[0].requirement == "Must have production Kubernetes experience"
    assert str(requirements[0].priority.value) == "must_have"


def test_metering_context_is_forwarded_to_runner(db):
    """Regression: the pre-screen sub-agent must thread ``metering_context``
    into ``run_pre_screen``. Without it the runner falls back to
    metering={"skip": True} and the agent's pre-screen Anthropic calls are
    never billed — they showed up only as feature_hint="skip" in
    claude_call_log (~$11/day of unattributed Haiku)."""
    org, role, _, app = make_full_application(db)
    ctx = {
        "agent_run_id": 7,
        "organization_id": int(org.id),
        "role_id": int(role.id),
        "entity_id": f"application:{app.id}",
        "feature": "evaluate_policy",
    }
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        metering_context=ctx,
    )

    class _FakePreScreenResult:
        decision = "yes"
        reason = "ok"
        score = 80.0
        cache_hit = False
        input_tokens = 100
        output_tokens = 20
        cache_read_tokens = 0
        cache_creation_tokens = 0

    with patch(
        "app.sub_agents.pre_screen.run_pre_screen",
        return_value=_FakePreScreenResult(),
    ) as runner:
        PRE_SCREEN_SUB_AGENT.run(req, db=db)
    runner.assert_called_once()
    assert runner.call_args.kwargs.get("metering_context") == ctx, (
        "pre_screen sub-agent dropped metering_context — agent pre-screens "
        "will leak as unmetered 'skip' calls"
    )


def test_live_provider_authority_denial_escapes_real_sub_agent_path(db):
    org, role, _, app = make_full_application(db)
    role.agentic_mode_enabled = True
    db.flush()
    client = MagicMock()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
        metering_context={
            "organization_id": int(org.id),
            "role_id": int(role.id),
            "entity_id": f"application:{app.id}",
            "require_role_authority": True,
        },
    )

    with (
        patch(
            "app.cv_matching.runner_pre_screen._resolve_anthropic_client",
            return_value=client,
        ),
        patch(
            "app.services.pre_screen_usage_admission.reserve_provider_usage",
            side_effect=AutomaticProviderAuthorityError("role agent is paused"),
        ),
        pytest.raises(AutomaticProviderAuthorityError, match="paused"),
    ):
        PRE_SCREEN_SUB_AGENT.run(req, db=db)

    client.messages.create.assert_not_called()


def test_unknown_application_returns_error(db):
    org, role, _, _app = make_full_application(db)
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=999_999,
        role_id=int(role.id),
    )
    result = PRE_SCREEN_SUB_AGENT.run(req, db=db)
    assert result.ok is False
