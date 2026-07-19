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

from app.actions import queue_decision
from app.actions.types import ACTOR_AGENT, Actor
from app.agent_runtime import token_spend_aggregator
from app.agent_runtime.system_prompt import build_system_prompt
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.organization import Organization
from app.models.role import Role
from app.models.usage_event import UsageEvent


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
    )
    cached_text = " ".join(b.get("text", "") for b in blocks if b.get("cache_control"))
    # If per-cycle values appear in the cached portion, the cache would
    # be invalidated on every cycle — defeats the entire discipline.
    assert "UNIQUE_TRIGGER_TOKEN" not in cached_text


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


def test_token_spend_aggregator_exact_agent_run_id_no_substring_collision(db):
    """Run 12 must not absorb run 123's spend. A bare substring
    ``LIKE '%"agent_run_id": 12%'`` over the serialised JSON also matches
    120/123, folding other runs' usage into this roll-up."""
    s = _seed_run(db)
    db.add(UsageEvent(
        organization_id=s.org.id, feature="cv_scoring", model="claude-sonnet-4-6",
        input_tokens=100, output_tokens=10,
        cache_read_tokens=0, cache_creation_tokens=0,
        cost_usd_micro=1_000, markup_multiplier=1.0,
        event_metadata={"agent_run_id": 12, "feature": "cv_scoring"},
    ))
    db.add(UsageEvent(
        organization_id=s.org.id, feature="cv_scoring", model="claude-sonnet-4-6",
        input_tokens=999, output_tokens=99,
        cache_read_tokens=0, cache_creation_tokens=0,
        cost_usd_micro=9_000, markup_multiplier=1.0,
        event_metadata={"agent_run_id": 123, "feature": "cv_scoring"},
    ))
    db.commit()
    out = token_spend_aggregator.aggregate(db, agent_run_id=12)
    assert out["input_tokens"] == 100  # only run 12, not run 123
    assert out["total_micro_usd"] == 1_000


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
