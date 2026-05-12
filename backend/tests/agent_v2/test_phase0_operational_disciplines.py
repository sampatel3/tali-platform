"""Phase 0 — the five operational disciplines from §8.

Covers:
- ``agent_models.yaml`` exists and exposes overrides for every named agent.
- ``get_model_for_agent`` returns the right (model, max_tokens) tuple
  per the cost-model review (graph_priors on Haiku, cv/assessment on Sonnet).
- Cache breakpoints in the system prompt: role_block is cached, runtime
  per-cycle block is NOT cached (per-candidate content must not leak
  above a cache breakpoint).
- ``token_spend_aggregator`` rolls up usage_events for an agent_run_id
  into the expected JSON shape.
- ``queue_decision.run`` persists the token_spend roll-up on the
  AgentDecision row.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import event

from app.actions import queue_decision
from app.actions.types import ACTOR_AGENT, Actor
from app.agent_runtime import model_config
from app.agent_runtime import token_spend_aggregator
from app.agent_runtime.system_prompt import build_system_prompt
from app.models.agent_decision import AgentDecision
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent


_BIG_PK_COUNTERS = {"agent_decisions": 0, "agent_runs": 0}


def _assign(mapper, connection, target):  # pragma: no cover
    name = target.__table__.name
    if target.id is None and name in _BIG_PK_COUNTERS:
        _BIG_PK_COUNTERS[name] += 1
        target.id = _BIG_PK_COUNTERS[name]


event.listen(AgentDecision, "before_insert", _assign)
event.listen(AgentRun, "before_insert", _assign)


# ---------------------------------------------------------------------------
# Discipline §8.1 — tiered models, configured not coded
# ---------------------------------------------------------------------------


def test_agent_models_yaml_overrides_global_default():
    model_config.invalidate()
    pre = model_config.get_model_for_agent("pre_screen")
    cv = model_config.get_model_for_agent("cv_scoring")
    gp = model_config.get_model_for_agent("graph_priors")
    ts = model_config.get_model_for_agent("task_selection")
    asx = model_config.get_model_for_agent("assessment_scoring")
    ip = model_config.get_model_for_agent("intent_parser")
    # Spec defaults + cost-model review.
    assert pre.model.startswith("claude-haiku")
    assert cv.model.startswith("claude-sonnet")
    # Cost-model review: graph_priors downshifted from spec's Sonnet.
    assert gp.model.startswith("claude-haiku"), (
        f"graph_priors should be on Haiku per cost-model review; got {gp.model}"
    )
    assert ts.model.startswith("claude-haiku")
    assert asx.model.startswith("claude-sonnet")
    assert ip.model.startswith("claude-haiku")


def test_max_tokens_bounded_per_agent():
    """Discipline §8.4: bounded outputs per natural schema size."""
    model_config.invalidate()
    assert model_config.get_model_for_agent("pre_screen").max_tokens == 256
    assert model_config.get_model_for_agent("cv_scoring").max_tokens == 512
    assert model_config.get_model_for_agent("graph_priors").max_tokens == 512
    assert model_config.get_model_for_agent("task_selection").max_tokens == 256
    assert model_config.get_model_for_agent("assessment_scoring").max_tokens == 1024


def test_unknown_agent_falls_through_to_global_default():
    model_config.invalidate()
    out = model_config.get_model_for_agent("nonexistent_agent")
    default = model_config.global_default()
    assert out.model == default.model
    assert out.max_tokens == default.max_tokens


# ---------------------------------------------------------------------------
# Discipline §8.2 — three-layer prompts with cache breakpoints
# ---------------------------------------------------------------------------


def test_system_prompt_caches_static_header_and_role_block(db):
    org = Organization(name="P0 Org", slug=f"p0-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="Backend Engineer", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
        job_spec_text="hire backend engineers",
    )
    db.add(role); db.flush()
    blocks = build_system_prompt(
        role=role,
        trigger_context="manual",
        budget_remaining_tokens=10_000,
        decision_budget_remaining=10,
    )
    # Layer 1 (static header) and Layer 2 (role block + intent overlay)
    # carry cache_control. The runtime block (Layer 3, per-cycle) must NOT.
    cached = [b for b in blocks if b.get("cache_control")]
    uncached = [b for b in blocks if not b.get("cache_control")]
    assert len(cached) >= 2, "expected static header + role block to be cached"
    # The runtime block (cycle context: trigger, budget remaining)
    # changes per cycle — must stay below the cache breakpoint so the
    # per-cycle variance doesn't invalidate the role-stable cache.
    runtime_blocks = [b for b in uncached if "Trigger:" in b.get("text", "")]
    assert runtime_blocks, "runtime cycle context should be present and uncached"


def test_system_prompt_does_not_leak_per_cycle_data_above_cache(db):
    """The trigger context, budget numbers, prompt version are per-cycle
    inputs and must NOT appear in cached layers. Verifies §8.2.
    """
    org = Organization(name="LeakOrg", slug=f"leak-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="ML Engineer", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    blocks = build_system_prompt(
        role=role,
        trigger_context="UNIQUE_TRIGGER_TOKEN",
        budget_remaining_tokens=123_456,
        decision_budget_remaining=789,
    )
    cached_text = " ".join(b.get("text", "") for b in blocks if b.get("cache_control"))
    # If per-cycle values appear in the cached portion, the cache would
    # be invalidated on every cycle — defeats the entire discipline.
    assert "UNIQUE_TRIGGER_TOKEN" not in cached_text
    assert "123456" not in cached_text and "123,456" not in cached_text
    assert "789" not in cached_text


# ---------------------------------------------------------------------------
# Discipline §8.5 — token spend logged per decision
# ---------------------------------------------------------------------------


def _seed_run(db):
    org = Organization(name="P0 Spend Org", slug=f"spend-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id, name="Backend Engineer", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email=f"p0-{id(db)}@x.test", full_name="P0 T")
    db.add(cand); db.flush()
    app = CandidateApplication(
        organization_id=org.id, candidate_id=cand.id, role_id=role.id,
        status="applied", pipeline_stage="review",
        pipeline_stage_source="recruiter", application_outcome="open",
        source="manual",
    )
    db.add(app); db.flush()
    run = AgentRun(
        organization_id=org.id, role_id=role.id, status="running",
        trigger="manual", model_version="m", prompt_version="p",
    )
    db.add(run); db.flush()
    return SimpleNamespace(org=org, role=role, app=app, run=run)


def test_token_spend_aggregator_empty_when_no_events(db):
    s = _seed_run(db)
    out = token_spend_aggregator.aggregate(db, agent_run_id=int(s.run.id))
    assert out == {}


def test_token_spend_aggregator_handles_none(db):
    out = token_spend_aggregator.aggregate(db, agent_run_id=None)
    assert out == {}


def test_token_spend_aggregator_rolls_up_events(db):
    s = _seed_run(db)
    # Two usage events tagged to this run, two different features.
    db.add(UsageEvent(
        organization_id=s.org.id, feature="cv_scoring",
        model="claude-sonnet-4-6",
        input_tokens=3200, output_tokens=180,
        cache_read_tokens=2800, cache_creation_tokens=400,
        cost_usd_micro=95_000, markup_multiplier=1.0,
        event_metadata={"agent_run_id": int(s.run.id), "feature": "cv_scoring"},
    ))
    db.add(UsageEvent(
        organization_id=s.org.id, feature="pre_screen",
        model="claude-haiku-4-5",
        input_tokens=800, output_tokens=60,
        cache_read_tokens=0, cache_creation_tokens=0,
        cost_usd_micro=12_000, markup_multiplier=1.0,
        event_metadata={"agent_run_id": int(s.run.id), "feature": "pre_screen"},
    ))
    db.commit()
    out = token_spend_aggregator.aggregate(db, agent_run_id=int(s.run.id))
    assert out["input_tokens"] == 4000
    assert out["output_tokens"] == 240
    assert out["total_micro_usd"] == 107_000
    assert "cv_scoring" in out["by_agent"]
    assert "pre_screen" in out["by_agent"]
    assert out["by_agent"]["cv_scoring"]["calls"] == 1
    assert out["by_agent"]["cv_scoring"]["input"] == 3200


def test_queue_decision_persists_token_spend_on_decision(db):
    s = _seed_run(db)
    db.add(UsageEvent(
        organization_id=s.org.id, feature="cv_scoring",
        model="claude-sonnet-4-6",
        input_tokens=1500, output_tokens=200,
        cost_usd_micro=45_000, markup_multiplier=1.0,
        event_metadata={"agent_run_id": int(s.run.id), "feature": "cv_scoring"},
    ))
    db.commit()
    decision = queue_decision.run(
        db,
        Actor(type=ACTOR_AGENT, agent_run_id=int(s.run.id)),
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        application_id=int(s.app.id),
        decision_type="advance_to_interview",
        reasoning="strong signals",
        model_version="m", prompt_version="p",
    )
    db.commit()
    db.refresh(decision)
    assert isinstance(decision.token_spend, dict)
    assert decision.token_spend.get("input_tokens") == 1500
    assert decision.token_spend.get("total_micro_usd") == 45_000
    assert "cv_scoring" in (decision.token_spend.get("by_agent") or {})
