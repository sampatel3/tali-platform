# Two-Tier QA Strategy

**Status:** Draft for sign-off · **Owner:** Sam · **Date:** 2026-05-29

This document is the design + sign-off artifact for the platform's QA strategy.
It covers the tier split, where each tier lives, the Tier 2 architecture, how
Tier 2 is triggered, and the CI gates and their turn-on schedule.

---

## 0. Context

The product is **one platform across four (well, six) GitHub repos**, TS + Python:

| Repo | Role | Lang |
|------|------|------|
| `mainspring` | **Substrate** — "a runtime for autonomous operations over a stateful business pipeline". The shared framework both brands build on. | Python |
| `taali-brand` | Brand built on the substrate | Python |
| `cadence` | Brand built on the substrate | Python |
| `tali-platform` | **Legacy monolith** (this repo) — the original TAALI app, FastAPI + Vite/React, from which the substrate/brand split is being extracted | Python + TS |
| `tali` | Older JS prototype | JS |

Solo founder, almost all work via Claude Code. Tests today live inside each
repo and run on deploy.

### The failure mode nothing currently catches

> A change in **mainspring** (the substrate) silently breaks **taali** and/or
> **cadence**.

Per-repo tests can't see across the boundary. mainspring's own tests pass; the
brands' tests aren't re-run when mainspring changes; the break only shows up in
production. **This is the entire reason Tier 2 exists.**

---

## 1. The two-tier model

### Tier 1 — co-located unit + integration tests (STAYS PUT)

- Unit + integration tests **stay inside each repo** and **run on that repo's
  deploy**. We do **not** extract them into a shared framework — that just
  creates sync drift between the framework and the code it tests.
- Tier 1 work is **stabilization, not relocation**: make the existing suites
  deterministic, isolated, and parallel-safe so they mean something.
- **Tier 1 must be solid before Tier 2 is worth building** — a robust Tier 2
  stacked on a flaky Tier 1 just inherits the flakiness.

### Tier 2 — cross-repo QA framework (NEW, SEPARATE, ROBUST)

- A **new standalone repo: `platform-qa`** (decision in §3).
- Its job, and only its job, is the cross-repo failure mode above. It holds:
  1. **Contract tests** between the substrate and each brand.
  2. **End-to-end tests** against the assembled platform.
  3. A **shared harness + deterministic fixtures** (real Postgres, seeded data,
     frozen clock/IDs).
  4. **Quality standards + CI gates** that run against the integrated platform.
- Tier 2 **complements, does not duplicate**, the existing per-repo gates
  (metering gate, architecture gate, alembic single-head gate).

---

## 2. What we fixed first (Tier 1 stabilization)

### The in-memory SQLite state-leak (fixed in this change)

**Symptom:** the backend suite passed only when files were run in isolation;
the same commit gave different pass/fail depending on which file-set ran
together.

**Root cause** (`backend/tests/conftest.py`): the suite shared **one** in-memory
SQLite DB for the whole pytest session (kept alive by a module-level keepalive
connection), and assigned BigInteger primary keys from **session-global,
monotonic counters that never reset**. So:

- Any test asserting a specific id or a clean first row passed alone but failed
  once another row-creating test ran first → order-dependent results.
- Tests that touched the DB without requesting the `db` fixture never triggered
  cleanup, so their rows leaked into later tests.

**Reproduction** (committed as `backend/tests/test_db_test_isolation.py`): two
tests each create the first `ClaudeCallLog` and assert `id == 1`. Before the
fix, run together the second failed with `id == 2`; run alone it passed.

**Fix:**
- An **autouse** `_isolate_test` fixture now runs for **every** test (not just
  those requesting `db`): it resets the PK counters and creates a pristine
  schema before each test, and drops the schema after. Run order can no longer
  change a single pass/fail.
- The datastore is now **pluggable** via `TEST_DATABASE_URL`. Unset → isolated
  SQLite (fast local/pre-pilot default). Set → a **real Postgres** for prod
  parity (CI service container, or a throwaway dev container on a **non-5432**
  port). The conftest **refuses port 5432** outright, because that is likely the
  host/prod Postgres (or an ssh tunnel to it) and the per-test teardown drops
  every table.
- The SQLite-only BigInteger-PK shim is now **guarded to SQLite**. On Postgres
  the real `BIGSERIAL` sequence assigns ids, exactly like prod.

