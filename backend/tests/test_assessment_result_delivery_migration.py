"""Revision 184 is additive and refuses to erase delivery evidence."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "184_add_assessment_result_delivery_receipt.py"
    )
    spec = importlib.util.spec_from_file_location("assessment_result_delivery_184", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_preserves_existing_assessment_and_downgrade_fails_closed():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    assessments = sa.Table(
        "assessments",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String, nullable=False),
        sa.Column("posted_to_workable", sa.Boolean, nullable=False),
    )
    with engine.connect() as connection:
        metadata.create_all(connection)
        connection.execute(
            assessments.insert(),
            {"id": 77, "token": "preserve-me", "posted_to_workable": False},
        )
        connection.commit()
        migration = _migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()
        connection.commit()

        columns = {column["name"] for column in sa.inspect(connection).get_columns("assessments")}
        assert {
            "workable_result_delivery_status",
            "workable_result_delivery_receipt",
            "workable_result_delivery_next_attempt_at",
            "workable_result_delivery_claimed_at",
        } <= columns
        assert connection.execute(
            sa.text(
                "SELECT token, posted_to_workable, workable_result_delivery_status "
                "FROM assessments WHERE id = 77"
            )
        ).one() == ("preserve-me", False, None)
        assert "ix_assessments_workable_result_delivery_recovery" in {
            index["name"] for index in sa.inspect(connection).get_indexes("assessments")
        }

        with pytest.raises(RuntimeError, match="must not be deleted"):
            migration.downgrade()

        assert connection.execute(
            sa.text("SELECT token FROM assessments WHERE id = 77")
        ).scalar_one() == "preserve-me"
        assert {
            "workable_result_delivery_status",
            "workable_result_delivery_receipt",
            "workable_result_delivery_next_attempt_at",
            "workable_result_delivery_claimed_at",
        } <= {
            column["name"]
            for column in sa.inspect(connection).get_columns("assessments")
        }
