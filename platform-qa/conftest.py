"""platform-qa shared harness.

Provides:
  * import path setup so `harness`, `examples`, and (later) the real repos are
    importable from the tests.
  * deterministic fixtures: frozen clock + seeded RNG.
  * a `qa_postgres` fixture yielding a real, throwaway Postgres URL — from
    QA_DATABASE_URL if set (CI service container / dev container on a NON-5432
    port), else skipped with a clear reason. We never touch port 5432 (likely
    host/prod pg or an ssh tunnel) — the test datastore must be disposable.

Standards for a "good" Tier 2 test live in README.md.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Deterministic ID/clock anchors the whole harness shares.
FROZEN_EPOCH = 1_748_000_000  # fixed unix time used by fixtures
SEED = 1729


@pytest.fixture(autouse=True)
def _deterministic_rng():
    """Every test starts from the same seed — no order-dependent randomness
    (the Tier 1 leak is the cautionary tale we don't repeat here)."""
    random.seed(SEED)
    yield


@pytest.fixture
def qa_postgres() -> str:
    """A real, throwaway Postgres URL for E2E/integration tests.

    Resolution order:
      1. QA_DATABASE_URL env (CI service container, or `docker compose -f
         docker-compose.qa.yml up` → port 55432). Must NOT be port 5432.
      2. otherwise skip — these tests need a real datastore by design, and we
         refuse to silently fall back to an in-memory shortcut.
    """
    url = os.environ.get("QA_DATABASE_URL", "").strip()
    if not url:
        pytest.skip(
            "QA_DATABASE_URL not set. Start a throwaway pg "
            "(`docker compose -f docker-compose.qa.yml up -d`, port 55432) "
            "and export QA_DATABASE_URL."
        )
    if ":5432/" in url or url.endswith(":5432"):
        pytest.fail(
            "QA_DATABASE_URL points at port 5432 — refusing to run destructive "
            "QA teardown against what is likely host/prod Postgres."
        )
    return url
