"""Regression checks for the destructive role-first reset migration contract."""

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
    assert 'op.create_table(\n        "roles"' in source
    assert 'op.create_table(\n        "candidate_applications"' in source
    assert 'op.create_table(\n        "role_tasks"' in source
    assert 'op.add_column("assessments", sa.Column("role_id"' in source
    assert 'op.add_column("assessments", sa.Column("application_id"' in source

    # Destructive reset contract
    assert "IRREVERSIBLE RESET" in source
    assert "2026-02-13-role-first-hardening" in source
    assert 'op.execute(sa.text("DELETE FROM assessments"))' in source
    assert 'op.execute(sa.text("DELETE FROM candidate_applications"))' in source
    assert 'op.execute(sa.text("DELETE FROM candidates"))' in source
