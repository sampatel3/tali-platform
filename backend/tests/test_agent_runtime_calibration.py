"""Tests for cross-cycle memory rendering in calibration.render_summary.

Covers the new fields added in the v7 cross-cycle-memory work:
- ``last_cycle`` block rendered as a one-liner
- ``notes`` rendered as a "NOTES FROM PRIOR CYCLES" section, newest first
- Empty inputs fall back to safe sentinels (no crashes, no leaking
  default keys)
"""

from __future__ import annotations

from app.agent_runtime import calibration


def test_render_summary_includes_last_cycle_when_present():
    rendered = calibration.render_summary(
        {
            "decisions_total": 0,
            "last_cycle": {
                "status": "aborted",
                "rounds_used": 18,
                "decisions_emitted": 0,
                "finished_via_complete": False,
                "error": "exceeded MAX_TOOL_ROUNDS without agent_run_complete",
            },
        }
    )
    assert "last cycle: status=aborted" in rendered
    assert "rounds=18" in rendered
    assert "did NOT call agent_run_complete" in rendered


def test_render_summary_last_cycle_absent_renders_sentinel():
    rendered = calibration.render_summary({"decisions_total": 0})
    assert "last cycle: none on record" in rendered


def test_render_summary_renders_notes_newest_first():
    rendered = calibration.render_summary(
        {
            "decisions_total": 0,
            "notes": [
                {
                    "note": "older note",
                    "kind": "pattern",
                    "recorded_at": "2026-05-20T10:00:00+00:00",
                },
                {
                    "note": "newer note",
                    "kind": "todo",
                    "recorded_at": "2026-05-21T09:00:00+00:00",
                },
            ],
        }
    )
    assert "NOTES FROM PRIOR CYCLES" in rendered
    newer_idx = rendered.index("newer note")
    older_idx = rendered.index("older note")
    assert newer_idx < older_idx, "newer note should render before older"
    assert "[todo @ 2026-05-21]" in rendered
    assert "[pattern @ 2026-05-20]" in rendered


def test_render_summary_no_notes_omits_section():
    rendered = calibration.render_summary({"decisions_total": 0, "notes": []})
    assert "NOTES FROM PRIOR CYCLES" not in rendered


def test_save_notes_caps_at_max_fifo():
    """Notes list must cap at _MAX_NOTES (10) with FIFO eviction."""
    role = type("R", (), {"agent_calibration": None})()

    class _StubSession:
        def add(self, _):
            pass

    db = _StubSession()
    for i in range(15):
        calibration.save(
            db,
            role=role,
            updates={
                "notes": [
                    {
                        "note": f"n{i}",
                        "kind": "context",
                        "recorded_at": "2026-05-21T09:00:00+00:00",
                    }
                ]
            },
        )
    notes = role.agent_calibration["notes"]
    assert len(notes) == 10
    # FIFO: notes 0-4 evicted; notes 5-14 retained.
    assert [n["note"] for n in notes] == [f"n{i}" for i in range(5, 15)]
