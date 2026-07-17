"""Disable unsafe automatic rejection on existing shared role families.

Revision ID: 189_shared_family_reject_repair
Revises: 188_anthropic_batch_receipts
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "189_shared_family_reject_repair"
down_revision = "188_anthropic_batch_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    roles = sa.table(
        "roles",
        sa.column("id", sa.Integer),
        sa.column("organization_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("auto_reject", sa.Boolean),
        sa.column("auto_reject_pre_screen", sa.Boolean),
        sa.column("role_kind", sa.String),
        sa.column("ats_owner_role_id", sa.Integer),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    events = sa.table(
        "role_change_events",
        sa.column("organization_id", sa.Integer),
        sa.column("role_id", sa.Integer),
        sa.column("actor_user_id", sa.Integer),
        sa.column("action", sa.String),
        sa.column("from_version", sa.Integer),
        sa.column("to_version", sa.Integer),
        sa.column("changes", sa.JSON),
        sa.column("reason", sa.Text),
        sa.column("request_id", sa.String),
    )
    related = roles.alias("related")
    unsafe_owners = bind.execute(
        sa.select(
            roles.c.id,
            roles.c.organization_id,
            roles.c.version,
            roles.c.auto_reject,
            roles.c.auto_reject_pre_screen,
        ).where(
            roles.c.deleted_at.is_(None),
            sa.or_(
                roles.c.auto_reject.is_(True),
                roles.c.auto_reject_pre_screen.is_(True),
            ),
            sa.exists(
                sa.select(sa.literal(1)).where(
                    related.c.organization_id == roles.c.organization_id,
                    related.c.ats_owner_role_id == roles.c.id,
                    related.c.role_kind == "sister",
                    related.c.deleted_at.is_(None),
                )
            ),
        )
    ).mappings()
    for owner in unsafe_owners:
        prior_version = int(owner["version"] or 1)
        changes: dict[str, dict[str, bool]] = {}
        if bool(owner["auto_reject"]):
            changes["auto_reject"] = {"before": True, "after": False}
        if bool(owner["auto_reject_pre_screen"]):
            changes["auto_reject_pre_screen"] = {
                "before": True,
                "after": False,
            }
        bind.execute(
            events.insert().values(
                organization_id=int(owner["organization_id"]),
                role_id=int(owner["id"]),
                actor_user_id=None,
                action="role_updated",
                from_version=prior_version,
                to_version=prior_version + 1,
                changes=changes,
                reason=(
                    "Migration disabled automatic rejection because this role "
                    "already shares one ATS candidate pool with related roles"
                ),
                request_id="migration:189_shared_family_reject_repair",
            )
        )
        bind.execute(
            roles.update()
            .where(roles.c.id == int(owner["id"]))
            .values(
                auto_reject=False,
                auto_reject_pre_screen=False,
                version=prior_version + 1,
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 189 is intentionally irreversible: automatically rejecting "
        "a shared ATS application cannot be restored safely."
    )
