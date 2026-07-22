# Universal AI routing — execution plan

Date: 2026-07-22
Decision: [`ADR 0003`](adr/0003-universal-ai-routing-control-plane-2026-07-22.md)

## Outcome

Tali will have one versioned application control plane that routes every named
AI workflow step to an approved model deployment and transport. Feature code
will state *what work is being done and under which constraints*; it will not
contain raw model selection, provider fallback lists, or pricing assumptions.

The initial release is deterministic and route-compatible with current
production behavior. The feedback loop begins with trustworthy decision and
attempt telemetry; it does not begin by allowing a model or bandit to change
live hiring routes.

## Implementation status

Phases 0–3 are implemented for six routed tasks: search parsing, qualitative
reranking, citation grounding, general recruiter chat, role-agent chat, and
autonomous recruiting. These tasks now use the shared policy, deployment and
transport registries, hard per-attempt admission, durable invocation/attempt
lineage, and centralized retry/fallback/pricing semantics.

This is the control-plane foundation, not a claim that every legacy provider
call has already moved. Candidate assessment, CV ingestion/scoring,
Graphiti/Voyage, Message Batches, the Claude Agent SDK, and the remaining
single-shot calls stay in the Phase 4 compatibility inventory. Architecture
tests freeze that inventory so it can shrink but cannot grow accidentally.

## Current parity matrix

| Workflow | Task | Current selection authority | Initial registered route |
|---|---|---|---|
| General recruiter chat | tool-loop orchestration | `CLAUDE_MODEL` | validated configured deployment; pinned per turn |
| Role-agent chat | tool-loop orchestration | `CLAUDE_MODEL` | validated configured deployment; pinned per turn |
| Autonomous recruiting | plan/tool loop | `role.agent_model`, then `CLAUDE_AGENT_AUTONOMOUS_MODEL`, then `CLAUDE_MODEL` | validated role/task alias, then configured autonomous deployment; pinned per cycle |
| Candidate search | NL query parsing | `CLAUDE_SEARCH_PARSER_MODEL`, default Sonnet 4.6 | Anthropic Sonnet 4.6; structured-output contract |
| Candidate search | qualitative rerank | `FAST_MODEL`, Haiku 4.5 | Anthropic Haiku 4.5 |
| Candidate search | citation grounding | `CLAUDE_GROUNDING_MODEL`, default Sonnet 4.6 | Anthropic Sonnet 4.6; citations required |
| Candidate assessment | agent SDK chat | task JSON, then `CLAUDE_CHAT_MODEL` | validated configured Agent SDK deployment; pinned per turn |
| CV ingestion | live/batch parse | `FAST_MODEL` / `CLAUDE_SCORING_BATCH_MODEL` | separate sync and batch task profiles |
| CV scoring | prescreen / legacy / holistic | Haiku pins / Sonnet 4.6 pin | separate task profiles; no implicit downgrade |
| Candidate graph | Graphiti extraction + Voyage embeddings | Graphiti/Voyage settings | composite route; migrated only with a composite adapter |

## Build sequence

### Phase 0 — inventory and decision record

- [x] Map every provider call, model source, transport, cache, billing path, and
  relevant regression suite.
- [x] Record the architecture decision and fallback invariants.
- [x] Establish the current parity matrix above.
- [x] Add a machine-readable call-site inventory test so new provider calls
  cannot bypass the migration.

### Phase 1 — universal control plane

- [x] Add stable workflow/task enums and versioned task profiles.
- [x] Add an immutable deployment registry with capabilities, lifecycle,
  transport, data policy, limits, pricing identity, and validated aliases.
- [x] Add pure route planning with deterministic reason codes, eligible and
  excluded candidates, ordered attempts, and route stickiness.
- [x] Validate registry closure, workflow acyclicity, bounded depth, exact
  pricing, and replacement/fallback compatibility at startup and in tests.
- [x] Add generic logical-invocation and physical-attempt persistence without
  replacing provider billing tables.
- [x] Add route metadata to existing metering records using the stable route,
  root/parent invocation, task, policy, profile, and registry versions.
- [x] Add architecture gates for raw production model IDs, provider calls, and
  unregistered task keys.

### Phase 2 — Anthropic Messages adapter and candidate search

- [x] Reuse `app.llm.one_call` and `generate_structured` behind an Anthropic
  adapter; do not move model choice into those primitives.
- [x] Migrate search parsing first, preserving deterministic parsing and the
  provider-forbidden guarantee.
- [x] Key the search-parser cache by semantic/schema revision and route behavior
  fingerprint; record cache provenance.
- [x] Migrate qualitative rerank and citation grounding as child tasks of the
  search workflow.
- [x] Keep domain degradation behavior unchanged: keyword-only parser fallback,
  explicit verification errors, and no fabricated negative hiring signal.
