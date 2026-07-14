# Agent decision matrix — autonomy, approvals, and spend

This is the operator-facing contract for a role after a requisition is
published. The normal flow is: create/publish the requisition, accept or edit
the monthly role cap, and click **Turn on** once. That persisted command owns
assessment generation, automated repair and validation, exact-task approval,
production readiness, activation, and the first cohort pass. Celery Beat owns
recovery after the browser closes and the ongoing proactive sweeps.

## Controls and activation defaults

| Control | Stored field | Column default | Effective behavior |
| --- | --- | --- | --- |
| **Agent** | `role.agentic_mode_enabled` | off | Turns autonomous role management on and materializes the visible effective policy. It never changes a saved role-specific choice. |
| **Send assessment** | `role.auto_send_assessment` | workspace default (platform: on) | Sends an on-policy assessment invite while enabled, unpaused, funded, and within contact safeguards. |
| **Resend assessment** | `role.auto_resend_assessment` | workspace default (platform: on) | Resends an eligible invite while enabled, unpaused, funded, and within resend safeguards. |
| **Advance to interview** | `role.auto_advance` | workspace default (platform: on) | Advances an on-policy candidate locally and, when configured, into the organization's Workable interview stage. |
| **Legacy Auto-promote aggregate** | `role.auto_promote` | derived | Compatibility value only; the three action-level controls above are authoritative. |
| **Auto-reject** | `role.auto_reject` | off | Allows the separate **deterministic pre-screen** path to reject automatically when its provider/policy safeguards pass. It does not bypass human confirmation for LLM, full-score, or assessment reject recommendations. |
| **Auto-reject pre-screen only** | `role.auto_reject_pre_screen` | off | Narrow explicit opt-in to the same deterministic pre-screen auto-reject path. Full-score and assessment rejection still require confirmation. |
| **Auto-skip assessment** | `role.auto_skip_assessment` | off | Bypasses assessment and translates a positive send verdict into advance-to-interview. With auto-promote on, an on-policy advance may execute automatically. |

The database defaults describe an inactive role. A new role copies workspace
defaults once; later workspace edits do not silently alter it. An untouched
workspace grants only reversible positive automation and keeps deterministic
rejection and assessment skipping off. Turning the role on persists that exact
effective policy.

## Lifecycle states

| State | Meaning | What happens next |
| --- | --- | --- |
| **Off** | `agentic_mode_enabled=false` | No agent cohort cycle runs. Deterministic pre-screen review cards may still be recovered by the free catch-up sweep. |
| **Turn-on queued** | disabled with `activation_intent.status=pending/retry_wait` | The durable command continues generation, validation, readiness, and activation automatically. The browser is not part of the workflow. |
| **Turn-on needs input** | disabled with `activation_intent.status=blocked` | Automated task repair was exhausted or the persisted job input is structurally unusable. Correct the reported input (or explicitly skip assessment) and click Turn on again. |
| **Starting** | enabled, `agent_bootstrap_status=starting` | The activation/resume handoff has been accepted and the first complete cohort pass is queued. |
| **On / ready** | enabled, unpaused, bootstrap ready | New application events can wake work immediately; the hourly cohort sweep is the recovery/proactive backstop. |
| **Paused** | enabled, `agent_paused_at` set | Paid scoring/reasoning and automatic positive actions stop. Existing Decision Hub cards remain actionable. |
| **Failed startup** | bootstrap failed and role auto-paused | The broker/worker or first pass exhausted its immediate retry budget. The system recovery sweep resumes it automatically after the reported dependency is healthy. |

A pause reason is material:

- **Monthly cap reached:** raise the cap or wait for a new month; system recovery
  resumes automatically after the full readiness check passes.
- **Usage credits exhausted:** top up credits; system recovery resumes
  automatically after the full readiness check passes.
- **Bootstrap/runtime failure:** restore worker/runtime health; system recovery
  retries without a recruiter click.
- **Paused by recruiter:** never clears automatically; the recruiter must
  explicitly resume it.

## Decision and approval boundary

| Candidate state / recommendation | Auto-promote on | Auto-reject on | Human confirmation? |
| --- | --- | --- | --- |
| Deterministic pre-screen failure, no full score | n/a | May disqualify automatically when the explicit role toggle and provider/policy safeguards pass; otherwise queues a card | Only when automatic pre-screen execution is not eligible or not enabled |
| Send assessment / resend invite | Executes when its action-level toggle is on and the role is enabled, unpaused, on-policy and within budget/credit/contact guards | n/a | When its toggle is off or a guard holds |
| Advance to interview | Executes when `auto_advance` is on and the role is enabled, unpaused and on-policy | n/a | When the toggle is off, role is paused/off, policy is ambiguous/off-policy, or the candidate is already post-handover |
| LLM-authored reject recommendation | n/a | Toggle records reject intent but cannot bypass the reject rail | **Always** |
| Deterministic full-score reject recommendation | n/a | Cannot bypass the reject rail | **Always** |
| Assessment-stage reject recommendation | n/a | Cannot bypass the reject rail | **Always** |
| Low-confidence / ambiguous / policy escalation | Never auto-executes | Never auto-executes | **Always** |

