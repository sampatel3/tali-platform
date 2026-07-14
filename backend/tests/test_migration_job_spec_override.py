"""Migration 161 must preserve pre-marker Taali job-spec edits."""

from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "161_add_role_job_spec_override.py"
)


def test_job_spec_override_migration_backfills_legacy_taali_provenance():
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'revision = "161_add_role_job_spec_override"' in source
    assert 'down_revision = "160_add_candidate_search_indexes"' in source
    assert 'op.add_column(\n        "roles"' in source
    assert "job_spec_manually_edited_at" in source
    assert "job_spec_text IS DISTINCT FROM description" in source
    assert "job_spec_uploaded_at" in source
    assert "job_spec_filename" in source
    assert "description = job_spec_text" in source
