# Related-role agent and funnel research — 2026-07-17

Baseline inspected: `origin/main` at `68e9c9be` in the production-baseline worktree. This is a source trace only; no product code was changed.

## Executive findings

| Reported behaviour | Finding | Confidence |
| --- | --- | --- |
| The Turn on message is hard to understand | It is a confirmation of the exact autonomy policy, shared-application consequences, assessment path, and monthly cap—not an error. Its purpose is sound, but the copy exposes internal policy language instead of leading with the user-visible outcome. | High |
| Funnel counts become zero after Turn on | The data is not erased. `PATCH /roles/{id}` returns a generic role payload with omitted aggregates defaulted to empty/zero; the frontend replaces its complete role state with that payload before a detail refetch restores it. | Confirmed |
| The agent animation is slow to turn on | The frontend deliberately remains OFF until the entire activation PATCH succeeds. The PATCH performs synchronous readiness/commit/queue-acceptance work. Related roles are also explicitly excluded from the persisted `starting` status, so there is no truthful intermediate animation. | Confirmed mechanism; the dominant production latency substep needs timing telemetry |
| Home has no funnel for the related role | The Home endpoint runs canonical application counts using the related role's own ID. Related roles deliberately own no `CandidateApplication` rows, so Home emits a zero-filled funnel; the frontend then hides all-zero funnels. | Confirmed |

The two funnel symptoms share one architectural cause: related-role presentation data is assembled through several different endpoint/serializer paths, and only some of them use the related-role-aware aggregate. Missing aggregates are then treated as authoritative zeros.

## Related-role data model

A related role is a Taali-only view over the original role's ATS application pool. It has `role_kind="sister"`, no ATS job/application ownership of its own, and an `ats_owner_role_id` link to the original role (`backend/app/models/role.py:28-30`, `backend/app/models/role.py:53-66`, `backend/app/models/role.py:257-270`).

The canonical `CandidateApplication` remains attached to the original role and owns provider IDs and the shared ATS outcome. Each related role stores its alternate score and local Taali stage in `SisterRoleEvaluation`, keyed by related role plus source application (`backend/app/models/sister_role_evaluation.py:39-83`). This is why `CandidateApplication.role_id == related_role.id` correctly finds no rows.

The family-aware counter already exists. `related_role_pipeline_counts_bulk` counts `SisterRoleEvaluation` rows, lets a canonical rejection/advance override the local view, and maps completed scoring/review states into funnel buckets (`backend/app/services/sister_role_service.py:52-121`). When no precomputed count override is supplied, `pipeline_counts_for_role` selects that path for a related role (`backend/app/services/sister_role_service.py:124-147`).

The candidate list follows the same model correctly: it queries the owner's applications, joins the related evaluation, then projects the page into the related role's local score/stage (`backend/app/domains/assessments_runtime/applications_routes.py:882-920`, `backend/app/domains/assessments_runtime/applications_routes.py:985-1004`, `backend/app/services/related_role_application_runtime.py:288-382`).

## 1. What the Turn on message is

The modal is the final policy confirmation before the recruiter grants the agent autonomy. It summarizes:

- which invitations, retries, advances, and rejections can happen automatically;
- which actions still require approval;
- the family-wide consequence of advancing or rejecting a shared ATS application;
- whether the assessment step will be skipped; and
- the monthly AI limit and pause behaviour.

Those are real safety constraints. Platform defaults keep candidate-facing positive actions under human approval (`backend/app/services/agent_policy_settings.py:20-46`), and automatic rejection is disabled when roles share an ATS application because one rejection affects the whole family (`backend/app/services/agent_policy_settings.py:56-89`). Advancing the canonical application also advances every related evaluation and discards now-moot family decisions (`backend/app/services/related_role_application_runtime.py:87-120`).

The frontend derives whether the pool is shared from the role family, role kind, or related-role count (`frontend/src/features/jobs/JobPipelinePage.jsx:110-122`). It then renders the screenshot's exact policy matrix and budget copy (`frontend/src/features/jobs/JobPipelinePage.jsx:2339-2390`).

