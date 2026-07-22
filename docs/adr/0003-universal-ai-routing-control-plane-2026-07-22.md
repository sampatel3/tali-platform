# ADR 0003 — Universal AI routing control plane

- **Status:** Accepted; control-plane foundation implemented
- **Date:** 2026-07-22
- **Owner:** @sampatel
- **Related:** `backend/app/llm/`, `docs/AI_ROUTING_EXECUTION_PLAN.md`

## Context

Tali has a solid Anthropic call and metering spine, but it does not have one
place that decides which workflow step, model deployment, or fallback policy a
piece of AI work is allowed to use.

Today, selection is distributed across:

- general, chat, scoring, and autonomous-agent environment settings;
- pinned `FAST_MODEL` and `SONNET_MODEL` constants;
- search-specific environment variables;
- per-role and per-assessment raw model overrides;
- feature-local fallback loops; and
- transports with materially different contracts: Messages, streaming,
  Message Batches, Graphiti/Voyage, and the Claude Agent SDK subprocess.

`backend/app/llm/core.py` and `structured.py` are useful Anthropic Messages
primitives. They are intentionally not universal routers: their request,
tool-use, cache-control, stream, and usage shapes are provider-specific.

The optimization objective is also Tali-specific. A generic gateway can choose
an available endpoint, but it cannot decide how much quality candidate search,
CV evidence grounding, recruiter chat, or an autonomous hiring action requires.
Those decisions must remain in Tali's application control plane and be evaluated
against Tali outcomes.

## Decision

Build four explicit layers.

### 1. Workflow and task registry

Every model-assisted operation has a stable task key and belongs to a named,
versioned workflow step. A task profile declares:

- semantic and output-schema revisions;
- execution mode (`sync`, `stream`, `batch`, `agent_sdk`, `embedding`, or
  `composite`);
- required capabilities such as tools, strict structured output, citations,
  streaming, prompt caching, or long context;
- risk and data classification;
- latency, token, iteration, and cost ceilings;
- route stickiness and fallback rules; and
- the existing billing `Feature` attribution.

Deterministic application code continues to orchestrate known workflows. The
router never invokes another model to decide how to route a request. Open-ended
agents may choose tools, but each model call and nested task still enters through
a registered task boundary.

### 2. Model-deployment registry

Provider model IDs live only in an immutable registry. A deployment record owns:

- provider, endpoint/runtime, and exact model snapshot;
- supported execution modes and capabilities;
- context and output limits;
- lifecycle state and approved replacement;
- exact pricing identity;
- allowed data classes, regions, retention policy, and credential strategy; and
- evaluated task eligibility.

Model discovery never implies authorization. Unknown, unpriced, retired, or
capability-incompatible models fail before a provider call. Raw database or
environment overrides are resolved through validated aliases during migration;
new raw IDs are rejected.

### 3. Pure routing policy

`RoutingPolicy.plan(RouteRequest) -> RouteDecision` is deterministic and has no
network, model, database, or provider side effects. It applies constraints in
this order:

1. execution-mode, capability, context, data, region, provider, and lifecycle
   eligibility;
2. the task's evaluated quality floor;
3. explicit tenant/task policy and cost ceiling;
4. expected cost; then
5. latency.

The immutable decision contains the policy/profile/registry versions, workflow
and task, requirements, eligible and excluded deployments with reason codes,
the ordered attempt chain, limits, fallback classes, and a stable route ID.

The initial policy is a parity policy: it reproduces current production choices.
It does not silently move chat, search, scoring, or assessment traffic to a new
model. Model optimization is a later policy-version promotion backed by evals.

### 4. Gateway and transport adapters

The gateway executes a precomputed decision. Provider/runtime adapters render
the task contract for their transport and normalize outcomes. The first adapter
reuses the existing metered Anthropic Messages gateway. Streaming, Message
Batches, Graphiti/Voyage, and Claude Agent SDK remain separate adapters rather
than being forced into a fake common `messages.create` API.

Every physical attempt gets its own hard admission, reservation, attempt record,
provider call, usage settlement, and reconciliation trail. A logical invocation
may contain several attempts, but a fallback never replays completed tools or
other workflow side effects.

## Fallback policy

Fallback is part of the task contract, not an arbitrary list of model names.

- Retry a deployment only for an explicitly retryable transport class.
- Fail over only to a pre-evaluated, fully contract-equivalent deployment.
- A retired/unavailable model may use its registered replacement.
- Schema or semantic validation failures use a bounded repair or explicit
  quality escalation only when the task profile permits it.
- Compliance, safety, authentication, billing, ordinary bad-request, and
  authorization failures never fall through to a weaker provider.
- Ambiguous timeouts, disconnects, and post-acceptance 5xx failures retain their
  provider-attempt hold and are not blindly replayed.
