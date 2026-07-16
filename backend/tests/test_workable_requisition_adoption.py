"""Stage 2 of the requisition -> Workable bridge: when a Workable job syncs in,
the import scans its spec for the requisition's ``Taali ref: TAL-XXXXX`` code and
ADOPTS the inactive draft job (draft -> open, no duplicate) instead of minting a
new role. Exercised at the ``_adopt_requisition_role`` seam (pure DB logic).
"""
from app.components.integrations.workable.sync_service import _adopt_requisition_role
from app.models.organization import Organization
from app.models.role import (
    JOB_STATUS_DRAFT,
    JOB_STATUS_FILLED,
    JOB_STATUS_OPEN,
)
from app.models.role_criterion import RoleCriterion
from app.services.role_brief_service import (
    create_brief,
    ensure_ref_code,
    materialize_brief_to_role,
    update_brief_fields,
)


def _org(db, name="Bridge Co"):
    org = Organization(name=name, slug=name.lower().replace(" ", "-"))
    db.add(org)
    db.flush()
    return org


def _published_draft(db, org, *, title="Backend Engineer", **fields):
    """A requisition published into an inactive draft role (Stage 1)."""
    b = create_brief(db, organization_id=org.id)
    update_brief_fields(db, b, title=title, **fields)
    code = ensure_ref_code(db, b)
    role = materialize_brief_to_role(db, b, mark_applied=False, job_status=JOB_STATUS_DRAFT)
    db.flush()
    return b, role, code


def _spec_with_code(code, *, lead="Senior Engineer\n\nGreat role."):
    return f"{lead}\n\n---\n_Taali ref: {code} — please keep this line._\n"


def test_adopts_draft_role_via_ref_code_in_description(db):
    org = _org(db)
    brief, role, code = _published_draft(db, org)

    adopted = _adopt_requisition_role(
        db, org, job_id="ENGIN001", title="Backend Engineer",
        description=_spec_with_code(code),
    )
    assert adopted is not None
    assert adopted.id == role.id  # same role — no duplicate
    assert adopted.workable_job_id == "ENGIN001"
    assert adopted.job_status == JOB_STATUS_OPEN
    # brief still linked to the (now live) role
    db.refresh(brief)
    assert brief.role_id == role.id


def test_adopts_via_ref_code_in_title_when_absent_from_description(db):
    org = _org(db)
    _, role, code = _published_draft(db, org)
    adopted = _adopt_requisition_role(
        db, org, job_id="J2", title=f"Backend Engineer {code}",
        description="no code in this body",
    )
    assert adopted is not None and adopted.id == role.id
    assert adopted.job_status == JOB_STATUS_OPEN


def test_adopts_an_already_open_native_role_after_one_click_turn_on(db):
    org = _org(db)
    _, role, code = _published_draft(db, org)
    role.job_status = JOB_STATUS_OPEN
    role.agentic_mode_enabled = True
    db.flush()

    adopted = _adopt_requisition_role(
        db,
        org,
        job_id="LATE-LINK",
        title="Backend Engineer",
        description=_spec_with_code(code),
    )

    assert adopted is not None
    assert adopted.id == role.id
    assert adopted.workable_job_id == "LATE-LINK"
    assert adopted.job_status == JOB_STATUS_OPEN
    assert adopted.agentic_mode_enabled is True


def test_no_code_in_spec_returns_none(db):
    org = _org(db)
    _published_draft(db, org)
    assert _adopt_requisition_role(
        db, org, job_id="J", title="Backend Engineer", description="plain JD, no ref"
    ) is None


def test_unknown_code_returns_none(db):
    org = _org(db)
    _published_draft(db, org)
    assert _adopt_requisition_role(
        db, org, job_id="J", title="Eng", description=_spec_with_code("TAL-ZZZZZ")
    ) is None


def test_does_not_hijack_an_already_linked_role(db):
    org = _org(db)
    _, role, code = _published_draft(db, org)
    role.workable_job_id = "ALREADY-LINKED"
    role.job_status = JOB_STATUS_OPEN
    db.flush()
    # a second Workable job carrying the same code must NOT steal the role
    assert _adopt_requisition_role(
        db, org, job_id="OTHER", title="Eng", description=_spec_with_code(code)
    ) is None
    db.refresh(role)
    assert role.workable_job_id == "ALREADY-LINKED"


def test_does_not_adopt_a_filled_or_cancelled_role(db):
    org = _org(db)
    _, role, code = _published_draft(db, org)
    role.job_status = JOB_STATUS_FILLED
    db.flush()
    assert _adopt_requisition_role(
        db, org, job_id="J", title="Eng", description=_spec_with_code(code)
    ) is None


def test_empty_job_id_returns_none(db):
    org = _org(db)
    _, _, code = _published_draft(db, org)
    # No stable link key -> refuse (else the next sync would re-adopt/duplicate).
    assert _adopt_requisition_role(
        db, org, job_id="", title="Eng", description=_spec_with_code(code)
    ) is None


def test_does_not_match_a_brief_in_another_org(db):
    org_a = _org(db, "Org A")
    org_b = _org(db, "Org B")
    _, _, code = _published_draft(db, org_a)
    # org B imports a job carrying org A's code -> no cross-org adoption
    assert _adopt_requisition_role(
        db, org_b, job_id="J", title="Eng", description=_spec_with_code(code)
    ) is None


def test_adopted_role_keeps_brief_criteria(db):
    org = _org(db)
    brief, role, code = _published_draft(
        db, org, must_haves=["Python", "Postgres"], dealbreakers=["Onsite only"]
    )
    before = db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).count()
    assert before == 3  # materialized from the brief on publish

    adopted = _adopt_requisition_role(
        db, org, job_id="ENGIN9", title="Backend Engineer",
        description=_spec_with_code(code),
    )
    assert adopted is not None
    # adoption itself never touches criteria (the caller's created=False path
    # preserves them; org-criteria snapshot is skipped)
    after = db.query(RoleCriterion).filter(RoleCriterion.role_id == role.id).count()
    assert after == before
