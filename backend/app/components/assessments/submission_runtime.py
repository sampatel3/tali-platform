"""Assessment submission orchestration extracted from the service facade."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Type

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...components.scoring.analytics import compute_all_heuristics
from ...components.scoring.service import calculate_mvp_score, generate_heuristic_summary
from ...components.scoring.tiers import compute_tier_reached, cv_claim_consistency
from ...models.assessment import Assessment, AssessmentStatus
from ...models.candidate import Candidate
from ...models.candidate_application import CandidateApplication
from ...models.role import Role
from ...models.task import Task
from ...models.user import User
from ...platform.request_context import get_request_id
from ...services.fit_matching_service import (
    CvMatchValidationError,
    calculate_cv_job_match_sync,
    calculate_cv_job_match_v4_sync,
)
from ...services.spec_normalizer import normalize_spec
from ...services.task_catalog import workspace_repo_root as canonical_workspace_repo_root
from ...services.task_repo_service import normalize_repo_files
from ...services.task_spec_loader import discover_verifier_files
from ...domains.assessments_runtime.pipeline_service import (
    ensure_pipeline_fields,
    initialize_pipeline_event_if_missing,
    transition_stage,
)
from ...domains.assessments_runtime.role_support import refresh_application_score_cache
from ...services.taali_scoring import (
    ROLE_FIT_WEIGHTS,
    TAALI_SCORING_RUBRIC_VERSION,
    TAALI_WEIGHTS,
    compute_role_fit_score,
    compute_taali_score,
)
from .repository import (
    append_assessment_timeline_event,
    build_timeline,
    claim_runtime_operation,
    ensure_utc,
    release_runtime_operation,
    utcnow,
)
from .task_snapshot import task_view_for_assessment

logger = logging.getLogger("taali.assessments")

_SUBMISSION_ARTIFACT_VERSION = 1
_MAX_SUBMISSION_FILES = 200
_MAX_SUBMISSION_FILE_BYTES = 512_000
_MAX_SUBMISSION_TOTAL_BYTES = 5_000_000
_MAX_VERIFIER_TEST_COUNT = 10_000
_GRADING_USER = "taali-grader"
_VERIFIER_SENTINEL = "__TAALI_TRUSTED_VERIFIER_RESULT__="
_ARTIFACT_DENIED_PARTS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".env",
        ".ssh",
        ".aws",
    }
)


def _terminal_usage_totals(assessment: Assessment) -> tuple[int, int]:
    """Aggregate provider usage emitted by the Claude CLI terminal transcript."""
    input_tokens = 0
    output_tokens = 0
    for entry in list(getattr(assessment, "cli_transcript", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event_type") or "") != "terminal_usage":
            continue
        input_tokens += max(0, int(entry.get("input_tokens") or 0))
        output_tokens += max(0, int(entry.get("output_tokens") or 0))
    return input_tokens, output_tokens


def _task_extra_data(task: Task) -> Dict[str, Any]:
    extra = getattr(task, "extra_data", None)
    return extra if isinstance(extra, dict) else {}


def _extract_process_output(result: Any) -> tuple[str, str, int | None]:
    if isinstance(result, dict):
        stdout = str(result.get("stdout") or result.get("out") or "")
        stderr = str(result.get("stderr") or result.get("err") or "")
        exit_code = result.get("exit_code")
        try:
            exit_code = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            exit_code = None
        return stdout, stderr, exit_code

    stdout = str(getattr(result, "stdout", "") or getattr(result, "out", "") or "")
    stderr = str(getattr(result, "stderr", "") or getattr(result, "err", "") or "")
    exit_code = getattr(result, "exit_code", None)
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return stdout, stderr, exit_code


def _execution_stdout_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("stdout") or "")

    logs = getattr(result, "logs", None)
    raw_stdout = getattr(logs, "stdout", None) if logs is not None else None
    if isinstance(raw_stdout, list):
        return "\n".join(str(item) for item in raw_stdout)
    if raw_stdout is not None:
        return str(raw_stdout)
    return str(getattr(result, "stdout", "") or "")


def _safe_artifact_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    try:
        parsed = PurePosixPath(raw)
    except Exception:
        return ""
    if parsed.is_absolute():
        return ""
    parts = [str(part).strip() for part in parsed.parts if str(part).strip()]
    if not parts or any(
        part in {".", ".."}
        or part.casefold().rstrip(" .") in _ARTIFACT_DENIED_PARTS
        for part in parts
    ):
        return ""
    return "/".join(parts)


def _artifact_digest(files: Dict[str, str]) -> str:
    canonical = json.dumps(
        {path: files[path] for path in sorted(files)},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_submission_artifact(files: Dict[str, str]) -> Dict[str, Any]:
    normalized: Dict[str, str] = {}
    total_bytes = 0
    for raw_path, raw_content in sorted(files.items()):
        path = _safe_artifact_path(raw_path)
        if not path or path in normalized:
            raise RuntimeError(f"Unsafe or duplicate submission artifact path: {raw_path!r}")
        content = str(raw_content)
        size = len(content.encode("utf-8"))
        if size > _MAX_SUBMISSION_FILE_BYTES:
            raise RuntimeError(f"Submission artifact file exceeds limit: {path}")
        total_bytes += size
        if total_bytes > _MAX_SUBMISSION_TOTAL_BYTES:
            raise RuntimeError("Submission artifact exceeds total size limit")
        normalized[path] = content
    if len(normalized) > _MAX_SUBMISSION_FILES:
        raise RuntimeError("Submission artifact exceeds file count limit")
    digest = _artifact_digest(normalized)
    return {
        "version": _SUBMISSION_ARTIFACT_VERSION,
        "sha256": digest,
        "file_count": len(normalized),
        "total_bytes": total_bytes,
        "file_hashes": {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in normalized.items()
        },
        "files": normalized,
    }


def _validated_submission_artifact(
    artifact: Any,
    *,
    expected_sha256: str | None = None,
) -> Dict[str, Any]:
    if not isinstance(artifact, dict) or artifact.get("version") != _SUBMISSION_ARTIFACT_VERSION:
        raise RuntimeError("Submission artifact is missing or has an unsupported version")
    raw_files = artifact.get("files")
    if not isinstance(raw_files, dict):
        raise RuntimeError("Submission artifact has no file manifest")
    rebuilt = _build_submission_artifact(
        {str(path): str(content) for path, content in raw_files.items()}
    )
    recorded = str(artifact.get("sha256") or "").strip()
    expected = str(expected_sha256 or recorded).strip()
    if not expected or rebuilt["sha256"] != expected or (recorded and recorded != expected):
        raise RuntimeError("Submission artifact digest verification failed")
    return rebuilt


def _capture_submission_artifact(
    sandbox: Any,
    repo_root: str,
    *,
    max_files: int = _MAX_SUBMISSION_FILES,
    max_file_bytes: int = _MAX_SUBMISSION_FILE_BYTES,
    max_total_bytes: int = _MAX_SUBMISSION_TOTAL_BYTES,
) -> Dict[str, Any]:
    """Freeze regular text files from the live candidate workspace.

    Control directories, symlinks, special files, binaries and paths escaping
    ``repo_root`` are excluded. Limit violations fail closed instead of silently
    grading a truncated repository.
    """
    snippet = (
        "import os, json, stat\n"
        f"configured_root = os.path.abspath({repo_root!r})\n"
        "root = os.path.realpath(configured_root)\n"
        f"skip_dirs = {set(_ARTIFACT_DENIED_PARTS)!r}\n"
        "out = {}\n"
        "total = 0\n"
        "error = None\n"
        "try:\n"
        "    root_info = os.lstat(configured_root)\n"
        "    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode) or root != configured_root:\n"
        "        error = 'unsafe_repo_root'\n"
        "except OSError:\n"
        "    error = 'unsafe_repo_root'\n"
        "if error:\n"
        "    print(json.dumps({'files': out, 'error': error}, ensure_ascii=False))\n"
        "    raise SystemExit(0)\n"
        "for dirpath, dirnames, filenames in os.walk(root):\n"
        "    dirnames[:] = sorted(d for d in dirnames if d.casefold().rstrip(' .') not in skip_dirs)\n"
        "    for fn in sorted(filenames):\n"
        f"        if len(out) >= {max_files}:\n"
        "            error = 'file_count_limit_exceeded'\n"
        "            break\n"
        "        full = os.path.join(dirpath, fn)\n"
        "        rel = os.path.relpath(full, root)\n"
        "        try:\n"
        "            info = os.lstat(full)\n"
        "            if not stat.S_ISREG(info.st_mode) or os.path.islink(full) or info.st_nlink != 1:\n"
        "                continue\n"
        "            resolved = os.path.realpath(full)\n"
        "            if os.path.commonpath([root, resolved]) != root:\n"
        "                continue\n"
        f"            if info.st_size > {max_file_bytes}:\n"
        "                error = 'file_size_limit_exceeded:' + rel\n"
        "                break\n"
        "            with open(full, 'rb') as fh:\n"
        "                raw = fh.read()\n"
        "            if b'\\x00' in raw:\n"
        "                continue\n"
        "            text = raw.decode('utf-8')\n"
        "            total += len(raw)\n"
        f"            if total > {max_total_bytes}:\n"
        "                error = 'total_size_limit_exceeded'\n"
        "                break\n"
        "            out[rel.replace(os.sep, '/')] = text\n"
        "        except UnicodeDecodeError:\n"
        "            continue\n"
        "        except Exception as exc:\n"
        "            error = 'capture_failed:' + rel + ':' + type(exc).__name__\n"
        "            break\n"
        "    if error:\n"
        "        break\n"
        "print(json.dumps({'files': out, 'error': error}, ensure_ascii=False))\n"
    )
    try:
        result = sandbox.run_code(snippet)
        text = _execution_stdout_text(result).strip().splitlines()
        if not text:
            raise RuntimeError("Submission artifact capture returned no output")
        payload = json.loads(text[-1])
        if not isinstance(payload, dict):
            raise RuntimeError("Submission artifact capture returned an invalid payload")
        if payload.get("error"):
            raise RuntimeError(f"Submission artifact capture failed: {payload['error']}")
        raw_files = payload.get("files")
        if not isinstance(raw_files, dict):
            raise RuntimeError("Submission artifact capture returned no files")
        return _build_submission_artifact(
            {str(path): str(content) for path, content in raw_files.items()}
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Submission artifact capture failed") from exc


def _materialize_submission_artifact(
    sandbox: Any,
    repo_root: str,
    artifact: Dict[str, Any],
) -> None:
    verified = _validated_submission_artifact(artifact)
    files_api = getattr(sandbox, "files", None)
    if files_api is None or not hasattr(files_api, "write"):
        raise RuntimeError("Grading sandbox file API is unavailable")
    sandbox.run_code(
        "import pathlib\n"
        f"root=pathlib.Path({repo_root!r})\n"
        "root.mkdir(parents=True, exist_ok=True)\n"
    )
    for path, content in verified["files"].items():
        parent = str(PurePosixPath(repo_root) / PurePosixPath(path).parent)
        sandbox.run_code(
            "import pathlib\n"
            f"pathlib.Path({parent!r}).mkdir(parents=True, exist_ok=True)\n"
        )
        files_api.write(f"{repo_root.rstrip('/')}/{path}", content)


def _capture_sandbox_repo_files(
    sandbox: Any,
    repo_root: str,
    *,
    max_files: int = 40,
    max_file_chars: int = 12000,
) -> Dict[str, str]:
    """Backward-compatible bounded view used by rubric prompt construction."""
    artifact = _capture_submission_artifact(
        sandbox,
        repo_root,
        max_files=max_files,
        max_file_bytes=max_file_chars * 4,
        max_total_bytes=max_files * max_file_chars * 4,
    )
    return {
        path: content[:max_file_chars]
        for path, content in artifact["files"].items()
    }


def _durable_candidate_branch_snapshot(
    assessment: Assessment,
) -> Dict[str, str] | None:
    """Return the exact pushed candidate branch/head recorded at submission.

    A retry may only rebuild a killed sandbox from this marker.  Merely having
    an assessment branch is not enough: that branch starts life as the task
    template, so cloning it without proof of a successful submission push can
    silently grade starter code as the candidate's work.
    """
    evidence = (
        assessment.git_evidence
        if isinstance(getattr(assessment, "git_evidence", None), dict)
        else {}
    )
    branch = str(getattr(assessment, "assessment_branch", None) or "").strip()
    repo_url = str(getattr(assessment, "assessment_repo_url", None) or "").strip()
    recorded_branch = str(evidence.get("candidate_branch") or "").strip()
    head_sha = str(evidence.get("candidate_branch_head_sha") or "").strip()
    try:
        push_succeeded = (
            evidence.get("candidate_branch_push_status") == "succeeded"
            and int(evidence.get("push_returncode")) == 0
        )
    except (TypeError, ValueError):
        push_succeeded = False
    if not (
        push_succeeded
        and branch
        and repo_url
        and recorded_branch == branch
        and head_sha
    ):
        return None
    return {"branch": branch, "head_sha": head_sha, "repo_url": repo_url}


def _durable_submission_artifact(assessment: Assessment) -> Dict[str, Any] | None:
    artifact = getattr(assessment, "submission_artifact", None)
    digest = str(getattr(assessment, "submission_artifact_sha256", None) or "").strip()
    if not artifact or not digest:
        return None
    try:
        return _validated_submission_artifact(artifact, expected_sha256=digest)
    except RuntimeError:
        logger.exception(
            "Stored submission artifact failed verification assessment_id=%s",
            getattr(assessment, "id", None),
        )
        return None


def _open_submission_sandbox(
    e2b: Any,
    assessment: Assessment,
    task: Task,
    *,
    retry_scoring: bool,
    recover_retry_sandbox_fn: Callable[[Any, Assessment, Task], Any] | None,
) -> Any:
    """Connect to the live candidate sandbox, or reconstruct a frozen retry.

    New submissions recover from the immutable content-addressed artifact.
    Historical candidate branches are deliberately not fetched: the closed
    workspace has no external Git credential or remote, and grading must never
    reconstruct an unverified or starter-code substitute.
    """
    if not retry_scoring:
        if assessment.e2b_session_id:
            return e2b.connect_sandbox(assessment.e2b_session_id)
        raise RuntimeError("Candidate workspace session is unavailable")

    frozen = _durable_submission_artifact(assessment)
    if frozen is not None:
        sandbox = e2b.create_sandbox()
        try:
            _materialize_submission_artifact(
                sandbox,
                canonical_workspace_repo_root(task),
                frozen,
            )
        except Exception:
            try:
                e2b.close_sandbox(sandbox)
            except Exception:
                pass
            raise
        return sandbox

    reconnect_error: Exception | None = None
    if assessment.e2b_session_id:
        try:
            return e2b.connect_sandbox(assessment.e2b_session_id)
        except Exception as exc:
            reconnect_error = exc
            logger.info(
                "Retry sandbox is unavailable; attempting pushed-branch recovery assessment_id=%s",
                assessment.id,
            )
    else:
        reconnect_error = RuntimeError("assessment has no sandbox session id")

    _ = recover_retry_sandbox_fn  # retained in the call signature for rollout compatibility
    raise RuntimeError(
        "Cannot recover assessment scoring: immutable submission artifact is unavailable"
    ) from reconnect_error


def _parse_test_runner_results(output: str, parse_pattern: str | None) -> Dict[str, Any]:
    if not parse_pattern:
        return {"passed": 0, "failed": 0, "total": 0, "parse_error": False}

    passed = 0
    failed = 0
    total = 0
    parse_error = False

    try:
        matches = list(
            re.finditer(parse_pattern, output or "", re.IGNORECASE | re.MULTILINE)
        )
        match = matches[-1] if matches else None
    except re.error as exc:
        # An invalid authored parse_pattern would otherwise silently yield
        # "0 passed / 0 failed" — flag it so the recruiter sees a runner error
        # instead of a misleading zero score.
        logger.warning("Invalid test_runner parse_pattern %r: %s", parse_pattern, exc)
        match = None
        parse_error = True
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
            try:
                passed = int(match.group(1))
            except (TypeError, ValueError):
                passed = 0

    if passed == 0:
        pass_matches = list(re.finditer(r"(?i)(\d+)\s+passed", output or ""))
        pass_match = pass_matches[-1] if pass_matches else None
        if pass_match:
            try:
                passed = int(pass_match.group(1))
            except (TypeError, ValueError):
                passed = 0
    if failed == 0:
        fail_matches = list(re.finditer(r"(?i)(\d+)\s+failed", output or ""))
        fail_match = fail_matches[-1] if fail_matches else None
        if fail_match:
            try:
                failed = int(fail_match.group(1))
            except (TypeError, ValueError):
                failed = 0
    if total == 0:
        total = passed + failed
        if total == 0 and passed > 0:
            total = passed
    if max(passed, failed, total) > _MAX_VERIFIER_TEST_COUNT:
        logger.warning(
            "Verifier output reported implausible test counts passed=%s failed=%s total=%s",
            passed,
            failed,
            total,
        )
        passed = failed = total = 0
        parse_error = True

    return {
        "passed": max(0, passed),
        "failed": max(0, failed),
        "total": max(0, total),
        "parse_error": parse_error,
    }


def _server_owned_verifier_files(task: Task) -> Dict[str, str]:
    """Return baseline files candidates must not redefine for final grading."""
    baseline = normalize_repo_files(getattr(task, "repo_structure", None))
    extra = _task_extra_data(task)
    runner = extra.get("test_runner")
    runner = runner if isinstance(runner, dict) else {}
    raw_explicit = runner.get("verifier_files")
    explicit_paths: set[str] = set()
    if raw_explicit is None:
        explicit_paths.update(discover_verifier_files(baseline))
    else:
        if not isinstance(raw_explicit, list) or not raw_explicit:
            raise RuntimeError("test_runner.verifier_files must be a non-empty list")
        for raw_path in raw_explicit:
            path = _safe_artifact_path(str(raw_path))
            if not path or path not in baseline:
                raise RuntimeError(
                    f"test_runner.verifier_files contains an invalid workspace path: {raw_path!r}"
                )
            explicit_paths.add(path)

    deliverable = extra.get("deliverable")
    deliverable = deliverable if isinstance(deliverable, dict) else {}
    primary_artifact = _safe_artifact_path(
        str(deliverable.get("primary_artifact") or "")
    )
    if primary_artifact and primary_artifact in explicit_paths:
        raise RuntimeError("The primary artifact cannot be a server-owned verifier file")

    protected_names = {
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "tox.ini",
        "setup.cfg",
    }
    protected: Dict[str, str] = {}
    for raw_path, content in baseline.items():
        path = _safe_artifact_path(str(raw_path))
        if not path:
            continue
        name = path.rsplit("/", 1)[-1]
        is_test = (
            path.startswith("tests/")
            or "/tests/" in f"/{path}"
            or name.startswith("test_")
            or name.endswith("_test.py")
        )
        # Compatibility inference for pre-contract tasks. New task specs must
        # enumerate every support module in test_runner.verifier_files.
        is_legacy_test_support = name.endswith(("_helpers.py", "_assertions.py"))
        if path in explicit_paths or is_test or name in protected_names or is_legacy_test_support:
            if path == primary_artifact:
                continue
            protected[path] = str(content or "")
    return protected


def _repo_files_for_rubric(
    task: Task,
    files: Dict[str, str],
) -> tuple[Dict[str, str], str]:
    """Exclude candidate-controlled grader/config surfaces from LLM evidence.

    Test sources, collection hooks and runner configuration are restored from
    the frozen task for deterministic execution, but they are not candidate
    deliverables and must not crowd the primary artifact out of the bounded
    rubric prompt (or smuggle instructions into it).
    """
    deliverable = _task_extra_data(task).get("deliverable")
    deliverable = deliverable if isinstance(deliverable, dict) else {}
    primary_artifact = _safe_artifact_path(
        str(deliverable.get("primary_artifact") or "")
    )
    protected = set(_server_owned_verifier_files(task))
    control_names = {
        ".coveragerc",
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "setup.cfg",
        "tox.ini",
    }
    filtered: Dict[str, str] = {}
    for raw_path, raw_content in files.items():
        path = _safe_artifact_path(raw_path)
        if not path or path in protected:
            continue
        name = path.rsplit("/", 1)[-1].casefold()
        parts = [part.casefold() for part in path.split("/")]
        if (
            "tests" in parts
            or name.startswith("test_")
            or name.endswith("_test.py")
            or name in control_names
        ):
            continue
        filtered[path] = str(raw_content or "")
    return filtered, primary_artifact


def _submission_artifact_delta(task: Task, artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Describe verified work relative to the authored workspace.

    Changing an unrelated file (or deleting tests) must not satisfy the
    submission gate. The frozen task must declare a primary artifact, and that
    artifact has to exist, contain non-whitespace content, and differ from its
    starter version. Legacy rows without that contract remain auditable but do
    not receive an authoritative work score.
    """
    verified = _validated_submission_artifact(artifact)
    baseline: Dict[str, str] = {}
    for raw_path, raw_content in normalize_repo_files(
        getattr(task, "repo_structure", None)
    ).items():
        path = _safe_artifact_path(str(raw_path))
        if not path:
            raise RuntimeError(f"Task contains an unsafe workspace path: {raw_path!r}")
        baseline[path] = str(raw_content or "")

    submitted = verified["files"]
    added = sorted(path for path in submitted if path not in baseline)
    deleted = sorted(path for path in baseline if path not in submitted)
    modified = sorted(
        path
        for path in submitted.keys() & baseline.keys()
        if submitted[path] != baseline[path]
    )
    deliverable = _task_extra_data(task).get("deliverable")
    deliverable = deliverable if isinstance(deliverable, dict) else {}
    primary_artifact = _safe_artifact_path(
        str(deliverable.get("primary_artifact") or "")
    )
    primary_status = "not_declared"
    if primary_artifact:
        if primary_artifact not in submitted:
            primary_status = "missing"
        elif not submitted[primary_artifact].strip():
            primary_status = "empty"
        elif primary_artifact in added:
            primary_status = "added"
        elif primary_artifact in modified:
            primary_status = "modified"
        else:
            primary_status = "unchanged"

    any_workspace_change = bool(added or deleted or modified)
    work_present = bool(
        primary_artifact and primary_status in {"added", "modified"}
    )
    return {
        "work_present": work_present,
        "any_workspace_change": any_workspace_change,
        "primary_artifact": primary_artifact or None,
        "primary_artifact_status": primary_status,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "changed_file_count": len(added) + len(modified) + len(deleted),
    }


