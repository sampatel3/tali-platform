# Agent decision matrix — what runs, what it costs, who approves

A reference for the three role-level toggles and how they interact with the
pipeline stages. Source of truth is the code; key files are cited inline.

## The three toggles

| Toggle | Field | Default | What it controls |
| --- | --- | --- | --- |
| **Agent** | `role.agentic_mode_enabled` (+ `agent_paused_at`) | off | Whether the role is under autonomous management — the 30-min cohort tick that auto-enqueues scoring, runs the reasoning cycle, and keeps the pre-screen reject queue aligned. |
| **Auto-reject** | `role.auto_reject` | off | Whether a below-threshold reject **executes immediately** or waits for you in the Decision Hub. |
| **Auto-promote** | `role.auto_promote` | off | Whether *send assessment* / *advance to interview* **execute immediately** or wait for you in the Decision Hub. |

`agent_paused_at` is a third agent state: **auto-paused** (agent is *on* but the monthly $ cap was hit). For everything automated it behaves like **off** — the cohort tick skips paused roles (`agent_tasks.py:241`, `:292`).

## Pipeline stages — cost & determinism

| Stage | What it does | LLM? (model) | Costs money? | Deterministic? | Blocked by budget cap? |
| --- | --- | --- | --- | --- | --- |
| **Fraud / JD-copy gate** | plagiarism + verbatim-JD detection inside pre-screen | No | **Free** | **Yes** | No |
| **Pre-screen score** | cheap fit filter, stage 1 of scoring (`Feature.PRESCREEN`, Haiku) | Yes (Haiku) | **Yes — low** | No | **Yes** (via `enqueue_score` → `can_spend_on_role`, `cv_score_orchestrator.py:302`) |
| **Pre-screen reject verdict** | "below threshold → reject" decision (`evaluate_auto_reject_decision`) | No | **Free** | **Yes** | No |
| **CV-match (v3)** | full role-fit score (`Feature.SCORE`) | Yes | **Yes — high** | No | **Yes** |
| **Assessment generate + grade** | task build + grading (`Feature.ASSESSMENT`) | Yes | **Yes** | No | Yes |
| **Agent reasoning cycle** | survey cohort, plan, call tools (`Feature.AGENT_AUTONOMOUS`, Sonnet) | Yes | **Yes** | No | **Yes** — pauses the role at the cap |
| **Decision Hub card / Workable disqualify / advance** | queue or execute a verdict | No | **Free** | **Yes** | No |

**Takeaway:** every *scoring/reasoning* stage costs money and is budget-gated. Every *decision/execution* stage (reject card, disqualify, advance) is deterministic and free — it should never be blocked by the budget cap. (Today the reject sweep *is* wrongly coupled to the cap — that's the bug fixed alongside this doc.)

## What runs automatically, by agent state

| Behavior | Agent **ON** | Agent **PAUSED** (budget) | Agent **OFF** |
| --- | --- | --- | --- |
| Auto-enqueue scoring (pre-screen + CV-match) | Yes — 30-min tick, budget-gated | No | No |
| Agent reasoning cycle (LLM) | Yes | No | No |
| Pre-screen reject queue maintained | Yes (cohort tick) | No¹ | No (queue is agent-gated) |
| Manual **"Process candidates"** | Yes | Yes | Yes |
| Reject at pre-screen short-circuit (score-time) | Yes¹ | only if scoring runs² | No |

¹ Fixed by this PR — the pre-screen reject now fires at score-time and the deterministic reject sweep runs even on budget-paused roles.
² When paused/over-budget, `enqueue_score` is blocked, so no new candidate reaches the pre-screen stage at all. The *backlog* already pre-screened is cleared by the sweep.

When the agent is **off**, nothing is automated: candidates sit unscored until you click **Process candidates**, and no pre-screen reject cards are created (`queue_pre_screen_reject` and `reconcile_*` both no-op for non-agentic roles).

## Below-threshold candidate — what happens to the reject

The reject only applies to candidates **without** a full CV-match score yet — once `cv_match_score` is set, the verdict defers to the agent's CV-match flow (`auto_reject.py:87`).

| Agent | Auto-reject | Outcome for a below-threshold (pre-screen) candidate |
| --- | --- | --- |
| ON / paused | **ON** | Reject **executes**: Workable disqualify when linked + connected, else a Decision Hub card (`run_auto_reject_if_needed`). Free. No human approval. |
| ON / paused | **OFF** | A **Decision Hub `skip_assessment_reject` card** is queued for your one-click approval (`queue_pre_screen_reject`). Free. |
| OFF | either | **Nothing** — the role isn't agent-managed, so no card and no disqualify (unless org-level legacy Workable auto-disqualify is configured). |

## Passing candidate — send assessment / advance

These are driven by the agent reasoning cycle, so they only happen when the **agent is ON** (and scored candidates exist).

| Auto-promote | `send_assessment` / `advance_to_interview` |
| --- | --- |
| **ON** | Executes immediately as a system action (`tool_registry.py:999`, `:1118`). |
| **OFF** | Queues a Decision Hub card (`send_assessment` / `advance` / `resend_assessment_invite`) for your approval. |

Auto-reject and auto-promote are independent: auto-reject governs the *bottom* of the funnel (culling), auto-promote the *top* (advancing). With both **off**, every candidate-affecting action lands in the Decision Hub; with both **on**, the agent runs the funnel end-to-end and only escalates genuine ambiguity.

## The eight permutations at a glance

`Agent → Auto-reject → Auto-promote`

| # | Agent | A-reject | A-promote | Net behavior |
| --- | --- | --- | --- | --- |
| 1 | off | – | – | Manual only. Score via "Process candidates"; every decision is yours. No automation. |
| 2 | on | off | off | Agent scores + recommends; **every** reject/send/advance is a Decision Hub card. (Max oversight.) |
| 3 | on | **on** | off | Below-threshold auto-culled; sends/advances still need your approval. |
| 4 | on | off | **on** | Sends/advances auto-fire; rejects wait for you. |
| 5 | on | **on** | **on** | Fully autonomous funnel; only ambiguous cases escalate. |
| 6 | paused | (as set) | (as set) | Same intent as its ON row, but **no scoring/LLM** runs until resumed; the free reject sweep still culls the already-screened backlog. |
| 7 | off | on | on | Toggles are stored but **inert** — nothing runs without the agent. |
| 8 | on | on/off | on/off | (covered by 2–5) |

> Money is only ever spent in rows where the **agent is ON and not paused** (scoring + reasoning). Rejecting, carding, disqualifying, and advancing are always free and deterministic.
