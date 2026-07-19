"""Assessment submission orchestration extracted from the service facade."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Type

from fastapi import HTTPException

logger = logging.getLogger("taali.assessments")
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ...components.scoring.analytics import compute_all_heuristics
from ...components.scoring.service import calculate_mvp_score, generate_heuristic_summary
from ...components.scoring.tiers import compute_tier_reached, cv_claim_consistency
from ...models.assessment import Assessment, AssessmentStatus
from ...models.task import Task
from ...platform.request_context import get_request_id
from ...services.fit_matching_service import (
    CvMatchValidationError,
    calculate_cv_job_match_sync,
    calculate_cv_job_match_v4_sync,
)
from ...services.spec_normalizer import normalize_spec
from ...services.taali_scoring import (
    ROLE_FIT_WEIGHTS,
    TAALI_SCORING_RUBRIC_VERSION,
    TAALI_WEIGHTS,
    compute_role_fit_score,
    compute_taali_score,
)
from .claude_budget import terminal_usage_totals as _terminal_usage_totals
from .error_policy import (
    public_git_evidence as _public_git_evidence,
    public_rubric_dimension_error as _public_rubric_dimension_error,
    public_test_results as _public_test_results,
)
from .repository import (
    append_assessment_timeline_event,
    build_timeline,
    ensure_utc,
    utcnow,
)
from .submission_provider_boundary import (
    finalize_submission_snapshot,
    persist_submission_git_checkpoint,
    snapshot_terminal_submission,
)
from .submission_role_dispatch import load_submission_role_kind
from .submission_workspace_serialization import serialized_submission_assessment


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


def _capture_sandbox_repo_files(
    sandbox: Any, repo_root: str, *, max_files: int = 40, max_file_chars: int = 12000
) -> Dict[str, str]:
    """Read the candidate's REAL final repo from the E2B sandbox.

    The agent-SDK chat path writes code to the sandbox via MCP tools — it
    never round-trips through the browser editor, so ``code_snapshots``
    and ``final_repo_state`` capture almost nothing for these
    assessments. The rubric's deliverable-lens grader needs the actual
    shipped artifact, so we walk ``repo_root`` in-sandbox and return
    ``{relpath: content}``.

    Bounded for prompt safety: skips VCS/venv/cache/binary, caps file
    count + per-file chars. Never raises — capture is best-effort; on
    failure the grader falls back to whatever ``code_snapshots`` held.
    """
    snippet = (
        "import os, json\n"
        f"root = {repo_root!r}\n"
        "skip_dirs = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules', '.mypy_cache'}\n"
        "text_ext = {'.py','.md','.txt','.json','.yaml','.yml','.toml','.cfg','.ini','.sh','.sql','.js','.ts','.tsx','.jsx','.html','.css'}\n"
        "out = {}\n"
        "count = 0\n"
        "for dirpath, dirnames, filenames in os.walk(root):\n"
        "    dirnames[:] = [d for d in dirnames if d not in skip_dirs]\n"
        "    for fn in sorted(filenames):\n"
        f"        if count >= {max_files}:\n"
        "            break\n"
        "        ext = os.path.splitext(fn)[1].lower()\n"
        "        if ext and ext not in text_ext:\n"
        "            continue\n"
        "        full = os.path.join(dirpath, fn)\n"
        "        rel = os.path.relpath(full, root)\n"
        "        try:\n"
        "            with open(full, 'r', encoding='utf-8', errors='replace') as fh:\n"
        f"                out[rel] = fh.read()[:{max_file_chars}]\n"
        "            count += 1\n"
        "        except Exception:\n"
        "            continue\n"
        "print(json.dumps(out))\n"
    )
    try:
        result = sandbox.run_code(snippet)
        text = _execution_stdout_text(result).strip().splitlines()
        if not text:
            return {}
        payload = json.loads(text[-1])
        if isinstance(payload, dict):
            return {str(k): str(v) for k, v in payload.items()}
    except Exception as exc:
        logger.warning("sandbox repo-file capture failed; code_snapshots fallback error_type=%s", type(exc).__name__)
    return {}


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


def _open_submission_sandbox(
    e2b: Any,
    assessment: Assessment,
    task: Task,
    *,
    retry_scoring: bool,
    recover_retry_sandbox_fn: Callable[[Any, Assessment, Task], Any] | None,
) -> Any:
    """Connect to the candidate sandbox, or recover a retry fail-closed."""
    if not retry_scoring:
        if assessment.e2b_session_id:
            return e2b.connect_sandbox(assessment.e2b_session_id)
        return e2b.create_sandbox()

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

    # Never create a blank sandbox here.  The recovery callback must clone and
    # verify the exact pushed candidate commit before returning it for tests.
    if _durable_candidate_branch_snapshot(assessment) is None:
        raise RuntimeError(
            "Cannot recover assessment scoring: no verified candidate branch push"
        ) from reconnect_error
    if recover_retry_sandbox_fn is None:
        raise RuntimeError(
            "Cannot recover assessment scoring: branch recovery is unavailable"
        ) from reconnect_error

    sandbox = recover_retry_sandbox_fn(e2b, assessment, task)
    if sandbox is None:
        raise RuntimeError(
            "Cannot recover assessment scoring: branch recovery returned no sandbox"
        ) from reconnect_error
    return sandbox


def _parse_test_runner_results(output: str, parse_pattern: str | None) -> Dict[str, Any]:
    if not parse_pattern:
        return {"passed": 0, "failed": 0, "total": 0, "parse_error": False}

    passed = 0
    failed = 0
    total = 0
    parse_error = False

    try:
        match = re.search(parse_pattern, output or "", re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        # An invalid authored parse_pattern would otherwise silently yield
        # "0 passed / 0 failed" — flag it so the recruiter sees a runner error
        # instead of a misleading zero score.
        logger.warning("Invalid test_runner parse_pattern error_type=%s", type(exc).__name__)
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
        pass_match = re.search(r"(?i)(\d+)\s+passed", output or "")
        if pass_match:
            try:
                passed = int(pass_match.group(1))
            except (TypeError, ValueError):
                passed = 0
    if failed == 0:
        fail_match = re.search(r"(?i)(\d+)\s+failed", output or "")
        if fail_match:
            try:
                failed = int(fail_match.group(1))
            except (TypeError, ValueError):
                failed = 0
    if total == 0:
        total = passed + failed
        if total == 0 and passed > 0:
            total = passed

    return {
        "passed": max(0, passed),
        "failed": max(0, failed),
        "total": max(0, total),
        "parse_error": parse_error,
    }


def _run_task_test_runner(
    e2b: Any,
    sandbox: Any,
    task: Task,
    repo_root: str,
) -> Dict[str, Any] | None:
    config = (_task_extra_data(task).get("test_runner") or {})
    if not isinstance(config, dict):
        return None
    command = str(config.get("command") or "").strip()
    if not command:
        return None

    working_dir = str(config.get("working_dir") or repo_root).strip() or repo_root
    try:
        timeout_seconds = int(config.get("timeout_seconds") or 60)
    except (TypeError, ValueError):
        timeout_seconds = 60
    timeout_seconds = max(5, min(timeout_seconds, 600))
    parse_pattern = str(config.get("parse_pattern") or "").strip()

    try:
        process = e2b.run_command(
            sandbox,
            command,
            cwd=working_dir,
            timeout=timeout_seconds,
        )
        stdout, stderr, exit_code = _extract_process_output(process)
        combined = "\n".join(part for part in [stdout, stderr] if part)
        parsed = _parse_test_runner_results(combined, parse_pattern)
        passed = parsed["passed"]
        failed = parsed["failed"]
        total = parsed["total"]
        success = (failed == 0) and (exit_code in (None, 0))
        return {
            "success": success,
            "source": "task_test_runner",
            "command": command,
            "working_dir": working_dir,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "passed": passed,
            "failed": failed,
            "total": total,
            "parse_error": parsed.get("parse_error", False),
        }
    except Exception as exc:
        logger.exception(
            "Task test runner failed task_id=%s",
            getattr(task, "id", None),
        )
        stdout, stderr, exit_code = _extract_process_output(exc)
        combined = "\n".join(part for part in [stdout, stderr] if part)
        parsed = _parse_test_runner_results(combined, parse_pattern)
        infrastructure_failure = exit_code is None
        return {
            "success": False,
            "source": "task_test_runner",
            "command": command,
            "working_dir": working_dir,
            "stdout": stdout,
            "stderr": "" if infrastructure_failure else stderr,
            "exit_code": exit_code,
            "error": "test_runner_unavailable" if infrastructure_failure else None,
            "passed": parsed["passed"],
            "failed": parsed["failed"],
            "total": parsed["total"],
            "parse_error": parsed.get("parse_error", False),
        }


def _assert_submission_provider_detached(db: Session, phase: str) -> None:
    if db.in_transaction():
        raise RuntimeError(
            f"request transaction remained open before submission {phase}"
        )


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
    suppress_completion_side_effects: bool = False,
    enqueue_rubric_retry_on_commit: bool = True,
    workspace_lock_held: bool = False,
) -> Dict[str, Any]:
    """Serialize chat/submit workspace authority across the detached runtime."""

    role_id = int(assessment.role_id) if assessment.role_id is not None else None
    role_kind = load_submission_role_kind(
        db,
        assessment,
        role_id=role_id,
    )
    with serialized_submission_assessment(
        db,
        assessment,
        workspace_lock_held=workspace_lock_held,
    ) as assessment:
        result = _submit_assessment_impl_serialized(
            assessment,
            final_code,
            tab_switch_count,
            db,
            settings_obj=settings_obj,
            e2b_service_cls=e2b_service_cls,
            workspace_repo_root_fn=workspace_repo_root_fn,
            collect_git_evidence_fn=collect_git_evidence_fn,
            recover_retry_sandbox_fn=recover_retry_sandbox_fn,
            retry_scoring=retry_scoring,
            suppress_completion_side_effects=suppress_completion_side_effects,
            enqueue_rubric_retry_on_commit=enqueue_rubric_retry_on_commit,
        )
    # The service facade dispatches the role wake immediately after return.
    # Seed the already-verified primitive so that broker dispatch cannot lazily
    # reopen the request session merely to read this expired ORM attribute.
    set_committed_value(assessment, "role_id", role_id)
    assessment._submission_role_kind = role_kind
    return result


def _submit_assessment_impl_serialized(
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
    suppress_completion_side_effects: bool = False,
    enqueue_rubric_retry_on_commit: bool = True,
) -> Dict[str, Any]:
    """Run tests, compute scores, persist results, and trigger notifications."""
    assessment_id = int(assessment.id)
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

    if not retry_scoring:
        # Atomically claim the submission to close the duplicate-submit race:
        # two rapid POST /submit calls would otherwise both run the expensive
        # scoring pipeline. Retry workers use their own durable lease and keep
        # the candidate's terminal lifecycle state intact.
        #
        # Persist the browser's final artifact in that same claim. Everything
        # after this commit depends on E2B/GitHub/provider availability; if one
        # of those fails, the durable retry must grade the candidate's actual
        # submission rather than falling back to an older snapshot/starter.
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
        claimed_tab_switch_count = (
            0 if settings_obj.MVP_DISABLE_PROCTORING else tab_switch_count
        )
        claimed = (
            db.query(Assessment)
            .filter(
                Assessment.id == assessment_id,
                Assessment.status == AssessmentStatus.IN_PROGRESS,
            )
            .update(
                {
                    Assessment.status: AssessmentStatus.COMPLETED,
                    Assessment.code_snapshots: claimed_snapshots,
                    Assessment.ai_prompts: claimed_prompts,
                    Assessment.tab_switch_count: claimed_tab_switch_count,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not claimed:
            raise HTTPException(status_code=409, detail="Assessment already submitted")

    provider_snapshot = snapshot_terminal_submission(
        db,
        assessment_id=assessment_id,
        terminal_statuses=terminal_statuses,
    )
    assessment = provider_snapshot.assessment
    task = provider_snapshot.task
    application_row = provider_snapshot.application

    assessment.tab_switch_count = 0 if settings_obj.MVP_DISABLE_PROCTORING else tab_switch_count

    # Backfill last prompt's code_after
    if assessment.ai_prompts:
        prompts = list(assessment.ai_prompts)
        if prompts:
            prompts[-1] = {**prompts[-1], "code_after": final_code}
            assessment.ai_prompts = prompts

    # --- 1. Run tests ---
    _assert_submission_provider_detached(db, "E2B/test/Git work")
    repo_root = workspace_repo_root_fn(task)
    e2b = e2b_service_cls(settings_obj.E2B_API_KEY)
    sandbox = _open_submission_sandbox(
        e2b,
        assessment,
        task,
        retry_scoring=retry_scoring,
        recover_retry_sandbox_fn=recover_retry_sandbox_fn,
    )

    test_results = _run_task_test_runner(e2b, sandbox, task, repo_root)
    if not isinstance(test_results, dict) or (
        int(test_results.get("passed", 0) or 0) == 0
        and int(test_results.get("total", 0) or 0) == 0
        and task.test_code
    ):
        test_results = e2b.run_tests(sandbox, task.test_code)
    if not isinstance(test_results, dict):
        test_results = {"passed": 0, "failed": 0, "total": 0}
    test_results = _public_test_results(test_results)

    if test_results.get("parse_error"):
        assessment.test_parse_error = True

    passed = test_results.get("passed", 0)
    total = test_results.get("total", 0)

    # Capture the REAL final repo from the sandbox (the agent-SDK path
    # writes here via tools, not the browser editor) so the rubric's
    # deliverable-lens grader sees the actual shipped artifact. Best-effort.
    sandbox_repo_files = _capture_sandbox_repo_files(sandbox, repo_root)

    # --- 2. Capture git evidence and durably push the exact candidate head. ---
    try:
        evidence = _public_git_evidence(collect_git_evidence_fn(sandbox, repo_root))
        assessment.git_evidence = evidence
        assessment.final_repo_state = evidence.get("head_sha")
        is_demo_assessment = bool(getattr(assessment, "is_demo", False))
        branch_name = (getattr(assessment, "assessment_branch", None) or "").strip()
        repo_url = (getattr(assessment, "assessment_repo_url", None) or "").strip()
        if is_demo_assessment and not branch_name:
            evidence["push_skipped"] = True
            evidence["push_reason"] = "demo_local_repository"
            assessment.git_evidence = evidence
        else:
            if not branch_name or not repo_url:
                raise HTTPException(
                    status_code=500,
                    detail="Candidate submission branch is not configured",
                )

            # Always push HEAD, even with a clean worktree. Terminal agents may
            # have committed locally already; gating on status_porcelain would
            # strand those commits in the soon-to-be-killed sandbox.
            push_target = f"HEAD:{branch_name}"
            push_result = sandbox.run_code(
                "import json,subprocess,pathlib\n"
                f"repo=pathlib.Path({repo_root!r})\n"
                "add=subprocess.run(['git','add','-A'],cwd=repo,check=False,capture_output=True,text=True)\n"
                "commit=subprocess.run(['git','-c','user.email=taali@local','-c','user.name=TAALI','commit','-m','submit: candidate'],cwd=repo,check=False,capture_output=True,text=True)\n"
                f"push=subprocess.run(['git','push','origin',{push_target!r}],cwd=repo,check=False,capture_output=True,text=True)\n"
                "head=subprocess.run(['git','rev-parse','HEAD'],cwd=repo,check=False,capture_output=True,text=True)\n"
                "payload={\n"
                " 'add_returncode': add.returncode,\n"
                " 'commit_returncode': commit.returncode,\n"
                " 'commit_stderr': (commit.stderr or '')[-500:],\n"
                " 'push_returncode': push.returncode,\n"
                " 'push_stderr': (push.stderr or '')[-500:],\n"
                " 'head_returncode': head.returncode,\n"
                " 'head_sha': (head.stdout or '').strip(),\n"
                "}\n"
                "print(json.dumps(payload))\n"
            )
            try:
                out = _execution_stdout_text(push_result).strip().splitlines()
                push_payload = json.loads(out[-1]) if out else {}
                add_rc = int(push_payload["add_returncode"])
                commit_rc = int(push_payload["commit_returncode"])
                push_rc = int(push_payload["push_returncode"])
                head_rc = int(push_payload["head_returncode"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to verify candidate branch push",
                ) from exc

            pushed_head = str(push_payload.get("head_sha") or "").strip()
            had_uncommitted_changes = bool(evidence.get("status_porcelain"))
            commit_ok = commit_rc == 0 or (
                commit_rc == 1 and not had_uncommitted_changes
            )
            checkpoint_ok = (
                add_rc == 0
                and commit_ok
                and push_rc == 0
                and head_rc == 0
                and bool(pushed_head)
            )
            if not checkpoint_ok:
                evidence["push_returncode"] = push_rc
                evidence["push_error"] = "candidate_branch_push_failed"
                evidence["candidate_branch_push_status"] = "failed"
                assessment.git_evidence = evidence
                if not is_demo_assessment:
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to push candidate branch updates",
                    )

            evidence = _public_git_evidence(
                collect_git_evidence_fn(sandbox, repo_root)
            )
            evidence.update(
                {
                    "push_returncode": push_rc,
                    "candidate_branch_push_status": (
                        "succeeded" if checkpoint_ok else "failed"
                    ),
                    "candidate_branch": branch_name,
                    "candidate_branch_head_sha": pushed_head,
                }
            )
            if push_rc != 0 and is_demo_assessment:
                evidence["push_skipped"] = True
                evidence["push_reason"] = "demo_push_not_required"
                evidence["push_error"] = "candidate_branch_push_failed"
            assessment.git_evidence = evidence
            assessment.final_repo_state = pushed_head or evidence.get("head_sha")

            if checkpoint_ok:
                # This commit is the durable recovery checkpoint. Provider
                # grading happens later and may fail independently.
                try:
                    persist_submission_git_checkpoint(
                        db,
                        provider_snapshot,
                        terminal_statuses=terminal_statuses,
                        git_evidence=assessment.git_evidence,
                        final_repo_state=assessment.final_repo_state,
                    )
                except Exception as exc:
                    db.rollback()
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to persist candidate branch checkpoint",
                    ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        import logging as _logging

        _logging.getLogger("taali.assessments").exception("Failed to capture git evidence on manual submit")
        if not bool(getattr(assessment, "is_demo", False)):
            raise HTTPException(
                status_code=500,
                detail="Failed to checkpoint candidate submission branch",
            ) from exc
    finally:
        e2b.close_sandbox(sandbox)

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
    _assert_submission_provider_detached(db, "CV matching")
    try:
        cv_text = provider_snapshot.cv_text
        job_spec_text = provider_snapshot.job_spec_text

        if cv_text and job_spec_text and settings_obj.ANTHROPIC_API_KEY:
            criteria_payload = provider_snapshot.criteria_payload
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
                    logger.warning(
                        "CV-job match response failed validation assessment_id=%s error_type=%s",
                        assessment.id,
                        type(exc).__name__,
                    )
                    scoring_errors.append(
                        {
                            "component": "cv_job_match",
                            "error": "cv_match_validation_failed",
                        }
                    )
            else:
                cv_match_result = calculate_cv_job_match_sync(
                    cv_text=cv_text,
                    job_spec_text=job_spec_text,
                    api_key=settings_obj.ANTHROPIC_API_KEY,
                    model=settings_obj.resolved_claude_scoring_model,
                    additional_requirements=provider_snapshot.additional_requirements,
                    metering=fit_metering,
                )
        elif provider_snapshot.candidate_present and (not cv_text or not job_spec_text):
            scoring_errors.append(
                {"component": "cv_job_match", "error": "Missing CV or job spec text — fit scoring skipped"}
            )
    except Exception:
        import logging as _logging

        _logging.getLogger("taali.assessments").exception("CV-job match failed, continuing without fit score")
        scoring_errors.append(
            {"component": "cv_job_match", "error": "cv_match_scoring_failed"}
        )

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
    _assert_submission_provider_detached(db, "rubric scoring")
    if rubric_required and settings_obj.ANTHROPIC_API_KEY:
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
                            "error": _public_rubric_dimension_error(d.error),
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

    grading_incomplete = rubric_required and not rubric_fully_graded
    if grading_incomplete:
        # A heuristic or partial rubric must never masquerade as an assessment
        # result. Keep the diagnostics in score_breakdown, clear every headline
        # score, and let the durable retry worker finish grading.
        assessment_score_100 = None
        assessment_score_10 = None
        taali_score_100 = None
        score_mode = "rubric_grading_pending"

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
    cv_consistency = cv_claim_consistency(
        tier_reached, role_name=provider_snapshot.role_name
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
    assessment.flags = composite.get("fraud", {}).get("flags", [])
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
        "flags": composite.get("fraud", {}).get("flags", []),
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

    try:
        side_effects = finalize_submission_snapshot(
            db,
            provider_snapshot,
            terminal_statuses=terminal_statuses,
            retry_scoring=retry_scoring,
            grading_incomplete=grading_incomplete,
            suppress_completion_side_effects=suppress_completion_side_effects,
            request_id=get_request_id(),
            settings_obj=settings_obj,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to submit assessment") from exc

    _assert_submission_provider_detached(db, "post-commit dispatch")

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

    # --- 5. Notifications (primitive payloads captured before final commit) ---
    if side_effects.notify_email:
        from ...components.notifications.tasks import send_results_email

        try:
            send_results_email.delay(
                user_email=side_effects.notify_email,
                candidate_name=side_effects.candidate_name,
                score=assessment.score,
                assessment_id=side_effects.assessment_id,
            )
        except Exception:
            # Scoring is already committed and authoritative. Never invalidate
            # it (or rerun grading and duplicate side effects) because the
            # notification broker is temporarily unavailable.
            logger.exception(
                "Failed to enqueue assessment result email assessment_id=%s",
                assessment.id,
            )

    # Assessments are Taali-native: only mirror the result back into Workable
    # when the org has write-back enabled (workable_writeback). Read-only orgs
    # keep the whole assessment lifecycle inside Taali — the same switch that
    # already governs the invite-send handoff (invite_flow._workable_handoff_eligible),
    # so read-only mode genuinely suppresses *every* assessment Workable write.
    workable_payload = side_effects.workable_payload
    if (
        not grading_incomplete
        and not suppress_completion_side_effects
        and not settings_obj.MVP_DISABLE_WORKABLE
        and workable_payload is not None
    ):
        from ...services.assessment_result_workable_delivery import (
            AssessmentResultDispatch,
            publish_assessment_result_delivery,
        )

        try:
            publish_assessment_result_delivery(
                AssessmentResultDispatch(
                    assessment_id=int(workable_payload["assessment_id"]),
                    organization_id=int(workable_payload["organization_id"]),
                    operation_id=str(workable_payload["operation_id"]),
                ),
            )
        except Exception:
            logger.exception(
                "Failed to enqueue assessment Workable writeback assessment_id=%s",
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
        "fraud_flags": composite.get("fraud", {}).get("flags", []),
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