### Why the current copy feels unclear

It starts with the abstract phrase “Candidate-action safeguards,” repeats “require your approval” across several sentences, and exposes implementation terms such as “deterministic,” “LLM-only,” and “linked roles.” It also says only that actions affect “all linked roles,” even though the role-family data can name the original role. The user must reconstruct the simple outcome from a policy matrix.

A clearer outcome-first version for the screenshot's policy would be:

> **Turn on the agent?**
>
> The agent will score candidates and recommend next steps. You approve every assessment invite, retry, advance, and rejection.
>
> This role shares candidates with AI Engineer #31. Advancing or rejecting a candidate updates both roles.
>
> No assessment is set, so candidates will skip that step. Monthly AI limit: $50. Pause anytime to stop new work and spend.

The copy must remain dynamic when a role actually has automatic invitations, retries, or advances enabled; the simplification should change presentation, not weaken the policy confirmation.

## 2. Why activation temporarily zeroes the role funnel

This is a response-shape/state-replacement bug, not a pipeline mutation.

1. A normal role-detail GET uses `_serialize_role_detail`, which computes the operational application count and the family-aware `pipeline_counts_for_role`, then passes those aggregates into `role_to_response` (`backend/app/domains/assessments_runtime/roles_management_routes.py:493-550`).
2. The generic PATCH handler instead ends with `return role_to_response(role)` (`backend/app/domains/assessments_runtime/roles_management_routes.py:763-790`, `backend/app/domains/assessments_runtime/roles_management_routes.py:1620-1629`).
3. When aggregates are not supplied, that serializer emits `stage_counts={}`, `pending_decisions_by_type={}`, and `active_candidates_count=0` (`backend/app/domains/assessments_runtime/role_support.py:387-400`, `backend/app/domains/assessments_runtime/role_support.py:527-532`).
4. On activation success, the frontend replaces the entire current role with the PATCH response via `setRole(response.data)`, then starts a status refetch and full workspace reload (`frontend/src/features/jobs/JobPipelinePage.jsx:1338-1361`).
5. `FunnelBoard` interprets missing stage keys as numeric zero (`frontend/src/shared/ui/FunnelBoard.jsx:38-58`). The subsequent lightweight shell merge deliberately preserves the current aggregates—now the false zeros—until the slower full detail GET completes (`frontend/src/features/jobs/JobPipelinePage.jsx:429-480`).

There is strong adjacent evidence that the intended client contract is to preserve independent funnel state across agent-control mutations: Turn off explicitly merges the response while retaining `stage_counts`, pending-by-type, and active-candidate count (`frontend/src/features/jobs/JobPipelinePage.jsx:1404-1422`), and its regression test calls out avoiding a false count-to-zero animation (`frontend/src/features/jobs/JobPipelinePage.test.jsx:1980-2021`). Turn on lacks the equivalent protection.

The best contract-level correction is for PATCH to return the same full detail serialization as GET. Preserving last-known aggregates in the client is still a worthwhile defensive measure, because “field not supplied” should not mean “the real count is zero.”

## Activation-animation delay

The initial lag is currently guaranteed by the frontend. First activation is intentionally non-optimistic: it keeps the strip OFF until the authoritative PATCH resolves, and it has no local “Turning on…” state (`frontend/src/features/jobs/JobPipelinePage.jsx:1345-1361`). Tests explicitly assert that a slow PATCH shows neither “Agent on” nor “Agent starting” (`frontend/src/features/jobs/JobPipelinePage.test.jsx:987-1024`, `frontend/src/features/jobs/JobPipelinePage.test.jsx:1049-1094`).

