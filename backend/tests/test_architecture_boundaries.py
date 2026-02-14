from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_V1_DIR = PROJECT_ROOT / "app" / "api" / "v1"
DOMAINS_DIR = PROJECT_ROOT / "app" / "domains"


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_api_v1_is_transport_only_no_component_api_imports() -> None:
    disallowed = re.compile(
        r"(?:from|import)\s+.*components\.[a-zA-Z0-9_\.]+\.api\b"
    )
    violations: list[str] = []
    for path in _python_files(API_V1_DIR):
        content = path.read_text(encoding="utf-8")
        if disallowed.search(content):
            violations.append(str(path))
    assert not violations, f"api/v1 must not import component api modules: {violations}"


def test_domains_do_not_import_other_domain_repositories_directly() -> None:
    violations: list[str] = []
    pattern_qualified = re.compile(r"(?:from|import)\s+app\.domains\.([a-zA-Z0-9_]+)\.repository\b")
    pattern_relative_three = re.compile(r"(?:from|import)\s+\.\.\.domains\.([a-zA-Z0-9_]+)\.repository\b")
    pattern_relative_two = re.compile(r"(?:from|import)\s+\.\.([a-zA-Z0-9_]+)\.repository\b")

    for path in _python_files(DOMAINS_DIR):
        rel_parts = path.relative_to(DOMAINS_DIR).parts
        if not rel_parts:
            continue
        current_domain = rel_parts[0]
        content = path.read_text(encoding="utf-8")

        matches = (
            [m.group(1) for m in pattern_qualified.finditer(content)]
            + [m.group(1) for m in pattern_relative_three.finditer(content)]
            + [m.group(1) for m in pattern_relative_two.finditer(content)]
        )
        for imported_domain in matches:
            if imported_domain != current_domain:
                violations.append(f"{path} imports {imported_domain}.repository")

    assert not violations, (
        "Domain modules must not directly import repositories from other domains. "
        f"Violations: {violations}"
    )


def test_legacy_component_api_modules_removed() -> None:
    removed_paths = [
        PROJECT_ROOT / "app/components/auth/api.py",
        PROJECT_ROOT / "app/components/candidates/api.py",
        PROJECT_ROOT / "app/components/tasks/api.py",
        PROJECT_ROOT / "app/components/team/api.py",
        PROJECT_ROOT / "app/components/organizations/api.py",
        PROJECT_ROOT / "app/components/integrations/workable/api.py",
        PROJECT_ROOT / "app/components/integrations/stripe/api.py",
    ]
    still_present = [str(path) for path in removed_paths if path.exists()]
    assert not still_present, f"Legacy component api modules must stay removed: {still_present}"


def test_service_shim_modules_removed() -> None:
    removed_paths = [
        PROJECT_ROOT / "app/services/access_control_service.py",
        PROJECT_ROOT / "app/services/claude_service.py",
        PROJECT_ROOT / "app/services/e2b_service.py",
        PROJECT_ROOT / "app/services/email_service.py",
        PROJECT_ROOT / "app/services/prompt_analytics.py",
        PROJECT_ROOT / "app/services/scoring_service.py",
        PROJECT_ROOT / "app/services/stripe_service.py",
        PROJECT_ROOT / "app/services/workable_service.py",
    ]
    still_present = [str(path) for path in removed_paths if path.exists()]
    assert not still_present, f"Service re-export shims must stay removed: {still_present}"


def test_model_schema_reexport_wrappers_removed() -> None:
    removed_paths = [
        PROJECT_ROOT / "app/components/assessments/models.py",
        PROJECT_ROOT / "app/components/assessments/schemas.py",
        PROJECT_ROOT / "app/components/auth/models.py",
        PROJECT_ROOT / "app/components/auth/schemas.py",
        PROJECT_ROOT / "app/components/candidates/models.py",
        PROJECT_ROOT / "app/components/candidates/schemas.py",
        PROJECT_ROOT / "app/components/organizations/models.py",
        PROJECT_ROOT / "app/components/organizations/schemas.py",
        PROJECT_ROOT / "app/components/tasks/models.py",
        PROJECT_ROOT / "app/components/tasks/schemas.py",
    ]
    still_present = [str(path) for path in removed_paths if path.exists()]
    assert not still_present, f"Model/schema re-export wrappers must stay removed: {still_present}"
