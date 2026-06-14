"""Local-write-wins guard: a sync must not clobber a stage Taali just moved."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.components.integrations.workable.sync_service import _stage_overwrite_blocked


def _app(stage, written_at):
    return SimpleNamespace(workable_stage=stage, workable_stage_local_write_at=written_at)


def test_no_local_write_is_not_blocked():
    # Never moved by Taali → the sync is authoritative.
    assert _stage_overwrite_blocked(_app("Applied", None), "Technical Interview") is False


def test_recent_local_write_with_different_stage_is_blocked():
    now = datetime.now(timezone.utc)
    app = _app("Technical Interview", now - timedelta(minutes=2))
    # Taali moved them 2 min ago; a stale sync wants to revert to Applied → block.
    assert _stage_overwrite_blocked(app, "Applied") is True


def test_recent_local_write_with_same_stage_is_not_blocked():
    now = datetime.now(timezone.utc)
    app = _app("Technical Interview", now - timedelta(minutes=2))
    # Sync agrees with Taali's value → nothing to protect.
    assert _stage_overwrite_blocked(app, "Technical Interview") is False


def test_old_local_write_is_not_blocked():
    now = datetime.now(timezone.utc)
    app = _app("Technical Interview", now - timedelta(minutes=30))
    # Past the guard window → Workable has settled, the sync wins again.
    assert _stage_overwrite_blocked(app, "Applied") is False
