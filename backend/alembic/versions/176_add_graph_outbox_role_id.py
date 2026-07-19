"""Add normalized role ownership to the graph episode outbox.

Revision ID: 176_graph_outbox_role_id
Revises: 175_workspace_bulk_role_pause
Create Date: 2026-07-19
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from alembic import op


revision = "176_graph_outbox_role_id"
down_revision = "175_workspace_bulk_role_pause"
branch_labels = None
depends_on = None

_BACKFILL_BATCH_SIZE = 500
_FOREIGN_KEY_NAME = "fk_graph_episode_outbox_role_id_roles"
_INDEX_NAME = "ix_graph_episode_outbox_role_id"
_MAX_INTEGER_ID = 2_147_483_647
_MAX_BIGINT_ID = 9_223_372_036_854_775_807


def _positive_int(value: Any, *, maximum: int) -> int | None:
    """Return an in-range positive integer without unsafe JSON coercion."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= maximum else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.isascii() or not stripped.isdecimal():
            return None
        significant = stripped.lstrip("0")
        if not significant:
            return None
        maximum_text = str(maximum)
        if len(significant) > len(maximum_text) or (
            len(significant) == len(maximum_text) and significant > maximum_text
        ):
            return None
        return int(significant)
    return None


def _payload_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, RecursionError):
            return {}
        if isinstance(decoded, Mapping):
            return decoded
    return {}


def _valid_roles(
    connection: sa.Connection,
    roles: sa.TableClause,
    role_ids: set[int],
) -> dict[int, int]:
    if not role_ids:
        return {}
    rows = connection.execute(
        sa.select(roles.c.id, roles.c.organization_id).where(
            roles.c.id.in_(sorted(role_ids)),
            roles.c.deleted_at.is_(None),
        )
    ).all()
    return {int(role_id): int(organization_id) for role_id, organization_id in rows}


def _backfill_role_ids(connection: sa.Connection) -> None:
    outbox = sa.table(
        "graph_episode_outbox",
        sa.column("id", sa.BigInteger),
        sa.column("organization_id", sa.Integer),
        sa.column("payload", sa.JSON),
        sa.column("status", sa.String),
        sa.column("role_id", sa.Integer),
    )
    roles = sa.table(
        "roles",
        sa.column("id", sa.Integer),
        sa.column("organization_id", sa.Integer),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    decisions = sa.table(
        "agent_decisions",
        sa.column("id", sa.BigInteger),
        sa.column("organization_id", sa.Integer),
        sa.column("role_id", sa.Integer),
    )

    last_id = 0
    while True:
        rows = connection.execute(
            sa.select(
                outbox.c.id,
                outbox.c.organization_id,
                # Own JSON decoding here so a pathological legacy number or
                # nesting depth cannot fail in the PostgreSQL driver before
                # the guarded parser gets a chance to skip it.
                sa.cast(outbox.c.payload, sa.Text).label("payload"),
            )
            .where(
                outbox.c.status == "pending",
                outbox.c.role_id.is_(None),
                outbox.c.id > last_id,
            )
            .order_by(outbox.c.id)
            .limit(_BACKFILL_BATCH_SIZE)
        ).mappings().all()
        if not rows:
            break
        last_id = int(rows[-1]["id"])

        parsed_rows: list[tuple[int, int, Mapping[str, Any]]] = []
        direct_role_ids: set[int] = set()
        for row in rows:
            payload = _payload_mapping(row["payload"])
            parsed_rows.append(
                (int(row["id"]), int(row["organization_id"]), payload)
            )
            role_id = _positive_int(
                payload.get("role_id"),
                maximum=_MAX_INTEGER_ID,
            )
            if role_id is not None:
                direct_role_ids.add(role_id)

        valid_direct_roles = _valid_roles(connection, roles, direct_role_ids)
        resolved: dict[int, int] = {}
        decision_candidates: dict[int, tuple[int, int]] = {}
        for outbox_id, organization_id, payload in parsed_rows:
            raw_role_id = payload.get("role_id")
            role_id = _positive_int(raw_role_id, maximum=_MAX_INTEGER_ID)
            if (
                role_id is not None
                and valid_direct_roles.get(role_id) == organization_id
            ):
                resolved[outbox_id] = role_id
                continue
            # Match the runtime ownership resolver: a decision is a legacy
            # fallback only when role_id is absent/null. An explicit malformed,
            # deleted, or cross-organization role must remain unresolved.
            if raw_role_id is not None:
                continue
            decision_id = _positive_int(
                payload.get("decision_id"),
                maximum=_MAX_BIGINT_ID,
            )
            if decision_id is not None:
                decision_candidates[outbox_id] = (organization_id, decision_id)

        if decision_candidates:
            decision_rows = connection.execute(
                sa.select(
                    decisions.c.id,
                    decisions.c.organization_id,
                    decisions.c.role_id,
                ).where(
                    decisions.c.id.in_(
                        sorted({item[1] for item in decision_candidates.values()})
                    )
                )
            ).all()
            decisions_by_id = {
                int(decision_id): (int(organization_id), int(role_id))
                for decision_id, organization_id, role_id in decision_rows
            }
            valid_decision_roles = _valid_roles(
                connection,
                roles,
                {role_id for _, role_id in decisions_by_id.values()},
            )
            for outbox_id, (
                organization_id,
                decision_id,
            ) in decision_candidates.items():
                decision = decisions_by_id.get(decision_id)
                if decision is None:
                    continue
                decision_org_id, role_id = decision
                if (
                    decision_org_id == organization_id
                    and valid_decision_roles.get(role_id) == organization_id
                ):
                    resolved[outbox_id] = role_id

        if resolved:
            connection.execute(
                outbox.update()
                .where(
                    outbox.c.id == sa.bindparam("outbox_id"),
                    outbox.c.role_id.is_(None),
                )
                .values(role_id=sa.bindparam("resolved_role_id")),
                [
                    {"outbox_id": outbox_id, "resolved_role_id": role_id}
                    for outbox_id, role_id in sorted(resolved.items())
                ],
            )


def upgrade() -> None:
    # Batch mode is a regular ALTER on PostgreSQL and lets local SQLite
    # migration tests recreate the table to add the named foreign key.
    with op.batch_alter_table("graph_episode_outbox") as batch_op:
        batch_op.add_column(sa.Column("role_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            _FOREIGN_KEY_NAME,
            "roles",
            ["role_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _backfill_role_ids(op.get_bind())
    op.create_index(
        _INDEX_NAME,
        "graph_episode_outbox",
        ["role_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="graph_episode_outbox")
    with op.batch_alter_table("graph_episode_outbox") as batch_op:
        batch_op.drop_constraint(_FOREIGN_KEY_NAME, type_="foreignkey")
        batch_op.drop_column("role_id")
