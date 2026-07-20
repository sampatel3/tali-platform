from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

from ..components.assessments.fluency_axes import validate_fluency_coverage
from ..components.assessments.interrogation import validate_decision_points, validate_traps
from .task_catalog import canonical_task_catalog_dir
from .task_repo_service import normalize_repo_file_content


RUBRIC_WEIGHT_TOLERANCE = 1e-3
WEIGHT_BOUND_EPSILON = 1e-9
MAX_CANDIDATE_QA_WEIGHT = 0.15
MIN_WORK_EVIDENCE_WEIGHT = 0.70
REQUIRED_TASK_FIELDS = (
    "task_id",
    "name",
    "role",
    "duration_minutes",
    "scenario",
    "repo_structure",
    "evaluation_rubric",
    "expected_candidate_journey",
    "interviewer_signals",
    "scoring_hints",
    "test_runner",
    "workspace_bootstrap",
    "role_alignment",
    "human_testing_checklist",
)
HUMAN_TESTING_KEYS = (
    "candidate_clarity",
    "repo_boot_ok",
    "tests_collect_ok",
    "baseline_failures_meaningful",
    "rubric_matches_role",
    "timebox_realistic",
)
ROLE_ALIGNMENT_KEYS = (
    "source_user_email",
    "source_role_name",
    "source_role_identifier",
    "captured_at",
    "must_cover",
    "must_not_cover",
    "jd_to_signal_map",
)
TEST_RUNNER_KEYS = ("command", "working_dir", "parse_pattern", "timeout_seconds")
WORKSPACE_BOOTSTRAP_KEYS = ("commands", "working_dir", "timeout_seconds", "must_succeed")

# The assessment template is intentionally networkless at runtime.  A task may
# only depend on packages baked into ``e2b.Dockerfile``; adding a dependency is
# therefore an image change and a contract change, never a bootstrap command.
OFFLINE_TASK_RUNTIME_PACKAGES = frozenset({"pytest", "python-hcl2"})

_PACKAGE_INSTALL_COMMAND = re.compile(
    r"""(?ix)
    \b(?:pip|pip3|pipx|uv|poetry|pdm|pipenv|npm|pnpm|yarn|bun)\b
    |\b(?:conda|mamba|micromamba|apt|apt-get|apk|dnf|yum|brew|gem)\b
    |\bsetup\.py\b
    """
)
_VIRTUAL_ENV_COMMAND = re.compile(
    r"(?ix)\b(?:python(?:3(?:\.\d+)?)?\s+-m\s+venv|virtualenv)\b"
)
_NETWORK_BOOTSTRAP_COMMAND = re.compile(
    r"""(?ix)
    \b(?:curl|wget|ftp|sftp|scp|ssh|telnet|ncat|nc|dig|host|nslookup)\b
    |\bgit\s+(?:clone|fetch|pull|ls-remote|submodule\s+update)\b
    |(?:https?|ftps?|git\+ssh|ssh|git)://
    |/dev/(?:tcp|udp)/
    |\b(?:urllib(?:\.request)?|requests|httpx|socket)\b
    |\bfetch\s*\(
    """
)
_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_ISOLATED_PYTEST_COMMAND = re.compile(r"^python3\s+-I\s+-m\s+pytest(?:\s|$)")


@dataclass
class TaskSpecValidationResult:
    task_id: str
    valid: bool
    errors: List[str]


class TaskSpecValidationMode(str, Enum):
    """How aggressively a task spec is validated.

    ``PUBLICATION`` is the fail-closed contract for every newly generated or
    updated task. ``LEGACY`` preserves read compatibility for stored/catalogue
    tasks that predate the artifact-first contract; it skips only the new
    publication rules and still applies every structural validation below.
    """

    PUBLICATION = "publication"
    LEGACY = "legacy"


_SUPPORTED_GRADERS = frozenset({"interrogation_outcome", "practice_outcome"})
_SUPPORTED_DELIVERABLE_KINDS = frozenset({"code", "doc"})
_DEFAULT_DELIVERABLE_KIND = "code"
# Rubric-dimension lens (selects the grader frame): "decision" punishes lazy
# delegation (judgment from the transcript); "deliverable" credits the shipped
# artifact regardless of who typed it. LLM-criteria dims should declare one;
# interrogation_outcome dims are inherently decision-lens and don't.
_SUPPORTED_LENSES = frozenset({"decision", "deliverable", "discernment", "diligence", "practice"})
_CANDIDATE_QA_LENSES = frozenset({"decision"})
_CANDIDATE_QA_GRADERS = frozenset({"interrogation_outcome"})
# These existing rubric routes are all grounded in evidence produced inside
# the assessment workspace:
# - deliverable: final code/document and source-grounded claims
# - diligence: tests/checks and verification trace
# - discernment: review of agent output against workspace evidence
# - practice/practice_outcome: repo, tool and process trace
_WORK_EVIDENCE_LENSES = frozenset({"deliverable", "diligence", "discernment", "practice"})
_WORK_EVIDENCE_GRADERS = frozenset({"practice_outcome"})
_VERIFIER_CONFIG_FILES = frozenset({
    ".coveragerc",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
})
_VERIFIER_HELPER_MARKERS = ("assertion", "assertions", "helper", "helpers", "validator", "verifier")


