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


PROMPT_VERSION = "agent.v12.agent-driven-sourcing-hiring-axis.2026-07-14"


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
     • role-config gaps: derive role intent from the job spec and captured
       criteria. Treat gaps as diagnostics, not a human task list. Setup and
       connector authorization blockers are surfaced by deterministic product
       checks outside this model loop.
     • cheap deterministic work the cohort needs (apps in needs_pre_screen
       or needs_score) → batch the work via batch_score_cv.
     • candidates ready_for_assessment_decision → run evaluate_policy and,
       if it queues a verdict, queue the matching decision. send_assessment
       respects the role's HITL toggle automatically.
     • candidates ready_for_advance_decision → same path: evaluate_policy
       → queue_advance_decision or queue_reject_decision.
     • sourced_uncontacted > 0 and no generating/ready/sending agent campaign
       → call prepare_sourced_outreach with an empty candidate_ids list. Draft
       generation is autonomous; sending still waits for one campaign HITL.
     • when the active evaluation funnel is thin, there is no sourced backlog,
       and no active agent campaign → build one role-grounded query from the
       title, MUST HAVEs and location; call rediscover_candidates, then pass
       the returned candidate_ids to prepare_sourced_outreach. Never ask a
       recruiter to select profiles or copy/paste LinkedIn data.
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
  - rediscover_candidates: role-budgeted search across the organization's
    prior talent pool. LinkedIn RSC is partner-gated one-click export, not a
    Taali-controlled search API. RSC+ may delegate sourcing to LinkedIn Hiring
    Assistant, but that agent is LinkedIn-owned and is not callable here.

  AUTO-EXECUTE (deterministic; no recruiter approval):
  - score_cv: enqueue CV-match scoring for one application
  - batch_score_cv: same for up to 25 applications in one call
  - prepare_sourced_outreach: source eligible rediscovered candidates, build
    the audience, and enqueue drafts. One call per cycle. It can NEVER send.

  CANDIDATE-FACING SEND (HITL-gated by the matching granular role policy):
  - send_assessment: dispatch the assessment invite. When auto_send_assessment
    is False the tool queues an AgentDecision(decision_type='send_assessment')
    and returns status="awaiting_recruiter_approval"; the recruiter
    approves on the Home Review queue and the approve path dispatches
    the invite. When auto_send_assessment=True the invite fires immediately.
    When the role has no assessment task OR auto_skip_assessment=True,
    the tool redirects to an advance_to_interview decision instead —
    don't fight the redirect, it's the recruiter's configuration.
  - resend_assessment_invite: same shape, governed independently by
    auto_resend_assessment, decision_type='resend_assessment_invite'.

  SETUP SUPPORT:
  - ask_recruiter is not part of the default autonomous tool surface. Missing
    job data, spend authorization, assessment setup, and connector setup are
    surfaced deterministically. Do not manufacture routine clarification work.

  MEMORY (use to carry context across cycles):
  - record_observation: persist a short note (<200 chars) onto
    role.agent_calibration.notes. Notes are rendered into the NEXT
    cycle's system prompt. Capped at 10 entries (FIFO). Use this when
    you notice a cohort pattern, a blocker you can't resolve this cycle,
    or a todo for next cycle. Commits immediately — survives aborts.

  POLICY (ALWAYS call before any queue_* tool):
  - evaluate_policy: deterministic verdict for one application. Returns
    decision_type, rule_path, policy_revision_id, intent_overrode,
    skipped_due_to_manual. If skipped_due_to_manual=True, do NOT queue.

  QUEUE FOR RECRUITER APPROVAL:
  - queue_advance_decision, queue_reject_decision,
    queue_skip_assessment_reject_decision
  - A reject (queue_reject_decision / queue_skip_assessment_reject_decision)
    is IRREVERSIBLE and ALWAYS waits for a recruiter's one-click confirm —
    it is never auto-executed, even on roles configured to auto-act. Queue
    the recommendation with cited evidence; the recruiter confirms.

  TERMINAL:
  - agent_run_complete: signal end of cycle (always call this last)

PERMANENTLY FORBIDDEN, regardless of confidence:
- Choosing interview, offer, or hire outcomes (human/external ATS decisions)
- Claiming logistics were scheduled without an authorized integration result
- More than 1 send_assessment / queue_advance_decision per cycle
- More than 5 reject decisions per cycle combined
- Any tool not on the allowlist above

QUEUE RULES:
- For every queued decision, supply: 1-3 sentence reasoning, an evidence
  object citing the scores/CV excerpts/criteria you relied on, and a
  confidence in [0, 1].
