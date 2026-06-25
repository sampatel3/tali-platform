# Region Migration Plan — move the Tali stack closer to users

**Status:** scoped, not started · **Author:** investigation 2026-06-07 · **Owner:** Sam

## Why (the problem this solves)
The recruiter app feels slow on *every* page. Root cause is **network geography, not server resourcing** — proven, not guessed:
- API runs in **`us-east4` (Virginia)**; you/recruiters are in the **UAE** → **~0.25–0.5s round-trip per API call**, and each page makes several calls (+ polling). The round-trips stack into multi-second loads, uniformly across the site.
- The container itself is healthy and idle-ish: cgroup shows **24 GB RAM (755 MB used, 0 OOM-kills)** and a **24-CPU quota (15 % load, ~0 throttling, 0 CPU pressure)**. Scaling does nothing.
- Proof point: `GET /api/v1/openapi.json` is a 404 that runs **zero app code** (~2 ms handler), yet takes **0.6–1.0 s** from the UAE — that gap is pure UAE↔us-east4 distance.

**Moving the whole stack to a region near users ~halves every round-trip** (≈250 ms → ≈120 ms), which is the only structural fix. Frontend (Vercel) is already a global CDN, so it is *not* the problem.

## Decision input #1 — where are your users? (answer before picking a region)
Railway has **no Middle East region**. Closest options to the UAE:
- **`europe-west4` (Amsterdam) — ~120 ms from UAE. Recommended** if users are MENA/Europe-centric.
- `asia-southeast1` (Singapore) — ~130–150 ms; better only if a large user share is South/SE Asia.
- Staying `us-east4` only makes sense if the bulk of users are in the Americas.

Recruiters (you) are in the UAE; **candidate assessments run on E2B (external, not Railway), so this move does NOT change candidate assessment latency** — it only speeds the recruiter UI + API. So optimise for where the *recruiters* are → europe-west4.

> Confirm `europe-west4` is offered on the current Railway plan (and whether it is a "Metal" region — the CLI `scale` command currently errors on a `railwayMetal` schema field, so verify in the dashboard).

## Hard constraint — move the WHOLE stack, co-located
The app makes **many** DB / Redis / Neo4j round-trips **per request** over Railway private networking (`*.railway.internal`). If the app moves but the DB stays, every query pays cross-region latency → **worse than today**. So this is all-or-nothing for:

`resourceful-adaptation` (web) · `taali-worker` · `taali-worker-scoring` · `Postgres` · `Redis` · `neo4j`

## What moves, and how
| Service | Has data? | Migration approach |
|---|---|---|
| **Postgres** (926 MB, 55 tables) | Yes — irreplaceable | New PG in target region → `pg_dump`/`pg_restore` in a maintenance window (~10–20 min downtime). Optional near-zero-downtime via logical replication (more setup). |
| **Neo4j** (candidate graph) | Yes, but re-derivable | Prefer `neo4j-admin dump`/`load` to the new instance (keeps the graph; avoids re-running Haiku/Voyage extraction = $$). Fallback: provision empty + `candidate_graph/backfill.py` (slower + costs LLM spend — see [graph_sync cost]). |
| **Redis** (Celery broker + cache) | Ephemeral | Provision **empty** in target region. No migration; in-flight Celery tasks lost during the window (acceptable). |
| **Web + 2 workers** | No (stateless) | New services in target region from the same repo/image; copy all env (below). |
| **Frontend** (Vercel) | n/a | Global CDN already. Only its `VITE_API_URL` may need repointing (see cutover). |