def validate_deliverable(deliverable: Any, repo_files: Dict[str, str]) -> List[str]:
    """Validate the top-level ``deliverable`` block.

    Optional only for ``LEGACY`` reads — when absent, the runtime treats the
    task as ``kind: code`` for back-compat. Publication mode separately
    requires this block. When present, ``kind`` must be one of the supported
    families and ``primary_artifact`` must point at a file that exists in
    ``repo_structure.files``. This catches the failure mode where the schema
    declares a deliverable the candidate workspace can never open.

    Adding a new family later (e.g. ``matrix``, ``deck``) means: add
    the kind to ``_SUPPORTED_DELIVERABLE_KINDS`` and teach the FE
    DeliverablePane how to render it. Schema-only change here.
    """
    if deliverable is None:
        return []
    if not isinstance(deliverable, dict):
        return ["deliverable must be an object"]
    errors: List[str] = []
    kind = deliverable.get("kind")
    if kind is None:
        errors.append("deliverable.kind is required when deliverable is set")
    elif not isinstance(kind, str) or kind not in _SUPPORTED_DELIVERABLE_KINDS:
        errors.append(
            f"deliverable.kind must be one of {sorted(_SUPPORTED_DELIVERABLE_KINDS)}; got {kind!r}"
        )
    primary = deliverable.get("primary_artifact")
    if primary is None:
        errors.append("deliverable.primary_artifact is required when deliverable is set")
    elif not isinstance(primary, str) or not primary.strip():
        errors.append("deliverable.primary_artifact must be a non-empty string")
    elif repo_files and primary not in repo_files:
        errors.append(
            f"deliverable.primary_artifact={primary!r} must match a file in repo_structure.files"
        )
    return errors


def _coerce_validation_mode(mode: TaskSpecValidationMode | str) -> TaskSpecValidationMode:
    try:
        return TaskSpecValidationMode(mode)
    except ValueError as exc:
        supported = ", ".join(item.value for item in TaskSpecValidationMode)
        raise ValueError(f"Unknown task-spec validation mode {mode!r}; expected one of: {supported}") from exc


def _numeric_rubric_weight(details: Any) -> float | None:
    if not isinstance(details, dict):
        return None
    weight = details.get("weight")
    if isinstance(weight, bool):
        return None
    try:
        numeric = float(weight)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and numeric >= 0.0 else None


def _dimension_uses_candidate_qa(details: Any) -> bool:
    if not isinstance(details, dict):
        return False
    grader = str(details.get("grader") or "").strip()
    lens = str(details.get("lens") or "").strip()
    return grader in _CANDIDATE_QA_GRADERS or lens in _CANDIDATE_QA_LENSES


def _dimension_uses_work_evidence(details: Any) -> bool:
    if not isinstance(details, dict):
        return False
    grader = str(details.get("grader") or "").strip()
    lens = str(details.get("lens") or "").strip()
    return grader in _WORK_EVIDENCE_GRADERS or lens in _WORK_EVIDENCE_LENSES


