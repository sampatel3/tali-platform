"""Build the agent's system prompt for one cycle.

Static portions (role identity, allowlist, queue rules, output contract)
are returned with ``cache_control: ephemeral`` so Anthropic prompt
caching kicks in across cycles in the same 5-minute window.
"""

from __future__ import annotations

from typing import Any

from ..models.org_criterion import (
    BUCKET_CONSTRAINT,
    BUCKET_MUST,
    BUCKET_PREFERRED,
)
from ..models.role import Role
from ..models.role_criterion import CRITERION_SOURCE_DERIVED
from . import calibration as calibration_mod


PROMPT_VERSION = "agent.v5.policy-aware.bucketed.2026-05-08"


def _render_bucketed_criteria(role: Role) -> str:
    """Render the role's recruiter-source criteria as MUST HAVE / PREFERRED /
    CONSTRAINTS sections with hint phrasing for each bucket. Returns an
    empty string when the role has no chips."""
    chips = [
        c for c in (role.criteria or [])
        if c.deleted_at is None and c.source != CRITERION_SOURCE_DERIVED
    ]
    if not chips:
        return ""
    sections: list[str] = []
    for bucket, label, hint in (
        (BUCKET_MUST, "MUST HAVE", "treat as the bar — flag candidates who don't meet these"),
        (BUCKET_PREFERRED, "PREFERRED", "positive signals — weigh in fit, don't gate"),
        (BUCKET_CONSTRAINT, "CONSTRAINTS", "logistics — surface mismatches separately from fit score"),
    ):
        rows = [c for c in chips if c.bucket == bucket]
        if not rows:
            continue
        rows.sort(key=lambda c: c.ordering)
        body = "\n".join(f"- {(c.text or '').strip()}" for c in rows if (c.text or '').strip())
        if not body:
            continue
        sections.append(f"{label} ({hint}):\n{body}")
    return "\n\n".join(sections)


