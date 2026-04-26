"""Regression checks for the Workable-era migration chain."""

from pathlib import Path


VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
ROLE_THRESHOLD_MIGRATION = VERSIONS_DIR / "035_add_role_scoring_criteria_threshold.py"
WORKABLE_HI_MIGRATION = VERSIONS_DIR / "035_workable_first_hiring_intelligence.py"


def test_role_threshold_migration_exists():
    assert ROLE_THRESHOLD_MIGRATION.exists()


def test_workable_hiring_intelligence_migration_exists():
    assert WORKABLE_HI_MIGRATION.exists()


def test_workable_migration_chain_contract():
    threshold_source = ROLE_THRESHOLD_MIGRATION.read_text(encoding="utf-8")
    workable_source = WORKABLE_HI_MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "035_add_role_scoring_criteria_threshold"' in threshold_source
    assert 'down_revision = "034_add_application_score_cache_columns"' in threshold_source
    assert 'sa.Column("scoring_criteria", sa.JSON(), nullable=True)' in threshold_source
    assert '"reject_threshold"' in threshold_source

    assert 'revision = "035_workable_first_hiring_intelligence"' in workable_source
    assert 'down_revision = "035_add_role_scoring_criteria_threshold"' in workable_source
    assert "server_default=sa.true()" in workable_source
