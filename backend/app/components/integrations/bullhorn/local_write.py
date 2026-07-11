"""Local-write-wins guard for Bullhorn ``bullhorn_status``.

Direct analogue of Workable's ``_stage_overwrite_blocked`` (see
``workable/sync_service.py``). When Taali itself writes a JobSubmission status
back to Bullhorn (a recruiter move / reject), it stamps
``bullhorn_status_local_write_at`` on the application. An inbound event or a
``dateLastModified`` sweep carrying a snapshot fetched BEFORE that write — or
just lagging Bullhorn's own settle — would otherwise clobber the fresh status
with the stale one. Inside the guard window we keep Taali's value when the
incoming status DIFFERS; after it, Bullhorn has settled and the sync wins again.

Shared by the event handler and the reconcile sweep so both honour the guard
identically. Kept in its own module (tiny, no cycles) so importing it never
pulls the heavier sync modules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Match Workable's 15-minute local-write guard exactly (one settle window across
# both integrations keeps the mental model single).
_LOCAL_STATUS_WRITE_GUARD = timedelta(minutes=15)


def bullhorn_status_overwrite_blocked(app, new_status) -> bool:
    """True when a sync/event must NOT overwrite ``bullhorn_status``.

    Blocks only when Taali set the status itself within the guard window AND the
    incoming value is *different* — an identical value is a harmless no-op and is
    never blocked. Any error in the time math fails open (allows the write) so
    the guard can never wedge a sync.
    """
    written_at = getattr(app, "bullhorn_status_local_write_at", None)
    if written_at is None:
        return False
    if str(new_status or "") == str(getattr(app, "bullhorn_status", None) or ""):
        return False  # same value — nothing to protect
    try:
        # A timestamp round-tripped through the DB can come back tz-NAIVE (SQLite
        # always, some Postgres column configs). Subtracting a naive from an aware
        # datetime raises — which would make the guard silently fail-open and never
        # fire for a DB-loaded row. Coerce naive → UTC so the window check is real.
        if written_at.tzinfo is None:
            written_at = written_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - written_at) < _LOCAL_STATUS_WRITE_GUARD
    except Exception:  # pragma: no cover — never let the guard break a sync
        return False
