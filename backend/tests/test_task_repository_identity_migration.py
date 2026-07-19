"""Revision 191 preserves live repository names while isolating collisions."""

from __future__ import annotations

import os
import re
from unittest.mock import patch

from alembic import command
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.scripts.database_migrate import _alembic_config


def _upgrade(database_url: str, revision: str) -> None:
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.upgrade(_alembic_config(), revision)


def test_revision_191_preserves_unique_legacy_names_and_isolates_collisions(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'task-repository-identity.sqlite3'}"
    _upgrade(database_url, "190_fireflies_org_index")
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            for organization_id in (1, 2):
                connection.execute(
                    text(
                        """
                        INSERT INTO organizations (
                            id, name, sso_enforced, saml_enabled,
                            billing_provider, credits_balance,
                            fireflies_single_account_mode, workable_connected
                        ) VALUES (
                            :id, :name, false, false, 'none', 0, false, false
                        )
                        """
                    ),
                    {"id": organization_id, "name": f"Org {organization_id}"},
                )
            connection.execute(
                text(
                    """
                    INSERT INTO tasks (id, organization_id, name, task_key)
                    VALUES
                        (11, 1, 'Unique', 'unique_task'),
                        (12, 1, 'First shared', 'shared_task'),
                        (13, 2, 'Case collision', 'SHARED_TASK'),
                        (14, 1, 'Exact collision', 'shared_task'),
                        (15, 1, 'Legacy lossy name', 'same/value'),
                        (16, 2, 'Legacy lossy collision', 'same value')
                    """
                )
            )

        _upgrade(database_url, "191_task_repo_identity")

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, task_key, template_repository_name "
                    "FROM tasks ORDER BY id"
                )
            ).mappings().all()
        names = {int(row["id"]): str(row["template_repository_name"]) for row in rows}
        assert names[11] == "unique_task"
        assert names[12] == "shared_task"
        assert names[13].endswith("-o2-t13")
        assert names[14].endswith("-o1-t14")
        # Pre-191 turned both slash and space into a hyphen. The first live
        # repository keeps that exact address; only the colliding task moves.
        assert names[15] == "same-value"
        assert names[16].endswith("-o2-t16")
        assert len({name.casefold() for name in names.values()}) == 6
        assert all(
            len(name) <= 100 and re.fullmatch(r"[A-Za-z0-9._-]+", name)
            for name in names.values()
        )

        columns = {column["name"]: column for column in inspect(engine).get_columns("tasks")}
        assert columns["template_repository_name"]["nullable"] is False
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("tasks")}
        assert indexes["ix_tasks_template_repository_name"]["unique"] == 1

        # The production rollout migrates before replacing all old web/worker
        # processes. An old writer that omits the new column must keep working,
        # and two such writes must still receive isolated lowercase identities.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tasks (id, organization_id, name, task_key) "
                    "VALUES (17, 1, 'Old writer one', 'old-shared'), "
                    "(18, 2, 'Old writer two', 'old-shared')"
                )
            )
            old_writer_names = connection.execute(
                text(
                    "SELECT template_repository_name FROM tasks "
                    "WHERE id IN (17, 18) ORDER BY id"
                )
            ).scalars().all()
        assert len(set(old_writer_names)) == 2
        assert all(
            re.fullmatch(r"task-auto-[0-9a-f]{32}", name)
            for name in old_writer_names
        )

        with engine.begin() as connection:
            connection.execute(
                text("UPDATE tasks SET task_key = 'renamed' WHERE id = 11")
            )
            persisted = connection.execute(
                text(
                    "SELECT template_repository_name FROM tasks WHERE id = 11"
                )
            ).scalar_one()
        assert persisted == "unique_task"

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tasks SET template_repository_name = "
                        "'unique_task' WHERE id = 13"
                    )
                )

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tasks SET template_repository_name = "
                        "'Case-Variant' WHERE id = 13"
                    )
                )
    finally:
        engine.dispose()