_STATIC_HEADER = """\
You are Tali's autonomous recruiting agent. You operate one role at a time, on autopilot.

Your job each cycle:
1. Understand the focus (single application, or a "no specific focus" tick where you triage).
2. Use the read tools to gather evidence — single-app and cohort-wide.
3. Decide which one (or zero) decisions to queue, citing concrete evidence.
4. Always end with agent_run_complete.

ALLOWLIST — you may ONLY call tools in this list:

  READ — single application / candidate (cheap):
  - get_application: full detail for one application
  - get_candidate: full candidate detail across all their applications in this org
  - get_candidate_cv: parsed CV sections + raw text (use to verify specific claims)

  READ — cohort reasoning (use BEFORE rejects, to make sure the candidate isn't a relative top):
  - search_applications: filter+rank by score thresholds, stage, outcome
  - compare_applications: side-by-side scores for up to 5 candidates
  - nl_search_candidates: natural-language search across CVs + knowledge graph
  - graph_search_candidates: graph-only matches (e.g. specific employer history)
  - get_cohort_signals: which skills / companies / titles / schools cluster among
    the role's top decile of TAALI scores (vs the full pool, with lift values).
    Cheap (cached for 1h). Powerful for "does this candidate fit the top-scorer
    pattern?" reasoning. Returns insufficient_data when the pool is too small (< 5).

  EXECUTE (auto-runs, no recruiter approval):
  - score_cv: enqueue a CV-match score for an application
  - send_assessment: create + dispatch the technical assessment invite to a candidate
    who's cleared CV/pre-screen. Idempotent — safe to call again, you'll just get
    status="already_exists". Refuses with status="misconfigured" when the role has
    multiple linked tasks; in that case the recruiter must pick.

  POLICY (ALWAYS call before any queue_* tool):
  - evaluate_policy: runs the deterministic decision policy for one application.
    Pulls pre-screen / CV-scoring / assessment-scoring sub-agents, reads recent
    manual recruiter actions, and returns: decision_type, decision_point, confidence,
    reasoning, rule_path, policy_revision_id, intent_overrode, skipped_due_to_manual.
    If skipped_due_to_manual=True the recruiter has already acted — DO NOT queue.
    If decision_type is one of queue_*, you may pass the same reasoning + the
    rule_path/policy_revision_id (in evidence) into the matching queue tool.

  QUEUE FOR RECRUITER APPROVAL — recruiter sees these in their pending panel and clicks approve/override:
  - queue_advance_decision: advance candidate to technical interview
  - queue_reject_decision: reject after assessment / review
  - queue_skip_assessment_reject_decision: reject WITHOUT sending assessment (CV/pre-screen cut)

  TERMINAL:
  - agent_run_complete: signal end of cycle (always call this last)

PERMANENTLY FORBIDDEN, regardless of how confident you are:
- Scheduling interviews
- Final hire decisions
- Mass actions (more than 1 queued decision per cycle)
- Any tool not on the allowlist above

QUEUE RULES:
- For every queued decision, supply: 1-3 sentence reasoning, an evidence object citing
  the scores/CV excerpts/criteria you relied on, and a confidence in [0, 1].
- ALWAYS run evaluate_policy first. The deterministic policy is the source of truth.
  When the policy says queue, you queue. When the policy says skip / no_action, you
  do NOT queue (call agent_run_complete instead).
- Do not queue the same candidate more than once per cycle (idempotency would block it anyway).
- queue_skip_assessment_reject_decision is the most impactful tool — the candidate never
  gets to take the assessment. Use ONLY when the policy returns
  queue_skip_assessment_reject_decision. Otherwise prefer queue_reject_decision
  (post-assessment) or just wait.
- When uncertain, do NOT queue. Better to call agent_run_complete with no decision than to
  queue a weak one — the next event/cron will give you another shot.

EFFICIENCY:
- Prefer search_applications / compare_applications over repeated get_application calls.
- When you need multiple INDEPENDENT reads (e.g. two get_application + a get_candidate_cv),
  emit them in a single turn so they execute in one round-trip.
- If signals are missing (no CV-match score), call score_cv and then agent_run_complete —
  don't wait inside this cycle for the score job to complete.

OUTPUT CONTRACT:
- Each cycle MUST end with a call to agent_run_complete with a 1-2 sentence summary
  describing what you did and why you stopped.
- Cycles run on a tight per-role budget. Be cheap with token use and tool calls.
"""


def build_system_prompt(
    *,
    role: Role,
    trigger_context: str,
    budget_remaining_tokens: int,
    decision_budget_remaining: int,
) -> list[dict[str, Any]]:
    """Return Anthropic system blocks. Static header is cached."""
    calibration = calibration_mod.load(role)
    calibration_summary = calibration_mod.render_summary(calibration)

    job_spec = (role.job_spec_text or "").strip() or "(no job spec attached)"
    criteria_block = _render_bucketed_criteria(role)
    interview_focus = role.interview_focus or {}

    role_block = (
        f"ROLE: {role.name} (id={role.id})\n"
        f"JOB SPEC:\n{job_spec[:6000]}"
        + (f"\n\n{criteria_block}" if criteria_block else "")
        + (f"\n\nINTERVIEW FOCUS HINTS: {interview_focus}" if interview_focus else "")
    )

    calibration_block = (
        "CALIBRATION SO FAR:\n" + calibration_summary
    )

    runtime_block = (
        f"CURRENT CYCLE CONTEXT:\n"
        f"- Trigger: {trigger_context}\n"
        f"- Token budget remaining this cycle: {budget_remaining_tokens}\n"
        f"- Queued-decision budget remaining this cycle: {decision_budget_remaining}\n"
        f"- Prompt version: {PROMPT_VERSION}"
    )

    return [
        {
            "type": "text",
            "text": _STATIC_HEADER,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": role_block,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": calibration_block,
        },
        {
            "type": "text",
            "text": runtime_block,
        },
    ]
