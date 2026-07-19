"""Pre-screen sub-agent: fraud detection penalty path.

These tests cover the deterministic copy-paste check that the pre-screen
agent runs after the LLM. A high-overlap CV must:
  * have its score capped below the gate (so downstream policy filters it),
  * carry ``fraud_capped=True`` and the original ``llm_score_100``,
  * surface evidence snippets in the agent output.
"""

from __future__ import annotations

from unittest.mock import patch

from app.sub_agents.base import SubAgentRequest
from app.sub_agents.pre_screen import PRE_SCREEN_SUB_AGENT

from .conftest import make_full_application


_JD_TEXT = (
    "About the role\n"
    "We are hiring a Senior Backend Engineer to lead our payments platform. "
    "The ideal candidate has deep experience designing scalable distributed "
    "systems and is comfortable being on-call for production services.\n\n"
    "Responsibilities\n"
    "  - Own the architecture of our settlement and reconciliation pipeline "
    "    end to end.\n"
    "  - Mentor junior engineers and uplevel team practices.\n"
    "  - Partner with product to scope ambiguous requirements.\n\n"
    "Requirements\n"
    "  - 5+ years of Python in production at scale.\n"
    "  - Experience with event-driven architectures (Kafka, Pulsar).\n"
)


class _StubLLMResult:
    decision = "yes"
    reason = "looks like a strong match"
    score = 82.0
    cache_hit = False
    input_tokens = 200
    output_tokens = 50
    cache_read_tokens = 0
    cache_creation_tokens = 0


def test_copy_paste_cv_caps_score_below_gate(db, monkeypatch):
    monkeypatch.setattr(
        "app.sub_agents.pre_screen.settings.FRAUD_COPY_PASTE_ACTION",
        "cap",
    )
    org, role, _, app = make_full_application(db, cv_text=_JD_TEXT, jd_text=_JD_TEXT)
    role.job_spec_text = _JD_TEXT
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
    )
    with patch(
        "app.sub_agents.pre_screen.run_pre_screen",
        return_value=_StubLLMResult(),
    ):
        result = PRE_SCREEN_SUB_AGENT.run(req, db=db)

    assert result.ok is True
    assert result.output["fraud_capped"] is True
    assert result.output["llm_score_100"] == 82.0
    # Cap is 10 by default — must be at-or-below the v3 gate threshold (30).
    assert result.output["score"] <= 10.0
    assert result.output["decision"] == "no"
    assert "copied verbatim" in result.output["reason"].lower()
    fraud_signal = result.output["fraud_signals"]["cv_copy_paste"]
    assert fraud_signal["triggered"] is True
    assert fraud_signal["evidence"], "expected evidence snippets"


def test_copy_paste_flag_does_not_change_sub_agent_verdict(db, monkeypatch):
    monkeypatch.setattr(
        "app.sub_agents.pre_screen.settings.FRAUD_COPY_PASTE_ACTION",
        "flag",
    )
    org, role, _, app = make_full_application(db, cv_text=_JD_TEXT, jd_text=_JD_TEXT)
    role.job_spec_text = _JD_TEXT
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
    )
    with patch(
        "app.sub_agents.pre_screen.run_pre_screen",
        return_value=_StubLLMResult(),
    ):
        result = PRE_SCREEN_SUB_AGENT.run(req, db=db)

    assert result.ok is True
    assert result.output["score"] == 82.0
    assert result.output["decision"] == "yes"
    assert result.output["fraud_capped"] is False
    copy_paste = result.output["fraud_signals"]["cv_copy_paste"]
    assert copy_paste["triggered"] is True
    assert copy_paste["action"] == "flag"
    assert copy_paste["review_flagged"] is True


def test_legit_cv_does_not_trigger_cap(db):
    org, role, _, app = make_full_application(
        db,
        cv_text=(
            "Maya Patel — Backend engineer with 10 years at Stripe and Klarna. "
            "Built the fraud-detection rules engine handling billions of events "
            "per day. Comfortable owning systems end to end and being on-call."
        ),
        jd_text=_JD_TEXT,
    )
    role.job_spec_text = _JD_TEXT
    db.flush()
    req = SubAgentRequest(
        organization_id=int(org.id),
        application_id=int(app.id),
        role_id=int(role.id),
        skip_cache=True,
    )
    with patch(
        "app.sub_agents.pre_screen.run_pre_screen",
        return_value=_StubLLMResult(),
    ):
        result = PRE_SCREEN_SUB_AGENT.run(req, db=db)

    assert result.ok is True
    assert result.output["fraud_capped"] is False
    assert result.output["score"] == 82.0  # untouched LLM score
    assert result.output["fraud_signals"]["cv_copy_paste"]["triggered"] is False