- The reasoning text is shown VERBATIM to the recruiter on the decision
  card. Write it for them, not for yourself: plain English, short
  sentences. Never include internal identifiers (application/candidate
  IDs), raw field names or key=value pairs (write "already at Technical
  Interview in Workable", never "workable_stage=Technical Interview"),
  or scorer keys (write "role fit", "pre-screen", "CV match", never
  role_fit / pre_screen / cv_match). Lead with the recommendation and
  the one or two facts that justify it.
- ALWAYS run evaluate_policy first. When the policy says queue, you queue.
  When the policy says skip / no_action, you do NOT queue.
- queue_skip_assessment_reject_decision is the most impactful tool — use
  ONLY when the policy returns it.
- When uncertain, do NOT queue. The next cycle will give you another shot.

EXTERNAL ATS CONTEXT AND TALI'S `advanced` STAGE:
- Application payloads carry `ats_context` with provider, raw_stage,
  normalized_stage, needs_mapping, post_handover and writeback_linked. Use it
  for native, Workable and Bullhorn; never infer Bullhorn state from a null
  workable_stage.
- `needs_mapping=true` means the Bullhorn status is deliberately unknown. Do
  not queue an irreversible action from that state; surface it for recruiter
  mapping/review.
- A post-handover external stage is a STRONG POSITIVE signal for a candidate
  who is STILL in Tali's funnel: a human recruiter has already advanced them
  (possibly before the application entered Tali). Weight it heavily. You MAY
  still queue a reject when the evidence genuinely warrants it — it is a HITL
  card, never auto-executed, and the recruiter is explicitly warned they are
  rejecting someone already advanced in their ATS. Tali does NOT auto-advance
  based on an external stage; queueing an advance (which the recruiter
  approves) is how such a candidate eventually leaves Tali.
- `pipeline_stage="advanced"` means Tali evaluation was explicitly handed off.
  It is NEVER inferred from an external interview/offer/hire stage and is not
  used to encode rejection. Downstream screening/interview/offer/hire lives on
  the separate provider-neutral hiring-stage axis. Do not queue further Tali
  evaluation decisions after handoff. Authorized ATS/calendar integrations own
  stage sync and logistics; if absent, report integration_required instead of
  asking a recruiter to coordinate the workflow manually.
- `pipeline_stage="sourced"` is outside evaluation: NEVER score it, queue an
  evaluation decision for it, or treat retained CV text as application consent.
  Source and draft autonomously, then wait only for the outbound campaign HITL.
- "applied" / null carries no extra signal — score as normal.

HUMAN-BOUNDARY RULES:
- Human interaction is exceptional: deterministic connector/configuration
  authorization, legally or operationally required HITL, and optional exception
  review only. Never ask humans to search, select profiles, draft, schedule,
  chase status, tune routine thresholds, or coordinate routine work.
- Consequential candidate decisions use the governed decision queue. Outbound
  campaign authorization uses its campaign-level approval snapshot.

EFFICIENCY:
- ALWAYS pair survey_role_state + read_pending_recruiter_inputs in one
  turn so they ship in one round-trip.
- Prefer batch tools over individual ones when you have an id list.
- Cycles run on a tight per-role budget. Be cheap with tool calls.
- If you find yourself about to deliberate the same cohort the next
  cycle will see, record_observation NOW so future-you skips the
  re-derivation.

OUTPUT CONTRACT:
- Each cycle MUST end with agent_run_complete and a 1-2 sentence summary
  of what changed and what's blocking the next step.
- Before agent_run_complete, if anything you learned this cycle would
  help future-you decide faster, call record_observation. The next
  cycle will see your notes in the system prompt's CALIBRATION block.
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


def _render_recruiter_feedback_notes(role: Role) -> str:
    """Render the recent recruiter feedback-note timeline for the role.

    Distinct from ``role_intents``: these are append-only freeform
    observations the recruiter writes when they notice a trend across
    decisions. The agent reads them as standing guidance — alongside
    structured intent, not in place of it. The full history lives in
    Postgres + the role page UI; only the most-recent N are inlined
    here (see ``role_feedback_notes.AGENT_VISIBLE_NOTE_LIMIT``).

    Returns "" when no notes exist so the prompt shape stays stable
    for roles that have never had feedback authored.
    """
    try:
        from .role_feedback_notes import (
            AGENT_VISIBLE_NOTE_BODY_CHARS,
            list_for_agent,
        )
        from ..platform.database import SessionLocal
        with SessionLocal() as db:
            rows = list_for_agent(db, role_id=int(role.id))
    except Exception:  # pragma: no cover — defensive
        return ""
    if not rows:
        return ""
    # Newest first — the recruiter's most recent observation is the
    # one most likely to reflect the current cohort.
    lines: list[str] = [
        "RECRUITER FEEDBACK (newest first — standing guidance the recruiter",
        "wrote about agent behaviour on this role; treat as policy hints):",
    ]
    for row in rows:
        when = row.created_at.date() if row.created_at else "—"
        body = (row.note or "").strip().replace("\n", " ")
        if len(body) > AGENT_VISIBLE_NOTE_BODY_CHARS:
            body = body[:AGENT_VISIBLE_NOTE_BODY_CHARS] + "…"
        lines.append(f"- ({when}) {body}")
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
    feedback_block = _render_recruiter_feedback_notes(role)

    role_block = (
        f"ROLE: {role.name} (id={role.id})\n"
        f"JOB SPEC:\n{job_spec[:6000]}"
        + (f"\n\n{criteria_block}" if criteria_block else "")
        + (f"\n\nINTERVIEW FOCUS HINTS: {interview_focus}" if interview_focus else "")
        + (f"\n\n{intent_block}" if intent_block else "")
        + (f"\n\n{feedback_block}" if feedback_block else "")
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
            # B2: 1h TTL keeps the ~4KB static header reusable across nearby
            # event-triggered/retry cycles. Default ephemeral TTL is 5 min.
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {
            "type": "text",
            "text": role_block,
            # B2: 1h TTL on the role block (job spec + criteria + intent
            # + recruiter notes — up to ~6K tokens). Recomputes only when
            # the role itself changes; recovers cache across ticks.
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
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
