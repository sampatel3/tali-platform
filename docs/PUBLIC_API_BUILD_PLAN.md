# Taali Public API — Build Plan

**Status:** Proposal / design doc · **Date:** 2026-06-04 · **Owner:** Sam

## Goal

Let external services connect to Taali through an authenticated, documented, stable public API — without exposing the internal app surface. This is the shared substrate for two things:

1. **Customer-owned integrations** — a Taali customer wiring their own systems (ATS, data warehouse, internal tools) to *their* Taali data.
2. **The Workable Assessments Provider add-on** (see `WORKABLE_ASSESSMENTS_PROVIDER_SPEC.md`) — Workable calls Taali's provider endpoints, which ride on this exact substrate.

Scope here is the **customer-integration** case (API keys + a curated surface + webhooks + docs). A full third-party OAuth app ecosystem (Taali as identity provider) is explicitly **out of scope** for now — see Phase 3.

## Design principles

1. **Curated, not internal.** Do **not** publish the ~36 internal routers under `/api/v1`. They change shape whenever the React app needs them to. The public API is a deliberately small `/public/v1` surface with frozen response models we commit to not breaking.
2. **An API key is just another way to resolve `organization_id`.** The entire data layer already filters every query by `current_user.organization_id`. A key that resolves to an org, fed into the same scoping, gets correct tenant isolation across all data **for free**. We are not building a new authorization model — we are adding a second front door to the existing one.
3. **Meter where compute happens, not on reads.** Reads are DB-only; don't invent new billing for them. Writes that trigger billable compute (e.g. creating an assessment → scoring) are *already* metered via `MeteredAnthropicClient` → `UsageEvent` → `BillingCreditLedger`. The public API inherits that.
4. **Server-side keys only.** Live keys are for server-to-server use. No browser/SPA exposure, no CORS for live keys.
5. **Lead simplest.** Ship Phase 1 (keys + read surface + docs) before webhooks; ship webhooks before any OAuth-provider ambition.

---

## What already exists (reuse, don't rebuild)

| Capability | Where | Reuse for |
|---|---|---|
| Org-scoped tenancy (every query filters `organization_id`) | pervasive, e.g. `backend/app/domains/assessments_runtime/applications_routes.py` | Tenant isolation for API-key requests, for free |
| Org model + per-org encrypted secrets pattern | `backend/app/models/organization.py` | Where to hang key relationships / provider tokens |
| Metering + credit ledger | `backend/app/services/usage_metering_service.py`, `backend/app/models/usage_event.py`, `backend/app/models/billing_credit_ledger.py` | Per-key usage accounting (reuse `record_event` / `_debit_ledger`) |
| IP rate-limit middleware | `backend/app/platform/middleware.py` | Extend to per-key buckets |
| Durable outbox + drain pattern | `backend/app/models/brain_feed_outbox.py`, `backend/app/brain_feed/outbox.py`; `backend/app/models/graph_episode_outbox.py`, `backend/app/tasks/graph_outbox_tasks.py` | Template for the webhook-delivery outbox |
| OpenAPI generator (FastAPI) | `backend/app/main.py` (currently `/api/docs`, disabled in prod) | Source spec for the public docs site |
| MCP server (JWT-auth, mounted `/mcp`) | `backend/app/mcp/server.py`, `backend/app/mcp/auth.py` | Optional agent-native public surface (Phase 1.5) |

## What's net-new

- API-key model + issuance/hashing + auth dependency.
- A curated `/public/v1` router with frozen schemas.
- Per-key rate limiting + per-key audit log.
- Outbound webhook subscriptions + delivery outbox (Phase 2).
- A hosted developer-docs site + an in-app "Developers" settings page.

---

## Phase 1 — Machine-to-machine auth + curated read surface + docs

### 1.1 `ApiKey` model + issuance

