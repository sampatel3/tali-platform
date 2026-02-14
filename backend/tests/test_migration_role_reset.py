"""Regression checks for the role-first migration contract."""

from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "015_role_first_applications_pause_reset.py"
)


def test_role_first_reset_migration_file_exists():
    assert MIGRATION_PATH.exists()


def test_role_first_reset_migration_contract():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    # Revision linkage
    assert 'revision = "015_role_first_applications_pause_reset"' in source
    assert 'down_revision = "014_add_completed_due_to_timeout"' in source

    # New role-first schema
    assert '"roles"' in source
    assert '"candidate_applications"' in source
    assert '"role_tasks"' in source
    assert 'op.add_column("assessments", sa.Column("role_id"' in source
    assert 'op.add_column("assessments", sa.Column("application_id"' in source

    # Backfill contract (non-destructive)
    assert "_backfill_legacy_role_data" in source
    assert "_normalize_role_name" in source
    assert "DELETE FROM assessments" not in source
    assert "DELETE FROM candidate_applications" not in source
    assert "DELETE FROM candidates" not in source