**Verified:** repro passes in both orders; the previously order-coupled
PK-counter files (`test_claude_call_log`, `test_decisions_breakdown`,
`test_dedupe_pending_decisions`, `test_graph_episode_outbox`,
`test_ask_recruiter_subject_id`) pass identically forward and reversed; 2448
tests collect with no errors; no new failures introduced (the handful of
remaining failures in this throwaway container are pre-existing and
environment-specific, e.g. task-create paths that need services this container
lacks — they fail identically on the original conftest).

This is the foundation Tier 2 builds on.

---

## 3. Where Tier 2 lives — **its own repo (`platform-qa`)**

**Decision: a standalone `sampatel3/platform-qa` repo.** (Signed off.)

Rationale vs. the alternative (folding it into `mainspring`):

- **No circular dependency.** Tier 2 depends on *all* of substrate + brands. If
  it lived in mainspring, mainspring would import its own brands → a cycle, and
  brands couldn't own their half of a contract.
- **Neutral ground / clear ownership.** One place that knows about the whole
  platform; nobody's per-repo deploy owns it.
- **Version control of the matrix.** `platform-qa` pins/maps which
  substrate + brand versions are being integration-tested, and can run a
  version matrix (e.g. mainspring@main × {taali, cadence}@main).

Cost: one more repo and its CI. Acceptable and bounded.

### Layout

```
platform-qa/
  README.md                  # what it is, how to run, test standards
  pyproject.toml             # pinned deps: pytest, httpx, pydantic, psycopg, testcontainers
  docker-compose.qa.yml      # throwaway Postgres on a NON-5432 port (55432)
  bin/setup.sh               # env bootstrap: clones/links repos, builds venv (worktrees have no .venv)
  conftest.py                # shared harness: pg container, deterministic clock/seed, brand bootstrap
  contracts/                 # substrate-published interface snapshots (the source of truth)
    mainspring/<contract>.json
  tests/
    contract/                # substrate <-> brand contract tests
    e2e/                     # end-to-end against the assembled platform
  examples/                  # runnable reference substrate + brand proving the mechanism
```

A runnable skeleton of this lives under `platform-qa/` in *this* repo as the
reference implementation, marked for extraction into `sampatel3/platform-qa`.
(It lives here only because this session can write `tali-platform` but not the
other repos — see §6.)

---

## 4. Tier 2 architecture

### 4.1 Contract tests (the core)

We use **consumer-driven contract testing on the substrate boundary**:

- **mainspring publishes its public interface as a versioned contract** —
  the schemas/signatures brands depend on (request/response models, event
  payloads, the pipeline-step interface). These are committed snapshots in
  `contracts/mainspring/`.
- **Each brand declares which contract version it consumes.**
- A **contract test** verifies, for every (substrate, brand) pair, that the
  brand's expectations are still satisfiable against the substrate's current
  published interface. An **incompatible substrate change** (removed field,
  narrowed type, renamed step) **fails the contract test immediately** —
  *before* it can reach a brand's production deploy.

Why contracts rather than "just run every brand's full suite against mainspring
main": contracts are **fast, precise, and localize the break** ("mainspring
removed `Pipeline.advance(reason=...)` that taali relies on") instead of a
sea of red downstream tests. We still keep a thin E2E layer (§4.2) for the
integration smoke that contracts can't express.

The reference harness in `platform-qa/examples/` implements and **proves** this
mechanism end-to-end: a substrate contract, a brand that consumes it, and a
checker test that goes **green when compatible and red on an incompatible
substrate change** — so "the substrate→brand break is caught" is itself an
asserted, runnable fact, not a promise.

### 4.2 End-to-end tests (the thin assembled-platform layer)

- Stand up the substrate + one brand against a **real throwaway Postgres**
  (container, non-5432 port — never the host/prod DB), seed deterministic
  fixtures, and drive a few critical user journeys through the assembled stack.
- Kept deliberately **thin** — E2E is slow and flaky-prone; contracts do the
  heavy lifting. E2E exists to catch wiring/assembly failures that unit-level
  contracts can't see.

### 4.3 Shared harness + deterministic fixtures

- **Real datastore:** Postgres via `docker-compose.qa.yml` (port **55432**),
  reset between tests (drop/recreate schema or truncate). Mirrors the Tier 1
  `TEST_DATABASE_URL` discipline so behavior matches prod, not SQLite quirks.
- **Determinism:** frozen clock, seeded RNG, fixed ID allocation, pinned model
  responses (no live LLM calls in QA — stubbed at the metered-client boundary,
  consistent with the metering gate).
