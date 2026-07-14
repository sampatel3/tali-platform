"""Cancellation must withdraw the paid assessment-generation outbox."""

from __future__ import annotations

from unittest.mock import patch

from app.models.organization import Organization
from app.models.role import Role
from app.services.role_activation_intent import (
    cancel_role_activation_intent,
    request_role_activation_intent,
)
from app.services.task_provisioning_state import (
    PROVISIONING_AWAITING_ACTIVATION,
    PROVISIONING_RUNNING,
    claim_assessment_task_provisioning,
    finish_assessment_task_provisioning,
    request_assessment_task_provisioning,
)
from app.tasks.assessment_tasks import generate_assessment_task_for_role


def _pending_activation_role(db, *, suffix: str) -> Role:
    org = Organization(name=f"Cancellation {suffix}", slug=f"cancel-{suffix}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=int(org.id),
        name="Platform Engineer",
        job_spec_text=(
            "Build and operate reliable distributed services, own production "
            "quality, security, incident response, observability, automated "
            "delivery, and measurable platform improvements across teams."
        ),
        agentic_mode_enabled=False,
        monthly_usd_budget_cents=5000,
    )
    db.add(role)
    db.flush()
    request_assessment_task_provisioning(
        role,
        reason="requisition_publish",
        defer_until_activation=True,
    )
    request_role_activation_intent(
        role,
        user_id=1,
        monthly_budget_cents=5000,
    )
    db.commit()
    db.refresh(role)
    return role


def test_cancelled_activation_returns_generation_to_no_spend_hold(db):
    role = _pending_activation_role(db, suffix="pending")
    assert role.assessment_task_provisioning["status"] == "pending"

    assert cancel_role_activation_intent(
        role, user_id=1, reason="agent turned off"
    )
    db.commit()
    db.refresh(role)

    state = role.assessment_task_provisioning
    assert state["status"] == PROVISIONING_AWAITING_ACTIVATION
    assert state["claim_token"] is None
    assert state["activation_intent"]["status"] == "cancelled"

    with patch(
        "app.services.task_provisioning_service.generate_and_link_task_for_role"
    ) as provider_work:
        result = generate_assessment_task_for_role.run(
            role.id, role.organization_id
        )

    assert result == {
        "status": "noop",
        "reason": PROVISIONING_AWAITING_ACTIVATION,
    }
    provider_work.assert_not_called()


def test_cancelled_activation_invalidates_an_existing_generation_claim(db):
    role = _pending_activation_role(db, suffix="running")
    claim = claim_assessment_task_provisioning(
        db,
        role_id=role.id,
        organization_id=role.organization_id,
    )
    assert claim.status == "claimed"
    assert claim.claim_token
    db.refresh(role)
    assert role.assessment_task_provisioning["status"] == PROVISIONING_RUNNING

    assert cancel_role_activation_intent(
        role, user_id=1, reason="agent turned off during generation"
    )
    db.commit()
    db.refresh(role)
    assert role.assessment_task_provisioning["status"] == PROVISIONING_AWAITING_ACTIVATION
    assert role.assessment_task_provisioning["claim_token"] is None

    assert finish_assessment_task_provisioning(
        db,
        role_id=role.id,
        organization_id=role.organization_id,
        claim_token=str(claim.claim_token),
        status="succeeded",
        task_id=999,
    ) is False
