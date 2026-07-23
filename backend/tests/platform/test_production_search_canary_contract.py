"""Static release-workflow contract for the production search canary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "production-search-canary.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SEARCH_BOOTSTRAP = ROOT / "backend" / "scripts" / "bootstrap_candidate_search_postgres.py"


def test_production_search_canary_is_exact_sha_push_gate():
    source = WORKFLOW.read_text()

    assert "push:" in source
    assert "branches: [main]" in source
    assert "workflow_dispatch" not in source
    assert "inputs.expected_sha" not in source
    assert "ref: ${{ github.sha }}" in source
    assert "TALI_EXPECTED_RELEASE_SHA: ${{ github.sha }}" in source
    assert "github.sha" in source
    assert "production-search-canary" in source
    assert "cancel-in-progress: true" in source
    assert "timeout-minutes: 20" in source
    assert "--expected-sha \"$TALI_EXPECTED_RELEASE_SHA\"" in source
    assert "--wait-seconds 900" in source
    assert "continue-on-error" not in source


def test_production_search_canary_uses_only_dedicated_read_credentials():
    source = WORKFLOW.read_text()

    for secret in (
        "TALI_PROD_URL",
        "TALI_SEARCH_CANARY_TOKEN",
        "TALI_SEARCH_CANARY_ROLE_ID",
    ):
        assert f"secrets.{secret}" in source
    assert "TALI_SEARCH_CANARY_PASSWORD" not in source
    assert "TALI_SEARCH_CANARY_EXPECTED_EMAIL" not in source
    assert "TALI_SEARCH_CANARY_EXCLUDED_EMAILS" not in source
    assert "prod_candidate_search_canary.py" in source


def test_real_canary_route_is_required_in_postgres_ci():
    source = CI_WORKFLOW.read_text()

    assert "postgres:16" in source
    assert "TALI_SEARCH_TEST_DATABASE_URL" in source
    assert "scripts/bootstrap_candidate_search_postgres.py" in source
    assert "tests/postgres/test_production_search_canary.py" in source


def test_candidate_search_bootstrap_rebuilds_disposable_schema_before_stamping():
    """A reused test DB cannot be stamped over an older physical schema."""

    source = SEARCH_BOOTSTRAP.read_text()

    guard = source.index("url.database != EXPECTED_DATABASE")
    reset = source.index('DROP SCHEMA IF EXISTS public CASCADE')
    create = source.index("Base.metadata.create_all")
    stamp = source.index("command.stamp(config, SEARCH_PARENT_REVISION)")

    assert guard < reset < create < stamp
