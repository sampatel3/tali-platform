"""Replace legacy ``email_mode`` with a clean ``workable_writeback`` bool.

``email_mode`` (values ``manual_taali`` / ``workable_preferred_fallback_manual``)
was a misnomer — assessment emails are always Taali; the field only ever
governed whether Taali writes candidate activity back to Workable. Rename it
to a TRUE binary ``workable_writeback`` and PRESERVE each org's current
effective write behavior.

For every org with a non-null ``workable_config`` JSON, set
``workable_config['workable_writeback']`` to a boolean equal to the org's
current effective write capability — i.e. ``w_candidates`` is in the
effective granted scopes (replicating ``workable_granted_scopes``):

  * ``granted_scopes`` non-empty  → ``'w_candidates' in granted_scopes``
  * else if the org was connected → ``email_mode ==
    'workable_preferred_fallback_manual' OR auto_reject_enabled == true``
  * else                          → False

Then drop the ``email_mode`` key. Idempotent (safe to re-run) and reversible
(downgrade restores ``email_mode`` from the bool and drops the new key).

Revision ID: 150_workable_writeback_flag
Revises: 149_extend_interview_feedback
Create Date: 2026-07-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "150_workable_writeback_flag"
down_revision = "149_extend_interview_feedback"
branch_labels = None
depends_on = None


_orgs = sa.table(
    "organizations",
    sa.column("id", sa.Integer),
    sa.column("workable_connected", sa.Boolean),
    sa.column("workable_config", sa.JSON),
)


def _effective_writeback(config: dict, *, connected: bool) -> bool:
    """Replicate ``workable_granted_scopes`` write-capability derivation."""
    granted = config.get("granted_scopes")
    granted = granted if isinstance(granted, list) else []
    if granted:
        return "w_candidates" in granted
    if not connected:
        return False
    return (
        str(config.get("email_mode") or "manual_taali")
        == "workable_preferred_fallback_manual"
        or bool(config.get("auto_reject_enabled"))
    )


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(
            _orgs.c.id, _orgs.c.workable_connected, _orgs.c.workable_config
        ).where(_orgs.c.workable_config.isnot(None))
    ).fetchall()

    for row in rows:
        config = row.workable_config
        if not isinstance(config, dict):
            continue
        writeback = _effective_writeback(
            config, connected=bool(row.workable_connected)
        )
        new_config = {k: v for k, v in config.items() if k != "email_mode"}
        new_config["workable_writeback"] = writeback
        if new_config == config:
            continue  # idempotent no-op
        bind.execute(
            sa.update(_orgs)
            .where(_orgs.c.id == row.id)
            .values(workable_config=new_config)
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_orgs.c.id, _orgs.c.workable_config).where(
            _orgs.c.workable_config.isnot(None)
        )
    ).fetchall()

    for row in rows:
        config = row.workable_config
        if not isinstance(config, dict) or "workable_writeback" not in config:
            continue
        writeback = bool(config.get("workable_writeback"))
        new_config = {k: v for k, v in config.items() if k != "workable_writeback"}
        new_config["email_mode"] = (
            "workable_preferred_fallback_manual" if writeback else "manual_taali"
        )
        bind.execute(
            sa.update(_orgs)
            .where(_orgs.c.id == row.id)
            .values(workable_config=new_config)
        )
