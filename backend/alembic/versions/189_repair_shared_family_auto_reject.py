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

_POSTGRES_ROLES_WRITE_FENCE_TIMEOUT_MS = 5_000
_SHARED_FAMILY_AUTOMATION_ERROR = (
    "shared role families cannot enable automatic rejection"
)
_CROSS_TENANT_ATS_OWNER_ERROR = (
    "related roles cannot reference an ATS owner in another organization"
)


def _fence_postgres_roles_writes(
    bind: sa.engine.Connection,
    *,
    timeout_ms: int = _POSTGRES_ROLES_WRITE_FENCE_TIMEOUT_MS,
) -> None:
    """Keep the repair stable while an older application version is live.

    ``EXCLUSIVE`` is the least restrictive mode that conflicts with both the
    ``ROW EXCLUSIVE`` lock used by DML and the ``ROW SHARE`` lock used by
    ``SELECT ... FOR UPDATE`` authorizers. It remains compatible with ordinary
    ``ACCESS SHARE`` reads. The transaction-local timeout bounds only lock
    acquisition and is restored as soon as the lock is held; the lock itself
    remains until transaction end. SQLite has no equivalent table-lock syntax.
    """

    if bind.dialect.name != "postgresql":
        return
    current_timeout_ms = int(
        bind.execute(
            sa.text(
                "SELECT setting::bigint FROM pg_settings "
                "WHERE name = 'lock_timeout'"
            )
        ).scalar_one()
    )
    # Respect an operator's tighter release setting; cap only an unbounded or
    # looser timeout. This keeps DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS useful
    # when the supported deployment wrapper deliberately sets it below 5s.
    timeout_was_capped = current_timeout_ms == 0 or current_timeout_ms > timeout_ms
    if timeout_was_capped:
        bind.execute(
            sa.text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{timeout_ms}ms"},
        )
    bind.execute(sa.text("LOCK TABLE roles IN EXCLUSIVE MODE"))
    if timeout_was_capped:
        bind.execute(
            sa.text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{current_timeout_ms}ms"},
        )


def _assert_no_cross_tenant_ats_owner_edges(
    bind: sa.engine.Connection,
    roles: sa.TableClause,
) -> None:
    """Fail before repair when related-role history crosses tenant ownership."""

    related = roles.alias("cross_tenant_related")
    owner = roles.alias("cross_tenant_owner")
    breach = bind.execute(
        sa.select(
            related.c.id.label("related_id"),
            related.c.organization_id.label("related_organization_id"),
            owner.c.id.label("owner_id"),
            owner.c.organization_id.label("owner_organization_id"),
        )
        .select_from(related.join(owner, owner.c.id == related.c.ats_owner_role_id))
        .where(
            related.c.role_kind == "sister",
            related.c.ats_owner_role_id.is_not(None),
            related.c.organization_id != owner.c.organization_id,
        )
        .order_by(related.c.id)
        .limit(1)
    ).mappings().first()
    if breach is None:
        return
    raise RuntimeError(
        "Revision 189 found a cross-tenant ATS owner edge "
        f"(related role {int(breach['related_id'])} in organization "
        f"{int(breach['related_organization_id'])} references owner role "
        f"{int(breach['owner_id'])} in organization "
        f"{int(breach['owner_organization_id'])}); refusing to repair or "
        "install the invariant until ownership is corrected."
    )


