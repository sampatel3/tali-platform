#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.task_catalog import canonical_task_catalog_dir  # noqa: E402
from app.services.task_spec_loader import load_task_specs  # noqa: E402


IMPORT_FAILURE_PATTERNS = (
    "ERROR collecting",
    "ImportError while importing",
    "ModuleNotFoundError",
    "No module named",
)


def _trim(text: str, limit: int = 1200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _localize_workspace_path(path: str, workspace_root: Path) -> Path:
    normalized = str(path or "").strip()
    if normalized.startswith("/workspace/"):
        suffix = normalized.replace("/workspace/", "", 1).lstrip("/")
        return workspace_root / suffix
    return workspace_root / normalized.lstrip("/")


def _materialize_repo(spec: Dict[str, Any], workspace_root: Path) -> Path:
    repo_structure = spec.get("repo_structure") or {}
    repo_dir = workspace_root / str(repo_structure.get("name") or spec["task_id"])
    repo_dir.mkdir(parents=True, exist_ok=True)
    files = (repo_structure.get("files") or {})
    for rel_path, content in files.items():
        target = repo_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
    return repo_dir


def _run_shell(command: str, cwd: Path, timeout: int) -> Dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    result = subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    return {
        "command": command,
        "cwd": str(cwd),
        "exit_code": result.returncode,
        "stdout_tail": _trim(result.stdout),
        "stderr_tail": _trim(result.stderr),
    }


def _parse_test_runner_results(output: str, parse_pattern: str | None) -> Dict[str, int]:
    if not parse_pattern:
        return {"passed": 0, "failed": 0, "total": 0}

    passed = 0
    failed = 0
    total = 0
    match = re.search(parse_pattern, output or "", re.IGNORECASE | re.MULTILINE)
    if match:
        groups = match.groupdict() if hasattr(match, "groupdict") else {}
        if groups:
            try:
                passed = int(groups.get("passed") or 0)
            except (TypeError, ValueError):
                passed = 0
            try:
                failed = int(groups.get("failed") or 0)
            except (TypeError, ValueError):
                failed = 0
            try:
                total = int(groups.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
        elif match.groups():
            passed = int(match.group(1))
    if failed == 0:
        failed_match = re.search(r"(?i)(\d+)\s+failed", output or "")
        if failed_match:
            failed = int(failed_match.group(1))
    if total == 0:
        total = passed + failed
        if total == 0 and passed > 0:
            total = passed
    return {"passed": max(0, passed), "failed": max(0, failed), "total": max(0, total)}


def _validate_spec_runtime(spec: Dict[str, Any]) -> Dict[str, Any]:
    bootstrap = spec["workspace_bootstrap"]
    runner = spec["test_runner"]

    with tempfile.TemporaryDirectory(prefix=f"task-validate-{spec['task_id']}-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        workspace_root = tmp_path / "workspace"
        repo_dir = _materialize_repo(spec, workspace_root)
        bootstrap_cwd = _localize_workspace_path(str(bootstrap["working_dir"]), workspace_root)
        runner_cwd = _localize_workspace_path(str(runner["working_dir"]), workspace_root)

        bootstrap_steps = []
        bootstrap_success = True
        for command in bootstrap["commands"]:
            step = _run_shell(str(command), bootstrap_cwd, int(bootstrap["timeout_seconds"]))
            bootstrap_steps.append(step)
            if step["exit_code"] != 0:
                bootstrap_success = False
                break

        test_result: Dict[str, Any] = {
            "command": str(runner["command"]),
            "cwd": str(runner_cwd),
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "test_collection_success": False,
            "missing_dependency_detected": False,
            "baseline_failures_meaningful": False,
        }
        if bootstrap_success:
            test_result = _run_shell(str(runner["command"]), runner_cwd, int(runner["timeout_seconds"]))
            combined = "\n".join([test_result.get("stdout_tail", ""), test_result.get("stderr_tail", "")]).strip()
            parsed = _parse_test_runner_results(combined, str(runner.get("parse_pattern") or ""))
            test_result.update(parsed)
            missing_dependency = any(pattern in combined for pattern in IMPORT_FAILURE_PATTERNS)
            test_collection_success = (
                test_result["exit_code"] in (0, 1)
                and not missing_dependency
                and parsed["total"] > 0
            )
            test_result["test_collection_success"] = test_collection_success
            test_result["missing_dependency_detected"] = missing_dependency
            test_result["baseline_failures_meaningful"] = test_collection_success

        return {
            "task_id": spec["task_id"],
            "repo_dir": str(repo_dir),
            "bootstrap_success": bootstrap_success,
            "bootstrap_steps": bootstrap_steps,
            "test_runner": test_result,
            "validator_ok": bootstrap_success and bool(test_result.get("test_collection_success")),
        }


def main() -> int:
    tasks_dir = canonical_task_catalog_dir()
    specs = load_task_specs(tasks_dir)
    report = {
        "tasks_dir": str(tasks_dir),
        "catalog_count": len(specs),
        "catalog_count_ok": len(specs) == 2,
        "tasks": [],
    }
    for spec in specs:
        report["tasks"].append(_validate_spec_runtime(spec))

    report["validator_ok"] = bool(report["catalog_count_ok"]) and all(
        bool(task_report.get("validator_ok")) for task_report in report["tasks"]
    )
    print(json.dumps(report, indent=2))
    return 0 if report["validator_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
