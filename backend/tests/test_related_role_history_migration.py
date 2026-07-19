from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "183_preserve_related_role_history.py"
    )
    spec = importlib.util.spec_from_file_location("related_role_history_183", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restrict_foreign_keys_preserve_related_roles_and_evaluations():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    roles = sa.Table(
        "roles",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "ats_owner_role_id",
            sa.Integer,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
        ),
        sa.Column("name", sa.String, nullable=False),
    )
    evaluations = sa.Table(
        "sister_role_evaluations",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "role_id",
            sa.Integer,
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text),
    )
    with engine.connect() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        metadata.create_all(connection)
        connection.execute(
            roles.insert(),
            [
                {"id": 1, "ats_owner_role_id": None, "name": "Owner"},
                {"id": 2, "ats_owner_role_id": 1, "name": "Related"},
                {"id": 3, "ats_owner_role_id": None, "name": "Empty"},
            ],
        )
        connection.execute(
            evaluations.insert(),
            {"id": 10, "role_id": 2, "summary": "Preserved score"},
        )
        connection.commit()

        migration = _load_migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.commit()

        trigger_names = set(
            connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name = 'roles'"
                )
            ).scalars()
        )
        assert {
            "preserve_owner_role_related_history",
            "preserve_related_role_evaluations",
        } <= trigger_names

        with pytest.raises(IntegrityError):
            connection.execute(roles.delete().where(roles.c.id == 1))
        connection.rollback()
        with pytest.raises(IntegrityError):
            connection.execute(roles.delete().where(roles.c.id == 2))
        connection.rollback()

        assert connection.execute(
            sa.select(roles.c.name).order_by(roles.c.id)
        ).scalars().all() == ["Owner", "Related", "Empty"]
        assert connection.execute(
            sa.select(evaluations.c.summary)
        ).scalar_one() == "Preserved score"

        connection.execute(roles.delete().where(roles.c.id == 3))
        connection.commit()
        assert connection.execute(
            sa.select(roles.c.id).order_by(roles.c.id)
        ).scalars().all() == [1, 2]


def test_unsupported_dialect_is_rejected_before_constraint_changes():
    migration = _load_migration()
    migration.op = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(
            dialect=SimpleNamespace(name="unsupported-test-dialect")
        )
    )

    with pytest.raises(RuntimeError, match="refusing to replace"):
        migration.upgrade()