- **Parallel-safe:** per-worker database/schema for `pytest-xdist`.
- **Environment bootstrap:** `bin/setup.sh` accounts for the fact that
  **worktrees and fresh containers have no `node_modules`/`.venv`** — it creates
  the venv, installs pinned deps, and links/clones the repos under test.

### 4.4 Test standards ("what a good Tier 2 test is")

Documented in `platform-qa/README.md`:
1. **Deterministic** — no wall-clock, no network, no random without a fixed seed.
2. **Isolated** — fresh DB state per test; no order dependence (the Tier 1 bug
   is the cautionary tale).
3. **Localizing** — when it fails, the message names the substrate symbol and
   the brand that broke.
4. **Real datastore** — Postgres, not in-memory shortcuts.
5. **Fast-ish & layered** — contracts first, thin E2E second.

---

## 5. CI gates and when they switch on

### 5.1 Existing gates (keep — pre-pilot reality)

CI is intentionally minimal pre-pilot (no users on prod yet):

- **`ci.yml`** — backend `compileall` syntax gate, **alembic single-head gate**,
  frontend architecture gate + build. No pytest/vitest yet.
- **In-suite gates** (run when pytest runs): **metering gate**
  (`test_metering_single_source` — every Anthropic call writes a `UsageEvent`
  via the metered client), **architecture gates**
  (`test_architecture_boundaries`, `test_ci_architecture_gates`).

Tier 2 gates **complement** these; they do not re-implement metering/arch checks.

### 5.2 New gates and their turn-on schedule

| Gate | Where | Turns on |
|------|-------|----------|
| **Per-repo pytest/vitest** (Tier 1, real Postgres service container, coverage threshold) | each repo's CI | **Pilot start** |
| **Contract gate** (substrate↔brand) | `platform-qa` CI | **Pilot start** |
| **E2E smoke** (assembled platform) | `platform-qa` CI, nightly + pre-release | **Pilot start**, nightly |

**Pre-pilot now:** the Tier 1 test gate ships as a **manual / `workflow_dispatch`**
workflow (`ci-tests.yml`) so it's real and reviewable but doesn't block while the
container-specific failures are still being burned down. **Turning it on at pilot
is a one-line change** (add `pull_request:` to its `on:` triggers) — documented in
that file.

### 5.3 How Tier 2 is triggered (relative to per-repo deploys)

- **On a PR to any repo under test** (mainspring/taali/cadence): a
  `repository_dispatch`/workflow-call fans out to `platform-qa`'s contract gate
  with that PR's ref. A substrate PR that breaks a brand contract **fails before
  merge**, which is the whole point.
- **On `platform-qa` `main`** and **nightly:** full contract + E2E matrix across
  `@main` of every repo — catches drift from merges that individually looked fine.
- **Pre-release:** E2E smoke as a release gate.

Sequencing vs. per-repo deploys: per-repo unit/integration (Tier 1) gate the
repo's own deploy; the cross-repo contract gate sits **upstream of the substrate
merge** so substrate changes can't ship a brand break. E2E is a slower
nightly/pre-release safety net, not on the hot path of every deploy.

---

## 6. Constraints surfaced during this work (need Sam's action)

This session could **write only `tali-platform`** — content access to
`mainspring`, `taali-brand`, and `cadence` was denied (session repo scope), and
**railway CLI / Docker daemon were unavailable** in the container. Consequences:

- The Tier 1 fix + regression test are **real and verified** in `tali-platform`.
- The `platform-qa` skeleton is committed **here** as a runnable reference, to be
  **extracted into `sampatel3/platform-qa`**.
- The contract/E2E **mechanism is proven** against a self-contained example +
  this repo's own substrate boundary (`app/platform` ↔ brand surfaces). Wiring
  it to the *real* mainspring/taali/cadence interfaces needs a session scoped to
  all those repos.

**To finish the real cross-repo wiring**, run Claude in a session scoped to
`mainspring` + `taali-brand` + `cadence` + a new `platform-qa`, and (for local
Postgres-backed runs) with Docker or railway CLI available.

---

## 7. Sign-off

- [ ] Tier split (Tier 1 stays co-located; Tier 2 separate) — **agreed**
- [ ] Tier 2 lives in its own `platform-qa` repo — **agreed**
- [ ] Contract-first + thin E2E architecture
- [ ] CI gate set + pilot turn-on schedule
- [ ] Plan to grant cross-repo access to complete real wiring (§6)
