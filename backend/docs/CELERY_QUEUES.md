# Celery queue layout

This document explains the two-queue setup and how to peel scoring off
to a dedicated worker on Railway when the existing single-worker
deployment becomes a bottleneck.

## Today: one worker, two queues

The Railway `taali-worker` service runs:

```
celery -A app.tasks worker --beat --queues=celery,scoring --concurrency=4
```

- **`celery` queue** — general tasks: emails, `sync_workable_orgs`,
  pre-screen scoring, fireflies hooks, `sync_workable_orgs`, etc.
- **`scoring` queue** — `score_application_job` (per-application v3.0
  score) and `batch_score_role` (fan-out for "Re-score all").

Routing is configured in [`app/tasks/celery_app.py`](../app/tasks/celery_app.py)
via `task_routes` AND on each scoring task via the `queue=` kwarg
(defense in depth — direct `.delay()` calls still land on the right
queue even if the route table is misconfigured).

The single worker consumes both queues. Concurrency 4 + the
`sync_workable_orgs` Redis lock means scoring slots stay free even
when a long Workable sync is running.

## Future: dedicated scoring worker

When the single worker becomes a bottleneck (signs: scoring latency
rising under load, batch_score_role spending >5 min queued, beat
schedule firings stacking), peel off scoring onto its own Railway
service.

### Steps

1. **Create a new Railway service** in the same project, pointing at
   the same repo (`backend/` root, same NIXPACKS build).
2. **Set env vars on the new service**:
   - Copy `DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`,
     `USE_CV_MATCH_V3`, plus the same Workable / AWS creds the existing
     worker uses (so it can fetch CVs).
   - Add `TALI_WORKER_QUEUES=scoring`
   - Add `TALI_WORKER_BEAT=false` (only one worker should run beat)
   - Optionally `TALI_WORKER_CONCURRENCY=2` (start small).
   - Same Procfile / startCommand: `python -m app.scripts.railway_worker_start`.
3. **Update the existing `taali-worker`**:
   - Set `TALI_WORKER_QUEUES=celery` (drop scoring).
   - Leave `TALI_WORKER_BEAT=true` (this one keeps the scheduler).
4. **Deploy both** — order doesn't matter; tasks just route to whichever
   worker is consuming the matching queue.

### Why this layout

- A single beat scheduler (in `taali-worker`) prevents duplicate
  scheduled fires.
- Scoring runs on its own pool, so a long-running general task can't
  starve it.
- The worker startup script (`app/scripts/railway_worker_start.py`)
  reads `TALI_WORKER_QUEUES`, `TALI_WORKER_CONCURRENCY`, and
  `TALI_WORKER_BEAT` env vars — no code change required to flip the
  topology.

### Reverting

Set the existing worker back to `TALI_WORKER_QUEUES=celery,scoring`
(or unset — that's the default), then delete the dedicated scoring
service. No data migration needed.

## Failure modes addressed

- **Long-running task starves the queue** → fixed by scoring's own
  queue + the `sync_workable_orgs` Redis lock.
- **Worker crash mid-task** → `task_acks_late=True` is global; the
  task returns to the queue when the connection drops.
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
