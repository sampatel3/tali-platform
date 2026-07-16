# Celery queue layout

This document defines the required two-queue, two-worker Railway production
topology.

## Production: two workers, one Beat scheduler

Both Railway worker services use the supported bootstrap command:

```
python -m app.scripts.railway_worker_start
```

Their topology variables are deliberately different:

| Service | `TALI_WORKER_QUEUES` | `TALI_WORKER_BEAT` | Responsibility |
|---|---|---|---|
| `taali-worker` | `celery` | `true` | General tasks and the only Beat scheduler |
| `taali-worker-scoring` | `scoring` | `false` | Dedicated scoring capacity; never runs Beat |

The repository deployment wrapper pins and reads back all three topology values
(`TALI_SERVICE_MODE=worker` plus the queue and Beat settings) before uploading
each service. It also pins live metering and native apply consistently across
web and both workers. It then waits for a new successful Railway deployment for
both; one healthy worker can never produce a successful rollout result on its
own.

Do not replace the bootstrap with a raw Celery command in production: doing so
skips dependency/runtime validation that makes agent activation readiness
trustworthy.

- **`celery` queue** — general tasks: emails, `sync_workable_orgs`,
  pre-screen scoring, fireflies hooks, `sync_workable_orgs`, etc.
- **`scoring` queue** — `score_application_job` (per-application v3.0
  score) and `batch_score_role` (fan-out for "Re-score all").

Routing is configured in [`app/tasks/celery_app.py`](../app/tasks/celery_app.py)
via `task_routes` AND on each scoring task via the `queue=` kwarg
(defense in depth — direct `.delay()` calls still land on the right
queue even if the route table is misconfigured).

The split prevents long general tasks and Workable syncs from consuming scoring
capacity. The `sync_workable_orgs` Redis lock still prevents overlapping syncs.

Celery Beat also publishes a high-priority canary every minute to **each**
queue. Production `/ready` and agent activation require fresh heartbeats for
both `celery` and `scoring`; authenticated `/admin/health` carries the detailed
queue diagnostics. A live general worker cannot hide a missing or misrouted
scoring worker. The worker-produced capability report probes
configured Anthropic models and GitHub access, while a separately cached daily
Resend test send validates the API key and sender domain without contacting a
real recipient. Role activation applies the assessment-only provider checks
only when that role will actually use an assessment.

The role-agent cohort sweep runs every **60 minutes**. Activation and resume
also enqueue an immediate complete role tick, so the hourly schedule is a
recovery/proactive backstop rather than a delay after Turn on.

## Deployment invariant

`backend/railway.json` is shared by web and both workers, so it intentionally
does **not** declare an HTTP healthcheck. Celery workers do not serve `/ready`.
The coordinated repository wrapper first pins live metering and native apply on
all services and applies/verifies migrations through production
`DATABASE_PUBLIC_URL`; it then deploys and validates both workers, deploys web,
polls public `/ready`, and verifies the default worker's E2B, Resend-delivery,
and real GitHub capabilities. This provides an end-to-end check without
applying an impossible HTTP probe to worker processes.

From repo root:

```bash
RAILWAY_BACKEND_SERVICE=resourceful-adaptation \
RAILWAY_WORKER_SERVICE=taali-worker \
RAILWAY_SCORING_WORKER_SERVICE=taali-worker-scoring \
RAILWAY_BACKEND_URL=https://resourceful-adaptation-production.up.railway.app \
  ./scripts/railway/deploy_production.sh
```

## Failure modes addressed

- **Long-running task starves the queue** → fixed by scoring's own
  queue + the `sync_workable_orgs` Redis lock.
- **Worker crash mid-task** → `task_acks_late=True` is global; the
  task returns to the queue when the connection drops.
- **Lost score attempt** → the worker commits a `running` lease before the
  model call; the reaper uses separate conservative cutoffs (6h pending, 1h
  running) and conditionally claims stale rows so it cannot race a live claim.
- **One queue has no consumer** → queue-specific Beat canaries make
  production activation fail closed and identify the missing queue.
- **Scheduled task overlap** → `sync_workable_orgs` SETNX lock with a
  2h TTL; subsequent fires return `{status: skipped, reason:
  already_running}`.

## Watching the queues

```
railway logs -s taali-worker -e production -d --lines 200 \
  | grep -E "Task .* received|Task .* succeeded|skipped.*already_running"
```

The `[ForkPoolWorker-N]` prefix tells you which slot processed which
task — useful to confirm the scoring queue isn't being dominated by
sync work.