- [x] Add fault tests for incompatible fallbacks, provider rejection, ambiguous
  outcomes, and one-reservation-per-attempt behavior.

### Phase 3 — recruiter chat and autonomous workflows

- [x] Plan and persist one orchestration route per chat turn or autonomous
  cycle; keep it sticky across rounds.
- [x] Migrate role-agent chat without changing commit boundaries, circuit
  breakers, tool history, search failure contracts, or durable receipts.
- [x] Migrate general Taali chat and its streaming call. Permit fallback only
  before stream acceptance and never after a delta is emitted.
- [x] Migrate the autonomous loop while preserving live workspace/role authority,
  per-round reservations, token ceilings, and deterministic policy/action gates.
- [x] Attribute nested routed work through child invocation IDs;
  never let a fallback replay a completed tool.
- [x] Resolve legacy per-role model values through validated deployment aliases;
  reject new raw provider IDs.

### Phase 4 — remaining transports and call sites

- [ ] Migrate remaining synchronous structured/single-shot calls.
- [ ] Add a Message Batches adapter with submission/result idempotency and route
  provenance in batch cache keys.
- [ ] Add a Graphiti/Voyage composite adapter with per-request route context;
  remove the process-global model singleton assumption.
- [ ] Add a Claude Agent SDK adapter with aggregated internal-call evidence and
  per-turn model/cost accounting.
- [ ] Introduce provider-neutral conversation/tool events before allowing a
  chat conversation to switch providers; until then, pin provider/transport.
- [ ] Remove feature-local model fallback loops and direct client construction.

### Phase 5 — feedback and optimization

- [ ] Build balanced route eval sets with both should-route and should-not-route
  examples for each task.
- [ ] Establish strongest-model and best-single-model baselines.
- [ ] Join route/attempt telemetry to deterministic task outcomes, calibrated
  rubric graders, human dispositions, latency, and true cost.
- [ ] Shadow only the alternative decision; do not duplicate provider work or
  workflow side effects.
- [ ] Canary approved candidates with a persistent control group and reversible
  policy activation.
- [ ] Promote a route only when quality is non-inferior and cost or latency
  improves. Exclude cache hits and deterministic short-circuits from model
  success evidence.
- [ ] Consider contextual optimization only inside the pre-evaluated candidate
  set; never explore unconstrained models on high-impact hiring actions.

## Acceptance criteria

The foundational build is complete when:

1. Every migrated provider attempt has a task key, route/root/parent invocation,
   task/profile/policy/registry versions, provider deployment, reason, status,
   and usage/cost or explicit unknown-usage outcome.
2. The router performs zero network, provider, model, or database calls.
3. Every primary and fallback satisfies execution-mode, tools/schema/citations,
   context, data, lifecycle, pricing, and risk constraints.
4. Unknown, unpriced, retired, and raw untrusted model overrides fail before
   provider execution.
5. Each physical attempt has exactly one hard reservation and reconciliation
   trail. Ambiguous attempts are neither refunded nor blindly replayed.
6. A route fallback can repeat generation only; completed tool or domain side
   effects remain at-most-once.
7. Candidate-search, role-chat, general-chat, and autonomous-agent routes match
   the parity matrix until an explicit optimized policy version is promoted.
8. Cache entries cannot cross incompatible task/schema/route behavior versions.
9. CI prevents new raw production model IDs and provider calls outside the
   registry/adapters/approved compatibility gateway.
10. Adding another provider requires a deployment entry, adapter, and contract
    tests—but no feature-code routing changes.

## Verification matrix

| Area | Required verification |
|---|---|
| Pure policy | unit/property tests for eligibility, exclusion reasons, aliases, fallbacks, stickiness, cycles, depth, and deterministic decisions |
| Persistence | migration upgrade, uniqueness/idempotency, status transitions, JSON safety, and provider-log linkage |
| Billing | provider-admission, credit-reservation, metering-single-source, pricing-per-model, reconciliation, timeout/ambiguous-outcome tests |
| Search | deterministic parser, provider-forbidden, parser cache, rerank, grounding, top-candidate, and chat-search contract suites |
| Chat/agent | agent-chat, Taali-chat streaming/history, autonomous orchestrator, authority, circuit-breaker, and tool-receipt suites |
| Architecture | provider-import/call gates, model-literal gate, task-registry completeness, file-size gate, compile/import smoke |

## Operational rollout

1. Land the parity policy and telemetry with no model-choice change.
2. Enable each migrated task by versioned allowlist and compare route metadata to
   the parity matrix.
3. Keep prior policy versions activatable for instant rollback.
4. Do not delete legacy settings or stored raw overrides until compatibility
   reads are measured at zero.
5. Do not adopt OpenRouter `auto`, LiteLLM auto-routing, or an online learning
   policy as a second independent model chooser. If a gateway is added, Tali
   remains the sole model-policy authority and the gateway owns endpoint health
   and explicitly constrained failover only.