## Approach — blue-green (parallel stack + cutover). Lowest risk.
1. **Provision the target stack** in `europe-west4`: new Postgres, Redis, Neo4j, and the 3 app services. **Copy env carefully** — the web service alone has **75 env vars**, and the 2 workers have their own sets. Export with `railway variables --service <svc> --kv` and re-set on each new service. **Mind the per-service drift rule** ([production_access]): a single missing var (`SECRET_KEY`, `MAINSPRING_*`, `FRONTEND_URL`, Anthropic/Voyage keys, Stripe, Workable) breaks things subtly.
2. **Rewire internal URLs** on the new app + workers: `DATABASE_URL`, `REDIS_URL`, `NEO4J_URI` → the new `*.railway.internal` hostnames. **Leave `DATABASE_PUBLIC_URL` unset on the web + worker services** — `platform/database.py` resolves `os.environ.get("DATABASE_PUBLIC_URL") or DATABASE_URL`, so a stray public-proxy value there silently routes the app off the internal network and defeats the whole co-location latency goal. Reserve `DATABASE_PUBLIC_URL` for local scripts/`psql` only.
3. **Migrate Postgres** in a maintenance window: freeze writes (stop the 2 old workers + put the old web in a maintenance state), `pg_dump` old → `pg_restore` new. **Verify row counts** (`candidate_applications`=55,564, `candidates`≈40k, `usage_events`≈59k, `roles`=111). Alembic is already at head on the restored DB → boot migration is a no-op.
4. **Migrate Neo4j** (`dump`/`load`) so candidate search works immediately post-cutover.
5. **Cut over the API entrypoint** (pick one):
   - **(a, recommended) Give the new web service a stable custom domain** (e.g. `api.taali.ai`) and point the frontend `VITE_API_URL` at it **once** — note this first switch still needs **one Vercel redeploy** (Vite inlines `import.meta.env.VITE_API_URL` at build time, so the already-built bundle keeps hitting the old host until rebuilt). After that, future region moves behind the stable domain need no frontend change. Or
   - **(b) Update Vercel `VITE_API_URL`** to the new Railway service URL + redeploy the frontend.
6. **Update external callbacks**: the **Stripe webhook endpoint URL** (Stripe dashboard) if it targets the old Railway host. (`WORKABLE_WEBHOOK_SECRET=skip` → Workable webhooks not in use; `MAINSPRING_INGEST_URL` is outbound to a *separate* project → unaffected.)
7. **Verify from the UAE**: login → home/jobs load (measure ttfb with the `openapi.json`/`X-Process-Time-Ms` method — expect ~half), a decision round-trip, batch scoring, Workable sync, metering writes, candidate search (Neo4j).
8. **Soak, then decommission** the old us-east4 stack (keep ~2–3 days as rollback).

## Downtime
- **~10–20 min** (simple dump/restore window). **Near-zero** with Postgres logical replication if worth the extra setup. For a pre-pilot tool, a scheduled window is fine.

## Rollback
- Blue-green keeps the old stack intact until decommission. If cutover misbehaves, repoint `VITE_API_URL`/custom domain back to us-east4.
- **Caveat:** writes that land on the new DB after cutover are lost if you roll back to the old DB. Keep the write-freeze→cutover window tight; don't roll back after real traffic has written to the new DB.

## Risks & gotchas (checklist)
- [ ] **Env drift** — copy all 75+ vars to each of the 3 new services; diff old-vs-new with `railway variables --kv`.
- [ ] **Internal hostnames** changed → `DATABASE_URL`/`REDIS_URL`/`NEO4J_URI` updated on web + both workers.
- [ ] **Stripe webhook URL** updated in the Stripe dashboard (currently live-key path is the one unverified per [stripe_sandbox_verified]).
- [ ] **Custom domain / `VITE_API_URL`** repointed; Vercel redeploy if not using a stable custom domain. Watch the [Vercel 100-deploys/day cap].
- [ ] **Neo4j dump/load** (not backfill) to avoid LLM re-extraction cost.
- [ ] **Row-count + spot-check** the restored DB before cutover.
- [ ] **Plan/region availability** for `europe-west4` confirmed in the dashboard.
- [ ] **Workers** auto-deploy wiring re-established for the new services (GitHub connection or `railway up`).

## Cost
- **~2× infra during the parallel-run window** (two stacks); steady-state ≈ same (Railway pricing is usage-based, largely region-agnostic).
- **Effort:** ~half a day to provision + migrate + verify, plus one scheduled maintenance window. Neo4j-backfill route adds LLM $ — avoid via dump/load.

## Lighter alternatives (if a full move is too much right now)
- **Postgres read-replica in europe-west4** + route read-heavy endpoints to it (writes stay in us-east4). Cuts *read* latency without moving the primary, but adds read/write routing complexity and writes still cross-globe. More moving parts than a clean full move — generally **not** worth it.
- **Keep cutting round-trips** (no migration): the cheapest path, already started (#550 cut Jobs 21→2 calls). Capped — geography stays. The next safe win there is deduping/relaxing the 30s pollers and making `/auth/me` non-blocking (the latter needs careful local verification — it broke integration tests when attempted blind).

## Recommendation
If your users are MENA/EU-centric, **do the blue-green move to `europe-west4`.** It's the only thing that structurally fixes the latency. The DB is only 926 MB, Redis is throwaway, and Neo4j is dump/load-able — so it's a contained migration behind a short maintenance window, with the old stack as a clean rollback.