def _restore_server_owned_verifier_files(
    sandbox: Any,
    repo_root: str,
    task: Task,
) -> None:
    protected = _server_owned_verifier_files(task)
    if not protected:
        if getattr(task, "test_code", None):
            return
        raise RuntimeError("Task has no server-owned verifier files")
    files_api = getattr(sandbox, "files", None)
    if files_api is None or not hasattr(files_api, "write"):
        raise RuntimeError("Grading sandbox file API is unavailable")

    # Candidate-authored tests are useful during the exercise but are not an
    # authoritative score input. Remove test collection hooks/configuration
    # from the reconstructed grading copy, then restore the exact server
    # baseline below. This prevents added conftests/tests from suppressing,
    # rewriting, or statistically diluting verifier failures.
    sandbox.run_code(
        "import pathlib\n"
        f"root = pathlib.Path({repo_root!r})\n"
        "control_names = {'.coveragerc', 'conftest.py', 'pytest.ini', 'pyproject.toml', 'tox.ini', 'setup.cfg'}\n"
        "for candidate in sorted(root.rglob('*'), key=lambda p: len(p.parts), reverse=True):\n"
        "  try:\n"
        "    rel = candidate.relative_to(root)\n"
        "    name = candidate.name.casefold()\n"
        "    in_tests = bool(rel.parts) and rel.parts[0].casefold() == 'tests'\n"
        "    is_test = name.startswith('test_') or name.endswith('_test.py')\n"
        "    if candidate.is_file() or candidate.is_symlink():\n"
        "      if in_tests or is_test or name in control_names:\n"
        "        candidate.unlink(missing_ok=True)\n"
        "  except Exception:\n"
        "    pass\n"
    )
    for path, content in protected.items():
        parent = str(PurePosixPath(repo_root) / PurePosixPath(path).parent)
        sandbox.run_code(
            "import pathlib\n"
            f"pathlib.Path({parent!r}).mkdir(parents=True, exist_ok=True)\n"
        )
        files_api.write(f"{repo_root.rstrip('/')}/{path}", content)


