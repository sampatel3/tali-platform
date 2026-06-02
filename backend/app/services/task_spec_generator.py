"""JD → task-spec generator.

Authors a complete, validated assessment task spec from a role + its job
description. This is the autogen pipeline the schema-first work was built
toward: the lens rubric, ``decision_points``, and ``deliverable`` schemas
are all declarative, so an LLM can emit a whole task and the runtime
consumes it with no per-task code.

Pipeline
--------
1. Build a generation prompt embedding the FULL spec contract (the lens
   rubric rules, decision_points schema, repo_structure requirements,
   test_runner, role_alignment) + the role's JD.
2. Call Sonnet (metered) → a candidate spec JSON.
3. Validate via ``task_spec_loader.validate_task_spec``. On errors, feed
   them back and re-generate (bounded repair loop) until valid or the
   retry budget is exhausted.
4. Return the validated spec dict (caller persists it as a Task +
   provisions the template repo; see the auto-assign path).

Design philosophy the generator is told to follow (the 7-lever framework):
real production scenario, embedded load-bearing decisions, a required
deliverable, brief ambiguity, and a rubric that grades JUDGMENT
(decision lens) over raw output (deliverable lens) — never delegation
penalised on the deliverable.

Metering: routes through ``MeteredAnthropicClient`` with
``sub_feature=task_spec_generation`` (platform invariant).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from ..services.metered_anthropic_client import MeteredAnthropicClient
from ..services.task_spec_loader import validate_task_spec

logger = logging.getLogger("taali.task_spec_generator")

_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
# A full spec (scenario + rubric criteria + decision_points + a starter
# repo_structure with several files + role_alignment) easily exceeds 8K
# output tokens; truncation produces unparseable JSON. Sonnet 4.5 supports
# large output — give it room.
_MAX_TOKENS = 20000
_DEFAULT_MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# The generation contract — embedded in the system prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = r"""You author technical-assessment task specs for an agent-native hiring platform.

The candidate works WITH an AI coding agent in a live workspace. The task
measures HOW THEY STEER + REASON, not whether they personally type code.
Follow the 7-lever design framework: a real production scenario (an
incident, a decision under pressure), embedded load-bearing decisions the
candidate must own, brief ambiguity, a required deliverable, and a rubric
that grades JUDGMENT over raw output.

Emit ONE JSON object — the complete task spec — and NOTHING else (no
markdown fences, no prose). It MUST satisfy this contract exactly:

TOP-LEVEL REQUIRED KEYS:
- task_id: snake_case slug, unique, derived from the role (e.g. "secops_vuln_triage_recovery")
- name: human title
- role: role slug (snake_case, e.g. "security_engineer")
- duration_minutes: 30
- calibration_prompt: one sentence the candidate answers to warm up
- scenario: 2-4 paragraph production scenario with an embedded manager
  message that names the decisions + the required deliverable. Make it
  concrete and role-specific to the JD.
- deliverable: {"kind": "code"|"doc", "primary_artifact": "<filename in repo>", "submission_check": "test_runner"}
    Use "code" for engineering roles (primary_artifact a source file),
    "doc" for non-coding roles like PM/security-governance/scrum
    (primary_artifact a .md the candidate writes, e.g. DECISION_MEMO.md).
- decision_points: 2-3 load-bearing decisions. Each:
    {"id": snake_case, "headline": short, "tension": one sentence why it's hard,
     "options": [{"label","summary"}, ... ≥2], "ask": the question forcing a commit,
     "valid_commit": what a substantive commit looks like,
     "valid_reframes": [1-2 senior reframes that also count as engaging],
     "anti_patterns": [2-3 dodge patterns to push back on]}
