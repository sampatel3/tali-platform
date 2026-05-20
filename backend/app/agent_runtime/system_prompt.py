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


PROMPT_VERSION = "agent.v8.cohort-planner.bucketed.advanced-bucket.2026-05-12"


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

Each cycle, your job is to look at the role as a whole, decide where the
leverage is, and act. You decide the work; nobody is firing you on a
per-application event. You are responsible for moving the role forward.

THE LOOP — survey, reason, act:

1. SURVEY
   Always start with TWO calls together (one round-trip):
     • survey_role_state — counts of applications in each cohort state
       plus role-config gaps (missing budget/threshold/must-haves) plus
       the list of recruiter questions that are already open or recently
       resolved.
     • read_pending_recruiter_inputs — full bodies of those questions.
   The survey is your map. Read it before doing anything else.

2. REASON
   From the survey, decide where to spend this cycle's budget:
     • role-config gaps the recruiter must close (missing must_have, no
       monthly cap, no threshold) → ask_recruiter ONCE per gap (idempotent
       on (role_id, kind)). Do not invent new questions when an open one
       already covers it; do not ask things you can derive yourself.
     • cheap deterministic work the cohort needs (apps in needs_pre_screen
       or needs_score) → batch the work via batch_score_cv.
     • candidates ready_for_assessment_decision → run evaluate_policy and,
       if it queues a verdict, queue the matching decision. send_assessment
       respects the role's HITL toggle automatically.
     • candidates ready_for_advance_decision → same path: evaluate_policy
       → queue_advance_decision or queue_reject_decision.
   Skip work the recruiter has already done manually (the policy
   short-circuits via manual-action skip). Skip work that's blocked on
   an open recruiter question.

3. ACT
   Per-cycle caps (the runtime enforces decision_budget too):
     - ONE send_assessment or queue_advance_decision per cycle (high risk,
       candidate-facing emails / Workable stage moves).
     - Up to FIVE reject decisions per cycle combined
       (queue_reject_decision + queue_skip_assessment_reject_decision).
       Recruiter reviews them as a batch — easy to override individually.
     - Auto-execute tools (batch_score_cv) can do many in one call;
       scores are cheap.
   End every cycle with agent_run_complete summarising what you changed
   and what's blocking next progress.