def _install_postgres_shared_family_invariant(
    bind: sa.engine.Connection,
) -> None:
    """Keep old and new application versions inside the same safety boundary.

    Every invariant-relevant related-role mutation locks its prospective and
    previous ATS owner rows in id order. Owner automation updates already hold
    their target row lock, so the two operations serialize without imposing a
    table lock on ordinary role writes. PostgreSQL trigger functions are
    volatile; the post-lock queries therefore observe the latest committed
    owner/family state under READ COMMITTED.
    """

    current_schema = str(
        bind.execute(sa.text("SELECT current_schema()"))
        .scalar_one()
    )
    quoted_schema = bind.dialect.identifier_preparer.quote(current_schema)
    bind.execute(
        sa.text(
            f"""
            CREATE FUNCTION enforce_shared_family_auto_reject_v189()
            RETURNS trigger
            LANGUAGE plpgsql
            SET search_path = {quoted_schema}, pg_temp
            AS $$
            DECLARE
                previous_owner_id integer;
                next_owner_id integer;
            BEGIN
                IF TG_OP = 'UPDATE'
                   AND OLD.role_kind = 'sister'
                   AND OLD.ats_owner_role_id IS NOT NULL
                THEN
                    previous_owner_id := OLD.ats_owner_role_id;
                END IF;

                IF NEW.role_kind = 'sister'
                   AND NEW.ats_owner_role_id IS NOT NULL
                THEN
                    next_owner_id := NEW.ats_owner_role_id;
                END IF;

                PERFORM owner.id
                FROM roles AS owner
                WHERE owner.id = previous_owner_id
                   OR owner.id = next_owner_id
                ORDER BY owner.id
                FOR UPDATE;

                IF NEW.role_kind = 'sister'
                   AND NEW.ats_owner_role_id IS NOT NULL
                   AND EXISTS (
                       SELECT 1
                       FROM roles AS owner
                       WHERE owner.id = NEW.ats_owner_role_id
                         AND owner.organization_id <> NEW.organization_id
                   )
                THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = '{_CROSS_TENANT_ATS_OWNER_ERROR}';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM roles AS related
                    WHERE related.ats_owner_role_id = NEW.id
                      AND related.role_kind = 'sister'
                      AND related.organization_id <> NEW.organization_id
                )
                THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = '{_CROSS_TENANT_ATS_OWNER_ERROR}';
                END IF;

                IF NEW.deleted_at IS NULL
                   AND (
                       COALESCE(NEW.auto_reject, false)
                       OR COALESCE(NEW.auto_reject_pre_screen, false)
                   )
                   AND (
                       NEW.role_kind = 'sister'
                       OR EXISTS (
                           SELECT 1
                           FROM roles AS related
                           WHERE related.ats_owner_role_id = NEW.id
                             AND related.role_kind = 'sister'
                             AND related.deleted_at IS NULL
                       )
                   )
                THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = '{_SHARED_FAMILY_AUTOMATION_ERROR}';
                END IF;

                IF NEW.role_kind = 'sister'
                   AND NEW.deleted_at IS NULL
                   AND EXISTS (
                       SELECT 1
                       FROM roles AS owner
                       WHERE owner.id = NEW.ats_owner_role_id
                         AND owner.deleted_at IS NULL
                         AND (
                             COALESCE(owner.auto_reject, false)
                             OR COALESCE(owner.auto_reject_pre_screen, false)
                         )
                   )
                THEN
                    RAISE EXCEPTION USING
                        ERRCODE = '23514',
                        MESSAGE = '{_SHARED_FAMILY_AUTOMATION_ERROR}';
                END IF;

                RETURN NEW;
            END;
            $$
            """
        )
    )
    bind.execute(
        sa.text(
            """
            CREATE TRIGGER enforce_shared_family_auto_reject_v189
            BEFORE INSERT OR UPDATE OF
                organization_id,
                role_kind,
                ats_owner_role_id,
                deleted_at,
                auto_reject,
                auto_reject_pre_screen
            ON roles
            FOR EACH ROW
            EXECUTE FUNCTION enforce_shared_family_auto_reject_v189()
            """
        )
    )


