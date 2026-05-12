"""Phase 6 — graph writeback validation + sensitivity routing + co-sign queue.

These tests exercise the pipeline without a running Graphiti — the
commit step is mocked out by leaving ``graph_client.is_configured()``
False in the test environment (default for SQLite tests).
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event

from app.actions import teach_decision
from app.actions.types import Actor
from app.graph_writeback import contracts as wb_contracts
from app.graph_writeback import pipeline as wb_pipeline
from app.graph_writeback import sensitivity as wb_sensitivity
from app.models.agent_decision import AgentDecision
from app.models.agent_exemplar import AgentExemplar
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.decision_feedback import DecisionFeedback
from app.models.graph_writeback import GraphWritebackQueueItem
from app.models.organization import Organization
from app.models.role import Role


_BIG_PK_COUNTERS = {
    "agent_decisions": 0,
    "decision_feedback": 0,
    "graph_writeback_queue": 0,
    "agent_exemplars": 0,
}


def _assign(mapper, connection, target):  # pragma: no cover
    name = target.__table__.name
    if target.id is None and name in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[name] += 1
        target.id = _BIG_PK_COUNTERS[name]


event.listen(AgentDecision, "before_insert", _assign)
event.listen(DecisionFeedback, "before_insert", _assign)
event.listen(GraphWritebackQueueItem, "before_insert", _assign)
event.listen(AgentExemplar, "before_insert", _assign)


def _seed(db):
    org = Organization(name="GwbOrg", slug=f"gwb-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="Senior", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="g@x.test", full_name="G T")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review",
        pipeline_stage_source="recruiter", application_outcome="open",
        source="manual",
    )
    db.add(app); db.flush()
    decision = AgentDecision(
        organization_id=org.id, role_id=role.id, application_id=app.id,
        decision_type="advance_to_interview", recommendation="advance_to_interview",
        status="pending", reasoning="...", confidence=0.7,
        model_version="m", prompt_version="p",
        idempotency_key=f"gwb:{app.id}:advance",
    )
    db.add(decision); db.flush()
    return SimpleNamespace(org=org, role=role, decision=decision)


def _user(db, org, idx=1):
    from app.models.user import User
    u = User(
        email=f"gwb-user-{idx}-{id(db)}@x.test",
        hashed_password="x", is_active=True, is_superuser=False,
        is_verified=True, full_name="GWB User", organization_id=org.id,
    )
    db.add(u); db.flush()
    return u


# ---------------------------------------------------------------------------
# Sensitivity classification
# ---------------------------------------------------------------------------


def test_low_risk_edge_classifies_low():
    hint = wb_contracts.GraphWriteHint(
        action="assert_edge",
        from_node_id="Candidate:1",
        edge_type="HAS_SKILL",
        to_node_id="Skill:python",
        confidence=0.8,
        rationale="confirmed in CV",
    )
    result = wb_sensitivity.classify_hint(hint)
    assert result.accepted
    assert result.sensitivity == "low"


def test_medium_risk_edge_classifies_medium():
    hint = wb_contracts.GraphWriteHint(
        action="assert_edge",
        from_node_id="Candidate:1",
        edge_type="SIMILAR_TO",
        to_node_id="Candidate:2",
        confidence=0.7,
        rationale="career-path parallel",
    )
    result = wb_sensitivity.classify_hint(hint)
    assert result.accepted
    assert result.sensitivity == "medium"


def test_protected_attribute_node_label_blocks_high():
    hint = wb_contracts.GraphWriteHint(
        action="assert_edge",
        from_node_id="Candidate:1",
        edge_type="HAS_SKILL",
        to_node_id="Gender:female",
        confidence=1.0,
        rationale="...",
    )
    result = wb_sensitivity.classify_hint(hint)
    assert result.accepted
    assert result.sensitivity == "high"


def test_blocked_edge_type_classifies_high():
    hint = wb_contracts.GraphWriteHint(
        action="assert_edge",
        from_node_id="Candidate:1",
        edge_type="HAS_PROTECTED_ATTR",
        to_node_id="X:1",
        confidence=1.0,
        rationale="...",
    )
    result = wb_sensitivity.classify_hint(hint)
    assert result.sensitivity == "high"


def test_property_proxy_blocks_high():
    hint = wb_contracts.GraphWriteHint(
        action="assert_edge",
        from_node_id="Candidate:1",
        edge_type="HAS_SKILL",
        to_node_id="Skill:python",
        properties={"gender": "female"},  # proxy attempt via properties
        confidence=1.0,
        rationale="...",
    )
    result = wb_sensitivity.classify_hint(hint)
    assert result.sensitivity == "high"


def test_unknown_edge_type_rejected():
    hint = wb_contracts.GraphWriteHint(
        action="assert_edge",
        from_node_id="Candidate:1",
        edge_type="MAKES_ME_HAPPY",
        to_node_id="Candidate:2",
        confidence=1.0,
        rationale="...",
    )
    result = wb_sensitivity.classify_hint(hint)
    assert result.accepted is False
    assert "unknown_edge_type" in (result.reason or "")


# ---------------------------------------------------------------------------
# End-to-end pipeline via teach action
# ---------------------------------------------------------------------------


def test_teach_with_low_risk_hints_writes_committed_queue_row(db):
    s = _seed(db)
    user = _user(db, s.org)
    hints = [
        {
            "action": "assert_edge",
            "from_node_id": "Candidate:42",
            "edge_type": "HAS_SKILL",
            "to_node_id": "Skill:python",
            "confidence": 0.9,
            "rationale": "confirmed in interview",
        }
    ]
    teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="missing_signal",
        correction_text="Add the python skill",
        scope="role",
        attributed_to="cv_scoring",
        direction="under",
        graph_write_hints=hints,
    )
    db.commit()
    rows = db.query(GraphWritebackQueueItem).all()
    assert len(rows) == 1
    assert rows[0].sensitivity == "low"
    assert rows[0].status == "committed"


def test_teach_with_medium_risk_hints_queues_for_cosign(db):
    s = _seed(db)
    user = _user(db, s.org)
    hints = [
        {
            "action": "assert_edge",
            "from_node_id": "Candidate:42",
            "edge_type": "SIMILAR_TO",
            "to_node_id": "Candidate:99",
            "properties": {"basis": "career_path"},
            "confidence": 0.7,
            "rationale": "Same finance->ML transition",
        }
    ]
    teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="missing_signal",
        correction_text="similar to top performer",
        scope="role",
        attributed_to="graph_priors",
        direction="under",
        graph_write_hints=hints,
    )
    db.commit()
    rows = db.query(GraphWritebackQueueItem).all()
    assert len(rows) == 1
    assert rows[0].sensitivity == "medium"
    assert rows[0].status == "pending_cosign"


def test_teach_with_protected_attribute_hint_blocks(db):
    s = _seed(db)
    user = _user(db, s.org)
    hints = [
        {
            "action": "assert_edge",
            "from_node_id": "Candidate:42",
            "edge_type": "HAS_SKILL",
            "to_node_id": "Gender:female",
            "confidence": 1.0,
            "rationale": "...",
        }
    ]
    teach_decision.run(
        db,
        Actor.recruiter(user),
        organization_id=s.org.id,
        decision_id=int(s.decision.id),
        failure_mode="missing_signal",
        correction_text="don't do this",
        scope="role",
        attributed_to="cv_scoring",
        direction="under",
        graph_write_hints=hints,
    )
    db.commit()
    rows = db.query(GraphWritebackQueueItem).all()
    assert len(rows) == 1
    assert rows[0].status == "blocked"
    assert rows[0].blocked_reason == "protected_attribute"


# ---------------------------------------------------------------------------
# Co-sign / reject helpers
# ---------------------------------------------------------------------------


def test_cosign_blocks_self_cosign(db):
    s = _seed(db)
    user = _user(db, s.org)
    fb = DecisionFeedback(
        decision_id=s.decision.id, reviewer_id=user.id,
        organization_id=s.org.id, role_id=s.role.id,
        failure_mode="other", correction_text="seed", scope="role",
    )
    db.add(fb); db.flush()
    item = GraphWritebackQueueItem(
        organization_id=s.org.id,
        source_feedback_id=fb.id,
        hint_json={"action": "assert_edge", "edge_type": "SIMILAR_TO",
                   "from_node_id": "Candidate:1", "to_node_id": "Candidate:2",
                   "confidence": 0.7, "rationale": "x"},
        sensitivity="medium",
        status="pending_cosign",
        proposed_by_user_id=user.id,
    )
    db.add(item); db.flush()
    ok = wb_pipeline.cosign_pending(db, item=item, cosigner_user_id=user.id)
    assert ok is False
    assert item.status == "pending_cosign"


def test_cosign_commits_with_second_user(db):
    s = _seed(db)
    proposer = _user(db, s.org, idx=1)
    cosigner = _user(db, s.org, idx=2)
    fb = DecisionFeedback(
        decision_id=s.decision.id, reviewer_id=proposer.id,
        organization_id=s.org.id, role_id=s.role.id,
        failure_mode="other", correction_text="seed", scope="role",
    )
    db.add(fb); db.flush()
    item = GraphWritebackQueueItem(
        organization_id=s.org.id,
        source_feedback_id=fb.id,
        hint_json={"action": "assert_edge", "edge_type": "SIMILAR_TO",
                   "from_node_id": "Candidate:1", "to_node_id": "Candidate:2",
                   "confidence": 0.7, "rationale": "x"},
        sensitivity="medium",
        status="pending_cosign",
        proposed_by_user_id=proposer.id,
    )
    db.add(item); db.flush()
    ok = wb_pipeline.cosign_pending(
        db, item=item, cosigner_user_id=cosigner.id, cosign_note="ok"
    )
    assert ok is True
    assert item.status == "committed"
    assert item.cosigned_by_user_id == cosigner.id


def test_reject_pending_records_reason(db):
    s = _seed(db)
    proposer = _user(db, s.org, idx=1)
    cosigner = _user(db, s.org, idx=2)
    fb = DecisionFeedback(
        decision_id=s.decision.id, reviewer_id=proposer.id,
        organization_id=s.org.id, role_id=s.role.id,
        failure_mode="other", correction_text="seed", scope="role",
    )
    db.add(fb); db.flush()
    item = GraphWritebackQueueItem(
        organization_id=s.org.id,
        source_feedback_id=fb.id,
        hint_json={"action": "assert_edge", "edge_type": "SIMILAR_TO",
                   "from_node_id": "Candidate:1", "to_node_id": "Candidate:2",
                   "confidence": 0.7, "rationale": "x"},
        sensitivity="medium",
        status="pending_cosign",
        proposed_by_user_id=proposer.id,
    )
    db.add(item); db.flush()
    wb_pipeline.reject_pending(
        db, item=item, cosigner_user_id=cosigner.id, reason="not enough evidence"
    )
    assert item.status == "rejected"
    assert item.rejection_reason == "not enough evidence"
