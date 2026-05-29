"""Regression test for cross-file test-state leakage (Tier 1 isolation).

History: the test suite shared one in-memory SQLite database for the whole
pytest session and assigned BigInteger PKs from session-global monotonic
counters that never reset. A test asserting a specific id (or a clean first
row) therefore passed when run alone but failed when any other row-creating
test ran first in the same process — the same commit produced different
pass/fail depending on which file-set ran together.

The fix (conftest `_isolate_test` autouse fixture) gives every test a fresh
schema and resets the PK counters before each test. These two tests both
create the first row of their kind and assert it is id == 1; if isolation
regresses, the second test to run will see id == 2 and fail. Order must not
matter.
"""
from app.models.claude_call_log import ClaudeCallLog


def test_first_claude_call_log_row_is_id_1_a(db):
    row = ClaudeCallLog(model="claude-test")
    db.add(row)
    db.commit()
    assert row.id == 1, "PK counter leaked from a prior test — isolation is broken"


def test_first_claude_call_log_row_is_id_1_b(db):
    row = ClaudeCallLog(model="claude-test")
    db.add(row)
    db.commit()
    assert row.id == 1, "PK counter leaked from a prior test — isolation is broken"
