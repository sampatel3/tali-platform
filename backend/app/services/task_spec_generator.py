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
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from ..platform.database import SessionLocal
from ..services.metered_anthropic_client import MeteredAnthropicClient
from ..services.pricing_service import (
    Feature,
    credits_charged,
    raw_cost_usd_micro,
)
from ..services.task_spec_loader import validate_task_spec
from ..services.usage_credit_reservations import (
    CreditReservation,
    InsufficientRoleBudgetError,
    release_credit_reservation,
    reserve_credits,
)
from ..services.usage_metering_service import InsufficientCreditsError

logger = logging.getLogger("taali.task_spec_generator")

_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
# A full spec (scenario + rubric criteria + decision_points + a starter
# repo_structure with several files + role_alignment) easily exceeds 8K
# output tokens; truncation produces unparseable JSON. Sonnet 4.5 supports
# large output — give it room.
_MAX_TOKENS = 20000
_DEFAULT_MAX_ATTEMPTS = 3
# Hard-hold enough for the configured 20K output ceiling plus a conservative
# 60K-token input context (system contract + JD + prior invalid spec/repairs).
# The hold is model-priced and reconciled to actual usage after every attempt.
_RESERVATION_INPUT_TOKENS = 60_000


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
- evaluation_rubric: 7-9 dimensions. LENS MODEL — weights MUST sum to 1.0.
    The rubric MUST grade all FIVE fluency axes (Delegation, Description,
    Discernment, Diligence, Deliverable). A rubric that only reaches Delegation
    and Deliverable is REJECTED by the spec validator.
    * decision lens total ≈ 0.40 (→ Delegation), including EXACTLY ONE dimension named
      "design_decisions_articulated" with {"weight": 0.25-0.28, "grader": "interrogation_outcome"}
      (NO criteria, NO lens — it's graded deterministically from decision_points)
      plus 1-2 more dims with {"weight", "lens": "decision", "criteria": {excellent,good,poor}}
      that grade the candidate's reasoning/diagnosis from the transcript.
    * deliverable lens total ≈ 0.30 (→ Deliverable): 1-3 dims with
      {"weight", "lens": "deliverable", "criteria": {...}} that grade the SHIPPED
      ARTIFACT on its merits. Criteria MUST say to credit good output regardless
      of who typed it, and that nothing shipped = poor.
    * EXACTLY these three, verbatim ids, 0.10 each — they cover the remaining axes:
      - "output_scrutiny" {"weight": 0.10, "lens": "discernment", "criteria": {...}}
        → did they critically evaluate the agent's output and override what was
        wrong? Write criteria around THIS scenario's real failure modes.
      - "verification_before_done" {"weight": 0.10, "lens": "diligence", "criteria": {...}}
        → did they verify (run the tests / check the artifact against the brief)
        before claiming done?
      - "ai_native_practice" {"weight": 0.10, "grader": "practice_outcome",
        "part": "applied", "fluency": "description"} (NO criteria, NO lens —
        graded deterministically from the repo + process trace)
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
- evaluation_rubric MUST include output_scrutiny, verification_before_done and
  ai_native_practice so all five fluency axes are graded.
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


def _revision_prompt(prior_spec: Dict[str, Any], feedback: str) -> str:
    """Seed message that hands the model the current draft + the recruiter's
    structured feedback and asks for a complete, contract-valid re-author."""
    return (
        "A recruiter reviewed the draft task below and asked for changes.\n\n"
        f"CURRENT DRAFT (JSON):\n{json.dumps(prior_spec)[:9000]}\n\n"
        f"RECRUITER FEEDBACK:\n{feedback.strip()}\n\n"
        "Re-author the COMPLETE task-spec JSON: keep everything the feedback "
        "did NOT criticise, and change exactly what it asks for. Keep the same "
        "task_id. Satisfy the full contract + every HARD RULE. JSON only, no prose."
    )


def _make_client(
    api_key: str,
    organization_id: int,
    role_slug: str,
    role_id: Optional[int] = None,
    trace_id: Optional[str] = None,
):
    """Build the metered client + metering payload shared by generate/revise."""
    resolved_trace = str(trace_id or f"task-spec-{uuid.uuid4().hex}")
    client = MeteredAnthropicClient(
        inner=Anthropic(api_key=api_key),
        organization_id=int(organization_id),
    )
    metering = {
        "feature": "assessment",
        "organization_id": int(organization_id),
        "trace_id": resolved_trace,
        "metadata": {
            "sub_feature": "task_spec_generation",
            "role_slug": role_slug,
            "trace_id": resolved_trace,
        },
    }
    if role_id is not None:
        metering["role_id"] = int(role_id)
        metering["entity_id"] = f"role:{int(role_id)}"
    else:
        metering["entity_id"] = f"role-slug:{role_slug}"
    return client, metering


def _task_spec_reservation_amount(model: str) -> int:
    """Conservative maximum charge for one configured generation attempt."""
    raw = raw_cost_usd_micro(
        input_tokens=_RESERVATION_INPUT_TOKENS,
        output_tokens=_MAX_TOKENS,
        model=model,
    )
    return credits_charged(
        feature=Feature.ASSESSMENT,
        cost_usd_micro=raw,
        cache_hit=False,
    )


def _reserve_generation_attempt(
    *,
    metering: Dict[str, Any],
    model: str,
    attempt: int,
) -> CreditReservation:
    trace_id = str(metering["trace_id"])
    external_ref = (
        f"usage-reservation:task-spec:{trace_id}:attempt:{attempt}:"
        f"{uuid.uuid4().hex[:12]}"
    )
    with SessionLocal() as meter_db:
        reservation = reserve_credits(
            meter_db,
            organization_id=int(metering["organization_id"]),
            feature=Feature.ASSESSMENT,
            external_ref=external_ref,
            amount=_task_spec_reservation_amount(model),
            metadata={
                "sub_feature": "task_spec_generation",
                "role_id": metering.get("role_id"),
                "entity_id": metering.get("entity_id"),
                "trace_id": trace_id,
                "attempt": int(attempt),
            },
            role_id=(
                int(metering["role_id"])
                if metering.get("role_id") is not None
                else None
            ),
            enforce_role_budget=metering.get("role_id") is not None,
        )
        meter_db.commit()
        return reservation


def _release_generation_attempt(
    reservation: CreditReservation, *, reason: str
) -> None:
    """Best-effort/idempotent compensation when no model usage was returned."""
    try:
        with SessionLocal() as meter_db:
            release_credit_reservation(
                meter_db,
                reservation=reservation,
                reason=reason,
            )
            meter_db.commit()
    except Exception:
        # Conservatively leave the durable hold in place. Its trace metadata is
        # recoverable, while an optimistic refund could double-credit the org.
        logger.exception(
            "task_spec failed to release credit reservation ref=%s",
            reservation.external_ref,
        )


def _run_generation_loop(
    *,
    client: MeteredAnthropicClient,
    chosen_model: str,
    metering: Dict[str, Any],
    messages: List[Dict[str, Any]],
    role_slug: str,
    max_attempts: int,
) -> GeneratedSpecResult:
    """Bounded generate→validate→repair loop over seed ``messages``.

    Shared by ``generate_task_spec`` (fresh authoring) and ``revise_task_spec``
    (re-author from a prior spec + recruiter feedback). Mutates ``messages``
    with repair turns. Never raises on a model/validation problem.
    """
    best: Optional[Dict[str, Any]] = None
    best_errors: List[str] = ["generation did not produce parseable JSON"]

    for attempt in range(1, max_attempts + 1):
        try:
            reservation = _reserve_generation_attempt(
                metering=metering,
                model=chosen_model,
                attempt=attempt,
            )
        except InsufficientCreditsError as exc:
            logger.info(
                "task_spec generation blocked before provider call role=%s: %s",
                role_slug,
                exc,
            )
            return GeneratedSpecResult(
                spec=best,
                valid=False,
                errors=[f"insufficient usage credits: {exc}"],
                attempts=attempt - 1,
                model_used=chosen_model,
            )
        except InsufficientRoleBudgetError as exc:
            logger.info(
                "task_spec generation blocked by role cap before provider "
                "call role=%s: %s",
                role_slug,
                exc,
            )
            return GeneratedSpecResult(
                spec=best,
                valid=False,
                errors=[f"insufficient role monthly budget: {exc}"],
                attempts=attempt - 1,
                model_used=chosen_model,
            )
        except Exception as exc:  # fail closed when the hard hold cannot land
            logger.exception(
                "task_spec reservation failed before provider call role=%s",
                role_slug,
            )
            return GeneratedSpecResult(
                spec=best,
                valid=False,
                errors=[f"usage reservation failed: {exc}"],
                attempts=attempt - 1,
                model_used=chosen_model,
            )

        attempt_metering = {
            **metering,
            "retry_attempt": attempt - 1,
            "credit_reservation": reservation.as_metering_payload(),
            "metadata": {
                **dict(metering.get("metadata") or {}),
                "attempt": attempt,
                "reservation_ref": reservation.external_ref,
            },
        }
        try:
            resp = client.messages.create(
                model=chosen_model,
                max_tokens=_MAX_TOKENS,
                temperature=0.3,
                system=_SYSTEM_PROMPT,
                messages=messages,
                metering=attempt_metering,
            )
            raw = resp.content[0].text if resp.content else ""
        except Exception as exc:  # noqa: BLE001 — resilience boundary
            # The real wrapper also compensates SDK errors centrally. Keeping
            # this idempotent fallback covers injected/mocked clients and any
            # failure before the wrapper takes ownership of the request.
            _release_generation_attempt(
                reservation,
                reason=f"generation_call_failed:{type(exc).__name__}",
            )
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


def generate_task_spec(
    *,
    role_name: str,
    role_slug: str,
    jd_text: str,
    api_key: str,
    organization_id: int,
    role_id: Optional[int] = None,
    deliverable_kind_hint: Optional[str] = None,
    model: Optional[str] = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    trace_id: Optional[str] = None,
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
    client, metering = _make_client(
        api_key,
        organization_id,
        role_slug,
        role_id=role_id,
        trace_id=trace_id,
    )
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": _user_prompt(role_name, role_slug, jd_text, deliverable_kind_hint)}
    ]
    return _run_generation_loop(
        client=client, chosen_model=chosen_model, metering=metering,
        messages=messages, role_slug=role_slug, max_attempts=max_attempts,
    )


def revise_task_spec(
    *,
    prior_spec: Dict[str, Any],
    feedback: str,
    role_name: str,
    role_slug: str,
    jd_text: str,
    api_key: str,
    organization_id: int,
    role_id: Optional[int] = None,
    model: Optional[str] = None,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    trace_id: Optional[str] = None,
) -> GeneratedSpecResult:
    """Re-author a draft spec from recruiter feedback.

    Seeds the loop with the original authoring turn, the prior spec (as the
    assistant's prior output), and a revision instruction carrying the
    recruiter's structured feedback — so the model edits in place rather than
    starting cold, then runs the same validate→repair loop. One metered call
    per attempt (cheap, opt-in: the recruiter asked for the revision).

    Never raises on a model/validation problem — only on a missing api_key.
    """
    if not api_key:
        raise ValueError("api_key is required")
    chosen_model = (model or "").strip() or _DEFAULT_MODEL
    client, metering = _make_client(
        api_key,
        organization_id,
        role_slug,
        role_id=role_id,
        trace_id=trace_id,
    )
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": _user_prompt(role_name, role_slug, jd_text, None)},
        {"role": "assistant", "content": json.dumps(prior_spec)[:8000]},
        {"role": "user", "content": _revision_prompt(prior_spec, feedback)},
    ]
    return _run_generation_loop(
        client=client, chosen_model=chosen_model, metering=metering,
        messages=messages, role_slug=role_slug, max_attempts=max_attempts,
    )


__all__ = ["GeneratedSpecResult", "generate_task_spec", "revise_task_spec"]
