"""P0: audit immutability — DB-enforced append-only candidate_application_events.

The application event log is append-only by convention; enforce it at the DB
layer with a BEFORE UPDATE trigger that raises. DELETE is intentionally allowed
(events are cascade-deleted when their application/org is removed). Postgres-only;
a no-op on sqlite test DBs (which are built via create_all + stamp, and rely on
the append-only convention).

Revision ID: 122_audit_event_immutability
Revises: 121_add_source_attribution_and_dispositions
Create Date: 2026-06-25
"""
from __future__ import annotations

from alembic import op

revision = "122_audit_event_immutability"
down_revision = "121_add_source_attribution_and_dispositions"
branch_labels = None
depends_on = None

_CREATE_FN = """
CREATE OR REPLACE FUNCTION reject_candidate_application_event_update()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'candidate_application_events is append-only; UPDATE is not permitted (id=%)',
        OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_TRIGGER = """
DROP TRIGGER IF EXISTS trg_candidate_application_events_no_update
    ON candidate_application_events;
CREATE TRIGGER trg_candidate_application_events_no_update
    BEFORE UPDATE ON candidate_application_events
    FOR EACH ROW
    EXECUTE FUNCTION reject_candidate_application_event_update();
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(_CREATE_FN)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        "DROP TRIGGER IF EXISTS trg_candidate_application_events_no_update "
        "ON candidate_application_events;"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_candidate_application_event_update();")
