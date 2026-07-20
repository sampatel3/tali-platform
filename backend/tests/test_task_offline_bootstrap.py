"""Offline sandbox-image contract for the canonical assessment catalogue."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from app.services.task_catalog import canonical_task_catalog_dir
from app.services.task_spec_loader import OFFLINE_TASK_RUNTIME_PACKAGES


_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _REPO_ROOT / "e2b.Dockerfile"
_EXPECTED_TOTALS = {
    "ai_eng_genai_production_readiness": 12,
    "ai_eng_rag_eval_harness": 8,
    "data_eng_aws_glue_pipeline_recovery": 11,
    "data_eng_bronze_ingestion": 11,
    "data_eng_data_quality_contract_framework": 9,
    "data_eng_pipeline_dag_recovery": 10,
    "platform_eng_aws_eks_misconfig_triage": 6,
    "platform_eng_azure_aks_misconfig_triage": 4,
    # pytest expands five memo headings into five collected cases.
    "product_mgmt_stakeholder_conflict": 9,
    # pytest expands seven handback headings into seven collected cases.
    "scrum_master_sprint_recovery_scenario": 13,
}
_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _canonical_specs() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(canonical_task_catalog_dir().glob("*.json"))
    ]


def _package_name(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement.strip())
    assert match, f"not a package requirement: {requirement!r}"
    return match.group(1).lower().replace("_", "-").replace(".", "-")


def _dockerfile_runtime_packages() -> set[str]:
    flattened = _DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    tokens = shlex.split(flattened)
    marker = ["python3", "-m", "pip", "install", "--no-cache-dir"]
    start = next(
        index + len(marker)
        for index in range(len(tokens) - len(marker) + 1)
        if tokens[index:index + len(marker)] == marker
    )
    requirement_tokens = tokens[start:tokens.index("&&", start)]
    return {_package_name(requirement) for requirement in requirement_tokens}


def test_image_bakes_every_canonical_task_dependency():
    required_packages = {
        _package_name(line)
        for spec in _canonical_specs()
        for line in spec["repo_structure"]["files"]["requirements.txt"].splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    image_packages = _dockerfile_runtime_packages()

    assert required_packages == {"pytest", "python-hcl2"}
    assert required_packages <= image_packages
    assert image_packages == OFFLINE_TASK_RUNTIME_PACKAGES


@pytest.mark.parametrize(
    "spec",
    _canonical_specs(),
    ids=lambda spec: spec["task_id"],
)
def test_catalog_uses_only_offline_readiness_bootstrap(spec):
    commands = spec["workspace_bootstrap"]["commands"]

    assert commands
    assert spec["workspace_bootstrap"]["timeout_seconds"] == 30
    assert all(command.startswith('python3 -I -c "import ') for command in commands)
    assert all("install" not in command and "://" not in command for command in commands)
    assert spec["test_runner"]["command"].startswith("python3 -I -m pytest ")
    assert ".venv" not in spec["test_runner"]["command"]


@pytest.mark.parametrize(
    "spec",
    _canonical_specs(),
    ids=lambda spec: spec["task_id"],
)
def test_catalog_declares_collected_verifier_suite_size(spec):
    assert spec["test_runner"]["expected_total"] == _EXPECTED_TOTALS[spec["task_id"]]
