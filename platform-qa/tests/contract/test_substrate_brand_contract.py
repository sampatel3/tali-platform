"""Tier 2 contract tests: substrate (mainspring) ↔ brand (taali).

This file proves the mechanism that catches the failure mode nothing else
tests: a mainspring change silently breaking a brand.

Two facts are asserted, both runnable today against the reference example:
  1. taali is compatible with the CURRENT substrate interface  → green.
  2. an incompatible substrate change IS CAUGHT by the checker  → green
     (i.e. the detector flags the break, with a localizing message).

When `platform-qa` is wired to the real repos, the example imports are swapped
for real interface extraction (see the REPLACE-WITH-REAL markers); the test
bodies stay the same.
"""
from __future__ import annotations

import pytest

from harness.contract import check_compatibility
from examples import substrate_mainspring as substrate
from examples import brand_taali as taali

# The matrix of (substrate, brand) pairs Tier 2 guards. Real platform-qa adds
# cadence and any future brand here; each new brand is one line.
BRANDS = {"taali": taali.required_interface()}


@pytest.mark.parametrize("brand_name,brand_contract", BRANDS.items())
def test_brand_compatible_with_current_substrate(brand_name, brand_contract):
    """The substrate as it exists today still honours every brand's contract."""
    problems = check_compatibility(substrate.published_interface(), brand_contract)
    assert problems == [], (
        f"{brand_name} is broken by the current substrate:\n  "
        + "\n  ".join(str(p) for p in problems)
    )


def test_breaking_substrate_change_is_caught_removed_output():
    """If mainspring removes an output taali reads, the contract gate fails.

    This is the substrate→brand break the platform could previously only
    discover in production. Here we assert the detector flags it."""
    broken_substrate = substrate.break_remove_output(
        substrate.published_interface(), "pipeline.advance", "state"
    )
    problems = check_compatibility(broken_substrate, taali.required_interface())

    assert problems, "a removed output taali depends on must be caught"
    assert any(p.kind == "missing_output" and "state" in p.detail for p in problems)


def test_breaking_substrate_change_is_caught_new_required_input():
    """If mainspring adds a newly-required input taali doesn't send, caught too."""
    broken_substrate = substrate.break_add_required_input(
        substrate.published_interface(), "pipeline.advance", "actor_id"
    )
    problems = check_compatibility(broken_substrate, taali.required_interface())

    assert any(p.kind == "newly_required_input" and "actor_id" in p.detail for p in problems)
