"""The role agent can apply a pasted job spec + re-derive its criteria (opt-in).

update_job_spec replaces role.job_spec_text, re-derives the spec criteria
(sync_derived_criteria — mocked here; its own derivation is tested separately),
returns the criteria diff + a re-screen cost estimate, and does NOT auto-spend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from app.agent_chat.constraints import update_job_spec
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_criterion import CRITERION_SOURCE_DERIVED, RoleCriterion


def _org(db) -> Organization:
    org = Organization(name="JS Org", slug=f"js-{id(db)}")
    db.add(org)
    db.flush()
    return org


def _role(db, org) -> Role:
    role = Role(organization_id=org.id, name="AI Engineer", source="manual",
                score_threshold=70, agentic_mode_enabled=True, job_spec_text="old spec")
    db.add(role)
    db.flush()
    return role


def _crit(db, role, text, *, ordering=0):
    c = RoleCriterion(
        role_id=role.id, text=text, bucket="must",
        source=CRITERION_SOURCE_DERIVED, ordering=ordering, weight=1.0,
    )
    db.add(c)
    db.flush()
    return c


def test_update_job_spec_applies_rederives_and_estimates(db):
    org = _org(db)
    role = _role(db, org)
    old = _crit(db, role, "Python", ordering=0)

    # Simulate the re-derive: drop the old derived chip, add a new one.
    def _sync(db_, role_):
        old.deleted_at = datetime.now(timezone.utc)
        _crit(db_, role_, "Kubernetes", ordering=1)

    new_jd = "Senior AI Engineer. Requirements: 5y Kubernetes, distributed systems, LLM serving." * 2
    with patch("app.services.role_criteria_service.sync_derived_criteria", side_effect=_sync):
        res = update_job_spec(db, role, job_spec_text=new_jd)

    assert res["type"] == "job_spec_change" and res["applied"] is True
    assert "Kubernetes" in res["added"]
    assert "Python" in res["removed"]
    assert "count" in res["would_rescreen"] and "est_cost_usd" in res["would_rescreen"]
    db.refresh(role)
    assert role.job_spec_text.startswith("Senior AI Engineer")
    assert role.description == role.job_spec_text
    assert role.job_spec_manually_edited_at is not None


def test_update_job_spec_rejects_too_short(db):
    org = _org(db)
    role = _role(db, org)
    res = update_job_spec(db, role, job_spec_text="too short")
    assert res.get("ok") is False
    db.refresh(role)
    assert role.job_spec_text == "old spec"  # unchanged
