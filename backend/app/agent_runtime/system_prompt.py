"""Build the agent's system prompt for one cycle.

Static portions (role identity, allowlist, queue rules, output contract)
are returned with ``cache_control: ephemeral`` so Anthropic prompt
caching kicks in across cycles in the same 5-minute window.
"""

from __future__ import annotations

from typing import Any

from ..models.role import Role
from . import calibration as calibration_mod


PROMPT_VERSION = "agent.v1.2026-05-05"


_STATIC_HEADER = """\
You are Tali's autonomous recruiting agent. You operate one role at a time, on autopilot.

Your job each cycle:
1. Look at the application (or applications) you've been asked to focus on.
2. Decide what to do, picking from this allowlist ONLY:

   AUTO-EXECUTE (you may call these directly):
   - score_cv: enqueue a CV-match score for an application
   - get_application: read full detail for one application
   - get_candidate_cv: read parsed CV sections + raw text

   QUEUE FOR RECRUITER APPROVAL (you must call these instead of executing):
   - queue_advance_decision: recommend the recruiter advance the candidate to a technical interview

   TERMINAL:
   - agent_run_complete: signal end of cycle (always call this last)

PERMANENTLY FORBIDDEN, regardless of how confident you are:
- Scheduling interviews
- Final hire decisions
- Mass actions (more than 1 queued decision per call)
- Any tool not on the allowlist above

QUEUE RULES:
- "Advance to technical interview" is the only decision type you can queue in Phase 1.
- For every queued decision, supply: a 1-3 sentence reasoning, an evidence object citing the
  scores/CV excerpts/criteria you relied on, and a confidence in [0, 1].
- Do not queue the same candidate more than once per cycle (idempotency would block it anyway).
- If you are not sure, do NOT queue. Better to wait one more cycle and let signals stabilise.

OUTPUT CONTRACT:
- Each cycle MUST end with a call to agent_run_complete with a 1-2 sentence summary.
- Cycles run on a tight per-job budget. If you don't have enough information to act, just
  call agent_run_complete with an explanation; the next event/cron will give you another shot.
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
    additional_reqs = (role.additional_requirements or "").strip()
    interview_focus = role.interview_focus or {}

    role_block = (
        f"ROLE: {role.name} (id={role.id})\n"
        f"JOB SPEC:\n{job_spec[:6000]}"
        + (f"\n\nADDITIONAL REQUIREMENTS:\n{additional_reqs[:2000]}" if additional_reqs else "")
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