“Advance to interview” above is always the Taali pipeline transition. For a
Workable-linked candidate, configure the organization-level
`interview_stage_name` once; autonomous advances then write to that external
stage. Without a configured target, the local transition succeeds and the
external move remains a safe human handoff. Invite delivery and its configured
assessment-stage/note handoff remain automatic.

Reject and `skip_assessment_reject` are irreversible candidate-facing outcomes:
they can disqualify in the ATS and trigger rejection communications. The agent
may create the recommendation and evidence, but the side effect remains pending
until a recruiter confirms it. The only auto-reject exception is the separate,
deterministic pre-screen gate under its explicit role toggle.

## What runs and what is metered

| Work | Cadence / trigger | Metered? | Spend/credit behavior |
| --- | --- | --- | --- |
| Complete agent cohort pass | Immediately on activation/resume; hourly Beat sweep as backstop | Yes when it calls Anthropic | Role monthly cap and organization credit ledger both apply |
| CV parse, pre-screen, full scoring | Application/event driven plus cohort recovery | Yes, except deterministic fraud checks/cache hits | Reservations fail closed when funded credits are unavailable; an enabled role is paused on depletion |
| Assessment authoring, repair, and grading | Turn on / funnel driven | Yes | A fresh hard hold is admitted before each Anthropic call; actual usage settles the hold and failures release it |
| Candidate graph projection | Durable event outboxes | Yes when Anthropic/Voyage is called | Role-attributed provider calls use hard holds and retry without dropping the episode |
| Deterministic decision policy | When a score lands and during cohort reconciliation | No LLM | Free; produces/dispatches the policy verdict under the autonomy rules above |
| Pre-screen reject catch-up | Every 30 minutes | No LLM | Free; recovers already-screened below-threshold candidates, including on off/paused roles |
| Decision execution | Event driven | No LLM itself | Free, but candidate-facing guards and the human-confirm reject rail still apply |

Every automatic Anthropic or Voyage call with role context is admitted against
both organization credits and the role cap before the provider is called, then
recorded in `usage_events` and settled to actual usage. `claude_call_logs`,
trace metadata, and stale-hold recovery provide the reconciliation/backstop
rails. The role's monthly cap covers parsing, pre-screening, scoring,
assessments, graph projection, and autonomous reasoning—not only the top-level
agent cycle. E2B and Resend delivery costs are exposed as operational estimates
and durable execution/delivery state; they are not debited as AI usage credits.
GitHub repository work is readiness-verified and state-tracked, but has no
per-request price ledger in this codebase.

## Production Turn-on readiness

Production activation fails closed unless all dependencies needed by that
role's actual funnel are ready:

- fresh Beat-to-worker canaries for both `celery` and `scoring` queues;
- `USAGE_METER_LIVE=true` on the API and workers;
- a valid shared `ANTHROPIC_API_KEY` and enough credits for one conservative
  funnel pass;
- native public apply enabled for a requisition without an ATS job;
- one unambiguous active task, or one generated draft whose battle test has
  passed and can be activated atomically by Turn on—or an explicit
  `auto_skip_assessment=true` choice;
- when assessment is used: E2B, Resend, and a real GitHub token with mock mode
  disabled on the relevant worker.

If any check fails, the durable Turn-on command records the actionable reason
and leaves the role off. Transient readiness and broker failures retry from the
server-side command; structurally unusable input or exhausted automated repair
becomes an explicit needs-input state instead of waiting forever.

## Manual controls

`Process candidates` and `Sync from Workable` are recovery controls. They are
not required in the healthy requisition → Turn on path. Necessary human steps
are intentionally limited to the Turn-on budget authority; irreversible reject
confirmation; ambiguous/off-policy exceptions; interview, offer, and hire
judgement; selecting the external Workable interview stage when applicable;
correcting genuinely unusable requisition input or exhausted task repair; and
restoring external funding/credentials. After a funding or runtime repair, the
system resumes itself. The Turn-on click itself authorizes the single
battle-tested generated assessment; it is not a separate manual step.

Applicant arrival is an external event, not an operator workflow step: Turn on
lists the native requisition on the organization's careers board and the agent
continuously processes native or Workable applications as they arrive. It does
not scrape third-party talent networks or send unsolicited outreach without a
lawful, supplied audience and the campaign-level human approval already
required by the outreach subsystem. Interviews, offers, and hiring decisions
likewise remain human judgement rather than simulated automation.
