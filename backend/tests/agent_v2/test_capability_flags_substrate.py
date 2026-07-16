"""v10 capability-flag substrate: client + registry + snapshot persistence.

Covers Sections 2-5 of capability_flags_addendum.md:
- scope filtering (org / role / role_family / cohort / time window)
- deterministic percentage rollout by decision_id hash
- dependency enforcement (recursive, with cycle protection)
- registry sanity (every ALL_CAPABILITIES entry has a Capability record,
  every requires-edge points at a known capability, no cycles)
- queue_decision persists ``active_capabilities`` snapshot
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.actions import queue_decision
from app.actions.types import Actor
from app.capabilities import (
    ALL_CAPABILITIES,
    CAPABILITIES,
    CapabilityFlags,
)
from app.models.agent_run import AgentRun
from app.models.candidate import Candidate
from app.models.candidate_application import CandidateApplication
from app.models.capability_flag import CapabilityFlag
from app.models.organization import Organization
from app.models.role import Role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_org(db, slug_suffix=""):
    org = Organization(name="FlagOrg", slug=f"flagorg-{id(db)}-{slug_suffix}")
    db.add(org)
    db.flush()
    return org


def _make_flag(
    db,
    *,
    capability,
    organization_id=None,
    enabled=True,
    scope=None,
    requires=(),
):
    row = CapabilityFlag(
        capability=capability,
        organization_id=organization_id,
        enabled=enabled,
        scope_json=scope or {},
        requires_json=list(requires),
        rolled_out_by="test",
    )
    db.add(row)
    db.flush()
    return row


def _substrate_client() -> CapabilityFlags:
    """Exercise flag mechanics independently of product readiness."""
    return CapabilityFlags(respect_availability=False)


# ---------------------------------------------------------------------------
# Registry sanity (no DB needed)
# ---------------------------------------------------------------------------


def test_every_listed_capability_has_registry_entry():
    for name in ALL_CAPABILITIES:
        assert name in CAPABILITIES, f"{name} missing from CAPABILITIES dict"


def test_registry_dependencies_are_all_known():
    for name, cap in CAPABILITIES.items():
        for dep in cap.requires:
            assert dep in CAPABILITIES, (
                f"capability {name} requires unknown capability {dep}"
            )


def test_registry_has_no_dependency_cycles():
    # Topological sort would also detect this; this iterative version
    # is sufficient at the canonical-4 capability size.
    def walk(name, visiting):
        if name in visiting:
            return False
        for dep in CAPABILITIES[name].requires:
            if not walk(dep, visiting | {name}):
                return False
        return True

    for name in CAPABILITIES:
        assert walk(name, set()), f"cycle reachable from {name}"


def test_registry_extends_and_replaces_are_disjoint():
    # A capability extends OR replaces a module, never both for the same
    # module name. Catches typos that would put e.g. "policy_engine" in
    # both fields.
    for name, cap in CAPABILITIES.items():
        assert not (set(cap.extends) & set(cap.replaces)), (
            f"capability {name} has overlapping extends/replaces"
        )


def test_capability_flags_fail_closed_until_a_runtime_caller_is_wired():
    """A rollout flag must never make a TODO or parallel env path look live."""
    assert not {name for name, cap in CAPABILITIES.items() if cap.available}
    assert all(
        cap.unavailable_reason
        for cap in CAPABILITIES.values()
        if not cap.available
    )
    assert "PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED" in (
        CAPABILITIES["bias_monitor_continuous"].unavailable_reason or ""
    )


def test_enable_rejects_unimplemented_capability_before_writing(db):
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from app.domains.capabilities.routes import EnableBody, enable_capability

    org = _seed_org(db, "not-ready")
    admin = SimpleNamespace(
        id=1,
        email="admin@example.com",
        organization_id=org.id,
        is_superuser=True,
    )

    with pytest.raises(HTTPException) as exc_info:
        enable_capability(
            "portfolio_agent",
            EnableBody(),
            db=db,
            current_user=admin,
        )

    assert exc_info.value.status_code == 501
    assert exc_info.value.detail["code"] == "CAPABILITY_NOT_READY"
    assert db.query(CapabilityFlag).filter_by(
        organization_id=org.id,
        capability="portfolio_agent",
    ).first() is None


# ---------------------------------------------------------------------------
# Flag client — scope filtering
# ---------------------------------------------------------------------------


def test_disabled_flag_is_never_active(db):
    org = _seed_org(db, "disabled")
    _make_flag(db, capability="portfolio_agent", organization_id=org.id, enabled=False)
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y"
    ) is False


def test_database_row_cannot_activate_unknown_capability(db):
    org = _seed_org(db, "unknown")
    _make_flag(db, capability="unreviewed_dynamic_code", organization_id=org.id, enabled=True)
    db.commit()

    assert _substrate_client().is_active(
        "unreviewed_dynamic_code",
        db=db,
        organization_id=org.id,
        decision_id="x:1:y",
    ) is False


def test_org_scoped_row_overrides_global(db):
    org = _seed_org(db, "override")
    _make_flag(db, capability="portfolio_agent", organization_id=None, enabled=True)
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=False,
    )
    db.commit()
    client = _substrate_client()
    # Org row says disabled — global enabled is shadowed.
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y"
    ) is False
    # A different org with no row falls through to the global.
    other_org_id = org.id + 999
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=other_org_id, decision_id="x:1:y"
    ) is True


def test_role_id_filter(db):
    org = _seed_org(db, "rolefilter")
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"role_ids": [42]},
    )
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y", role_id=42,
    ) is True
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y", role_id=99,
    ) is False
    # Missing role_id when scope demands one → off.
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
    ) is False


def test_role_family_filter(db):
    org = _seed_org(db, "rolefamilyfilter")
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"role_families": ["engineering"]},
    )
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
        role_family="engineering",
    ) is True
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
        role_family="sales",
    ) is False


def test_cohort_tag_intersection(db):
    org = _seed_org(db, "cohort")
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"cohort_tags": ["beta", "early-access"]},
    )
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
        cohort_tags=["beta"],
    ) is True
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
        cohort_tags=["unrelated-tag"],
    ) is False
    # No cohort tags supplied — scope demands ≥1 → off.
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
    ) is False


def test_time_window(db):
    org = _seed_org(db, "timewindow")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"starts_at": past.isoformat(), "ends_at": future.isoformat()},
    )
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y"
    ) is True
    # Inject a probe time outside the window.
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
        now=past - timedelta(days=1),
    ) is False
    assert client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="x:1:y",
        now=future + timedelta(days=1),
    ) is False


# ---------------------------------------------------------------------------
# Percentage rollout
# ---------------------------------------------------------------------------


def test_percentage_rollout_is_deterministic(db):
    org = _seed_org(db, "pct")
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"percentage": 50.0},
    )
    db.commit()
    client = _substrate_client()
    # Same decision_id → same answer twice.
    a = client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="cycle:42:advance"
    )
    b = client.is_active(
        "portfolio_agent", db=db, organization_id=org.id, decision_id="cycle:42:advance"
    )
    assert a is b


def test_percentage_zero_disables_everyone(db):
    org = _seed_org(db, "pctzero")
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"percentage": 0.0},
    )
    db.commit()
    client = _substrate_client()
    # All 100 sampled decision_ids must be off.
    assert not any(
        client.is_active(
            "portfolio_agent", db=db, organization_id=org.id, decision_id=f"id:{i}"
        )
        for i in range(100)
    )


def test_percentage_full_enables_everyone(db):
    org = _seed_org(db, "pctfull")
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
        scope={"percentage": 100.0},
    )
    db.commit()
    client = _substrate_client()
    assert all(
        client.is_active(
            "portfolio_agent", db=db, organization_id=org.id, decision_id=f"id:{i}"
        )
        for i in range(20)
    )


# ---------------------------------------------------------------------------
# Dependency enforcement
# ---------------------------------------------------------------------------


def test_dependency_blocks_when_dep_not_active(db):
    """The canonical 4 capabilities have no inter-deps, but the flag
    client's requires-walking mechanism is general — exercise it with
    a synthetic edge between two canonical capabilities so the test
    still covers the contract.
    """
    org = _seed_org(db, "dep")
    # Synthetic edge: capability_auditor requires causal_mode. Only
    # enable the parent — dep is unset → off.
    _make_flag(
        db, capability="capability_auditor", organization_id=org.id, enabled=True,
        requires=["causal_mode"],
    )
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "capability_auditor",
        db=db, organization_id=org.id, decision_id="x:1:y",
    ) is False


def test_dependency_satisfied_when_dep_active(db):
    org = _seed_org(db, "depsat")
    # Two-hop synthetic dep chain: capability_auditor → causal_mode →
    # portfolio_agent. All three enabled; the walk completes.
    _make_flag(
        db, capability="portfolio_agent", organization_id=org.id, enabled=True,
    )
    _make_flag(
        db, capability="causal_mode", organization_id=org.id, enabled=True,
        requires=["portfolio_agent"],
    )
    _make_flag(
        db, capability="capability_auditor", organization_id=org.id, enabled=True,
        requires=["causal_mode"],
    )
    db.commit()
    client = _substrate_client()
    assert client.is_active(
        "capability_auditor",
        db=db, organization_id=org.id, decision_id="x:1:y",
    ) is True


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_dict_with_every_listed_capability(db):
    org = _seed_org(db, "snap")
    _make_flag(
        db, capability="capability_auditor", organization_id=org.id, enabled=True,
    )
    db.commit()
    client = _substrate_client()
    snap = client.snapshot(
        ALL_CAPABILITIES,
        db=db, organization_id=org.id, decision_id="x:1:y",
    )
    assert set(snap.keys()) == set(ALL_CAPABILITIES)
    assert snap["capability_auditor"] is True
    # Everything else should be off.
    assert snap["portfolio_agent"] is False


# ---------------------------------------------------------------------------
# Persistence via queue_decision
# ---------------------------------------------------------------------------


def _seed_queue_context(db):
    org = _seed_org(db, "queue")
    role = Role(
        organization_id=org.id, name="Backend", source="manual",
        agentic_mode_enabled=True, monthly_usd_budget_cents=0,
    )
    db.add(role); db.flush()
    cand = Candidate(organization_id=org.id, email="q@x.test", full_name="Q T")
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
        trigger="manual",
        model_version="m", prompt_version="p",
    )
    db.add(run); db.flush()
    return SimpleNamespace(org=org, role=role, app=app, run=run)


def test_queue_decision_persists_snapshot_with_no_flags(db):
    # Even when no v10 flags are set, the snapshot column is populated
    # with an all-false dict — the audit trail is intact from day one.
    s = _seed_queue_context(db)
    decision = queue_decision.run(
        db,
        Actor(type="agent", agent_run_id=int(s.run.id)),
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        application_id=int(s.app.id),
        decision_type="advance_to_interview",
        reasoning="t",
        model_version="m",
        prompt_version="p",
    )
    db.commit()
    db.refresh(decision)
    assert isinstance(decision.active_capabilities, dict)
    # All registered capabilities present and false.
    for name in ALL_CAPABILITIES:
        assert decision.active_capabilities[name] is False


def test_queue_decision_snapshot_rejects_stale_unavailable_flag(db):
    s = _seed_queue_context(db)
    _make_flag(
        db, capability="capability_auditor", organization_id=s.org.id, enabled=True,
    )
    db.commit()
    # Force the shared client to re-read.
    from app.capabilities.flags import get_shared
    get_shared().invalidate()

    decision = queue_decision.run(
        db,
        Actor(type="agent", agent_run_id=int(s.run.id)),
        organization_id=int(s.org.id),
        role_id=int(s.role.id),
        application_id=int(s.app.id),
        decision_type="advance_to_interview",
        reasoning="t",
        model_version="m",
        prompt_version="p",
    )
    db.commit()
    db.refresh(decision)
    assert decision.active_capabilities["capability_auditor"] is False
    assert decision.active_capabilities["portfolio_agent"] is False