New table `api_keys`:

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `organization_id` | FK → organizations | the only identity that matters for scoping |
| `name` | str | human label ("Data warehouse sync") |
| `prefix` | str | `tali_live_` / `tali_test_`, plus a short public id segment for display (`tali_live_a1b2…`) |
| `hashed_secret` | str | **SHA-256 of the secret only.** Never store the secret. |
| `scopes` | JSON | e.g. `["roles:read","applications:read","assessments:write","webhooks:manage"]` |
| `last_used_at` | datetime nullable | |
| `expires_at` | datetime nullable | optional rotation |
| `created_by_user_id` | FK nullable | audit |
| `revoked_at` | datetime nullable | soft revoke |
| `created_at` | datetime | |

- Generate: `secrets.token_urlsafe(32)`, prefix it, hash it, **show the full secret exactly once** on creation (mirror the share-link `shr_` ergonomics already in the codebase).
- Support multiple active keys per org + independent revoke (for rotation without downtime).
- Test keys (`tali_test_`) resolve to the same org but flag requests as non-billing / sandbox.

### 1.2 Auth dependency

A new FastAPI dependency `get_api_principal()` (parallel to `get_current_user`):

- Accept `Authorization: Bearer tali_…` (and/or `X-API-Key`).
- Look up by prefix's public id, constant-time compare SHA-256 of the presented secret against `hashed_secret`, reject revoked/expired.
- Update `last_used_at` (cheap, async/debounced).
- Yield a lightweight principal exposing `.organization_id` and `.scopes`.
- Public route handlers depend on this and reuse the **same** org-scoped queries as today. A scope check decorator/dependency (`require_scope("assessments:write")`) gates writes.

> Keep public handlers in their own router module — do **not** retrofit `get_api_principal` onto internal routes. Internal routes stay JWT-only; the public surface is a separate, frozen contract.

### 1.3 Curated `/public/v1` surface (read-first)

Mount a new router at `/public/v1` (and, later, a dedicated host like `api.taali.ai`). Each endpoint has its **own frozen Pydantic response model** in a `public_schemas` module — decoupled from internal serializers so internal refactors can't break the contract.

Initial endpoints:

- `GET /public/v1/roles` · `GET /public/v1/roles/{id}` — open roles + their assessment tasks.
- `GET /public/v1/candidates/{id}` — candidate basics.
- `GET /public/v1/applications` (filter by role/stage) · `GET /public/v1/applications/{id}` — scores, recommendation, evidence (the serialized result shape Taali already produces).
- `GET /public/v1/assessments/{id}` — assessment status + result.
- `GET /public/v1/tests` (alias of the task catalog) and `POST /public/v1/assessments` — the **write path the Workable provider needs** (create an assessment from `{candidate, role/test}`). Gated by `assessments:write`.
- `POST /public/v1/applications/{id}/share-links` — mint a `results_url` (reuses the share-link domain).

Versioning: `/public/v1` is frozen. Additive changes only within v1; breaking changes → `/public/v2` with a published deprecation window. A `Taali-Version` response header echoes the contract version.

### 1.4 Per-key rate limiting + metering + audit

- **Rate limit:** extend `middleware.py` so the limiter keys on `api_key.id` (fallback IP) with per-plan buckets; return `429` + `Retry-After` + `X-RateLimit-*` headers.
- **Metering:** no new billing for reads. For writes that trigger compute (assessment creation → scoring), the existing `MeteredAnthropicClient` path already records `UsageEvent` and debits `BillingCreditLedger`. Tag those events with the key id via `event_metadata` so usage is attributable per key.
- **Audit:** minimal `api_request_log` (key_id, method, path, status, ms, ts) for security/debugging. Surface in the Developers settings page.

### 1.5 (Optional, on-brand) MCP as the agent-native surface

The MCP server already exists and is JWT-authenticated. Teaching `mcp/auth.py` to also accept `tali_…` keys gives an **agent-consumable public API** with almost no extra work — directly reinforcing Taali's agent-native positioning. Low cost, high narrative value; do it alongside 1.2 if time allows.

### 1.6 Developer experience

- **Docs site:** publish the OpenAPI spec via Scalar / Redoc / Mintlify / ReadMe. Sections: quickstart, authentication, rate limits, errors, webhooks (Phase 2), full reference, changelog. (ReadMe is worth noting — it's also what Workable's own API docs run on.)
- **In-app "Developers" settings page:** mint / name / scope / rotate / revoke keys; show last-used + the per-key request log; link to docs.