That PATCH is more than a flag write. In production it validates the budget and effective assessment path, runs activation readiness, persists the role, and—when the workspace is not held—requires the worker queue to accept the initial related-role cycle before performing post-activation work and returning (`backend/app/domains/assessments_runtime/roles_management_routes.py:1002-1156`, `backend/app/domains/assessments_runtime/roles_management_routes.py:1387-1392`, `backend/app/domains/assessments_runtime/roles_management_routes.py:1425-1451`, `backend/app/domains/assessments_runtime/roles_management_routes.py:1538-1581`). Production readiness includes worker health, model/runtime capabilities, the related role's ATS owner, assessment dependencies when used, credits, the role cap, and required ATS writeback configuration (`backend/app/services/agent_activation_readiness.py:23-80`, `backend/app/services/agent_activation_readiness.py:171-217`, `backend/app/services/agent_activation_readiness.py:317-390`, `backend/app/services/agent_activation_readiness.py:580-645`). Source inspection proves that the UI waits for this whole request, but not which check dominated the observed production delay; phase timings are needed for that.

There is a related-role-specific status gap too. The activation handler sets `agent_bootstrap_status="starting"` only when the role is **not** related (`backend/app/domains/assessments_runtime/roles_management_routes.py:1329-1334`). Dispatch correctly selects `related_role_agent_cycle` (`backend/app/services/role_agent_dispatch.py:8-22`), but the related worker stamps `ready` only after it later runs (`backend/app/tasks/sister_role_tasks.py:451-507`, `backend/app/services/related_role_runtime.py:403-412`). The status endpoint and header know how to show `starting` (`backend/app/domains/agentic/routes.py:1755-1782`, `frontend/src/shared/layout/AgentHeader.jsx:272-286`, `frontend/src/shared/layout/AgentHeader.jsx:899-911`), but related activation never supplies that intermediate state.

The safe UX split is:

- show an immediate local **Turning on…** request state while the server is validating, without falsely claiming success;
- once the server commits and accepts dispatch, persist/return **starting** for related roles too; and
- move to **ready/on** when the related worker reports completion.

The related-role exclusion can still skip standard-only artifact/checklist work; it should not remove the generic lifecycle signal.

## 3. Why Home has no related-role funnel

The funnel component is present and mounted above the review queue (`frontend/src/features/home/HomeNow.jsx:1301-1307`). Its data comes from `/agent/roles/breakdown`, loaded and polled by Home (`frontend/src/features/home/HomePage.jsx:278-290`, `frontend/src/features/home/HomePage.jsx:300-322`).

The endpoint fetches all roles, then calls canonical `role_pipeline_counts_bulk` with each raw `Role.id` (`backend/app/domains/agentic/hub_routes.py:307-323`). That helper zero-fills every requested role and counts only `CandidateApplication.role_id` matches (`backend/app/domains/assessments_runtime/pipeline_service.py:1220-1244`, `backend/app/domains/assessments_runtime/pipeline_service.py:1259-1279`). For related role 135 there are intentionally no such rows, so the endpoint emits the zero-filled result at `stage_counts` (`backend/app/domains/agentic/hub_routes.py:400-440`).

The Jobs role list demonstrates the intended batched implementation: count canonical applications by operational owner ID, then overlay `related_role_pipeline_counts_bulk` for related role IDs before serialization (`backend/app/domains/assessments_runtime/roles_management_routes.py:375-437`, `backend/app/domains/assessments_runtime/roles_management_routes.py:477-490`). Home bypasses that adapter.

Finally, `PipelineStandingStrip` reads the selected role's `stage_counts` and returns `null` when every displayed stage is zero (`frontend/src/features/home/HomeNow.jsx:248-289`). So the screenshot does not show a missing Home component; it shows the deliberate empty-funnel suppression reacting to an incorrect backend payload.

A direct related-role Home regression was added in `backend/tests/test_sister_roles.py`; it captures the required nonzero local funnel and failed against the original route implementation. The first test attempt used a broken local Python environment and stalled during startup. Verification was then rerun with the signed bundled runtime: the new regression passed, followed by all 26 tests in `test_sister_roles.py` and `test_role_pipeline_counts_bulk.py`.

## Concise data-flow map