def _lock_grading_workspace(e2b: Any, sandbox: Any, repo_root: str) -> None:
    """Make the reconstructed repo read-only to the untrusted test identity."""
    quoted_root = shlex.quote(repo_root)
    command = (
        "set -eu; "
        f"test -d {quoted_root}; "
        f"id -u {_GRADING_USER} >/dev/null 2>&1 || "
        f"useradd --system --no-create-home --shell /usr/sbin/nologin {_GRADING_USER}; "
        f"chown -R root:root {quoted_root}; "
        f"find {quoted_root} -type d -exec chmod 0555 {{}} +; "
        f"find {quoted_root} -type f -exec chmod 0444 {{}} +"
    )
    process = e2b.run_command(
        sandbox,
        command,
        cwd="/",
        envs={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        user="root",
        timeout=30,
    )
    _stdout, stderr, exit_code = _extract_process_output(process)
    if exit_code not in (None, 0):
        raise RuntimeError(
            "Failed to establish the read-only grading identity boundary: "
            + stderr[-500:]
        )


def _trusted_test_runner_command(command: str) -> str:
    """Keep the authored command while refusing candidate-owned bootstrap code."""
    value = str(command or "").strip()
    value = re.sub(
        r"(?<!\S)(?:\./)?\.venv/bin/python(?:3)?(?=\s|$)",
        "python3",
        value,
    )
    # Isolated mode prevents a candidate-created pytest.py, sitecustomize.py,
    # or PYTHONPATH entry in the workspace from hijacking the verifier's own
    # interpreter startup. Pytest still adds its selected test root for normal
    # imports after the trusted module has loaded.
    value = re.sub(
        r"(?<!\S)python3\s+-m\s+pytest(?=\s|$)",
        "python3 -I -m pytest",
        value,
    )
    try:
        tokens = shlex.split(value)
    except ValueError:
        return ""
    if len(tokens) < 4 or tokens[0] not in {"python", "python3"}:
        return ""
    if tokens[1:4] != ["-I", "-m", "pytest"]:
        return ""
    shell_markers = (";", "|", "&", "`", "$", ">", "<")
    if any(any(marker in token for marker in shell_markers) for token in tokens):
        return ""
    if not any(token == "no:cacheprovider" for token in tokens):
        tokens.extend(["-p", "no:cacheprovider"])
    return shlex.join(tokens)


def _run_task_test_runner(
    e2b: Any,
    sandbox: Any,
    task: Task,
    repo_root: str,
) -> Dict[str, Any] | None:
    config = (_task_extra_data(task).get("test_runner") or {})
    if not isinstance(config, dict):
        return None
    command = _trusted_test_runner_command(str(config.get("command") or ""))
    if not command:
        return None

    try:
        expected_total = int(config.get("expected_total"))
    except (TypeError, ValueError):
        return None
    if not 1 <= expected_total <= _MAX_VERIFIER_TEST_COUNT:
        return None

    working_dir = str(config.get("working_dir") or repo_root).strip() or repo_root
    try:
        timeout_seconds = int(config.get("timeout_seconds") or 60)
    except (TypeError, ValueError):
        timeout_seconds = 60
    timeout_seconds = max(5, min(timeout_seconds, 600))
    parse_pattern = str(config.get("parse_pattern") or "").strip()

    # The child process imports candidate code and therefore owns every byte it
    # writes to stdout/stderr. Never turn those bytes directly into a score.
    # A root-owned parent launches pytest after dropping to the unprivileged
    # grading identity, waits for its real process exit, then appends the final
    # structured record. Candidate code can print a lookalike record, but it
    # cannot write *after* the parent. Test counts come from the frozen task
    # contract; the child output is retained only as bounded diagnostics.
    wrapper_payload = base64.b64encode(
        json.dumps(
            {
                "argv": shlex.split(command),
                "cwd": working_dir,
                "expected_total": expected_total,
                "timeout_seconds": timeout_seconds,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")
    wrapper_script = (
        "import base64,json,os,pwd,subprocess,sys\n"
        f"cfg=json.loads(base64.b64decode({wrapper_payload!r}))\n"
        f"sentinel={_VERIFIER_SENTINEL!r}\n"
        f"account=pwd.getpwnam({_GRADING_USER!r})\n"
        "def demote():\n"
        " os.setgroups([])\n"
        " os.setgid(account.pw_gid)\n"
        " os.setuid(account.pw_uid)\n"
        "env={'PATH':'/usr/local/bin:/usr/bin:/bin',"
        "'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONDONTWRITEBYTECODE':'1',"
        "'PIP_NO_INDEX':'1','HOME':'/tmp'}\n"
        "record={'completed':False,'exit_code':None,'expected_total':cfg['expected_total']}\n"
        "try:\n"
        " child=subprocess.run(cfg['argv'],cwd=cfg['cwd'],env=env,text=True,"
        "capture_output=True,timeout=cfg['timeout_seconds'],preexec_fn=demote,close_fds=True)\n"
        " sys.stdout.write(child.stdout or '')\n"
        " sys.stderr.write(child.stderr or '')\n"
        " record.update({'completed':True,'exit_code':int(child.returncode)})\n"
        "except subprocess.TimeoutExpired as exc:\n"
        " sys.stdout.write((exc.stdout or '') if isinstance(exc.stdout,str) else '')\n"
        " sys.stderr.write((exc.stderr or '') if isinstance(exc.stderr,str) else '')\n"
        " record['error']='timeout'\n"
        "except BaseException as exc:\n"
        " record['error']=type(exc).__name__\n"
        "print('\\n'+sentinel+json.dumps(record,separators=(',',':')),flush=True)\n"
    )
    wrapper_command = shlex.join(["python3", "-I", "-c", wrapper_script])

    try:
        process = e2b.run_command(
            sandbox,
            wrapper_command,
            cwd="/",
            envs={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
            },
            user="root",
            timeout=timeout_seconds + 15,
        )
        stdout, stderr, exit_code = _extract_process_output(process)
        stdout = stdout[-20_000:]
        stderr = stderr[-20_000:]
        combined = "\n".join(part for part in [stdout, stderr] if part)
        reported = _parse_test_runner_results(combined, parse_pattern)
        sentinel_lines = [
            line[len(_VERIFIER_SENTINEL) :]
            for line in stdout.splitlines()
            if line.startswith(_VERIFIER_SENTINEL)
        ]
        trusted_record: Dict[str, Any] = {}
        if sentinel_lines:
            try:
                parsed_record = json.loads(sentinel_lines[-1])
                if isinstance(parsed_record, dict):
                    trusted_record = parsed_record
            except (TypeError, ValueError, json.JSONDecodeError):
                trusted_record = {}
        trusted_exit = trusted_record.get("exit_code")
        try:
            trusted_exit = int(trusted_exit) if trusted_exit is not None else None
        except (TypeError, ValueError):
            trusted_exit = None
        completed = trusted_record.get("completed") is True
        trusted_total = trusted_record.get("expected_total")
        try:
            trusted_total = int(trusted_total)
        except (TypeError, ValueError):
            trusted_total = 0
        reported_total_matches = (
            reported["parse_error"] is False
            and reported["total"] == expected_total
            and reported["passed"] + reported["failed"] == expected_total
        )
        reported_outcome_matches = bool(
            (trusted_exit == 0 and reported["passed"] == expected_total and reported["failed"] == 0)
            or (trusted_exit == 1 and reported["failed"] > 0)
        )
        verifier_ready = (
            completed
            and trusted_total == expected_total
            and trusted_exit in (0, 1)
            and exit_code in (None, 0)
            and reported_total_matches
            and reported_outcome_matches
        )
        success = verifier_ready and trusted_exit == 0
        passed = expected_total if success else 0
        failed = expected_total if verifier_ready and not success else 0
        total = expected_total if verifier_ready else 0
        return {
            "success": success,
            "verifier_ready": verifier_ready,
            "source": "task_test_runner",
            "command": command,
            "working_dir": working_dir,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": trusted_exit,
            "wrapper_exit_code": exit_code,
            "passed": passed,
            "failed": failed,
            "total": total,
            "expected_total": expected_total,
            "parse_error": not verifier_ready,
            "reported_passed": reported["passed"],
            "reported_failed": reported["failed"],
            "reported_total": reported["total"],
        }
    except Exception as exc:
        stdout, stderr, exit_code = _extract_process_output(exc)
        combined = "\n".join(part for part in [stdout, stderr] if part)
        reported = _parse_test_runner_results(combined, parse_pattern)
        return {
            "success": False,
            "verifier_ready": False,
            "source": "task_test_runner",
            "command": command,
            "working_dir": working_dir,
            "stdout": stdout,
            "stderr": stderr or (str(exc) if exit_code is None else ""),
            "exit_code": exit_code,
            "error": str(exc) if exit_code is None else None,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "expected_total": expected_total,
            "parse_error": True,
            "reported_passed": reported["passed"],
            "reported_failed": reported["failed"],
            "reported_total": reported["total"],
        }


def _queued_scoring_breakdown(
    assessment: Assessment,
    *,
    artifact: Dict[str, Any],
    artifact_delta: Dict[str, Any],
    captured_at: datetime,
) -> Dict[str, Any]:
    """Persist the frozen artifact as the existing grading-retry outbox.

    The completed assessment row already has a leased, swept retry mechanism.
    Reusing that row keeps submission to one durable state transition: once the
    artifact and this marker commit together, the candidate is safely done and
    grading can run independently of the request.
    """
    breakdown = (
        dict(assessment.score_breakdown)
        if isinstance(getattr(assessment, "score_breakdown", None), dict)
        else {}
    )
    rubric = (
        dict(breakdown.get("rubric_grading"))
        if isinstance(breakdown.get("rubric_grading"), dict)
        else {}
    )
    retry = (
        dict(rubric.get("retry"))
        if isinstance(rubric.get("retry"), dict)
        else {}
    )
    retry.update(
        {
            "status": "pending",
            "attempt_count": max(0, int(retry.get("attempt_count") or 0)),
            "next_attempt_at": captured_at.isoformat(),
            "claimed_at": None,
            "last_error": None,
        }
    )
    rubric.update(
        {
            "status": "pending",
            "fully_graded": False,
            "failed_dimension_ids": [],
            "retry": retry,
        }
    )
    breakdown.update(
        {
            "rubric_grading": rubric,
            "artifact_gate": {
                **artifact_delta,
                "artifact_sha256": artifact["sha256"],
                "required": True,
                "status": (
                    "satisfied" if artifact_delta["work_present"] else "incomplete"
                ),
            },
        }
    )
    return breakdown


def build_submission_receipt(
    assessment: Assessment,
    task: Task,
) -> Dict[str, Any]:
    """Return the idempotent candidate receipt for one frozen submission."""
    artifact = _durable_submission_artifact(assessment)
    if artifact is None:
        raise RuntimeError("Immutable submission artifact is unavailable")
    evidence = (
        dict(assessment.git_evidence)
        if isinstance(getattr(assessment, "git_evidence", None), dict)
        else {}
    )
    artifact_delta = evidence.get("artifact_delta")
    if not isinstance(artifact_delta, dict):
        artifact_delta = _submission_artifact_delta(task, artifact)
    grading_pending = bool(
        getattr(assessment, "scoring_partial", False)
        or getattr(assessment, "scoring_failed", False)
        or getattr(assessment, "scored_at", None) is None
    )
    return {
        "success": True,
        "score": getattr(assessment, "score", None),
        "grading_status": "pending" if grading_pending else "complete",
        "scoring_partial": bool(getattr(assessment, "scoring_partial", False)),
        "scoring_failed": bool(getattr(assessment, "scoring_failed", False)),
        "tests_passed": int(getattr(assessment, "tests_passed", 0) or 0),
        "tests_total": int(getattr(assessment, "tests_total", 0) or 0),
        "quality_analysis": None,
        "prompt_scores": {},
        "component_scores": {},
        "fraud_flags": list(getattr(assessment, "flags", None) or []),
        "artifact_gate": {
            **artifact_delta,
            "artifact_sha256": artifact["sha256"],
            "required": True,
            "status": "satisfied" if artifact_delta["work_present"] else "incomplete",
        },
    }


def submit_assessment_impl(
    assessment: Assessment,
    final_code: str,
    tab_switch_count: int,
    db: Session,
    *,
    settings_obj: Any,
    e2b_service_cls: Type[Any],
    workspace_repo_root_fn: Callable[[Task], str],
    collect_git_evidence_fn: Callable[[Any, str], Dict[str, Any]],
    recover_retry_sandbox_fn: Callable[[Any, Assessment, Task], Any] | None = None,
    retry_scoring: bool = False,
    defer_scoring: bool = False,
    suppress_completion_side_effects: bool = False,
    enqueue_rubric_retry_on_commit: bool = True,
) -> Dict[str, Any]:
    """Run tests, compute scores, persist results, and trigger notifications."""
    if retry_scoring and defer_scoring:
        raise ValueError("A scoring retry cannot be deferred")
    terminal_statuses = {
        AssessmentStatus.COMPLETED,
        AssessmentStatus.COMPLETED_DUE_TO_TIMEOUT,
    }
    if retry_scoring:
        if assessment.status not in terminal_statuses:
            raise HTTPException(status_code=400, detail="Only a completed assessment can be re-scored")
        terminal_status = assessment.status
    else:
        if assessment.status != AssessmentStatus.IN_PROGRESS:
            raise HTTPException(status_code=400, detail="Assessment cannot be submitted in current state")
        terminal_status = AssessmentStatus.COMPLETED

    task = db.query(Task).filter(Task.id == assessment.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        task = task_view_for_assessment(assessment, task)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=500,
            detail="Assessment task snapshot verification failed",
        ) from exc
    application_row: CandidateApplication | None = None

    # Block every other candidate workspace mutation before reading the final
    # files. Unlike the old terminal-status claim, this lease is recoverable if
    # capture fails: the row stays IN_PROGRESS and the candidate can retry.
    submission_operation_id: str | None = None
    if not retry_scoring:
        submission_operation_id = claim_runtime_operation(
            assessment,
            db,
            kind="submit",
        )

    assessment.tab_switch_count = 0 if settings_obj.MVP_DISABLE_PROCTORING else tab_switch_count

    # Backfill last prompt's code_after
    if assessment.ai_prompts:
        prompts = list(assessment.ai_prompts)
        if prompts:
            prompts[-1] = {**prompts[-1], "code_after": final_code}
            assessment.ai_prompts = prompts

    # --- 1. Freeze the candidate's work before executing any grader code. ---
    repo_root = workspace_repo_root_fn(task)
    e2b = e2b_service_cls(settings_obj.E2B_API_KEY)
    source_sandbox = None
    try:
        source_sandbox = _open_submission_sandbox(
            e2b,
            assessment,
            task,
            retry_scoring=retry_scoring,
            recover_retry_sandbox_fn=recover_retry_sandbox_fn,
        )
    except Exception:
        if submission_operation_id:
            try:
                release_runtime_operation(
                    assessment.id,
                    db,
                    submission_operation_id,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to release submission connection lease assessment_id=%s",
                    assessment.id,
                )
        raise
    frozen_artifact = _durable_submission_artifact(assessment) if retry_scoring else None
    source_is_fresh_artifact = frozen_artifact is not None
    try:
        if frozen_artifact is None:
            frozen_artifact = _capture_submission_artifact(source_sandbox, repo_root)
            artifact_delta = _submission_artifact_delta(task, frozen_artifact)
            artifact_work_present = bool(artifact_delta["work_present"])
            evidence = collect_git_evidence_fn(source_sandbox, repo_root)
            if not isinstance(evidence, dict):
                evidence = {}
            evidence.update(
                {
                    "checkpoint_type": "immutable_submission_artifact",
                    "artifact_sha256": frozen_artifact["sha256"],
                    "artifact_file_count": frozen_artifact["file_count"],
                    "artifact_total_bytes": frozen_artifact["total_bytes"],
                    "candidate_branch_push_status": "not_used",
                    "artifact_delta": artifact_delta,
                }
            )
            captured_at = utcnow()
            artifact_event = {
                "event_type": "submission_artifact_frozen",
                "timestamp": captured_at.isoformat(),
                "sha256": frozen_artifact["sha256"],
                "file_count": frozen_artifact["file_count"],
                "total_bytes": frozen_artifact["total_bytes"],
            }
            captured_timeline = list(assessment.timeline or []) + [artifact_event]
            if not artifact_work_present:
                captured_timeline.append(
                    {
                        "event_type": "assessment_incomplete_no_artifact_work",
                        "timestamp": captured_at.isoformat(),
                        "artifact_sha256": frozen_artifact["sha256"],
                        "changed_file_count": artifact_delta["changed_file_count"],
                        "primary_artifact": artifact_delta["primary_artifact"],
                        "primary_artifact_status": artifact_delta[
                            "primary_artifact_status"
                        ],
                    }
                )
            if retry_scoring:
                assessment.submission_artifact = frozen_artifact
                assessment.submission_artifact_sha256 = frozen_artifact["sha256"]
                assessment.submission_artifact_captured_at = captured_at
                assessment.final_repo_state = frozen_artifact["sha256"]
                assessment.git_evidence = evidence
                assessment.timeline = captured_timeline
                db.commit()
                db.refresh(assessment)
            else:
                assert submission_operation_id is not None
                claimed_snapshots = [
                    dict(item)
                    for item in (assessment.code_snapshots or [])
                    if isinstance(item, dict)
                ]
                claimed_snapshots.append({"final": final_code})
                claimed_prompts = [
                    dict(item)
                    for item in (assessment.ai_prompts or [])
                    if isinstance(item, dict)
                ]
                if claimed_prompts:
                    claimed_prompts[-1] = {
                        **claimed_prompts[-1],
                        "code_after": final_code,
                    }
                claimed = (
                    db.query(Assessment)
                    .filter(
                        Assessment.id == assessment.id,
                        Assessment.status == AssessmentStatus.IN_PROGRESS,
                        Assessment.runtime_operation_id == submission_operation_id,
                    )
                    .update(
                        {
                            Assessment.status: AssessmentStatus.COMPLETED,
                            Assessment.completed_at: captured_at,
                            Assessment.code_snapshots: claimed_snapshots,
                            Assessment.ai_prompts: claimed_prompts,
                            Assessment.tab_switch_count: (
                                0
                                if settings_obj.MVP_DISABLE_PROCTORING
                                else tab_switch_count
                            ),
                            Assessment.submission_artifact: frozen_artifact,
                            Assessment.submission_artifact_sha256: frozen_artifact["sha256"],
                            Assessment.submission_artifact_captured_at: captured_at,
                            Assessment.final_repo_state: frozen_artifact["sha256"],
                            Assessment.git_evidence: evidence,
                            Assessment.timeline: captured_timeline,
                            Assessment.score_breakdown: _queued_scoring_breakdown(
                                assessment,
                                artifact=frozen_artifact,
                                artifact_delta=artifact_delta,
                                captured_at=captured_at,
                            ),
                            Assessment.scoring_partial: True,
                            Assessment.scoring_failed: False,
                            Assessment.score: None,
                            Assessment.final_score: None,
                            Assessment.assessment_score: None,
                            Assessment.taali_score: None,
                            Assessment.scored_at: None,
                            Assessment.runtime_operation_id: None,
                            Assessment.runtime_operation_kind: None,
                            Assessment.runtime_operation_started_at: None,
                        },
                        synchronize_session=False,
                    )
                )
                db.commit()
                if not claimed:
                    raise HTTPException(
                        status_code=409,
                        detail="Assessment submission changed before it could be frozen",
                    )
                submission_operation_id = None
                db.refresh(assessment)
    except Exception as exc:
        db.rollback()
        if submission_operation_id:
            try:
                release_runtime_operation(
                    assessment.id,
                    db,
                    submission_operation_id,
                )
            except Exception:
                db.rollback()
                logger.exception(
                    "Failed to release submission capture lease assessment_id=%s",
                    assessment.id,
                )
        logger.exception(
            "Failed to freeze candidate submission assessment_id=%s",
            assessment.id,
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=500,
            detail="Failed to securely freeze the submitted workspace",
        ) from exc

    assert frozen_artifact is not None
    sandbox_repo_files = dict(frozen_artifact["files"])
    artifact_delta = _submission_artifact_delta(task, frozen_artifact)
    artifact_work_present = bool(artifact_delta["work_present"])
    current_evidence = (
        dict(assessment.git_evidence)
        if isinstance(getattr(assessment, "git_evidence", None), dict)
        else {}
    )
    current_evidence["artifact_delta"] = artifact_delta
    assessment.git_evidence = current_evidence
    if not artifact_work_present and retry_scoring:
        append_assessment_timeline_event(
            assessment,
            "assessment_incomplete_no_artifact_work",
            {
                "artifact_sha256": frozen_artifact["sha256"],
                "changed_file_count": artifact_delta["changed_file_count"],
                "primary_artifact": artifact_delta["primary_artifact"],
                "primary_artifact_status": artifact_delta["primary_artifact_status"],
            },
        )

    if defer_scoring:
        # The immutable database artifact is now authoritative. Retire the
        # mutable candidate sandbox only after that commit; capture failures
        # above deliberately leave it running so the candidate can retry.
        try:
            e2b.close_sandbox(source_sandbox)
        except Exception:
            logger.warning(
                "Failed to close accepted candidate sandbox assessment_id=%s",
                assessment.id,
                exc_info=True,
            )
        return build_submission_receipt(assessment, task)

    # --- 2. Independently verify the frozen digest in a clean sandbox. ---
    grading_sandbox = source_sandbox if source_is_fresh_artifact else None
    try:
        if grading_sandbox is None:
            grading_sandbox = e2b.create_sandbox()
            _materialize_submission_artifact(grading_sandbox, repo_root, frozen_artifact)
        _restore_server_owned_verifier_files(grading_sandbox, repo_root, task)
        _lock_grading_workspace(e2b, grading_sandbox, repo_root)

        # Publication tasks always use their frozen, explicit verifier
        # manifest. Legacy inline test_code executes under the candidate UID
        # and cannot establish this identity/read-only boundary, so it is no
        # longer an authoritative scoring path.
        test_results = _run_task_test_runner(
            e2b,
            grading_sandbox,
            task,
            repo_root,
        )
        verification_source = "restored_task_test_runner"
        if not isinstance(test_results, dict):
            test_results = {
                "success": False,
                "passed": 0,
                "failed": 0,
                "total": 0,
                "parse_error": True,
                "error": "trusted_test_runner_unavailable",
            }
        test_results = {
            **test_results,
            "verification_source": verification_source,
            "verification_environment": "fresh_networkless_sandbox",
            "artifact_sha256": frozen_artifact["sha256"],
        }
    finally:
        closed_ids: set[int] = set()
        for candidate_sandbox in (grading_sandbox, source_sandbox):
            if candidate_sandbox is None or id(candidate_sandbox) in closed_ids:
                continue
            closed_ids.add(id(candidate_sandbox))
            try:
                e2b.close_sandbox(candidate_sandbox)
            except Exception:
                logger.warning("Failed to close assessment sandbox", exc_info=True)

    if test_results.get("parse_error"):
        assessment.test_parse_error = True

    verifier_ready = bool(test_results.get("verifier_ready"))
    passed = int(test_results.get("passed", 0) or 0) if verifier_ready else 0
    total = int(test_results.get("total", 0) or 0) if verifier_ready else 0
    if not verifier_ready:
        assessment.test_parse_error = True

    # --- 3. Prompt/session analysis + heuristics ---
    quality: Dict[str, Any] = {"success": False, "analysis": None}
    prompts = assessment.ai_prompts or []
    prompt_analysis: Dict[str, Any] = {"success": False, "scores": {}, "per_prompt_scores": [], "fraud_flags": []}
    heuristics = compute_all_heuristics(assessment, prompts)

    # Heuristic scoring — the only path now. Populates the radar's atomic
    # *_score columns and is the authoritative assessment score for tasks
    # with no evaluation_rubric (RubricScorer overrides it when a rubric is
    # present). The legacy LLM analyze_code_quality/analyze_prompt_session
    # branch was removed — its output was computed but never persisted to
    # any scored column.
    length_stats = heuristics.get("prompt_length_stats", {}) or {}
    code_delta = heuristics.get("code_delta", {}) or {}
    token_eff = heuristics.get("token_efficiency", {}) or {}
    self_corr = heuristics.get("self_correction_rate", {}) or {}
    ttfp = heuristics.get("time_to_first_prompt", {}) or {}
    copy_paste = heuristics.get("copy_paste_detection", {}) or {}

    avg_words = length_stats.get("avg_words") or 0
    prompt_quality_score = max(0.0, min(10.0, 10.0 - (abs(avg_words - 80) / 12.0)))
    prompt_efficiency_score = max(0.0, min(10.0, (token_eff.get("solve_rate", 0) * 10.0)))
    independence_score = 5.0
    if ttfp.get("value") is not None:
        first_prompt_seconds = max(0, int(ttfp.get("value") or 0))
        independence_score = max(0.0, min(10.0, min(first_prompt_seconds, 600) / 60.0))
    context_utilization_score = max(
        0.0,
        min(10.0, float(code_delta.get("utilization_rate", 0) or 0) * 10.0),
    )
    design_thinking_score = prompt_quality_score
    debugging_strategy_score = max(0.0, min(10.0, float((self_corr.get("rate") or 0)) * 10.0))
    written_communication_score = prompt_quality_score
    learning_velocity_score = prompt_quality_score
    error_recovery_score_val = debugging_strategy_score
    requirement_comprehension_score = prompt_quality_score
    code_quality_score = 5.0
    ai_scores = {
        "prompt_clarity": round(prompt_quality_score, 2),
        "prompt_efficiency": round(prompt_efficiency_score, 2),
        "independence": round(independence_score, 2),
        "context_utilization": round(context_utilization_score, 2),
        "design_thinking": round(design_thinking_score, 2),
        "debugging_strategy": round(debugging_strategy_score, 2),
        "written_communication": round(written_communication_score, 2),
        "learning_velocity": round(learning_velocity_score, 2),
        "error_recovery": round(error_recovery_score_val, 2),
        "requirement_comprehension": round(requirement_comprehension_score, 2),
    }
    prompt_analysis["fraud_flags"] = copy_paste.get("flags", []) or []

    # --- 3. CV-Job fit matching (single Claude call — done first so it feeds into scoring) ---
    scoring_errors = []
    cv_match_result = {
        "cv_job_match_score": None,
        "skills_match": None,
        "experience_relevance": None,
        "match_details": {},
    }
    try:
        candidate = (
            db.query(Candidate).filter(Candidate.id == assessment.candidate_id).first()
            if assessment.candidate_id
            else None
        )
        app_cv_text = None
        role_job_spec_text = None
        if assessment.application_id:
            app_row = db.query(CandidateApplication).filter(
                CandidateApplication.id == assessment.application_id
            ).first()
            application_row = app_row
            app_cv_text = app_row.cv_text if app_row else None
        if assessment.role_id:
            role_row = db.query(Role).filter(Role.id == assessment.role_id).first()
            role_job_spec_text = role_row.job_spec_text if role_row else None

        frozen_cv_text = str(
            getattr(assessment, "cv_text_snapshot", None) or ""
        ).strip()
        cv_text = frozen_cv_text or app_cv_text or (candidate.cv_text if candidate else None)
        job_spec_text = role_job_spec_text or (candidate.job_spec_text if candidate else None)

        if cv_text and job_spec_text and settings_obj.ANTHROPIC_API_KEY:
            role_for_criteria = locals().get("role_row")
            criteria_payload: list[dict] = []
            if role_for_criteria is not None:
                try:
                    for c in sorted(role_for_criteria.criteria or [], key=lambda c: getattr(c, "ordering", 0)):
                        if getattr(c, "deleted_at", None) is not None:
                            continue
                        criteria_payload.append(
                            {
                                "id": int(c.id),
                                "text": str(c.text or "").strip(),
                                "must_have": bool(c.must_have),
                                "source": str(c.source or "recruiter"),
                            }
                        )
                except Exception:
                    criteria_payload = []
            # NB: the local actually bound above is ``application_row`` (line 506).
            # The earlier code referenced a bare ``application``, which raised
            # NameError whenever cv_text + job_spec_text were both present —
            # masked by the broad ``except`` below ("CV-job match failed,
            # continuing without fit score" in assessment-71 submit logs,
            # 2026-05-26).
            fit_metering = {
                "feature": "fit_matching",
                "organization_id": getattr(application_row, "organization_id", None),
                "role_id": getattr(application_row, "role_id", None),
                "entity_id": (
                    f"application:{application_row.id}" if application_row is not None else None
                ),
            }
            if criteria_payload:
                spec = normalize_spec(job_spec_text)
                try:
                    cv_match_result = calculate_cv_job_match_v4_sync(
                        cv_text=cv_text,
                        role_criteria=criteria_payload,
                        spec_description=spec.description,
                        spec_requirements=spec.requirements,
                        api_key=settings_obj.ANTHROPIC_API_KEY,
                        model=settings_obj.resolved_claude_scoring_model,
                        metering=fit_metering,
                    )
                except CvMatchValidationError as exc:
                    scoring_errors.append({"component": "cv_job_match", "error": exc.reason})
            else:
                from ...services.role_criteria_service import render_role_intent_lines

                chip_lines = (
                    render_role_intent_lines(role_for_criteria)
                    if role_for_criteria is not None
                    else []
                )
                additional = "\n".join(chip_lines) or None
                cv_match_result = calculate_cv_job_match_sync(
                    cv_text=cv_text,
                    job_spec_text=job_spec_text,
                    api_key=settings_obj.ANTHROPIC_API_KEY,
                    model=settings_obj.resolved_claude_scoring_model,
                    additional_requirements=additional,
                    metering=fit_metering,
                )
        elif candidate and (not cv_text or not job_spec_text):
            scoring_errors.append(
                {"component": "cv_job_match", "error": "Missing CV or job spec text — fit scoring skipped"}
            )
    except Exception as exc:
        import logging as _logging

        _logging.getLogger("taali.assessments").exception("CV-job match failed, continuing without fit score")
        scoring_errors.append({"component": "cv_job_match", "error": str(exc)})

    # --- 4. MVP composite score (30+ metrics, 8 categories) ---
    duration_seconds = 0
    if assessment.started_at:
        duration_seconds = max(0, int((utcnow() - ensure_utc(assessment.started_at)).total_seconds()))

    interactions = _build_interactions(prompts)
    task_scoring_hints = None
    task_extra_data = _task_extra_data(task)
    if isinstance(task_extra_data.get("scoring_hints"), dict):
        task_scoring_hints = task_extra_data.get("scoring_hints")

    # A per-assessment knob override (set by an A/B experiment arm at invite
    # time) wins over the task's default weights; NULL falls back to the task.
    score_weights = dict(
        getattr(assessment, "score_weights_override", None) or task.score_weights or {}
    )
    # CV-match contribution is layered in via the TAALI role-fit blend below,
    # so the inner composite always treats cv_match weight as zero. If a task
    # configures a non-zero cv_match weight it would be double-counted; clamp.
    if score_weights.get("cv_match"):
        logger.warning(
            "Task %s configured cv_match weight=%s — ignored; CV fit applies via taali role_fit blend",
            getattr(task, "task_key", task.id),
            score_weights.get("cv_match"),
        )
    score_weights["cv_match"] = 0.0

    composite = calculate_mvp_score(
        interactions=interactions,
        tests_passed=passed,
        tests_total=total,
        total_duration_seconds=duration_seconds,
        time_limit_minutes=assessment.duration_minutes or 30,
        weights=score_weights,
        cv_match_result=cv_match_result,
        task_scoring_hints=task_scoring_hints,
    )
    assessment_score_100 = composite["final_score"]
    assessment_score_10 = round(assessment_score_100 / 10.0, 1)
    component_scores = composite["component_scores"]
    category_scores = composite.get("category_scores", {})
    per_prompt_scores = composite.get("per_prompt_scores", [])
    detailed_scores = composite.get("detailed_scores", {})
    explanations = composite.get("explanations", {})

    cv_fit_score_100 = cv_match_result.get("cv_job_match_score")
    requirements_fit_score_100 = (
        cv_match_result.get("match_details", {}).get("requirements_match_score_100")
        if isinstance(cv_match_result.get("match_details", {}), dict)
        else None
    )
    role_fit_score_100 = cv_match_result.get("role_fit_score")
    if role_fit_score_100 is None:
        role_fit_score_100 = compute_role_fit_score(cv_fit_score_100, requirements_fit_score_100)
    taali_score_100 = compute_taali_score(assessment_score_100, role_fit_score_100)
    if taali_score_100 is None:
        taali_score_100 = round(float(assessment_score_100), 1)
        score_mode = "assessment_only_fallback"
    else:
        score_mode = "assessment_plus_role_fit" if role_fit_score_100 is not None else "assessment_only_fallback"

    # --- 3b. Rubric-driven scoring (#37): grade against the task's
    # ``evaluation_rubric.dimensions`` via the Claude-driven RubricScorer
    # shipped in #419. Overrides ``assessment_score_100`` when the rubric
    # grades cleanly. A partial/failed rubric is evidence, never a score: the
    # heuristic remains available for diagnostics but no authoritative
    # assessment/TAALI value is persisted until every dimension is graded.
    # Deterministic process features — the loop skeleton (test runs,
    # challenges, cadence) counted from ai_prompts + timeline. Computed
    # regardless of whether rubric grading runs: recruiter evidence first,
    # grader context second. Never fatal to submission.
    from .process_features import compute_process_features

    try:
        process_features = compute_process_features(assessment.ai_prompts, assessment.timeline)
    except Exception:
        logger.exception("process feature computation failed assessment_id=%s", assessment.id)
        process_features = {}

    rubric_required = bool(task.evaluation_rubric)
    rubric_fully_graded = not rubric_required
    rubric_partial = False
    rubric_failed = False
    heuristic_assessment_score_100 = assessment_score_100
    rubric_breakdown: Dict[str, Any] = {}
    if (
        rubric_required
        and artifact_work_present
        and verifier_ready
        and settings_obj.ANTHROPIC_API_KEY
    ):
        try:
            from .rubric_scoring import (
                RubricScorer,
                ScoringArtifacts,
                summarize_fluency_4d,
                summarize_part_scores,
            )

            # Build artifacts from the actual submission state. Prefer the
            # real sandbox repo (where the agent-SDK path wrote the code);
            # fall back to / merge with code_snapshots for the legacy
            # browser-editor path. Sandbox files win on key collision.
            repo_files_for_grader: Dict[str, str] = {}
            for snap in (assessment.code_snapshots or []) + [{"final": final_code}]:
                if not isinstance(snap, dict):
                    continue
                for k, v in snap.items():
                    if isinstance(v, str) and "/" in k:
                        repo_files_for_grader[k] = v
            if sandbox_repo_files:
                repo_files_for_grader.update(sandbox_repo_files)
            repo_files_for_grader, primary_artifact_for_grader = _repo_files_for_rubric(
                task,
                repo_files_for_grader,
            )
            # Pull DESIGN.md-style files from final_code if it was the last edit
            # (legacy path; new tasks don't ship scaffolds, transcript IS the doc).
            design_doc = ""
            for snap in reversed(assessment.code_snapshots or []):
                if isinstance(snap, dict) and "final" in snap and isinstance(snap["final"], str):
                    if "DESIGN" in snap["final"] or "LIBRARY_DESIGN" in snap["final"] or "LAUNCH_DECISION" in snap["final"] or "INCIDENT_DECISION" in snap["final"] or "EVAL_DESIGN" in snap["final"]:
                        design_doc = snap["final"]
                        break

            # Pull structured decision_points off the task spec so the
            # ``interrogation_outcome`` grader can deterministically
            # re-score the design_decisions dimension from the per-turn
            # classifier state written by the chat route. No Anthropic
            # call needed for this dim — it's pure replay.
            task_extra = task.extra_data if isinstance(task.extra_data, dict) else {}
            decision_points_for_grader = []
            raw_dps_for_grader = task_extra.get("decision_points") if isinstance(task_extra, dict) else None
            if isinstance(raw_dps_for_grader, list):
                decision_points_for_grader = [dp for dp in raw_dps_for_grader if isinstance(dp, dict)]
            raw_traps_for_grader = task_extra.get("traps") if isinstance(task_extra, dict) else None
            traps_for_grader = (
                [t for t in raw_traps_for_grader if isinstance(t, dict)]
                if isinstance(raw_traps_for_grader, list) else []
            )
            artifacts = ScoringArtifacts(
                repo_files=repo_files_for_grader,
                primary_artifact_path=primary_artifact_for_grader,
                design_doc=design_doc,
                prompt_transcript=prompts,
                test_results_summary=f"{passed} of {total} tests passed",
                task_scenario=task.scenario or "",
                candidate_role=str(task.role or ""),
                decision_points=decision_points_for_grader,
                # Process-visible grading is always on now: the grader sees the
                # agent's tool calls/results + git diff (ScoringArtifacts
                # defaults include_process_trace=True), so it scores HOW the
                # candidate worked, not just the message/response text.
                git_evidence=(assessment.git_evidence or {}) if isinstance(assessment.git_evidence, dict) else {},
                traps=traps_for_grader,
                process_features=process_features,
            )
            scorer = RubricScorer(
                api_key=settings_obj.ANTHROPIC_API_KEY,
                organization_id=int(assessment.organization_id),
                assessment_id=int(assessment.id),
                role_id=(
                    int(assessment.role_id)
                    if getattr(assessment, "role_id", None) is not None
                    else None
                ),
                trace_id=(
                    f"assessment:{int(assessment.id)}:submission:"
                    f"{get_request_id() or 'background'}"
                ),
            )
            rubric_result = scorer.grade_rubric(task.evaluation_rubric, artifacts)
            if rubric_result.dimensions:
                rubric_fully_graded = rubric_result.fully_graded
                has_successful_dimension = any(
                    dimension.error is None for dimension in rubric_result.dimensions
                )
                rubric_partial = not rubric_fully_graded and has_successful_dimension
                rubric_failed = not rubric_fully_graded and not has_successful_dimension
                partial_weighted_score = round(float(rubric_result.weighted_score_100), 2)
                rubric_breakdown = {
                    "status": (
                        "complete"
                        if rubric_fully_graded
                        else ("partial" if rubric_partial else "failed")
                    ),
                    "weighted_score_100": partial_weighted_score if rubric_fully_graded else None,
                    "partial_weighted_score_100": (
                        partial_weighted_score if rubric_partial else None
                    ),
                    "model_used": rubric_result.model_used,
                    "fully_graded": rubric_fully_graded,
                    "failed_dimension_ids": rubric_result.failed_dimension_ids,
                    "dimensions": [
                        {
                            "id": d.dimension_id,
                            "score": d.score,
                            "rating": d.rating,
                            "reasoning": d.reasoning,
                            "evidence_citations": d.evidence_citations,
                            "weight": d.weight,
                            "error": d.error,
                        }
                        for d in rubric_result.dimensions
                    ],
                    "heuristic_score_for_comparison": heuristic_assessment_score_100,
                    # Anthropic AI Fluency "4 Ds" rollup (Delegation / Description
                    # / Discernment / Diligence) + Deliverable. Derived from the
                    # same dimension grades; additive, does NOT change the score.
                    "fluency_4d": summarize_fluency_4d(task.evaluation_rubric, rubric_result.dimensions),
                }
                # Two-stage scoring: when the task has a Part 1 (Practice & Setup)
                # dimension, the authoritative assessment score is the part-blend
                # (w1*Practice + w2*Applied) rather than the flat weighted score.
                # Tasks with no practice dimension yield practice=None and the
                # blend collapses to the ordinary score — existing tasks unchanged.
                part_weights = task_extra.get("part_weights") if isinstance(task_extra, dict) else None
                part_scores = summarize_part_scores(
                    task.evaluation_rubric, rubric_result.dimensions, part_weights,
                )
                rubric_breakdown["part_scores"] = part_scores
                if rubric_fully_graded:
                    # Only a complete rubric may become authoritative.
                    assessment_score_100 = partial_weighted_score
                    if (
                        part_scores.get("practice") is not None
                        and part_scores.get("blended_100") is not None
                    ):
                        assessment_score_100 = round(float(part_scores["blended_100"]), 2)
                    assessment_score_10 = round(assessment_score_100 / 10.0, 1)
                    rubric_breakdown["weighted_score_100"] = assessment_score_100
                    taali_score_100 = (
                        compute_taali_score(assessment_score_100, role_fit_score_100)
                        or round(float(assessment_score_100), 1)
                    )
                logger.info(
                    "RubricScorer applied assessment=%s heuristic=%.2f rubric=%.2f parts=%s failed=%s",
                    assessment.id, heuristic_assessment_score_100, partial_weighted_score,
                    {k: part_scores.get(k) for k in ("practice", "applied")},
                    rubric_result.failed_dimension_ids,
                )
            else:
                rubric_failed = True
                rubric_breakdown = {
                    "status": "failed",
                    "fully_graded": False,
                    "failed_dimension_ids": list((task.evaluation_rubric or {}).keys()),
                    "dimensions": [],
                    "error": "rubric_returned_no_dimensions",
                    "heuristic_score_for_comparison": heuristic_assessment_score_100,
                }
        except Exception:
            logger.exception(
                "RubricScorer wire-in failed assessment_id=%s",
                assessment.id,
            )
            rubric_failed = True
            rubric_breakdown = {
                "status": "failed",
                "fully_graded": False,
                "failed_dimension_ids": list((task.evaluation_rubric or {}).keys()),
                "dimensions": [],
                "error": "rubric_scoring_failed",
                "heuristic_score_for_comparison": heuristic_assessment_score_100,
            }
    elif rubric_required and not artifact_work_present:
        rubric_fully_graded = True
        rubric_breakdown = {
            "status": "incomplete",
            "fully_graded": True,
            "failed_dimension_ids": [],
            "dimensions": [],
            "error": "no_substantive_workspace_change",
            "heuristic_score_for_comparison": heuristic_assessment_score_100,
        }
    elif rubric_required and not verifier_ready:
        rubric_failed = True
        rubric_breakdown = {
            "status": "failed",
            "fully_graded": False,
            "failed_dimension_ids": list((task.evaluation_rubric or {}).keys()),
            "dimensions": [],
            "error": "verifier_not_ready",
            "heuristic_score_for_comparison": heuristic_assessment_score_100,
        }
    elif rubric_required:
        rubric_failed = True
        rubric_breakdown = {
            "status": "failed",
            "fully_graded": False,
            "failed_dimension_ids": list((task.evaluation_rubric or {}).keys()),
            "dimensions": [],
            "error": "rubric_grader_unavailable",
            "heuristic_score_for_comparison": heuristic_assessment_score_100,
        }

    grading_incomplete = (
        (rubric_required and not rubric_fully_graded)
        or (artifact_work_present and not verifier_ready)
    )
    if grading_incomplete:
        # A heuristic or partial rubric must never masquerade as an assessment
        # result. Keep the diagnostics in score_breakdown, clear every headline
        # score, and let the durable retry worker finish grading.
        assessment_score_100 = None
        assessment_score_10 = None
        taali_score_100 = None
        score_mode = "rubric_grading_pending"

    if not artifact_work_present:
        # Capability cannot be demonstrated through chat alone. Keep the row
        # terminal and auditable, but make the no-work gate authoritative over
        # heuristics, role fit and any conversational evidence.
        grading_incomplete = False
        rubric_partial = False
        rubric_failed = False
        assessment_score_100 = 0.0
        assessment_score_10 = 0.0
        taali_score_100 = 0.0
        score_mode = "incomplete_no_artifact_work"

    # --- 3c. Difficulty tier reached + CV-claim-consistency tell (central
    # tiers model). Computed from the test pass-ratio + the judgment dimension.
    # The soft cv_claim_consistency signal surfaces for recruiter review and
    # NEVER gates the score. ---
    _design_score_10 = next(
        (d.get("score") for d in (rubric_breakdown.get("dimensions") or [])
         if "design_decisions" in str(d.get("id", ""))),
        None,
    )
    tier_reached = compute_tier_reached(
        (task.extra_data or {}).get("tiers") if isinstance(task.extra_data, dict) else None,
        tests_passed=passed,
        tests_total=total,
        design_score_10=_design_score_10,
    )
    _role_for_tier = locals().get("role_row")
    cv_consistency = cv_claim_consistency(
        tier_reached, role_name=getattr(_role_for_tier, "name", None)
    )

    # --- 4. Persist ---
    completion_ts = datetime.now(timezone.utc)
    assessment.status = terminal_status
    if not retry_scoring:
        assessment.completed_due_to_timeout = False
        assessment.completed_at = completion_ts
    elif not assessment.completed_at:
        assessment.completed_at = completion_ts
    assessment.scored_at = None if grading_incomplete else completion_ts
    assessment.scoring_partial = bool(rubric_partial)
    assessment.scoring_failed = bool(rubric_failed)
    assessment.score = assessment_score_10
    assessment.final_score = assessment_score_100
    assessment.assessment_score = assessment_score_100
    assessment.taali_score = taali_score_100
    assessment.tests_passed = passed
    assessment.tests_total = total
    assessment.tests_run_count = total
    assessment.tests_pass_count = passed
    assessment.test_results = test_results
    assessment.code_snapshots = [
        {"prompt_index": i, "code_before": p.get("code_before", ""), "code_after": p.get("code_after", "")}
        for i, p in enumerate(prompts)
    ] + [{"final": final_code}]

    append_assessment_timeline_event(
        assessment,
        "assessment_submit",
        {
            "session_id": assessment.e2b_session_id,
            "final_code_length": len(final_code or ""),
            "tests_passed": passed,
            "tests_total": total,
            "duration_seconds": duration_seconds,
            "tab_switch_count": assessment.tab_switch_count,
        },
    )
    existing_timeline = list(assessment.timeline or [])
    derived_timeline = build_timeline(assessment)
    assessment.timeline = existing_timeline + [e for e in derived_timeline if e not in existing_timeline]
    assessment.code_quality_score = code_quality_score

    # Map category scores (0-10) to individual assessment columns for the radar chart.
    # These columns are read directly by the frontend radar chart.
    assessment.prompt_quality_score = category_scores.get(
        "prompt_clarity",
        round((component_scores.get("clarity_score", 0) + component_scores.get("specificity_score", 0)) / 20.0, 2),
    )
    assessment.prompt_efficiency_score = category_scores.get(
        "efficiency",
        round(component_scores.get("efficiency_score", 0) / 10.0, 2),
    )
    assessment.independence_score = category_scores.get(
        "independence",
        round(component_scores.get("independence_score", 0) / 10.0, 2),
    )
    assessment.context_utilization_score = category_scores.get(
        "context_provision",
        round(component_scores.get("context_score", 0) / 10.0, 2),
    )
    assessment.design_thinking_score = round(component_scores.get("decomposition_score", 0) / 10.0, 2)
    assessment.debugging_strategy_score = round(component_scores.get("iteration_score", 0) / 10.0, 2)
    assessment.written_communication_score = category_scores.get(
        "communication",
        round(component_scores.get("clarity_score", 0) / 10.0, 2),
    )
    assessment.learning_velocity_score = round(
        composite.get("metric_details", {}).get("prompt_quality_trend", 0) * 7.0,
        2,
    )
    assessment.error_recovery_score = round(
        composite.get("metric_details", {}).get("error_recovery_score", 0) / 10.0,
        2,
    )
    assessment.requirement_comprehension_score = round(component_scores.get("specificity_score", 0) / 10.0, 2)
    # ``assessment.calibration_score`` is no longer written. The separate
    # warmup-prompt scoring axis was dropped — the in-session prompts already
    # produce a ``prompt_clarity`` signal across every real prompt, so the
    # warmup was a separate UI step for a sample of the same signal. The
    # column stays for historical rows; new assessments leave it NULL.

    # CV-Job fit matching scores (Phase 2)
    assessment.cv_job_match_score = cv_match_result.get("cv_job_match_score")
    assessment.cv_job_match_details = cv_match_result.get("match_details", {})

    heuristic_summary = generate_heuristic_summary(
        category_scores=category_scores,
        soft_signals=composite.get("soft_signals", {}),
        fraud_flags=composite.get("fraud", {}).get("flags", []),
    )

    prior_breakdown = (
        assessment.score_breakdown
        if isinstance(getattr(assessment, "score_breakdown", None), dict)
        else {}
    )
    prior_rubric = (
        prior_breakdown.get("rubric_grading")
        if isinstance(prior_breakdown.get("rubric_grading"), dict)
        else {}
    )
    prior_retry = (
        dict(prior_rubric.get("retry"))
        if isinstance(prior_rubric.get("retry"), dict)
        else {}
    )
    if grading_incomplete:
        attempts = max(0, int(prior_retry.get("attempt_count") or 0))
        delay_minutes = min(360, max(1, 2 ** min(attempts, 8)))
        prior_retry.update(
            {
                "status": (
                    "running" if prior_retry.get("status") == "running" else "pending"
                ),
                "attempt_count": attempts,
                "next_attempt_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                ).isoformat(),
                "last_error": (
                    rubric_breakdown.get("error")
                    or ", ".join(rubric_breakdown.get("failed_dimension_ids") or [])
                    or "rubric_grading_incomplete"
                ),
            }
        )
    elif retry_scoring or prior_retry:
        prior_retry.update(
            {
                "status": "complete",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "next_attempt_at": None,
                "last_error": None,
            }
        )
    if prior_retry:
        rubric_breakdown["retry"] = prior_retry

    # Store the full breakdown: component scores (0-100) + 8 category scores (0-10) +
    # detailed per-metric scores + explanations + fit match + rubric grades (#37).
    assessment.score_breakdown = {
        **component_scores,
        "category_scores": category_scores,
        "detailed_scores": detailed_scores,
        "explanations": explanations,
        "rubric_grading": rubric_breakdown,
        "artifact_gate": {
            **artifact_delta,
            "artifact_sha256": frozen_artifact["sha256"],
            "required": True,
            "status": "satisfied" if artifact_work_present else "incomplete",
        },
        "verification_gate": {
            "required": True,
            "status": (
                "passed"
                if verifier_ready and bool(test_results.get("success"))
                else ("failed" if verifier_ready else "unavailable")
            ),
            "verifier_ready": verifier_ready,
            "expected_total": test_results.get("expected_total"),
            "artifact_sha256": frozen_artifact["sha256"],
        },
        "process_features": process_features,
        "tier_reached": tier_reached,
        "cv_claim_consistency": cv_consistency,
        "score_formula_version": TAALI_SCORING_RUBRIC_VERSION,
        "score_mode": score_mode,
        "score_components": {
            "taali_score": taali_score_100,
            "assessment_score": assessment_score_100,
            "cv_fit_score": cv_fit_score_100,
            "requirements_fit_score": requirements_fit_score_100,
            "role_fit_score": role_fit_score_100,
            "role_fit_components": {
                "cv_fit_score": cv_fit_score_100,
                "requirements_fit_score": requirements_fit_score_100,
            },
            "weights": {
                "cv_fit_score": ROLE_FIT_WEIGHTS["cv_fit"],
                "requirements_fit_score": ROLE_FIT_WEIGHTS["requirements_fit"],
                "assessment_score": TAALI_WEIGHTS["assessment"],
                "role_fit_score": TAALI_WEIGHTS["role_fit"],
            },
        },
        "cv_job_match": {
            "overall": cv_match_result.get("cv_job_match_score"),
            "skills": cv_match_result.get("skills_match"),
            "experience": cv_match_result.get("experience_relevance"),
            "role_fit": role_fit_score_100,
        },
        "heuristic_summary": heuristic_summary,
        "uncapped_final_score": composite.get("uncapped_final_score"),
        "applied_caps": composite.get("applied_caps", []),
        "errors": scoring_errors if scoring_errors else [],
    }
    assessment.score_weights_used = composite.get("weights_used", {})
    assessment.flags = list(composite.get("fraud", {}).get("flags", []) or [])
    if not artifact_work_present:
        assessment.flags.append("incomplete_no_artifact_work")
    assessment.scored_at = None if grading_incomplete else utcnow()
    assessment.total_duration_seconds = duration_seconds
    assessment.total_prompts = len(interactions)
    prompt_input_tokens = sum(max(0, int(it.get("input_tokens", 0) or 0)) for it in interactions)
    prompt_output_tokens = sum(max(0, int(it.get("output_tokens", 0) or 0)) for it in interactions)
    terminal_input_tokens, terminal_output_tokens = _terminal_usage_totals(assessment)
    computed_input_tokens = prompt_input_tokens + terminal_input_tokens
    computed_output_tokens = prompt_output_tokens + terminal_output_tokens
    assessment.total_input_tokens = max(
        int(getattr(assessment, "total_input_tokens", 0) or 0),
        computed_input_tokens,
    )
    assessment.total_output_tokens = max(
        int(getattr(assessment, "total_output_tokens", 0) or 0),
        computed_output_tokens,
    )

    fraud_flags = [
        {"type": f, "confidence": 1.0, "evidence": f, "prompt_index": None}
        for f in (composite.get("fraud", {}).get("flags", []) or [])
    ]
    if (assessment.tab_switch_count or 0) > 5:
        fraud_flags.append(
            {
                "type": "tab_switching",
                "confidence": 0.8,
                "evidence": f"{assessment.tab_switch_count} tab switches recorded",
                "prompt_index": None,
            }
        )
    assessment.prompt_fraud_flags = fraud_flags

    # Build prompt_analytics with all the data the frontend needs.
    # The frontend reads: ai_scores (for radar fallback), per_prompt_scores (line chart),
    # component_scores (bar chart), weights_used (bar chart labels).
    assessment.prompt_analytics = {
        "ai_scores": {
            "prompt_clarity": assessment.prompt_quality_score,
            "prompt_efficiency": assessment.prompt_efficiency_score,
            "independence": assessment.independence_score,
            "context_utilization": assessment.context_utilization_score,
            "design_thinking": assessment.design_thinking_score,
            "debugging_strategy": assessment.debugging_strategy_score,
            "written_communication": assessment.written_communication_score,
            "learning_velocity": assessment.learning_velocity_score,
            "error_recovery": assessment.error_recovery_score,
            "requirement_comprehension": assessment.requirement_comprehension_score,
            "prompt_specificity": round(component_scores.get("specificity_score", 0) / 10.0, 2),
            "prompt_progression": assessment.learning_velocity_score,
        },
        "per_prompt_scores": per_prompt_scores,
        "component_scores": {k: round(v / 10.0, 2) for k, v in component_scores.items()},
        "weights_used": composite.get("weights_used", {}),
        "category_scores": category_scores,
        "heuristics": heuristics,
        "metric_details": composite.get("metric_details", {}),
        "soft_signals": composite.get("soft_signals", {}),
        "fraud": composite.get("fraud", {}),
        "final_score": assessment_score_100,
        "assessment_score": assessment_score_100,
        "taali_score": taali_score_100,
        "score_mode": score_mode,
        "uncapped_final_score": composite.get("uncapped_final_score"),
        "applied_caps": composite.get("applied_caps", []),
        "heuristic_summary": heuristic_summary,
        "flags": list(assessment.flags or []),
        "artifact_gate": {
            **artifact_delta,
            "artifact_sha256": frozen_artifact["sha256"],
            "required": True,
            "status": "satisfied" if artifact_work_present else "incomplete",
        },
        "verification_gate": {
            "required": True,
            "status": (
                "passed"
                if verifier_ready and bool(test_results.get("success"))
                else ("failed" if verifier_ready else "unavailable")
            ),
            "verifier_ready": verifier_ready,
            "expected_total": test_results.get("expected_total"),
            "artifact_sha256": frozen_artifact["sha256"],
        },
        "v2": composite.get("v2", {}),
        "cv_job_match": {
            "overall": cv_match_result.get("cv_job_match_score"),
            "skills": cv_match_result.get("skills_match"),
            "experience": cv_match_result.get("experience_relevance"),
            "details": cv_match_result.get("match_details", {}),
        },
        "detailed_scores": detailed_scores,
        "explanations": explanations,
    }

    focus = heuristics.get("browser_focus_ratio", {})
    assessment.browser_focus_ratio = focus.get("ratio")
    if assessment.time_to_first_prompt_seconds is None:
        assessment.time_to_first_prompt_seconds = (heuristics.get("time_to_first_prompt", {}) or {}).get("value")
    assessment.ai_usage_score = round(
        (
            assessment.prompt_quality_score
            + assessment.independence_score
            + assessment.prompt_efficiency_score
        )
        / 3.0,
        2,
    )
    assessment.time_efficiency_score = round(component_scores.get("time_efficiency", 0.0) / 10.0, 2)

    if application_row is not None:
        from ...services.related_role_application_runtime import (
            assessment_uses_related_role_pipeline,
            transition_related_role_assessment_stage,
        )

        related_pipeline = assessment_uses_related_role_pipeline(db, assessment)
        if related_pipeline:
            if not grading_incomplete:
                transition_related_role_assessment_stage(
                    db,
                    assessment=assessment,
                    to_stage="review",
                    source="system",
                )
        else:
            ensure_pipeline_fields(application_row)
            initialize_pipeline_event_if_missing(
                db,
                app=application_row,
                actor_type="system",
                reason="Pipeline initialized at assessment submit",
            )
            if not grading_incomplete:
                transition_stage(
                    db,
                    app=application_row,
                    to_stage="review",
                    source="system",
                    actor_type="system",
                    reason=(
                        "Assessment grading completed"
                        if retry_scoring
                        else "Assessment completed"
                    ),
                    metadata={
                        "assessment_id": assessment.id,
                        "completed_due_to_timeout": bool(
                            getattr(assessment, "completed_due_to_timeout", False)
                        ),
                    },
                )
            refresh_application_score_cache(application_row, db=db)

    try:
        db.commit()
        db.refresh(assessment)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit assessment")

    if grading_incomplete and not retry_scoring and enqueue_rubric_retry_on_commit:
        # The DB row is the durable outbox; this direct kick keeps latency low,
        # while the periodic sweep recovers broker outages and worker crashes.
        try:
            from ...tasks.rubric_retry_tasks import retry_incomplete_rubric_scoring

            retry_incomplete_rubric_scoring.delay(int(assessment.id))
        except Exception:
            logger.exception(
                "Failed to enqueue rubric retry assessment_id=%s; sweep will recover",
                assessment.id,
            )

    # --- 5. Notifications ---
    # Notify the org's primary admin — the oldest active admin account, falling
    # back to the oldest active member. Deterministic, unlike a bare .first()
    # (which picked an arbitrary user); the assessment-complete email now has a
    # well-defined recipient rather than whoever the DB happened to return.
    notify_user = None
    if not grading_incomplete and not suppress_completion_side_effects:
        notify_user = (
            db.query(User)
            .filter(
                User.organization_id == assessment.organization_id,
                User.is_active.is_(True),
            )
            .order_by(User.is_superuser.desc(), User.created_at.asc())
            .first()
        )
    if notify_user:
        from ...components.notifications.tasks import send_results_email

        candidate_name = (
            (assessment.candidate.full_name or assessment.candidate.email)
            if assessment.candidate
            else "Candidate"
        )
        try:
            send_results_email.delay(
                user_email=notify_user.email,
                candidate_name=candidate_name,
                score=assessment.score,
                assessment_id=assessment.id,
            )
        except Exception:
            # Scoring is already committed and authoritative. Never invalidate
            # it (or rerun grading and duplicate side effects) because the
            # notification broker is temporarily unavailable.
            logger.exception(
                "Failed to enqueue assessment result email assessment_id=%s",
                assessment.id,
            )

    return {
        "success": True,
        "score": assessment.score,
        "grading_status": "pending" if grading_incomplete else "complete",
        "scoring_partial": bool(assessment.scoring_partial),
        "scoring_failed": bool(assessment.scoring_failed),
        "tests_passed": passed,
        "tests_total": total,
        "quality_analysis": quality.get("analysis") if quality.get("success") else None,
        "prompt_scores": ai_scores,
        "component_scores": component_scores,
        "fraud_flags": list(assessment.flags or []),
        "artifact_gate": {
            **artifact_delta,
            "artifact_sha256": frozen_artifact["sha256"],
            "required": True,
            "status": "satisfied" if artifact_work_present else "incomplete",
        },
    }


def _build_interactions(prompts: list) -> List[Dict[str, Any]]:
    """Convert raw ai_prompts records into scoring-engine interaction dicts."""
    def _parse_ts(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    base_ts = None
    for raw in prompts:
        candidate_ts = _parse_ts((raw or {}).get("timestamp"))
        if candidate_ts is not None:
            base_ts = candidate_ts
            break

    interactions = []
    for i, p in enumerate(prompts):
        msg = p.get("message", "") or ""
        code_before = p.get("code_before")
        if not isinstance(code_before, str):
            code_before = p.get("code_context")
        code_before = code_before or ""

        code_after = p.get("code_after")
        if not isinstance(code_after, str):
            next_prompt = prompts[i + 1] if i + 1 < len(prompts) else {}
            code_after = (
                next_prompt.get("code_before")
                or next_prompt.get("code_context")
                or code_before
            )
        code_after = code_after or ""
        before_lines = code_before.splitlines()
        after_lines = code_after.splitlines()
        code_diff_lines_added = max(0, len(after_lines) - len(before_lines))
        code_diff_lines_removed = max(0, len(before_lines) - len(after_lines))

        ts = _parse_ts(p.get("timestamp"))
        time_since_assessment_start_ms = p.get("time_since_assessment_start_ms")
        if time_since_assessment_start_ms is None and ts and base_ts:
            time_since_assessment_start_ms = max(0, int((ts - base_ts).total_seconds() * 1000))
        if time_since_assessment_start_ms is None and i == 0:
            time_since_assessment_start_ms = p.get("time_since_last_prompt_ms")

        references_previous = p.get("references_previous")
        if references_previous is None:
            references_previous = bool(
                re.search(r"(?i)\b(as mentioned|previous|earlier|before|last response|you suggested)\b", msg)
            )
        retry_after_failure = p.get("retry_after_failure")
        if retry_after_failure is None:
            retry_after_failure = bool(
                re.search(r"(?i)\b(retry|try again|failed|still failing|another attempt)\b", msg)
            )

        interactions.append(
            {
                "id": str(p.get("id") or i + 1),
                "sequence_number": i + 1,
                "timestamp": p.get("timestamp"),
                "message": msg,
                "response": p.get("response", "") or "",
                "input_tokens": p.get("input_tokens", 0) or 0,
                "output_tokens": p.get("output_tokens", 0) or 0,
                "response_latency_ms": p.get("response_latency_ms"),
                "code_before": code_before,
                "code_after": code_after,
                "code_diff_lines_added": code_diff_lines_added,
                "code_diff_lines_removed": code_diff_lines_removed,
                "word_count": p.get("word_count") or len(msg.split()),
                "question_count": p.get("question_count") or msg.count("?"),
                "code_snippet_included": p.get(
                    "code_snippet_included",
                    ("```" in msg) or bool(re.search(r"(?m)^(?: {4}|\t)\S", msg)),
                ),
                "error_message_included": p.get(
                    "error_message_included",
                    bool(re.search(r"(?i)(error|traceback|exception|failed|assert|stack trace)", msg)),
                ),
                "line_number_referenced": p.get(
                    "line_number_referenced",
                    bool(re.search(r"(?i)line\\s+\\d+|:\\d+(?::\\d+)?\\b", msg)),
                ),
                "file_reference": p.get(
                    "file_reference",
                    bool(re.search(r"(?i)(src/|app/|tests?/|\\.(py|js|jsx|ts|tsx|json|yml|yaml|md)\\b)", msg)),
                ),
                "references_previous": bool(references_previous),
                "retry_after_failure": bool(retry_after_failure),
                "time_since_assessment_start_ms": time_since_assessment_start_ms,
                "time_since_last_prompt_ms": p.get("time_since_last_prompt_ms"),
                "paste_detected": p.get("paste_detected", False),
                "paste_length": p.get("paste_length", 0) or 0,
            }
        )
    return interactions
