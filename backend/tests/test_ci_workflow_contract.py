"""Supply-chain and cost regressions for repository CI workflows."""

from __future__ import annotations

from pathlib import Path
import re

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
PRODUCTION_SMOKE_WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "production-smoke.yml"
)


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_workflow_parses_and_pins_external_execution_inputs() -> None:
    source = _source()
    assert isinstance(yaml.safe_load(source), dict)
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", source, flags=re.MULTILINE)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", value) for value in uses), uses
    assert "ubuntu-latest" not in source
    assert "postgres:16.14@sha256:" in source
    assert "node-version: '22.23.1'" in source


def test_ci_uses_fresh_hashed_python_lock_in_both_backend_jobs() -> None:
    source = _source()
    install = "python -m pip install --require-hashes -r requirements-lock.txt"
    assert source.count(install) == 2
    assert source.count("python scripts/check_requirements_lock.py") == 2
    assert "pip install --upgrade pip" not in source


def test_ci_deduplicates_branch_events_and_skips_unaffected_expensive_jobs() -> None:
    source = _source()
    assert "github.head_ref || github.ref_name" in source
    assert "github.event.pull_request.head.repo.full_name || github.repository" in source
    assert "docs/*|*.md" in source
    assert source.count("needs: changes") == 3
    assert "needs.changes.outputs.backend == 'true'" in source
    assert "needs.changes.outputs.frontend == 'true'" in source


def test_frontend_ci_fails_on_test_warnings() -> None:
    source = _source()
    assert "run: npm run test:ci -- --maxWorkers=4" in source
    assert "run: npm test -- --maxWorkers=4" not in source


def test_production_smoke_uses_pinned_hashed_supported_toolchain() -> None:
    source = PRODUCTION_SMOKE_WORKFLOW.read_text(encoding="utf-8")
    assert isinstance(yaml.safe_load(source), dict)
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", source, flags=re.MULTILINE)
    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", value) for value in uses), uses
    assert "runs-on: ubuntu-24.04" in source
    assert "ubuntu-latest" not in source
    assert "python-version: '3.11.9'" in source
    assert source.count("python scripts/check_requirements_lock.py") == 1
    assert source.count(
        "python -m pip install --require-hashes -r requirements-lock.txt"
    ) == 1
    assert source.count("python -m pip check") == 1
    assert source.count("pip-audit --local") == 1
    assert "pip install --upgrade pip" not in source
    assert "pip install -r requirements-dev.txt" not in source
    assert "- cron: '0 */12 * * *'" in source
    assert 'run: test -n "$TALI_PROD_URL"' in source
    assert source.count(
        "TALI_PROD_URL: ${{ inputs.tali_prod_url || secrets.TALI_PROD_URL }}"
    ) == 2
    assert (
        "pytest -q -m production tests/test_qa_production_smoke.py" in source
    )
