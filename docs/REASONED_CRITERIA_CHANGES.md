# Reasoned criteria changes — design & implementation plan

> Status: **draft for review** (2026‑06‑03). Owner: agent platform.
> Goal: a constraint / must‑have / preferred edit should be a **reasoned, minimal‑cost
> operation**, not a blanket full re‑score.

## 1. The problem

Today, any edit to a `must` or `constraint` criterion via the chat (or the role
settings) calls `mark_role_scores_stale` → `sweep_stale_scores`, which **re‑runs
the full LLM scorer over the entire pool**. Concrete incident (role 116, "Senior
Specialist – SOC Threat Intelligence"): a single chat edit — *"Salary expectation
≤ 25,000 AED"* — triggered **159 prescreen + 1,157 score calls ≈ $7**, and discarded
125 pending reject decisions, all while every agent was paused. The recruiter
changed one number and the system re‑read 278 whole CVs.

Two layers of waste:
1. **Whole‑pool**: it re‑scores everyone, including candidates the change can't
   possibly affect.
2. **Whole‑CV**: it re‑runs the entire scoring prompt (every criterion + the full
   CV), not just the one criterion that changed.

The agent does **no reasoning** about what the change means before spending.

## 2. Principle

A criteria edit should make the agent **think before it spends**:

> Reason about *intent* → scope the *genuinely‑affected* candidates → re‑decide
> using what we already wrote down (re‑read a CV only as a last resort) → show the
> plan + cost → act on opt‑in.

Worked examples (the recruiter's mental model):
- **Widening** — `Based in UAE → Based in MENA`: the new set ⊇ the old. *Why* a
  candidate failed UAE decides it — India still fails MENA, Saudi now passes. So
  re‑decide only the previously‑failing, **using the reason we recorded** (their
  location), not a re‑read.
- **Narrowing** — `western company → western enterprise`: new ⊆ old. The
  previously‑failing still fail; re‑decide only the previously‑passing.
- **Rewording** — `enterprize → enterprise` (typo) is a **no‑op**; `company →
  enterprise` (meaning shift) is a scoped re‑decide. The agent judges which.
- **Salary** — frequently **unverified** (questionnaire unanswered). Unverified
  can't be filtered; a cap change only acts on the candidates who stated a figure.
  The agent says so: *"22 of 278 stated a figure; the rest are unverified."*

## 3. Current state (what we have to build on)

- `candidate_application.pre_screen_evidence` (JSON) stores a free‑text **`summary`**
  (mentions salary/constraints in prose, e.g. *"salary expectation unverified"*),
  `matching_skills` / `missing_skills`, and a score. The structured
  **`requirements_assessment` array is empty for 100% of candidates** — the scorer
  *computes* per‑requirement met/missing/unknown + evidence (`cv_matching/prompts.py`)
  and then we **discard it**.
- `app/agent_chat/constraints.py` → `_trigger_rescreen` → full‑pool sweep. No intent,
  no scoping.
- `app/agent_chat/impact.py` already does instant, no‑LLM **threshold** re‑filter +
  decision‑queue reconcile (`apply_threshold`, retract/reconcile) — the template for
  the cheap path.
- **Search** lives in `app/domains/taali_chat/` (a candidate‑search agent over the
  Graphiti/GraphRAG vector layer — Neo4j + Voyage) + an MCP server at
  `app/mcp/server.py`. The role‑agent (`app/agent_chat/`) and the search‑agent have
  **separate toolsets** today.

## 4. Design

### 4.1 Enabler — persist per‑criterion reasoning + value (the foundation)
Stop discarding what the scorer already produces. For each candidate, persist a
per‑criterion record:

```
requirements_assessment: [
  { criterion_key, status: met|missing|unknown,
    value: "<extracted, e.g. 'India' | '30000 AED/mo' | null>",
    confidence: 0..1, evidence_quote, verified: bool }
]
```

`value` + `verified` are the new, important bits — they're what lets a later change
be re‑decided **without re‑reading the CV**. `verified=false` (e.g. salary inferred
or absent) is first‑class: it drives the "can't filter, here's what I can do"
behaviour. No schema change (it's JSON on the existing column); needs a scorer‑output
change + a backfill strategy (below).

### 4.2 Intent classification (the "critical thinking" step)
A small reasoning call: given `(old_text, new_text, bucket, role_context)`, return:
- `class`: `cosmetic | widening | narrowing | lateral | new`
- `affected_rule`: which prior population to re‑decide (`failed | passed | borderline | all | none`)
- `rationale` (shown to the recruiter)

Cheap, deterministic‑ish, and the single place the "judgement" lives.

### 4.3 Scoping — find the genuinely‑affected
From the classification + the stored per‑criterion records, select the candidate
subset to re‑decide. Use the **shared search tools** (4.6) where a semantic query is
the natural way to scope ("candidates currently based in MENA"). Output: a (usually
small) candidate set + a per‑candidate hint of whether their stored `value` already
answers the new criterion.

### 4.4 Minimal re‑decide (the action)
For each affected candidate, in cheapness order:
1. **Re‑decide over stored reasoning** — the stored `value` answers the new
   criterion (Saudi ∈ MENA → flip to met). **No LLM.**
2. **Targeted re‑judge** — the stored reasoning is insufficient (we recorded "not
   UAE" without the country); re‑evaluate **only this criterion** against the CV +
   Workable data. One small call, not the full score.
3. Update the candidate's assessment + recommendation, then **reuse the existing
   retract/reconcile** (`impact.py`) to re‑card the decision queue. Same machinery
   as a threshold change.

A full re‑score is never the default; it's only the fallback when the criterion is
genuinely new and CV‑derived.

### 4.5 Opt‑in + cost estimate (the guard) — ships first
Before any spend, the agent presents the plan: classification, affected count, how
many need a CV re‑read × per‑call cost = **estimated $**, and the verified/unverified
split. The recruiter confirms. Under every version of the design this is needed; it
stops surprise spend **today** even before the smart scoping lands (initially the
estimate is "whole pool" until 4.1–4.4 narrow it).

### 4.6 Shared candidate‑search toolset
Unify the role‑agent and search‑agent toolsets so the role‑agent can semantically
search the pool (Graphiti/GraphRAG + the MCP tools) for scoping and for richer
recruiter questions ("who's in MENA?", "who stated a salary?"). Extract a shared
`candidate_search` tool module both `agent_chat` and `taali_chat` register.

### 4.7 Salary / unverified handling
Salary is the canonical `verified=false` case. The agent uses a stored *view* where
one exists (inferred from seniority, flagged as inferred), never filters on
unverified, and is transparent about the split.

## 5. Phased plan

| Phase | Deliverable | Key files | Risk |
|---|---|---|---|
| **P0 — Opt‑in guard** | Constraint edit no longer auto‑re‑screens; agent shows affected count (initially whole‑pool) + cost estimate + asks; on yes, runs the current sweep. Stops the bleed now. | `agent_chat/constraints.py` (`trigger_rescreen=False` by default + estimate), `tools.py`, `system_prompt.py` | low |
| **P1 — Persist reasoning** | Scorer emits + we store `requirements_assessment` with `value`/`verified`; backfill job for the active pool. | `cv_matching/prompts*.py` (emit), `cv_score_orchestrator.py` (persist), `candidate_application` (no migration — JSON), a backfill task | med |
| **P2 — Intent + scoping** | `criteria_change_planner` (the reasoning step) + scoping over stored records. Returns a plan object. | new `agent_chat/criteria_planner.py`, `constraints.py` | med |
| **P3 — Minimal re‑decide** | Re‑decide over stored reasoning; targeted single‑criterion re‑judge fallback; reconcile via `impact.py`. | new `agent_chat/redecide.py`, reuse `impact.py` | med‑high |
| **P4 — Shared search** | Extract shared `candidate_search` tools (Graphiti/MCP); role‑agent registers them. | new `agent_chat/search_tools.py`, `taali_chat/tool_registry.py`, `mcp/server.py` | med |
| **P5 — Polish + generalise** | Plan/impact card UI, cost transparency, metrics (spend per criteria edit before/after), then lift the pattern into mainspring's generic agent layer. | frontend `agentchat/cards.jsx`, mainspring `governance/`+`accelerator/` | med |

Each phase ships behind a per‑org flag and is independently valuable. P0 first.

## 6. Open questions (the judgement calls to settle)
1. **Trust vs re‑confirm** when scoping "previously‑failed": trust the stored
   `value`, or have the agent re‑confirm the boundary cases? (cheaper vs safer)
2. **Reword boldness**: how confident before treating an edit as `cosmetic` (no‑op)?
   A wrong call silently mis‑screens. Lean conservative (only obvious typos).
3. **Backfill**: re‑judge the whole active pool once to populate `requirements_assessment`
   (a one‑time cost), or populate lazily as candidates are next touched?
4. **Auto vs opt‑in past P0**: once scoping makes most changes cheap (<$0.50), do
   small re‑decides auto‑run, with opt‑in only above a $ threshold?
5. **Search scope**: does the role‑agent get the *full* search toolset or a
   read‑only subset?

## 7. Testing & rollout
- Unit: intent classifier (the worked examples as fixtures), scoping over synthetic
  stored records, the cheap‑path re‑decide (no LLM), the reconcile.
- Shadow: run the planner over real recent edits, log "would‑have spent $X vs actual
  $Y" — prove the saving before enabling execution.
- Metric: **$ per criteria edit** (target: salary‑cap change → ~$0).
- Flag‑gated per org; P0 on first.

## 8. Mainspring
This is the conversational‑agent layer being generalised into mainspring (Phase A,
branch `sam/mainspring-agent-layer`). The reasoned‑change engine (intent → scope →
minimal re‑decide) is brand‑blind and belongs in `governance/` (the reasoning +
reconcile) + `accelerator/` (the tools), alongside the impact engine already there.
Build in Taali first, then lift.
