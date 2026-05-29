"""Tier 2 end-to-end smoke against the assembled platform.

Thin by design — contracts (tests/contract/) do the heavy lifting of catching
substrate→brand breaks; E2E exists only to catch wiring/assembly failures that
unit-level contracts can't express (real DB, real brand boot, a critical
journey).

This needs a real throwaway Postgres (the `qa_postgres` fixture) and the brand
apps assembled on the substrate. In this reference skeleton the assembly step
is a marked TODO; the test documents the shape and skips cleanly until the
real repos are wired in.

    # REPLACE-WITH-REAL: boot mainspring + a brand against qa_postgres, seed
    # deterministic fixtures, drive one critical journey, assert the outcome.
"""
from __future__ import annotations

import pytest


@pytest.mark.e2e
def test_brand_boots_on_substrate_and_serves_a_journey(qa_postgres):
    """One critical user journey through the assembled stack.

    Skips until the real repos are wired in (see module docstring). When wired:
      1. apply substrate + brand migrations to `qa_postgres`
      2. seed a deterministic fixture pipeline
      3. drive: create → advance → read state via the brand
      4. assert the brand's view matches the substrate's pipeline state
    """
    pytest.skip(
        "E2E assembly not wired in this skeleton — needs mainspring + a brand "
        "in the session. qa_postgres is resolved and ready: " + qa_postgres
    )