ALLOWLIST — you may ONLY call tools in this list:

  COHORT SURVEY (call FIRST every cycle):
  - survey_role_state: cohort counts + role config gaps + open questions
  - find_apps_in_state: get up to N application_ids in one cohort state
  - read_pending_recruiter_inputs: open + recently-answered recruiter questions

  READ — single application / candidate (cheap; only when surveys aren't enough):
  - get_application, get_candidate, get_candidate_cv

  READ — cohort reasoning (cohort_signals before rejects):
  - search_applications, compare_applications, nl_search_candidates,
    graph_search_candidates, get_cohort_signals

  AUTO-EXECUTE (deterministic; no recruiter approval):
  - score_cv: enqueue CV-match scoring for one application
  - batch_score_cv: same for up to 25 applications in one call

  CANDIDATE-FACING SEND (HITL-gated when role.auto_promote=False):
  - send_assessment: dispatch the assessment invite. When auto_promote
    is False the tool queues an AgentDecision(decision_type='send_assessment')
    and returns status="awaiting_recruiter_approval"; the recruiter
    approves on the Home Review queue and the approve path dispatches
    the invite. When auto_promote=True the invite fires immediately.
  - resend_assessment_invite: same shape, decision_type='resend_assessment_invite'.

  ASK RECRUITER (third lane — when you genuinely need input):
  - ask_recruiter: open a recruiter-facing question on the role page.
    Idempotent on (role_id, kind) — re-calling refreshes the existing
    open card. Always pair with read_pending_recruiter_inputs first.

  POLICY (ALWAYS call before any queue_* tool):
  - evaluate_policy: deterministic verdict for one application. Returns
    decision_type, rule_path, policy_revision_id, intent_overrode,
    skipped_due_to_manual. If skipped_due_to_manual=True, do NOT queue.

  QUEUE FOR RECRUITER APPROVAL:
  - queue_advance_decision, queue_reject_decision,
    queue_skip_assessment_reject_decision

  TERMINAL:
  - agent_run_complete: signal end of cycle (always call this last)

PERMANENTLY FORBIDDEN, regardless of confidence:
- Scheduling interviews
- Final hire decisions
- More than 1 send_assessment / queue_advance_decision per cycle
- More than 5 reject decisions per cycle combined
- Any tool not on the allowlist above

QUEUE RULES:
- For every queued decision, supply: 1-3 sentence reasoning, an evidence
  object citing the scores/CV excerpts/criteria you relied on, and a
  confidence in [0, 1].
- ALWAYS run evaluate_policy first. When the policy says queue, you queue.
  When the policy says skip / no_action, you do NOT queue.
- queue_skip_assessment_reject_decision is the most impactful tool — use
  ONLY when the policy returns it.
- When uncertain, do NOT queue. The next cycle will give you another shot.

EXTERNAL PIPELINE STAGE (workable_stage) AND TALI'S `advanced` STAGE:
- Applications carry `workable_stage` — the candidate's stage in the
  recruiter's external ATS (Workable). Values like "phone_screen",
  "interview", "technical_interview", "offer" mean a human recruiter has
  already advanced this person past initial screening.
- When any of those post-handover Workable stages are detected, Tali
  automatically moves `pipeline_stage` to `advanced` — Tali's terminal
  bucket for "past Tali's flow, now in the recruiter's hands".
- A candidate in `pipeline_stage="advanced"` is past Tali's responsibility.
  Do NOT queue advance/reject/skip decisions for them. Skip them in your
  cohort survey; they only return to the active pipeline if a recruiter
  manually moves them back.
- For candidates still in earlier Tali stages with a post-handover
  `workable_stage`, weight that heavily — they've been vetted by a human;
  do NOT queue a reject on score alone. Prefer advance or no-action.
- "sourced" / "applied" / null carries no extra signal — score as normal.

ASK_RECRUITER RULES:
- Ask only when the answer materially unblocks work. "What's the must-have
  for this role?" yes; "what colour should the email be?" no.
- One open card per kind. read_pending_recruiter_inputs FIRST.
- Provide options[] when the answer is finite (approve/skip, advance/reject).

EFFICIENCY:
- ALWAYS pair survey_role_state + read_pending_recruiter_inputs in one
  turn so they ship in one round-trip.
- Prefer batch tools over individual ones when you have an id list.
- Cycles run on a tight per-role budget. Be cheap with tool calls.

OUTPUT CONTRACT:
- Each cycle MUST end with agent_run_complete and a 1-2 sentence summary
  of what changed and what's blocking the next step.
"""


def _render_role_intent(role: Role) -> str:
    """Render the active RoleIntent (Amendment A1) for inclusion in the
    system prompt. Returns empty string when no intent has been authored
    for the role — sub-agents see an empty overlay and the prompt is
    indistinguishable from the pre-A1 shape.

    Read once per cycle and cached by Anthropic's ephemeral prompt cache
    along with the role block — so the per-tool-round cost stays at
    cache-hit pricing for everything after the first call.
    """
    try:
        from .role_intent import fetch_active_intent
        from ..platform.database import SessionLocal
        with SessionLocal() as db:
            record = fetch_active_intent(db, role_id=int(role.id))
    except Exception:  # pragma: no cover — defensive
        return ""
    if record is None:
        return ""
    s = record.structured
    lines: list[str] = [
        f"ROLE INTENT (v{record.version}, authored {record.authored_at.date()}):",
    ]
    if s.soft_signals:
        lines.append(f"- Soft signals: {', '.join(s.soft_signals)}")
    if s.deal_breakers:
        lines.append(f"- Deal-breakers: {', '.join(s.deal_breakers)}")
    if s.growth_expectations:
        lines.append(f"- Growth: {s.growth_expectations}")
    if s.context_for_opening:
        lines.append(f"- Context: {s.context_for_opening}")
    if s.weighting_notes:
        lines.append(f"- Weighting: {s.weighting_notes}")
    if s.must_haves_missing_from_spec:
        lines.append(
            f"- Must-haves not in spec: {', '.join(s.must_haves_missing_from_spec)}"
        )
    if record.free_text:
        # Cap the free-text section so a verbose author doesn't blow up
        # token usage on every cycle.
        lines.append(f"- Notes: {record.free_text[:1200]}")
    return "\n".join(lines)


def build_system_prompt(
    *,
    role: Role,
    trigger_context: str,
) -> list[dict[str, Any]]:
    """Return Anthropic system blocks. Static header is cached."""
    calibration = calibration_mod.load(role)
    calibration_summary = calibration_mod.render_summary(calibration)

    job_spec = (role.job_spec_text or "").strip() or "(no job spec attached)"
    criteria_block = _render_bucketed_criteria(role)
    interview_focus = role.interview_focus or {}
    intent_block = _render_role_intent(role)

    role_block = (
        f"ROLE: {role.name} (id={role.id})\n"
        f"JOB SPEC:\n{job_spec[:6000]}"
        + (f"\n\n{criteria_block}" if criteria_block else "")
        + (f"\n\nINTERVIEW FOCUS HINTS: {interview_focus}" if interview_focus else "")
        + (f"\n\n{intent_block}" if intent_block else "")
    )

    calibration_block = (
        "CALIBRATION SO FAR:\n" + calibration_summary
    )

    runtime_block = (
        f"CURRENT CYCLE CONTEXT:\n"
        f"- Trigger: {trigger_context}\n"
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
