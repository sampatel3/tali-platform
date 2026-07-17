"""Provider-neutral ATS role response contract."""

from app.domains.assessments_runtime.role_support import role_to_response
from app.models.organization import Organization
from app.models.role import (
    JOB_STATUS_CANCELLED,
    JOB_STATUS_OPEN,
    ROLE_KIND_SISTER,
    Role,
)
from app.services.job_page_lifecycle import role_allows_new_paid_ats_work
from app.services.role_execution_guard import automatic_role_action_block_reason


def _role(db, **overrides) -> Role:
    org = Organization(name="ATS contract org")
    db.add(org)
    db.flush()
    values = {
        "organization_id": org.id,
        "name": "Platform Engineer",
        "source": "manual",
    }
    values.update(overrides)
    role = Role(**values)
    db.add(role)
    db.flush()
    return role


def test_bullhorn_role_serializes_provider_neutral_job_contract(db):
    role = _role(
        db,
        source="bullhorn",
        bullhorn_job_order_id="9001",
        bullhorn_job_data={"id": 9001, "status": "Accepting Candidates", "isOpen": False},
    )

    payload = role_to_response(role, summary=True).model_dump()

    assert payload["ats_provider"] == "bullhorn"
    assert payload["external_job_id"] == "9001"
    assert payload["external_job_state"] == "accepting candidates"
    assert payload["external_job_live"] is False
    # Existing clients retain the old Workable fields unchanged.
    assert payload["workable_job_id"] is None
    assert payload["workable_job_state"] is None
    assert payload["workable_job_live"] is True


def test_workable_wins_provider_neutral_contract_on_dual_linked_role(db):
    role = _role(
        db,
        source="workable",
        workable_job_id="WORK-42",
        workable_job_data={"state": "published"},
        bullhorn_job_order_id="9002",
        bullhorn_job_data={"status": "Closed", "isOpen": False},
        agentic_mode_enabled=True,
    )

    payload = role_to_response(role, summary=True).model_dump()

    assert payload["ats_provider"] == "workable"
    assert payload["external_job_id"] == "WORK-42"
    assert payload["external_job_state"] == "published"
    assert payload["external_job_live"] is True
    assert payload["workable_job_id"] == "WORK-42"
    assert payload["workable_job_state"] == "published"
    assert payload["workable_job_live"] is True
    # The stale secondary Bullhorn link cannot suppress paid processing for the
    # authoritative live Workable job.
    assert role_allows_new_paid_ats_work(role) is True


def test_closed_bullhorn_role_blocks_new_paid_work(db):
    role = _role(
        db,
        source="bullhorn",
        bullhorn_job_order_id="9003",
        bullhorn_job_data={"status": "Closed", "isOpen": False},
        agentic_mode_enabled=True,
    )

    assert role_allows_new_paid_ats_work(role) is False


def test_native_role_has_no_external_job_contract(db):
    payload = role_to_response(_role(db), summary=True).model_dump()

    assert payload["ats_provider"] is None
    assert payload["external_job_id"] is None
    assert payload["external_job_state"] is None
    assert payload["external_job_live"] is None


def test_automatic_guard_blocks_managed_local_terminal_role(db):
    role = _role(
        db,
        job_status=JOB_STATUS_CANCELLED,
        agentic_mode_enabled=True,
    )

    assert automatic_role_action_block_reason(role) == (
        "job is not open (status: cancelled)"
    )


def test_automatic_guard_blocks_closed_workable_role(db):
    role = _role(
        db,
        source="workable",
        workable_job_id="WORK-CLOSED",
        workable_job_data={"state": "closed"},
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
    )

    assert automatic_role_action_block_reason(role) == (
        "linked workable job is not live"
    )


def test_automatic_guard_blocks_closed_bullhorn_role(db):
    role = _role(
        db,
        source="bullhorn",
        bullhorn_job_order_id="9004",
        bullhorn_job_data={"status": "Closed", "isOpen": False},
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
    )

    assert automatic_role_action_block_reason(role) == (
        "linked bullhorn job is not live"
    )


def test_automatic_guard_uses_related_roles_ats_owner_lifecycle(db):
    owner = _role(
        db,
        source="workable",
        workable_job_id="WORK-RELATED-CLOSED",
        workable_job_data={"state": "closed"},
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
    )
    related = Role(
        organization_id=owner.organization_id,
        name="Related Platform Engineer",
        source="taali",
        role_kind=ROLE_KIND_SISTER,
        ats_owner_role_id=owner.id,
        job_status=JOB_STATUS_OPEN,
        agentic_mode_enabled=True,
    )
    db.add(related)
    db.flush()

    assert automatic_role_action_block_reason(related, db=db) == (
        "linked workable job is not live"
    )
