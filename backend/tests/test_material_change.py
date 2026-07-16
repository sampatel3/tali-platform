"""Material-change assessment on Workable spec edits for agent-on roles.

Guards the cost-control invariant: a changed spec is NOT silently re-derived
(which would invalidate pending decisions + force paid re-evaluation). Instead
the change is judged by a (mocked here) LLM:
  - material   => HITL confirm item raised, criteria held unchanged.
  - immaterial => criteria applied silently, pending decisions rebaselined
                  (stay fresh).
  - no change  => no LLM call at all.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.actions import ask_recruiter
from app.actions.types import Actor
from app.models.agent_needs_input import AgentNeedsInput
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import CRITERION_SOURCE_DERIVED, RoleCriterion
from app.models.user import User
from app.services import material_change
from app.services.role_criteria_service import sync_derived_criteria

_SPEC_A = "Requirements\n- 5+ years Python\n- Postgres at scale\n"
_SPEC_B = "Requirements\n- 5+ years Python\n- Postgres at scale\n- Kubernetes in prod\n"


def _seed_role(db, *, agentic=True, spec=_SPEC_A) -> Role:
    org = Organization(name="O", slug=f"o-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id,
        name="Backend Eng",
        source="workable",
        job_spec_text=spec,
        agentic_mode_enabled=agentic,
    )
    db.add(role)
    db.flush()
    # Populate the role's derived criteria to match the current spec.
    sync_derived_criteria(db, role)
    db.flush()
    return role


def _derived(db, role) -> list[str]:
    # Query directly (not role.criteria) so a stale relationship after a
    # delete+insert doesn't mislead the assertion.
    rows = (
        db.query(RoleCriterion)
        .filter(
            RoleCriterion.role_id == role.id,
            RoleCriterion.source == CRITERION_SOURCE_DERIVED,
            RoleCriterion.deleted_at.is_(None),
        )
        .all()
    )
    return sorted((c.text or "").lower() for c in rows)


def _mock_llm(monkeypatch, *, material: bool, summary: str = "changed"):
    """Patch get_client_for_org so no real Anthropic call happens."""
    import json

    payload = json.dumps({"material": material, "summary": summary})
    fake_resp = SimpleNamespace(content=[SimpleNamespace(text=payload)])
    called = {"n": 0}

    class _Msgs:
        def create(self, **kwargs):
            called["n"] += 1
            return fake_resp

    fake_client = SimpleNamespace(messages=_Msgs())
    monkeypatch.setattr(material_change, "get_client_for_org", lambda org: fake_client)
    return called


def test_no_criteria_change_is_noop_and_skips_llm(db, monkeypatch):
    role = _seed_role(db)
    called = _mock_llm(monkeypatch, material=True)
    # Spec text unchanged from what produced the current criteria.
    status = material_change.handle_spec_change(db, role)
    assert status == "no_change"
    assert called["n"] == 0  # never paid for an LLM call


def test_material_change_raises_confirm_and_holds_criteria(db, monkeypatch):
    role = _seed_role(db)
    before = _derived(db, role)
    _mock_llm(monkeypatch, material=True, summary="Added Kubernetes as a hard requirement.")

    role.job_spec_text = _SPEC_B  # external Workable edit
    db.flush()
    status = material_change.handle_spec_change(db, role)
    db.flush()

    assert status == "material"
    # Criteria are HELD — not re-derived until the recruiter confirms.
    assert _derived(db, role) == before
    # A confirm HITL item was raised with apply/ignore options.
    row = (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == "confirm_material_change",
        )
        .one()
    )
    assert row.resolved_at is None
    values = {o["value"] for o in (row.options or [])}
    assert {"apply", "ignore"} <= values
    assert (row.response_schema or {}).get("proposed_criteria_fp")


def test_immaterial_change_applies_silently(db, monkeypatch):
    role = _seed_role(db)
    _mock_llm(monkeypatch, material=False, summary="Just reworded.")

    role.job_spec_text = _SPEC_B
    db.flush()
    status = material_change.handle_spec_change(db, role)
    db.flush()

    assert status == "immaterial"
    # Criteria WERE applied (Kubernetes now present).
    assert any("kubernetes" in t for t in _derived(db, role))
    # No confirm item raised.
    assert (
        db.query(AgentNeedsInput)
        .filter(
            AgentNeedsInput.role_id == role.id,
            AgentNeedsInput.kind == "confirm_material_change",
        )
        .count()
        == 0
    )


def test_already_pending_does_not_recall_llm(db, monkeypatch):
    role = _seed_role(db)
    _mock_llm(monkeypatch, material=True)
    role.job_spec_text = _SPEC_B
    db.flush()
    assert material_change.handle_spec_change(db, role) == "material"
    db.flush()

    # Second sync, same proposed spec → must short-circuit (no re-ask).
    called2 = _mock_llm(monkeypatch, material=True)
    assert material_change.handle_spec_change(db, role) == "already_pending"
    assert called2["n"] == 0


def _recruiter(db, role) -> User:
    user = User(
        email=f"r-{id(db)}@x.test",
        hashed_password="x",
        full_name="R",
        organization_id=role.organization_id,
        is_active=True,
        is_verified=True,
        is_superuser=False,
        role="owner",
    )
    db.add(user)
    db.flush()
    return user


def test_confirm_apply_rederives_criteria(db, monkeypatch):
    role = _seed_role(db)
    _mock_llm(monkeypatch, material=True)
    role.job_spec_text = _SPEC_B
    db.flush()
    material_change.handle_spec_change(db, role)
    db.flush()
    row = (
        db.query(AgentNeedsInput)
        .filter(AgentNeedsInput.kind == "confirm_material_change")
        .one()
    )
    user = _recruiter(db, role)

    ask_recruiter.answer(
        db,
        Actor.recruiter(user),
        organization_id=int(role.organization_id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "apply"},
    )
    db.flush()
    # "apply" re-derives → Kubernetes now in the criteria.
    assert any("kubernetes" in t for t in _derived(db, role))


def test_confirm_ignore_keeps_criteria(db, monkeypatch):
    role = _seed_role(db)
    before = _derived(db, role)
    _mock_llm(monkeypatch, material=True)
    role.job_spec_text = _SPEC_B
    db.flush()
    material_change.handle_spec_change(db, role)
    db.flush()
    row = (
        db.query(AgentNeedsInput)
        .filter(AgentNeedsInput.kind == "confirm_material_change")
        .one()
    )
    user = _recruiter(db, role)

    ask_recruiter.answer(
        db,
        Actor.recruiter(user),
        organization_id=int(role.organization_id),
        needs_input_id=int(row.id),
        expected_version=int(role.version or 1),
        response={"value": "ignore"},
    )
    db.flush()
    # "ignore" keeps the current bar — criteria unchanged.
    assert _derived(db, role) == before