```text
Original Role #31
  └── CandidateApplication (canonical ATS roster, provider IDs, shared outcome)
        ├── original-role funnel -> role_pipeline_counts(_bulk)
        └── SisterRoleEvaluation (role_id #135, source_application_id)
              ├── alternate score + local Taali pipeline stage
              └── related-role funnel -> related_role_pipeline_counts(_bulk)

GET /roles/135
  -> _serialize_role_detail
  -> pipeline_counts_for_role
  -> related evaluation counts                         [correct]

PATCH /roles/135 (Turn on)
  -> generic role_to_response without aggregates
  -> frontend replaces current role
  -> missing keys render as zero
  -> later GET /roles/135 restores counts              [temporary zero]

GET /agent/roles/breakdown
  -> canonical bulk count using raw role id 135
  -> zero-filled result
  -> Home hides an all-zero FunnelBoard                 [funnel absent]
```

## Likely shared root causes

1. **No single role-presentation aggregate contract.** Detail GET, list, mutation, and Home each assemble role data differently. Related roles work only where the family-aware adapter is called.
2. **“Not included” is serialized as “zero.”** Generic serialization substitutes empty/zero aggregates, and clients cannot distinguish unknown/omitted data from a real empty pipeline.
3. **Destructive mutation-response replacement.** Turn on replaces a richer cached entity with a poorer response. Turn off already demonstrates the safer merge behaviour.
4. **Related runtime and lifecycle status are coupled unnecessarily.** Skipping standard-role bootstrap work also skips the generic `starting` status that the UI needs.

## Candidate test seams

### Backend

1. Seed an original role, a related role, canonical applications, and differing `SisterRoleEvaluation.pipeline_stage` values. Assert parity for the related role across:
   - `GET /roles/{id}`;
   - `GET /roles?include_pipeline_stats=true`;
   - `PATCH /roles/{id}` for activation; and
   - `GET /agent/roles/breakdown`.
2. Activation response invariant: a related role's nonzero `stage_counts`, `applications_count`, `active_candidates_count`, and pending-by-type values are unchanged before versus immediately after Turn on.
3. Home aggregation cases: local `done + applied -> scored`, local `review -> completed`, local invited/advanced, canonical rejection overriding local stage, canonical advance overriding local stage, and organization isolation.
4. Lifecycle contract: related activation returns `bootstrap_status=starting` after dispatch acceptance; the related worker changes it to `ready`; broker rejection compensates/fails without an ON state.
5. Add phase timing around readiness, primary commit, dispatch acceptance, post-activation work, and response serialization. Use those measurements to optimize the real production bottleneck rather than guessing from the route's breadth.

### Frontend

1. Resolve Turn on with a payload that omits or empties aggregates. Assert the last known funnel never becomes zero and the full-detail refetch eventually replaces it with authoritative counts.
2. Hold the activation Promise. Assert an immediate disabled **Turning on…** state, stable funnel counts, no false **Agent on**, and recovery to OFF plus an error on rejection.
3. Resolve an accepted related activation with `bootstrap_status=starting`, then poll `ready`; assert the header transitions Turning on -> Agent starting -> Agent on.
4. Feed Home a related role with nonzero local evaluation counts and assert the funnel renders above Review queue. Keep a separate genuinely empty-role case for the intentional all-zero suppression.
5. Modal copy tests should assert the plain-language outcome and named shared role, while retaining dynamic coverage for each effective automation policy and the no-assessment branch.

## Adjacent audit candidates

These are not required to reproduce the three screenshots, but they repeat the same raw-related-role-ID pattern:

- Role agent status finds candidate activity with `CandidateApplication.role_id == role_id`; a related role can therefore show no candidate activity even though its work occurred over the owner's applications (`backend/app/domains/agentic/routes.py:1629-1643`).
- Home panel “scoring” counts only canonical `CvScoreJob.role_id` and does not include pending/running `SisterRoleEvaluation` scoring (`backend/app/domains/agentic/hub_panel_routes.py:239-245`).

They should be covered when consolidating the family-aware presentation/query layer so the same issue does not reappear beside the funnel.