**Phase 1 exit:** an external service can authenticate with a key and read its org's roles/candidates/applications/results, create assessments, and mint share links — fully documented.

---

## Phase 2 — Outbound webhooks (event push)

So external systems are *notified* instead of polling. Reuse the established
outbox mechanics while keeping webhook delivery policy explicit.

### 2.1 Models

- `webhook_subscription` (org_id, target_url, signing_secret, event_types JSON, active, created_at).
- `webhook_delivery_outbox` mirroring `brain_feed_outbox`: `(org_id, event_type, dedup_key UNIQUE, payload JSON, status pending|sent|failed, attempts, last_error, created_at, updated_at, sent_at)`.

### 2.2 Events (initial)

- `application.scored` — hook where `scored_at` is set (`backend/app/components/assessments/submission_runtime.py`).
- `decision.made` — hook the decision dispatch path.
- `assessment.completed` — assessment reaches `COMPLETED` + scored.

### 2.3 Delivery

- HMAC-SHA256 sign the body with the subscription secret (`X-Taali-Signature`), include a timestamp + event id for idempotency.
- Drain via a Celery task borrowing the graph outbox's bounded-batch,
  idempotency, and exponential-backoff mechanics. Unlike irreplaceable graph
  signals, webhook delivery deliberately uses a finite attempt budget, then
  exposes the dead letter in the Developers page for manual replay.
- **Register the new Celery task in `app/tasks/__init__.py`** — autodiscover is a no-op; unregistered tasks are silently dropped.

**Phase 2 exit:** customers subscribe to events and receive signed, retried, idempotent deliveries; failures are observable and replayable.

---

## Phase 3 — Third-party app ecosystem (DEFER)

Only if there's real pull for *other vendors* to build apps any Taali customer can authorize. That means Taali becomes an OAuth 2.0 **provider** (auth-code + client registration + consent + app review). An order of magnitude more work (consent UI, app lifecycle, security review, per-app scoping). For "an external service connects to Taali," Phases 1–2 are sufficient. **Do not build on spec.**

---

## Security & ops checklist

- Secrets hashed at rest (SHA-256), revealed once, never logged.
- Constant-time secret comparison; reject revoked/expired before any work.
- Per-key scopes enforced on every write; least-privilege default scopes.
- Per-key rate limits + global abuse protection; `429` semantics.
- Per-key audit log; alert on anomalous spikes (reuse existing alerting posture).
- Rotation story: multiple live keys + independent revoke.
- Secret-scanning note in docs (don't embed live keys client-side).
- Live-mode metering parity with the app (no unmetered compute via the API).

## Sequencing & effort (rough)

| Item | Size | Depends on |
|---|---|---|
| 1.1 ApiKey model + migration | S | — |
| 1.2 Auth dependency + scope gate | S–M | 1.1 |
| 1.3 Curated `/public/v1` (reads + create-assessment + share-link) | M | 1.2 |
| 1.4 Per-key rate limit + audit | S–M | 1.2 |
| 1.5 MCP key auth (optional) | S | 1.2 |
| 1.6 Docs site + Developers settings page | M | 1.3 |
| 2.x Webhooks (subscription + outbox + drain + events) | M–L | Phase 1 |
| 3.x OAuth provider | L | deferred |

## Testing (per repo norms)

Run vitest + the arch gate + relevant pytest locally before every push (CI is not the iteration loop). New surfaces need: auth dependency unit tests (valid/revoked/expired/wrong-scope), tenant-isolation tests (key for org A cannot read org B), rate-limit tests, and webhook outbox drain/retry tests. Watch the in-memory-SQLite batch-flakiness — run new suites in isolation to judge real pass/fail.

## How this connects to Workable

`POST /public/v1/assessments`, `GET /public/v1/tests`, the share-link `results_url`, and the webhook-delivery outbox are **exactly** the primitives the Workable Assessments Provider integration consumes. Build this substrate once; the Workable add-on is a thin, Workable-shaped adapter on top (see `WORKABLE_ASSESSMENTS_PROVIDER_SPEC.md`).
