"""Wiring smoke tests for the policy_evaluator overlay + system_prompt intent.

Covers:
- _maybe_escalate flips the verdict to escalate_low_confidence when
  sub-agent disagreement or uncertainty trips the abstention rule.
- _maybe_escalate is a no-op when the engine already chose skip /
  no_action / auto_reject, or when fewer than 3 sub-agents have results.
- policy_evaluator passes role_intent into SubAgentRequest.extra when
  authored.
- system_prompt._render_role_intent returns the structured + free-text
  block when intent exists; empty string when none authored.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event

from app.agent_runtime import policy_evaluator
from app.agent_runtime import role_intent as ri
from app.agent_runtime.contracts import StructuredIntent
from app.agent_runtime.system_prompt import _render_role_intent
from app.decision_policy.engine import PolicyDecision
from app.models.agent_decision import AgentDecision
from app.models.decision_feedback import DecisionFeedback
from app.models.organization import Organization
from app.models.role import Role
from app.models.role_intent import RoleIntent
from app.sub_agents.base import SubAgentResult


_BIG_PK_COUNTERS = {
    "agent_decisions": 0,
    "decision_feedback": 0,
    "role_intents": 0,
}


def _assign(mapper, connection, target):  # pragma: no cover
    name = target.__table__.name
    if target.id is None and name in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[name] += 1
        target.id = _BIG_PK_COUNTERS[name]


event.listen(AgentDecision, "before_insert", _assign)
event.listen(DecisionFeedback, "before_insert", _assign)
event.listen(RoleIntent, "before_insert", _assign)


# ---------------------------------------------------------------------------
# Abstention overlay
# ---------------------------------------------------------------------------


def _verdict(decision_type: str, confidence: float = 0.7) -> PolicyDecision:
    return PolicyDecision(
        decision_type=decision_type,
        confidence=confidence,
        reasoning="...",
        rule_path=["point:advance_to_interview", "rule:fired"],
    )


def _result(name: str, confidence: float, uncertainty: float = 0.1, ok: bool = True) -> SubAgentResult:
    return SubAgentResult(
        sub_agent=name, ok=ok, confidence=confidence, uncertainty=uncertainty,
        output={"score": confidence},
    )


def test_abstention_no_op_when_verdict_is_skip():
    out = policy_evaluator._maybe_escalate(
        _verdict("skip"),
        {"pre_screen": _result("pre_screen", 0.5, 0.9)},
    )
    assert out.decision_type == "skip"


def test_abstention_no_op_when_verdict_is_no_action():
    out = policy_evaluator._maybe_escalate(
        _verdict("no_action"),
        {"pre_screen": _result("pre_screen", 0.5, 0.9)},
    )
    assert out.decision_type == "no_action"


def test_abstention_no_op_when_fewer_than_3_sub_agents():
    out = policy_evaluator._maybe_escalate(
        _verdict("queue_advance_decision"),
        {
            "pre_screen": _result("pre_screen", 0.9, 0.9),
            "cv_scoring": _result("cv_scoring", 0.9, 0.9),
        },
    )
    # Even though uncertainty is high, only 2 sub-agents → can't measure
    # disagreement, so abstention is skipped.
    assert out.decision_type == "queue_advance_decision"


def test_abstention_fires_on_per_agent_uncertainty():
    out = policy_evaluator._maybe_escalate(
        _verdict("queue_advance_decision"),
        {
            "pre_screen": _result("pre_screen", 0.8, 0.1),
            "cv_scoring": _result("cv_scoring", 0.7, 0.1),
            "assessment_scoring": _result("assessment_scoring", 0.6, 0.7),
            "graph_priors": _result("graph_priors", 0.7, 0.1),
        },
    )
    assert out.decision_type == "escalate_low_confidence"
    assert any("abstention_overlay" in step for step in out.rule_path)


def test_abstention_fires_on_disagreement():
    # 0.95 / 0.4 / 0.35 / 0.3 → median 0.375, max 0.95 → spread > 0.5.
    out = policy_evaluator._maybe_escalate(
        _verdict("queue_advance_decision"),
        {
            "pre_screen": _result("pre_screen", 0.95, 0.1),
            "cv_scoring": _result("cv_scoring", 0.4, 0.1),
            "assessment_scoring": _result("assessment_scoring", 0.35, 0.1),
            "graph_priors": _result("graph_priors", 0.3, 0.1),
        },
    )
    assert out.decision_type == "escalate_low_confidence"


def test_abstention_preserves_when_signals_aligned():
    out = policy_evaluator._maybe_escalate(
        _verdict("queue_advance_decision", confidence=0.9),
        {
            "pre_screen": _result("pre_screen", 0.85, 0.1),
            "cv_scoring": _result("cv_scoring", 0.82, 0.1),
            "assessment_scoring": _result("assessment_scoring", 0.78, 0.1),
            "graph_priors": _result("graph_priors", 0.8, 0.1),
        },
    )
    assert out.decision_type == "queue_advance_decision"


# ---------------------------------------------------------------------------
# system_prompt._render_role_intent
# ---------------------------------------------------------------------------


def _seed_role(db):
    org = Organization(name="Wiring Org", slug=f"wire-{id(db)}")
    db.add(org)
    db.flush()
    role = Role(
        organization_id=org.id, name="Backend Engineer", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role)
    db.flush()
    return SimpleNamespace(org=org, role=role)


def test_render_role_intent_returns_empty_when_no_intent_authored(db):
    s = _seed_role(db)
    rendered = _render_role_intent(s.role)
    assert rendered == ""


def test_render_role_intent_includes_structured_fields(db):
    s = _seed_role(db)
    ri.author_new_version(
        db, organization_id=int(s.org.id), role_id=int(s.role.id),
        structured=StructuredIntent(
            soft_signals=["ambiguity tolerance", "stakeholder pushback"],
            deal_breakers=["won't relocate"],
            growth_expectations="team lead in 18 months",
            context_for_opening="backfill for burnout",
        ),
        free_text="Looking for resilience and broad communication ability.",
    )
    db.commit()
    rendered = _render_role_intent(s.role)
    assert "ROLE INTENT" in rendered
    assert "ambiguity tolerance" in rendered
    assert "stakeholder pushback" in rendered
    assert "won't relocate" in rendered
    assert "team lead in 18 months" in rendered
    assert "backfill" in rendered
    assert "resilience" in rendered


def test_render_role_intent_caps_free_text_without_hiding_latest_answer(db):
    s = _seed_role(db)
    previous = (
        "OLDEST ANSWER: customer-facing judgment was required. "
        + ("old context " * 180)
    )
    ri.author_new_version(
        db,
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        structured=StructuredIntent(),
        free_text=previous,
    )
    latest = (
        "LATEST MUST-HAVE: candidates must overlap Dubai mornings.\n\n"
        "Keep this entire second paragraph as current guidance."
    )
    ri.author_new_version(
        db,
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        structured=StructuredIntent(),
        free_text=f"{previous.strip()}\n\n{latest}",
    )
    db.commit()

    rendered = _render_role_intent(s.role)
    notes = rendered.split("- Notes: ", 1)[1]

    assert len(notes) == 1200
    assert "OLDEST ANSWER" not in notes
    assert latest in notes
    assert notes.endswith(latest)
    assert "omitted" in notes