- evaluation_rubric: 4-6 dimensions. LENS MODEL — weights MUST sum to 1.0:
    * decision lens total ≈ 0.60, including EXACTLY ONE dimension named
      "design_decisions_articulated" with {"weight": 0.35-0.40, "grader": "interrogation_outcome"}
      (NO criteria, NO lens — it's graded deterministically from decision_points)
      plus 1-2 more dims with {"weight", "lens": "decision", "criteria": {excellent,good,poor}}
      that grade the candidate's reasoning/diagnosis from the transcript.
    * deliverable lens total ≈ 0.40: 1-3 dims with {"weight", "lens": "deliverable", "criteria": {...}}
      that grade the SHIPPED ARTIFACT on its merits. Criteria MUST say to
      credit good output regardless of who typed it, and that nothing
      shipped = poor.
    Decision-lens criteria punish lazy delegation; deliverable-lens criteria do NOT.
- expected_candidate_journey: object with ≥3 phases, each a non-empty list of steps.
- interviewer_signals: {"strong_positive": [...], "red_flags": [...]} both non-empty.
- scoring_hints: object (calibration notes; can include common_failure_modes list).
- test_runner: {"command": "./.venv/bin/python -m pytest -q --tb=short",
    "working_dir": "/workspace/<repo_structure.name>",
    "parse_pattern": "(?P<passed>\\d+) passed|(?P<failed>\\d+) failed",
    "timeout_seconds": 90}
- workspace_bootstrap: {"commands": ["python3 -m venv .venv", "./.venv/bin/pip install -r requirements.txt"],
    "working_dir": "/workspace/<repo_structure.name>", "timeout_seconds": 180, "must_succeed": true}
- repo_structure: {"name": kebab-case repo name, "files": { "<path>": "<contents>", ... }}
    MUST include: README.md; at least one OTHER .md (a scenario/diagnostic/brief doc);
    at least one test file (tests/test_*.py); for code tasks at least one
    executable source file (.py) with stubs the candidate fixes;
    requirements.txt (pytest). For doc tasks: the primary_artifact .md (a
    template with the required section headings), input brief .md files, a
    light helper .py, and tests/ that check the doc has the required
    sections (coverage, not correctness). The baseline tests MUST
    meaningfully FAIL on the starter repo.
- role_alignment: {"source_user_email": "generated@taali.ai",
    "source_role_name": <role name>, "source_role_identifier": <role slug>,
    "captured_at": "2026-01-01T00:00:00Z", "must_cover": [≥1 strings],
    "must_not_cover": [strings], "jd_to_signal_map": [ one entry PER rubric
    dimension: {"job_requirement","task_artifact","rubric_dimension"} — the
    rubric_dimension values MUST exactly cover every evaluation_rubric key ]}
- human_testing_checklist: {"candidate_clarity": true, "repo_boot_ok": true,
    "tests_collect_ok": true, "baseline_failures_meaningful": true,
    "rubric_matches_role": true, "timebox_realistic": true}

HARD RULES:
- evaluation_rubric weights sum to EXACTLY 1.0.
- jd_to_signal_map covers EVERY rubric dimension (one entry each).
- deliverable.primary_artifact MUST be a key in repo_structure.files.
- deliverable.kind MUST match primary_artifact: "doc" ⇒ a .md file the
  candidate writes; "code" ⇒ a source file (.py/.js/...). Never code+.md.
- test_runner.working_dir and workspace_bootstrap.working_dir end with "/<repo_structure.name>".
- Output VALID JSON only. No trailing commas. No comments. No markdown."""


@dataclass
class GeneratedSpecResult:
    """Outcome of a generation run."""

    spec: Optional[Dict[str, Any]]
    valid: bool
    errors: List[str] = field(default_factory=list)
    attempts: int = 0
    model_used: str = _DEFAULT_MODEL


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from the model output, tolerant of stray fences."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0].strip()
    # Grab the outermost {...} if there's leading/trailing prose.
    if not s.startswith("{"):
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            s = m.group(0)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _user_prompt(role_name: str, role_slug: str, jd_text: str, kind_hint: Optional[str]) -> str:
    kind_line = (
        f"\nThe role is non-coding — prefer deliverable.kind=\"doc\"."
        if kind_hint == "doc"
        else (
            "\nThe role is an engineering role — prefer deliverable.kind=\"code\"."
            if kind_hint == "code"
            else ""
        )
    )
    return (
        f"Author a task spec for this role.\n\n"
        f"Role name: {role_name}\n"
        f"Role slug: {role_slug}\n"
        f"{kind_line}\n\n"
        f"Job description:\n{(jd_text or '').strip()[:6000]}\n\n"
        "Emit the complete task-spec JSON now. JSON only."
    )


def _repair_prompt(errors: List[str]) -> str:
    joined = "\n".join(f"- {e}" for e in errors[:25])
    return (
        "The spec you produced failed validation with these errors:\n"
        f"{joined}\n\n"
        "Fix EVERY error and re-emit the COMPLETE corrected task-spec JSON. "
        "JSON only, no prose."
    )


def generate_task_spec(
    *,
    role_name: str,
    role_slug: str,
    jd_text: str,
    api_key: str,
    organization_id: int,
    deliverable_kind_hint: Optional[str] = None,
    model: Optional[str] = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> GeneratedSpecResult:
    """Generate a validated task spec from a role + JD.

    Runs a bounded generation→validate→repair loop. Returns the validated
    spec on success, or the best (still-invalid) attempt + its errors on
    exhaustion so the caller can surface them for human authoring.

    Never raises on a model/validation problem — only on a missing api_key.
    """
    if not api_key:
        raise ValueError("api_key is required")
    chosen_model = (model or "").strip() or _DEFAULT_MODEL
    client = MeteredAnthropicClient(
        inner=Anthropic(api_key=api_key),
        organization_id=int(organization_id),
    )
    metering = {
        "feature": "assessment",
        "organization_id": int(organization_id),
        "metadata": {"sub_feature": "task_spec_generation", "role_slug": role_slug},
    }

    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": _user_prompt(role_name, role_slug, jd_text, deliverable_kind_hint)}
    ]
    best: Optional[Dict[str, Any]] = None
    best_errors: List[str] = ["generation did not produce parseable JSON"]

    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.messages.create(
                model=chosen_model,
                max_tokens=_MAX_TOKENS,
                temperature=0.3,
                system=_SYSTEM_PROMPT,
                messages=messages,
                metering=metering,
            )
            raw = resp.content[0].text if resp.content else ""
        except Exception as exc:  # noqa: BLE001 — resilience boundary
            logger.warning("task_spec generation call failed (attempt %d): %s", attempt, exc)
            return GeneratedSpecResult(
                spec=best, valid=False,
                errors=[f"generation call failed: {exc}"],
                attempts=attempt, model_used=chosen_model,
            )

        spec = _extract_json(raw)
        if spec is None:
            best_errors = ["model output was not valid JSON"]
            messages += [
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content": "That was not valid JSON. Re-emit the COMPLETE task-spec as a single JSON object. JSON only."},
            ]
            continue

        result = validate_task_spec(spec)
        if result.valid:
            logger.info(
                "task_spec generated for role=%s in %d attempt(s)", role_slug, attempt
            )
            return GeneratedSpecResult(
                spec=spec, valid=True, errors=[], attempts=attempt, model_used=chosen_model
            )

        best, best_errors = spec, list(result.errors)
        logger.info(
            "task_spec attempt %d invalid for role=%s: %d error(s)",
            attempt, role_slug, len(result.errors),
        )
        if attempt < max_attempts:
            messages += [
                {"role": "assistant", "content": json.dumps(spec)[:4000]},
                {"role": "user", "content": _repair_prompt(result.errors)},
            ]

    return GeneratedSpecResult(
        spec=best, valid=False, errors=best_errors,
        attempts=max_attempts, model_used=chosen_model,
    )


__all__ = ["GeneratedSpecResult", "generate_task_spec"]
