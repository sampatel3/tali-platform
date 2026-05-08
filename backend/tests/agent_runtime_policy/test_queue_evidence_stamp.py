"""Queue tools auto-stamp policy_revision_id into evidence."""

from __future__ import annotations

from app.actions.types import Actor
from app.agent_runtime.tool_registry import _stamp_policy_revision_in_evidence
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate_application import CandidateApplication

from .conftest import make_world


def test_evidence_gets_policy_revision_id_added(db):
    _org, role, _, _app = make_world(db)
    out = _stamp_policy_revision_in_evidence(
        db, role=role, evidence={"role_fit_score": 80}
    )
    assert "policy_revision_id" in out
    assert out["role_fit_score"] == 80


def test_existing_policy_revision_id_is_preserved(db):
    _org, role, _, _app = make_world(db)
    out = _stamp_policy_revision_in_evidence(
        db, role=role, evidence={"policy_revision_id": 42, "x": "y"}
    )
    assert out["policy_revision_id"] == 42


def test_none_evidence_yields_dict_with_revision_id(db):
    _org, role, _, _app = make_world(db)
    out = _stamp_policy_revision_in_evidence(db, role=role, evidence=None)
    assert isinstance(out, dict)
    assert "policy_revision_id" in out