def _normalize_repo_path(path: Any) -> str:
    normalized = str(path or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def discover_verifier_files(repo_files: Dict[str, str]) -> List[str]:
    """Infer server-owned test/config/helper files for legacy compatibility.

    New tasks must persist this exact set under ``test_runner.verifier_files``.
    The runtime may use this helper only for historical specs that predate the
    explicit manifest. Candidate implementation modules are deliberately not
    inferred: the grader must restore tests and their helpers without restoring
    the code or document being assessed.
    """

    discovered: set[str] = set()
    for raw_path in repo_files:
        path = _normalize_repo_path(raw_path)
        if not path:
            continue
        lowered = path.lower()
        name = Path(lowered).name
        parts = Path(lowered).parts
        stem = Path(name).stem
        is_test_tree = bool(parts) and parts[0] in {"test", "tests"}
        is_test_module = name == "conftest.py" or name.startswith("test_") or stem.endswith("_test")
        is_test_config = name in _VERIFIER_CONFIG_FILES
        is_named_helper = any(marker in stem for marker in _VERIFIER_HELPER_MARKERS)
        if is_test_tree or is_test_module or is_test_config or is_named_helper:
            discovered.add(path)
    return sorted(discovered)


def _validate_verifier_files(spec: Dict[str, Any], deliverable: Any) -> List[str]:
    errors: List[str] = []
    runner = spec.get("test_runner")
    if not isinstance(runner, dict):
        return errors  # structural validation reports the missing runner

    raw_manifest = runner.get("verifier_files")
    if not isinstance(raw_manifest, list) or not raw_manifest:
        return [
            "test_runner.verifier_files must be a non-empty list of server-owned "
            "test, test-config, and verifier-helper repo paths"
        ]

    manifest: List[str] = []
    for idx, raw_path in enumerate(raw_manifest):
        if not _is_non_empty_string(raw_path):
            errors.append(f"test_runner.verifier_files[{idx}] must be a non-empty repo path")
            continue
        manifest.append(_normalize_repo_path(raw_path))

    duplicates = sorted(path for path in set(manifest) if manifest.count(path) > 1)
    if duplicates:
        errors.append(
            "test_runner.verifier_files must not contain duplicates: "
            + ", ".join(duplicates)
        )

    repo_files = _repo_files(spec.get("repo_structure"))
    normalized_repo_paths = {_normalize_repo_path(path) for path in repo_files}
    missing_from_repo = sorted(set(manifest) - normalized_repo_paths)
    if missing_from_repo:
        errors.append(
            "test_runner.verifier_files paths must exist in repo_structure.files; missing "
            + ", ".join(missing_from_repo)
        )

    primary_artifact = (
        _normalize_repo_path(deliverable.get("primary_artifact"))
        if isinstance(deliverable, dict)
        else ""
    )
    if primary_artifact and primary_artifact in manifest:
        errors.append(
            f"test_runner.verifier_files must exclude deliverable.primary_artifact={primary_artifact!r}; "
            "restoring the candidate's artifact would invalidate the proof of work"
        )

    discovered = set(discover_verifier_files(repo_files))
    missing_discovered = sorted(discovered - set(manifest))
    if missing_discovered:
        errors.append(
            "test_runner.verifier_files must include every discovered test/config/helper file; missing "
            + ", ".join(missing_discovered)
        )
    return errors


def _validate_offline_workspace_contract(spec: Dict[str, Any]) -> List[str]:
    """Reject publishable tasks that cannot start in a networkless image."""

    errors: List[str] = []
    bootstrap = spec.get("workspace_bootstrap")
    if isinstance(bootstrap, dict):
        commands = bootstrap.get("commands")
        if isinstance(commands, list):
            for index, command in enumerate(commands):
                command_text = str(command)
                if _VIRTUAL_ENV_COMMAND.search(command_text):
                    errors.append(
                        f"workspace_bootstrap.commands[{index}] may not create a virtual environment; "
                        "use the dependencies baked into the system interpreter"
                    )
                if _PACKAGE_INSTALL_COMMAND.search(command_text):
                    errors.append(
                        f"workspace_bootstrap.commands[{index}] may not install packages; "
                        "assessment dependencies must be baked into the sandbox image"
                    )
                if _NETWORK_BOOTSTRAP_COMMAND.search(command_text):
                    errors.append(
                        f"workspace_bootstrap.commands[{index}] may not access the network; "
                        "assessment bootstrap must run offline"
                    )

    runner = spec.get("test_runner")
    runner_command = str(runner.get("command") or "").strip() if isinstance(runner, dict) else ""
    if runner_command and not _ISOLATED_PYTEST_COMMAND.match(runner_command):
        errors.append(
            "test_runner.command must use the baked isolated interpreter and start "
            "with 'python3 -I -m pytest'"
        )

    repo_files = _repo_files(spec.get("repo_structure"))
    requirements = repo_files.get("requirements.txt")
    if requirements is not None:
        requirements_text = normalize_repo_file_content(requirements)
        for line_number, raw_line in enumerate(requirements_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _REQUIREMENT_NAME.match(line)
            package = (
                match.group(1).lower().replace("_", "-").replace(".", "-")
                if match
                else ""
            )
            is_remote_or_option = (
                line.startswith("-")
                or "@" in line
                or "://" in line
            )
            if (
                not package
                or is_remote_or_option
                or package not in OFFLINE_TASK_RUNTIME_PACKAGES
            ):
                errors.append(
                    "repo_structure.files['requirements.txt'] line "
                    f"{line_number} declares {line!r}, which is not baked into the "
                    "offline assessment image"
                )
    return errors


def validate_publication_contract(spec: Dict[str, Any]) -> List[str]:
    """Validate the artifact-first contract for a publishable task.

    This deliberately sits above the legacy structural schema. A task may be
    structurally readable yet unsuitable for publication because candidates
    can pass through Q&A rather than doing verifiable work. New and updated
    tasks use this contract; historical task reads opt into ``LEGACY`` mode.
    """

    errors: List[str] = []
    deliverable = spec.get("deliverable")
    if not isinstance(deliverable, dict):
        errors.append(
            "publication contract requires deliverable with kind, primary_artifact, "
            "required=true, no_artifact_outcome='incomplete', and submission_check='test_runner'"
        )
    else:
        if deliverable.get("required") is not True:
            errors.append(
                "deliverable.required must be true; a no-artifact attempt cannot be a valid submission"
            )
        if deliverable.get("no_artifact_outcome") != "incomplete":
            errors.append(
                "deliverable.no_artifact_outcome must be 'incomplete'; no artifact cannot receive a passing score"
            )
        if deliverable.get("submission_check") != "test_runner":
            errors.append(
                "deliverable.submission_check must be 'test_runner' so the required artifact is independently checked"
            )
    errors.extend(_validate_verifier_files(spec, deliverable))
    errors.extend(_validate_offline_workspace_contract(spec))

    runner = spec.get("test_runner")
    expected_total = runner.get("expected_total") if isinstance(runner, dict) else None
    if (
        isinstance(expected_total, bool)
        or not isinstance(expected_total, int)
        or expected_total <= 0
    ):
        errors.append(
            "test_runner.expected_total must be a positive integer matching the "
            "trusted verifier suite's collected test count"
        )

    rubric = spec.get("evaluation_rubric")
    if not isinstance(rubric, dict):
        return errors

    qa_dimensions: List[tuple[str, float]] = []
    work_dimensions: List[tuple[str, float]] = []
    unmapped_dimensions: List[tuple[str, float]] = []
    for dim_id, details in rubric.items():
        weight = _numeric_rubric_weight(details)
        if weight is None:
            continue
        if _dimension_uses_candidate_qa(details):
            qa_dimensions.append((str(dim_id), weight))
        if _dimension_uses_work_evidence(details):
            work_dimensions.append((str(dim_id), weight))
        elif not _dimension_uses_candidate_qa(details):
            unmapped_dimensions.append((str(dim_id), weight))

    qa_weight = sum(weight for _, weight in qa_dimensions)
    if qa_weight > MAX_CANDIDATE_QA_WEIGHT + WEIGHT_BOUND_EPSILON:
        breakdown = ", ".join(f"{dim}={weight:.3f}" for dim, weight in qa_dimensions)
        errors.append(
            "publication contract candidate-visible Q&A/interrogation weight must be "
            f"<= {MAX_CANDIDATE_QA_WEIGHT:.2f}; got {qa_weight:.3f} from [{breakdown}]. "
            "Move scoring weight to the delivered artifact, source grounding, verification, "
            "or other workspace evidence."
        )

    work_weight = sum(weight for _, weight in work_dimensions)
    if work_weight + WEIGHT_BOUND_EPSILON < MIN_WORK_EVIDENCE_WEIGHT:
        counted = ", ".join(f"{dim}={weight:.3f}" for dim, weight in work_dimensions) or "none"
        unmapped = ", ".join(f"{dim}={weight:.3f}" for dim, weight in unmapped_dimensions) or "none"
        errors.append(
            "publication contract work-evidence weight must be "
            f">= {MIN_WORK_EVIDENCE_WEIGHT:.2f}; got {work_weight:.3f}. "
            f"Counted [{counted}]; unmapped non-Q&A dimensions [{unmapped}]. "
            "Use lens='deliverable' for artifact/source-grounding evidence, lens='diligence' "
            "for verification, lens='discernment' for output review, or practice_outcome/workspace evidence."
        )
    return errors


def resolve_deliverable_kind(deliverable: Any) -> str:
    """Return the kind for a task spec, defaulting to ``"code"``.

    Single place to apply the back-compat default so the runtime never
    has to do ``deliverable.get("kind") or "code"`` ad-hoc.
    """
    if isinstance(deliverable, dict):
        kind = deliverable.get("kind")
        if isinstance(kind, str) and kind in _SUPPORTED_DELIVERABLE_KINDS:
            return kind
    return _DEFAULT_DELIVERABLE_KIND


def _validate_decisions_dim(
    evaluation_rubric: Dict[str, Any] | None,
    decision_points: Any,
) -> List[str]:
    """If a rubric dimension declares ``grader: "interrogation_outcome"`` it
    MUST have a corresponding non-empty ``decision_points`` block at the
    top level of the spec, and vice-versa.

    Without this guard you can ship a task whose rubric scores
    "design_decisions_articulated" via the interrogation grader while
    the spec declares no decisions — the grader would silently 0-score
    every candidate. Catch it at boot, not at submit.
    """
    errors: List[str] = []
    if not isinstance(evaluation_rubric, dict):
        return errors
    dims_with_interrogation_grader: List[str] = []
    for dim_id, details in evaluation_rubric.items():
        if not isinstance(details, dict):
            continue
        grader = str(details.get("grader") or "").strip()
        if grader and grader not in _SUPPORTED_GRADERS:
            errors.append(
                f"evaluation_rubric.{dim_id}.grader={grader!r} is not a supported grader; "
                f"supported: {sorted(_SUPPORTED_GRADERS)}"
            )
        if grader == "interrogation_outcome":
            dims_with_interrogation_grader.append(dim_id)
        # Lens (selects the grader frame). Optional for back-compat; when
        # set it must be supported. interrogation_outcome dims are
        # inherently decision-lens and should not also declare a lens.
        lens = details.get("lens")
        if lens is not None:
            if not isinstance(lens, str) or lens not in _SUPPORTED_LENSES:
                errors.append(
                    f"evaluation_rubric.{dim_id}.lens={lens!r} must be one of {sorted(_SUPPORTED_LENSES)}"
                )
            elif grader == "interrogation_outcome":
                errors.append(
                    f"evaluation_rubric.{dim_id} declares grader=interrogation_outcome AND lens={lens!r}; "
                    "interrogation_outcome is inherently decision-lens — drop the lens field"
                )
    has_decisions = isinstance(decision_points, list) and len(decision_points) > 0
    if dims_with_interrogation_grader and not has_decisions:
        errors.append(
            "rubric dim(s) "
            + ", ".join(dims_with_interrogation_grader)
            + " declare grader=interrogation_outcome but no decision_points block is defined"
        )
    if has_decisions and not dims_with_interrogation_grader:
        errors.append(
            "decision_points are defined but no rubric dimension declares "
            "grader=interrogation_outcome; the candidate would never be scored on them"
        )
    return errors


def validate_rubric_weights(evaluation_rubric: Dict[str, Any] | None) -> List[str]:
    if not evaluation_rubric:
        return []
    total = 0.0
    errors: List[str] = []
    for category, details in evaluation_rubric.items():
        if not isinstance(details, dict):
            errors.append(f"Category '{category}' must be an object")
            continue
        weight = details.get("weight")
        if weight is None:
            errors.append(f"Category '{category}' missing weight")
            continue
        numeric = _numeric_rubric_weight(details)
        if numeric is None:
            errors.append(f"Category '{category}' has invalid weight: {weight!r}")
            continue
        total += numeric
    if abs(total - 1.0) > RUBRIC_WEIGHT_TOLERANCE:
        errors.append(f"Rubric weights must sum to 1.0 (+/- {RUBRIC_WEIGHT_TOLERANCE}); got {total:.6f}")
    return errors


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _repo_files(repo_structure: Dict[str, Any] | None) -> Dict[str, str]:
    if not isinstance(repo_structure, dict):
        return {}
    files = (repo_structure or {}).get("files") or {}
    normalized: Dict[str, str] = {}
    if isinstance(files, dict):
        for path, content in files.items():
            if not _is_non_empty_string(path):
                continue
            normalized[str(path)] = normalize_repo_file_content(
                content if isinstance(content, str) else json.dumps(content, indent=2, sort_keys=True)
            )
    elif isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if not _is_non_empty_string(path):
                continue
            content = entry.get("content", "")
            normalized[str(path)] = normalize_repo_file_content(
                content if isinstance(content, str) else json.dumps(content, indent=2, sort_keys=True)
            )
    return normalized


def _normalize_task_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    repo_structure = spec.get("repo_structure")
    if not isinstance(repo_structure, dict):
        return spec

    repo_structure["files"] = _repo_files(repo_structure)
    return spec


def _validate_repo_structure(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    repo_structure = spec.get("repo_structure")
    if not isinstance(repo_structure, dict):
        return ["repo_structure must be an object"]

    repo_name = str(repo_structure.get("name") or "").strip()
    if not repo_name:
        errors.append("repo_structure.name is required")

    files = _repo_files(repo_structure)
    if not files:
        errors.append("repo_structure.files must contain at least one file")
        return errors

    file_paths = list(files.keys())
    if "README.md" not in files:
        errors.append("repo_structure.files must include README.md")

    if not any(path.lower().endswith(".md") and path != "README.md" for path in file_paths):
        errors.append("repo_structure.files must include at least one scenario or diagnostic document beyond README.md")

    has_test_file = any(
        path.lower().startswith("tests/") or "/tests/" in path.lower() or Path(path).name.lower().startswith("test_")
        for path in file_paths
    )
    if not has_test_file:
        errors.append("repo_structure.files must include at least one test file")

    source_file_paths = [
        path
        for path in file_paths
        if Path(path).suffix.lower() in {".py", ".js", ".ts", ".tsx", ".jsx", ".sh"}
        and not (
            path.lower().startswith("tests/")
            or "/tests/" in path.lower()
            or Path(path).name.lower().startswith("test_")
        )
    ]
    if not source_file_paths:
        errors.append("repo_structure.files must include executable source files")

    has_python_source = any(Path(path).suffix.lower() == ".py" for path in source_file_paths)
    if has_python_source and "requirements.txt" not in files:
        errors.append("Python task specs must include requirements.txt in repo_structure.files")

    return errors


def _validate_expected_candidate_journey(spec: Dict[str, Any]) -> List[str]:
    journey = spec.get("expected_candidate_journey")
    if not isinstance(journey, dict):
        return ["expected_candidate_journey must be an object"]
    if len(journey) < 3:
        return ["expected_candidate_journey must contain at least 3 phases"]
    errors: List[str] = []
    for phase, steps in journey.items():
        if not isinstance(steps, list) or not steps:
            errors.append(f"expected_candidate_journey phase '{phase}' must be a non-empty list")
    return errors


def _validate_interviewer_signals(spec: Dict[str, Any]) -> List[str]:
    signals = spec.get("interviewer_signals")
    if not isinstance(signals, dict):
        return ["interviewer_signals must be an object"]
    errors: List[str] = []
    for key in ("strong_positive", "red_flags"):
        values = signals.get(key)
        if not isinstance(values, list) or not values:
            errors.append(f"interviewer_signals.{key} must be a non-empty list")
    return errors


def _validate_test_runner(spec: Dict[str, Any]) -> List[str]:
    runner = spec.get("test_runner")
    if not isinstance(runner, dict):
        return ["test_runner must be an object"]

    errors: List[str] = []
    for key in TEST_RUNNER_KEYS:
        if key not in runner:
            errors.append(f"test_runner missing field: {key}")
    command = str(runner.get("command") or "").strip()
    if not command:
        errors.append("test_runner.command must be a non-empty string")
    working_dir = str(runner.get("working_dir") or "").strip()
    if not working_dir.startswith("/workspace/"):
        errors.append("test_runner.working_dir must be an absolute /workspace/... path")
    try:
        timeout = int(runner.get("timeout_seconds") or 0)
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("test_runner.timeout_seconds must be a positive integer")
    return errors


def _validate_workspace_bootstrap(spec: Dict[str, Any]) -> List[str]:
    bootstrap = spec.get("workspace_bootstrap")
    if not isinstance(bootstrap, dict):
        return ["workspace_bootstrap must be an object"]

    errors: List[str] = []
    for key in WORKSPACE_BOOTSTRAP_KEYS:
        if key not in bootstrap:
            errors.append(f"workspace_bootstrap missing field: {key}")
    commands = bootstrap.get("commands")
    if not isinstance(commands, list) or not commands or not all(_is_non_empty_string(command) for command in commands):
        errors.append("workspace_bootstrap.commands must be a non-empty list of commands")
    working_dir = str(bootstrap.get("working_dir") or "").strip()
    if not working_dir.startswith("/workspace/"):
        errors.append("workspace_bootstrap.working_dir must be an absolute /workspace/... path")
    try:
        timeout = int(bootstrap.get("timeout_seconds") or 0)
        if timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("workspace_bootstrap.timeout_seconds must be a positive integer")
    if bootstrap.get("must_succeed") is not True:
        errors.append("workspace_bootstrap.must_succeed must be true")
    return errors


def _validate_role_alignment(spec: Dict[str, Any], rubric_dimensions: set[str]) -> List[str]:
    role_alignment = spec.get("role_alignment")
    if not isinstance(role_alignment, dict):
        return ["role_alignment must be an object"]

    errors: List[str] = []
    for key in ROLE_ALIGNMENT_KEYS:
        if key not in role_alignment:
            errors.append(f"role_alignment missing field: {key}")
    for key in ("source_user_email", "source_role_name", "source_role_identifier", "captured_at"):
        if not _is_non_empty_string(role_alignment.get(key)):
            errors.append(f"role_alignment.{key} must be a non-empty string")

    must_cover = role_alignment.get("must_cover")
    if not isinstance(must_cover, list) or not must_cover:
        errors.append("role_alignment.must_cover must be a non-empty list")

    must_not_cover = role_alignment.get("must_not_cover")
    if not isinstance(must_not_cover, list):
        errors.append("role_alignment.must_not_cover must be a list")

    mappings = role_alignment.get("jd_to_signal_map")
    if not isinstance(mappings, list) or not mappings:
        errors.append("role_alignment.jd_to_signal_map must be a non-empty list")
        return errors

    covered_dimensions: set[str] = set()
    for idx, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            errors.append(f"role_alignment.jd_to_signal_map[{idx}] must be an object")
            continue
        for key in ("job_requirement", "task_artifact", "rubric_dimension"):
            if not _is_non_empty_string(mapping.get(key)):
                errors.append(f"role_alignment.jd_to_signal_map[{idx}].{key} must be a non-empty string")
        rubric_dimension = str(mapping.get("rubric_dimension") or "").strip()
        if rubric_dimension:
            covered_dimensions.add(rubric_dimension)

    missing_dimensions = rubric_dimensions - covered_dimensions
    if missing_dimensions:
        errors.append(
            "role_alignment.jd_to_signal_map must cover every rubric dimension; missing "
            + ", ".join(sorted(missing_dimensions))
        )

    return errors


def _validate_human_testing_checklist(spec: Dict[str, Any]) -> List[str]:
    checklist = spec.get("human_testing_checklist")
    if not isinstance(checklist, dict):
        return ["human_testing_checklist must be an object"]
    errors: List[str] = []
    for key in HUMAN_TESTING_KEYS:
        if key not in checklist:
            errors.append(f"human_testing_checklist missing field: {key}")
        elif not isinstance(checklist.get(key), bool):
            errors.append(f"human_testing_checklist.{key} must be a boolean")
    return errors


def _validate_tiers(spec: Dict[str, Any]) -> List[str]:
    """Optional ``tiers`` block — the L1/L2/L3 difficulty ladder. When present it
    MUST declare L1, L2, L3, each with a non-empty label and a numeric
    ``min_tests_ratio`` in [0, 1] that is non-decreasing across tiers, and L3
    must be the design-gated (judgment) tier."""
    errors: List[str] = []
    tiers = spec.get("tiers")
    if tiers is None:
        return errors  # tiers are opt-in per task
    if not isinstance(tiers, dict):
        return ["tiers must be an object"]
    expected = ["L1", "L2", "L3"]
    if set(tiers.keys()) != set(expected):
        errors.append(f"tiers must declare exactly {expected}; got {sorted(tiers.keys())}")
    prev = -1.0
    for tier in expected:
        cfg = tiers.get(tier)
        if not isinstance(cfg, dict):
            errors.append(f"tiers.{tier} must be an object")
            continue
        if not str(cfg.get("label") or "").strip():
            errors.append(f"tiers.{tier}.label is required")
        ratio = cfg.get("min_tests_ratio")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not (0.0 <= float(ratio) <= 1.0):
            errors.append(f"tiers.{tier}.min_tests_ratio must be a number in [0, 1]")
        else:
            if float(ratio) < prev:
                errors.append(f"tiers.{tier}.min_tests_ratio ({ratio}) must be >= the previous tier's")
            prev = float(ratio)
    l3 = tiers.get("L3")
    if isinstance(l3, dict) and not l3.get("requires_design"):
        errors.append("tiers.L3 must set requires_design=true (it is the judgment tier)")
    return errors


def validate_task_spec(
    spec: Dict[str, Any],
    *,
    mode: TaskSpecValidationMode | str = TaskSpecValidationMode.PUBLICATION,
) -> TaskSpecValidationResult:
    validation_mode = _coerce_validation_mode(mode)
    task_id = spec.get("task_id") or "unknown"
    errors: List[str] = []
    for req in REQUIRED_TASK_FIELDS:
        if req not in spec:
            errors.append(f"Missing required field: {req}")

    evaluation_rubric = spec.get("evaluation_rubric")
    if not isinstance(evaluation_rubric, dict):
        errors.append("evaluation_rubric must be an object")
        rubric_dimensions: set[str] = set()
    else:
        rubric_dimensions = set(evaluation_rubric.keys())
        # 4-9 dimensions. The lens model drives the natural count per task.
        # The floor of 4 predates full axis coverage; the ceiling was 7 until
        # every task had to grade all five fluency axes (see
        # validate_fluency_coverage). A task that already carried two decision
        # dims and two deliverable dims needs three more to reach coverage, so
        # 9 is the real ceiling now. It is a ceiling, not a target — each
        # criteria-graded dimension costs one Anthropic call per submission.
        if not (4 <= len(rubric_dimensions) <= 9):
            errors.append(
                f"evaluation_rubric must define 4-9 dimensions; got {len(rubric_dimensions)}"
            )

    errors.extend(validate_rubric_weights(evaluation_rubric))
    errors.extend(
        validate_fluency_coverage(evaluation_rubric, spec.get("fluency_coverage_exemption"))
    )
    errors.extend(_validate_decisions_dim(evaluation_rubric, spec.get("decision_points")))
    errors.extend(validate_decision_points(spec.get("decision_points")))
    errors.extend(validate_traps(spec.get("traps")))
    errors.extend(_validate_repo_structure(spec))
    # Structural validation keeps deliverable optional for LEGACY reads. The
    # PUBLICATION contract below requires it. When present in either mode,
    # cross-check that primary_artifact actually exists in the repo.
    errors.extend(validate_deliverable(
        spec.get("deliverable"),
        _repo_files(spec.get("repo_structure")),
    ))
    errors.extend(_validate_expected_candidate_journey(spec))
    errors.extend(_validate_interviewer_signals(spec))
    errors.extend(_validate_test_runner(spec))
    errors.extend(_validate_workspace_bootstrap(spec))
    errors.extend(_validate_role_alignment(spec, rubric_dimensions))
    errors.extend(_validate_human_testing_checklist(spec))
    errors.extend(_validate_tiers(spec))
    if validation_mode is TaskSpecValidationMode.PUBLICATION:
        errors.extend(validate_publication_contract(spec))

    repo_structure = spec.get("repo_structure")
    workspace_bootstrap = spec.get("workspace_bootstrap")
    test_runner = spec.get("test_runner")
    repo_name = str(repo_structure.get("name") or "").strip() if isinstance(repo_structure, dict) else ""
    bootstrap_working_dir = (
        str(workspace_bootstrap.get("working_dir") or "").strip()
        if isinstance(workspace_bootstrap, dict)
        else ""
    )
    test_working_dir = (
        str(test_runner.get("working_dir") or "").strip()
        if isinstance(test_runner, dict)
        else ""
    )
    if repo_name:
        expected_suffix = f"/{repo_name}"
        if bootstrap_working_dir and not bootstrap_working_dir.endswith(expected_suffix):
            errors.append("workspace_bootstrap.working_dir must end with repo_structure.name")
        if test_working_dir and not test_working_dir.endswith(expected_suffix):
            errors.append("test_runner.working_dir must end with repo_structure.name")

    return TaskSpecValidationResult(task_id=task_id, valid=len(errors) == 0, errors=errors)


def load_task_specs(
    tasks_dir: str | Path,
    *,
    validation_mode: TaskSpecValidationMode | str = TaskSpecValidationMode.PUBLICATION,
) -> List[Dict[str, Any]]:
    mode = _coerce_validation_mode(validation_mode)
    root = Path(tasks_dir or canonical_task_catalog_dir())
    specs: List[Dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for path in sorted(root.glob("*.json")):
        with path.open("r", encoding="utf-8") as f:
            data = _normalize_task_spec(json.load(f))
        result = validate_task_spec(data, mode=mode)
        if not result.valid:
            joined = "; ".join(result.errors)
            raise ValueError(f"Invalid task spec {path.name}: {joined}")
        if result.task_id in seen_task_ids:
            raise ValueError(f"Invalid task spec {path.name}: Duplicate task_id {result.task_id!r}")
        seen_task_ids.add(result.task_id)
        specs.append(data)
    return specs


def candidate_rubric_view(evaluation_rubric: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Candidate-safe rubric payload: category + weight only (no criteria leakage)."""
    safe: List[Dict[str, Any]] = []
    for category, details in (evaluation_rubric or {}).items():
        try:
            if isinstance(details, (int, float)):
                weight = float(details)
            else:
                weight = float((details or {}).get("weight", 0))
        except (TypeError, ValueError):
            weight = 0.0
        safe.append({"category": category, "weight": weight})
    return safe
