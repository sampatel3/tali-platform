from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def test_no_endpoint_decorators_in_legacy_paths() -> None:
    legacy_roots = [
        PROJECT_ROOT / "app" / "api" / "v1",
        PROJECT_ROOT / "app" / "components",
    ]
    # Files awaiting migration to canonical domain modules. New entries
    # are not welcome — fix the migration instead of expanding this list.
    allowlist: dict[str, str] = {
        "app/api/v1/background_jobs.py": "background-job status endpoints, pending domain split",
    }
    violations: list[str] = []
    pattern = re.compile(r"@router\.(?:get|post|put|patch|delete)\(")

    for root in legacy_roots:
        for path in _python_files(root):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if rel in allowlist:
                continue
            content = path.read_text(encoding="utf-8")
            if pattern.search(content):
                violations.append(str(path))

    assert not violations, (
        "Endpoint decorators must only live in canonical domain route files. "
        f"Violations: {violations}"
    )


def test_no_duplicate_endpoint_signatures_across_domains() -> None:
    domain_root = PROJECT_ROOT / "app" / "domains"
    prefix_re = re.compile(r"APIRouter\([^)]*prefix\s*=\s*['\"]([^'\"]+)['\"]")
    route_re = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]")

    signatures: dict[str, list[str]] = {}
    for path in _python_files(domain_root):
        content = path.read_text(encoding="utf-8")
        prefix_match = prefix_re.search(content)
        prefix = prefix_match.group(1) if prefix_match else ""

        for method, route_path in route_re.findall(content):
            if route_path.startswith("/"):
                combined = f"{prefix}{route_path}"
            else:
                combined = f"{prefix}/{route_path}"
            normalized = re.sub(r"/{2,}", "/", combined) or "/"
            signature = f"{method.upper()} {normalized}"
            signatures.setdefault(signature, []).append(str(path))

    duplicates = {sig: files for sig, files in signatures.items() if len(set(files)) > 1}
    assert not duplicates, f"Duplicate endpoint signatures detected across domain routers: {duplicates}"


def test_file_size_guard_for_api_and_service_paths() -> None:
    size_limit = 500
    allowlist: dict[str, str] = {
        "app/components/assessments/service.py": "assessment orchestration",
        "app/components/integrations/workable/sync_service.py": "Workable sync flow",
        "app/components/integrations/workable/service.py": "legacy Workable integration service",
        "app/domains/agentic/routes.py": "agent decisions queue + status + run-now (cohesive surface, 7 LOC over)",
        "app/domains/assessments_runtime/analytics_routes.py": "Mission Control reporting summary aggregator",
        "app/domains/assessments_runtime/applications_routes.py": "applications API",
        "app/domains/assessments_runtime/candidate_runtime_routes.py": "candidate runtime API",
        "app/domains/assessments_runtime/candidate_terminal_routes.py": "candidate terminal API",
        "app/domains/assessments_runtime/pipeline_service.py": "assessment runtime pipeline orchestration",
        "app/domains/assessments_runtime/roles_management_routes.py": "roles + job-spec upload API",
        "app/domains/billing_webhooks/billing_routes.py": "Stripe + credit-pack billing routes (TODO: split webhook handlers)",
        "app/domains/workable_sync/routes.py": "legacy Workable sync API",
        "app/services/fit_matching_service.py": "CV-to-role fit scoring pipeline",
        "app/services/interview_support_service.py": "interview pack builder (1 LOC over after chip-helper extraction)",
    }

    target_files = set(_python_files(PROJECT_ROOT / "app" / "api" / "v1"))
    target_files.update((PROJECT_ROOT / "app").rglob("*service.py"))
    target_files.update((PROJECT_ROOT / "app" / "domains").rglob("*routes.py"))

    violations: list[str] = []
    for path in sorted(target_files):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        if lines <= size_limit:
            continue
        if rel in allowlist:
            continue
        violations.append(f"{rel} ({lines} LOC)")

    assert not violations, (
        f"API/service paths must stay <= {size_limit} LOC unless explicitly allowlisted. "
        f"Violations: {violations}"
    )


def test_alembic_resolves_to_a_single_head() -> None:
    """The migration graph must always reduce to one head.

    Two PRs landing on main with overlapping migration ancestry can leave
    alembic with multiple heads. ``alembic upgrade head`` then refuses to
    pick between them, the Railway start script fails fast on the
    migration step, and uvicorn never boots — production restart-loops.

    This test catches that pre-merge: it loads the migration graph the
    same way alembic itself does and asserts ``len(heads) == 1``. When
    a CI run fails here, the fix is a small merge-marker migration
    (e.g. ``revision = "065_merge_*"`` with a tuple ``down_revision``
    pointing at every current head).
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # The alembic.ini's ``script_location`` is a path relative to the
    # config file's directory; resolving it manually keeps this test
    # independent of the working dir pytest is launched from.
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = list(script.get_heads())

    assert len(heads) == 1, (
        "Alembic must resolve to exactly one head; found "
        f"{len(heads)}: {heads}. Add a merge migration with these "
        "as its `down_revision` tuple."
    )


def test_no_imports_of_removed_service_shims() -> None:
    removed_shim_names = [
        "access_control_service",
        "claude_service",
        "e2b_service",
        "email_service",
        "prompt_analytics",
        "scoring_service",
        "stripe_service",
        "workable_service",
    ]
    shim_group = "|".join(removed_shim_names)
    patterns = [
        re.compile(rf"(?:from|import)\s+app\.services\.({shim_group})\b"),
        re.compile(rf"(?:from|import)\s+\.\.\.?services\.({shim_group})\b"),
    ]

    scan_roots = [PROJECT_ROOT / "app", PROJECT_ROOT / "tests"]
    violations: list[str] = []
    for root in scan_roots:
        for path in _python_files(root):
            content = path.read_text(encoding="utf-8")
            if any(pattern.search(content) for pattern in patterns):
                violations.append(str(path))

    assert not violations, f"Removed service shims must not be imported: {violations}"