def _install_sqlite_shared_family_invariant(
    bind: sa.engine.Connection,
) -> None:
    """Mirror the production invariant on SQLite's single-writer runtime."""

    for operation in ("INSERT", "UPDATE"):
        update_columns = ""
        if operation == "UPDATE":
            update_columns = (
                " OF organization_id, role_kind, ats_owner_role_id, deleted_at, "
                "auto_reject, auto_reject_pre_screen"
            )
        bind.execute(
            sa.text(
                f"""
                CREATE TRIGGER enforce_shared_family_auto_reject_{operation.lower()}_v189
                BEFORE {operation}{update_columns} ON roles
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, '{_CROSS_TENANT_ATS_OWNER_ERROR}')
                    WHERE (
                        NEW.role_kind = 'sister'
                        AND NEW.ats_owner_role_id IS NOT NULL
                        AND EXISTS (
                            SELECT 1
                            FROM roles AS owner
                            WHERE owner.id = NEW.ats_owner_role_id
                              AND owner.organization_id <> NEW.organization_id
                        )
                    ) OR EXISTS (
                        SELECT 1
                        FROM roles AS related
                        WHERE related.ats_owner_role_id = NEW.id
                          AND related.role_kind = 'sister'
                          AND related.organization_id <> NEW.organization_id
                    );

                    SELECT RAISE(ABORT, '{_SHARED_FAMILY_AUTOMATION_ERROR}')
                    WHERE NEW.deleted_at IS NULL
                      AND (
                          (
                              (
                                  COALESCE(NEW.auto_reject, 0) <> 0
                                  OR COALESCE(NEW.auto_reject_pre_screen, 0) <> 0
                              )
                              AND (
                                  NEW.role_kind = 'sister'
                                  OR EXISTS (
                                      SELECT 1
                                      FROM roles AS related
                                      WHERE related.ats_owner_role_id = NEW.id
                                        AND related.role_kind = 'sister'
                                        AND related.deleted_at IS NULL
                                  )
                              )
                          )
                          OR (
                              NEW.role_kind = 'sister'
                              AND EXISTS (
                                  SELECT 1
                                  FROM roles AS owner
                                  WHERE owner.id = NEW.ats_owner_role_id
                                    AND owner.deleted_at IS NULL
                                    AND (
                                        COALESCE(owner.auto_reject, 0) <> 0
                                        OR COALESCE(
                                            owner.auto_reject_pre_screen, 0
                                        ) <> 0
                                    )
                              )
                          )
                      );
                END
                """
            )
        )


def _install_shared_family_invariant(bind: sa.engine.Connection) -> None:
    if bind.dialect.name == "postgresql":
        _install_postgres_shared_family_invariant(bind)
    elif bind.dialect.name == "sqlite":
        _install_sqlite_shared_family_invariant(bind)
    else:
        raise RuntimeError(
            "Revision 189 supports only PostgreSQL and SQLite; refusing to "
            f"install shared-family automation invariants on {bind.dialect.name!r}."
        )


def upgrade() -> None:
    bind = op.get_bind()
    _fence_postgres_roles_writes(bind)
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
    _assert_no_cross_tenant_ats_owner_edges(bind, roles)
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
    unsafe_shared_roles = bind.execute(
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
            sa.or_(
                roles.c.role_kind == "sister",
                sa.exists(
                    sa.select(sa.literal(1)).where(
                        related.c.ats_owner_role_id == roles.c.id,
                        related.c.role_kind == "sister",
                        related.c.deleted_at.is_(None),
                    )
                )
            ),
        )
    ).mappings()
    for shared_role in unsafe_shared_roles:
        prior_version = int(shared_role["version"] or 1)
        changes: dict[str, dict[str, bool]] = {}
        if bool(shared_role["auto_reject"]):
            changes["auto_reject"] = {"before": True, "after": False}
        if bool(shared_role["auto_reject_pre_screen"]):
            changes["auto_reject_pre_screen"] = {
                "before": True,
                "after": False,
            }
        bind.execute(
            events.insert().values(
                organization_id=int(shared_role["organization_id"]),
                role_id=int(shared_role["id"]),
                actor_user_id=None,
                action="role_updated",
                from_version=prior_version,
                to_version=prior_version + 1,
                changes=changes,
                reason=(
                    "Migration disabled automatic rejection because this role "
                    "belongs to a shared ATS candidate pool"
                ),
                request_id="migration:189_shared_family_reject_repair",
            )
        )
        bind.execute(
            roles.update()
            .where(roles.c.id == int(shared_role["id"]))
            .values(
                auto_reject=False,
                auto_reject_pre_screen=False,
                version=prior_version + 1,
                updated_at=sa.func.now(),
            )
        )
    _install_shared_family_invariant(bind)


def downgrade() -> None:
    raise RuntimeError(
        "Revision 189 is intentionally irreversible: automatically rejecting "
        "a shared ATS application cannot be restored safely."
    )