- Streaming can fail over only before a provider accepts the stream; never after
  a user has received partial output.
- A chat turn or autonomous cycle pins its chosen deployment. Nested tasks such
  as search parsing or evidence grounding receive child invocations and their
  own task-specific routes.

## Telemetry and feedback contract

The first implementation records a generic logical invocation and each physical
attempt. Provider-specific `ClaudeCallLog`, Anthropic wire evidence, and
`UsageEvent` remain intact and are linked rather than renamed.

The decision/attempt record contains no prompt or candidate content. It records:

- route, root invocation, parent invocation, operation, workflow, and task IDs;
- registry, task-profile, and policy versions;
- requirements, candidate chain, exclusions, selection, and reason codes;
- provider/model/runtime, attempt ordinal, fallback source, and terminal status;
- latency, tokens, cost, request ID, and explicit unknown-usage state; and
- cache, prompt, tool, and schema revisions needed to interpret outcomes.

This is the substrate for an offline feedback loop. New policies are evaluated
against balanced task suites, shadow decisions, and a persistent control group.
They are promoted only when task quality is non-inferior and cost or latency
improves. Cached results and deterministic short-circuits do not count as model
success evidence. High-impact hiring, scoring, and autonomous-action routes do
not use unconstrained online exploration.

## Consequences

- Feature code names the task and supplies constraints; it no longer chooses raw
  model IDs or implements local fallback policy.
- Adding OpenRouter, LiteLLM, or another direct provider requires registry and
  adapter work, not feature-code rewrites.
- OpenRouter may perform endpoint routing underneath a Tali-selected model set,
  but its auto router does not replace Tali's task policy.
- Existing Anthropic metering and reconciliation remain the billing authority
  while generic routing telemetry is introduced alongside them.
- Cache keys that can cross a model route include the task semantic revision,
  schema revision, and route behavior fingerprint before dynamic routing is
  enabled.
- The migration is deliberately transport-by-transport. A universal contract is
  not permission to hide incompatible provider semantics.

## Guardrails

CI must prove all of the following:

1. The routing policy performs no provider, network, model, or database calls.
2. Every registered primary and fallback satisfies the task's complete contract.
3. The workflow/task graph is acyclic and has a bounded routing depth.
4. Production provider model IDs exist only in the deployment registry.
5. Provider SDK calls exist only in approved adapters or compatibility gateways.
6. Every migrated gateway call carries a literal/enum task key and versioned
   route decision.
7. Every physical provider attempt has exactly one reservation and attempt
   trail, including explicit unknown-usage outcomes.
8. Unknown, unpriced, and retired deployments fail closed.
9. Current-model parity is tested before any optimized policy is activated.

## Rollout and deprecation plan

The concrete steps and acceptance tests are maintained in
`docs/AI_ROUTING_EXECUTION_PLAN.md`.

- **2026-07-22:** land the registry, pure parity policy, generic telemetry, and
  Anthropic Messages compatibility adapter; migrate candidate-search model
  steps, then recruiter chat and the autonomous loop while preserving their
  workflow state machines.
- **Next transport phase:** add explicit adapters for Messages Batch,
  Graphiti/Voyage, and Claude Agent SDK; migrate remaining single-shot calls and
  remove raw model selection from feature code.
- **After complete route telemetry:** run offline route evals and shadow
  alternative decisions. Promote the first optimized policy only through a
  versioned, reversible activation.
- Remove legacy model settings and raw DB/task overrides only after compatibility
  reads reach zero and stored values have been migrated to deployment aliases.

## Implementation checkpoint

The foundation release implements the shared control plane and migrates six
tasks: search parsing, qualitative reranking, citation grounding, general
recruiter chat, role-agent chat, and autonomous recruiting. The compatibility
inventory and CI gates deliberately remain in place for the Phase 4 transports
and call sites; this checkpoint does not claim that every legacy provider call
has moved.

## Research basis

- Anthropic distinguishes deterministic workflows from agents and recommends
  routing easy/common work to smaller models and difficult/unusual work to more
  capable models: [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).
- OpenRouter separates model fallbacks from provider routing and exposes privacy,
  parameter-support, price, throughput, and latency constraints:
  [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
  and [model fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks).
- OpenRouter's Auto Router uses generic task classification and ecosystem usage
  rankings, which are useful gateway signals but not Tali-specific quality
  evidence: [Auto Router](https://openrouter.ai/docs/guides/routing/routers/auto-router).
- OpenTelemetry's GenAI conventions distinguish workflow, operation, provider,
  model, and usage attributes: [GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/).
- Anthropic documents that provider errors can occur after a streaming 200 and
  that SDK retries cover transient classes, reinforcing the no-mid-stream and
  no-ambiguous-replay rules: [Claude API errors](https://platform.claude.com/docs/en/api/errors).
