"""Anthropic prompt-cache ordering invariant for the agent request.

Anthropic processes cache_control blocks in a fixed order — ``tools``,
``system``, ``messages`` — and rejects (400) a ``ttl='1h'`` block that
comes AFTER a ``ttl='5m'`` block:

    "a ttl='1h' cache_control block must not come after a ttl='5m'
     cache_control block."

B2 set the agent system-prompt blocks to 1h for cohort-tick cache reuse.
The AGENT_TOOLS block is processed first; if it stays at the 5m default
while system is 1h, EVERY agent cycle 400s (this happened in prod —
run 276, role 112). This test locks the invariant in: across the full
tools+system block sequence, no 1h block may follow a 5m one.
"""
from __future__ import annotations

from app.agent_runtime.system_prompt import build_system_prompt
from app.agent_runtime.tool_registry import AGENT_TOOLS
from app.models.organization import Organization
from app.models.role import Role


def _ttl_of(block: dict) -> str | None:
    """The effective cache TTL of a block: None = uncached, else the
    ttl ('1h') or the ephemeral default ('5m')."""
    cc = block.get("cache_control")
    if not cc:
        return None
    return cc.get("ttl", "5m")


def _assert_cache_order_valid(blocks_in_order: list[dict]) -> None:
    """Replicates Anthropic's rule: once a 5m cache block appears, no
    later block may be 1h."""
    seen_5m = False
    for i, block in enumerate(blocks_in_order):
        ttl = _ttl_of(block)
        if ttl == "5m":
            seen_5m = True
        elif ttl == "1h" and seen_5m:
            raise AssertionError(
                f"cache block #{i} is ttl='1h' but a ttl='5m' block precedes it "
                "— Anthropic will 400 the whole agent request"
            )


def _seed_role(db) -> Role:
    org = Organization(name="O", slug=f"o-cache-{id(db)}")
    db.add(org); db.flush()
    role = Role(
        organization_id=org.id,
        name="R",
        source="manual",
        job_spec_text="hire an engineer",
        agentic_mode_enabled=True,
        monthly_usd_budget_cents=5000,
        auto_reject=False,
    )
    db.add(role); db.flush()
    return role


def test_agent_tools_then_system_cache_order_is_valid(db):
    """The exact prod failure path: tools block + system blocks combined
    in Anthropic's processing order must not put a 1h after a 5m."""
    role = _seed_role(db)
    db.commit()
    system_blocks = build_system_prompt(role=role, trigger_context="test")
    # Anthropic order: tools first, then system.
    combined = list(AGENT_TOOLS) + list(system_blocks)
    _assert_cache_order_valid(combined)


def test_all_agent_cache_blocks_share_one_ttl(db):
    """Strongest guard: every cached block in the agent request uses the
    same TTL, so reordering can never reintroduce the 1h-after-5m 400."""
    role = _seed_role(db)
    db.commit()
    system_blocks = build_system_prompt(role=role, trigger_context="test")
    ttls = {
        _ttl_of(b)
        for b in (list(AGENT_TOOLS) + list(system_blocks))
        if _ttl_of(b) is not None
    }
    assert ttls == {"1h"}, f"mixed cache TTLs in agent request: {ttls}"


def test_invariant_helper_catches_bad_order():
    """Sanity-check the checker itself: a 1h after a 5m must raise."""
    bad = [
        {"cache_control": {"type": "ephemeral"}},               # 5m
        {"cache_control": {"type": "ephemeral", "ttl": "1h"}},  # 1h after 5m
    ]
    import pytest
    with pytest.raises(AssertionError):
        _assert_cache_order_valid(bad)
