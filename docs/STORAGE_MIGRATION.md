# Storage migration: AWS S3 → Tigris

This document is the playbook for moving the platform's object storage
from AWS S3 to Tigris (Railway's S3-compatible storage add-on). Run it
once per environment.

## Why

The AWS S3 IAM credentials in production rotated (or were revoked) and
`HeadBucket` returns 403, so:
- New uploads can't write to S3.
- The CV-redirect optimisation can't presign URLs.
- The cached-PDF-report optimisation can't write its cache.

Switching to Tigris is simpler than recovering AWS access: it's
S3-compatible (existing `boto3` code path stays the same), Railway-native
(one-click add-on, env vars auto-populated), and the migration script
handles the data move.

## Prerequisites

1. **A short-lived AWS read-only IAM key** for the old bucket. Needed
   only for the duration of the migration script run. Delete it
   immediately after.
2. **A Tigris bucket on Railway**. Add the Tigris add-on to your
   Railway project; it provisions a bucket and exposes credentials.

## Step-by-step

### 1. Provision Tigris on Railway

1. Railway dashboard → your project → **+ New** → **Database** →
   **Tigris** (or "Object Storage").
2. Note the auto-generated env vars Railway exposes for it. They
   typically include `BUCKET_NAME`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL_S3`. Names may vary —
   confirm in the Tigris service's "Variables" tab.

### 2. Wire env vars on the API service

On the **backend API** service (and the **scoring worker**, if
separate), set:

```
AWS_ACCESS_KEY_ID=<from Tigris>
AWS_SECRET_ACCESS_KEY=<from Tigris>
AWS_S3_BUCKET=<from Tigris, e.g. taali-prod>
AWS_S3_ENDPOINT_URL=<from Tigris, e.g. https://fly.storage.tigris.dev>
AWS_REGION=auto
```

Leave `S3_DISABLED` unset (or `false`).

Redeploy. Confirm the new state at `/health`:

```json
{
  "s3": { "available": true, "reason": "ok" }
}
```

If you still see `head_bucket failed: 403`, the credentials don't
match the bucket — re-check the Tigris service variables.

### 3. Get a short-lived AWS read-only key

In the AWS console:
1. IAM → Users → create a user `taali-migration-readonly`.
2. Attach an inline policy granting `s3:GetObject` and `s3:ListBucket`
   on the source bucket only.
3. Generate access key + secret. Note them.
4. **Schedule yourself to delete this user immediately after the
   migration succeeds.**

### 4. Dry-run the migration

From a Railway one-off shell on the backend service (or any environment
with the same DB + Tigris env wired up), set the source env vars and
run a dry-run:

```bash
export MIGRATION_SRC_AWS_ACCESS_KEY_ID=<short-lived AWS key>
export MIGRATION_SRC_AWS_SECRET_ACCESS_KEY=<short-lived AWS secret>
export MIGRATION_SRC_AWS_S3_BUCKET=taali-assessments        # the OLD bucket
export MIGRATION_SRC_AWS_REGION=us-east-1                   # the OLD region

python -m app.scripts.migrate_storage_to_tigris --dry-run
```

The output prints `examined`, `skipped_*`, `copied`, `rewritten` counts
plus per-row `DRY` lines. Confirm the numbers look right (rough order
of magnitude matches your candidate count).

### 5. Run the migration for real

Same env vars, drop `--dry-run`:

```bash
python -m app.scripts.migrate_storage_to_tigris
```

Per-row progress logs to stdout. The script commits one DB row at a
time, so a Ctrl-C or crash mid-run loses at most one in-flight copy
and the script is fully restartable — re-run it and rows already
migrated (URL pointing at the destination bucket) will be skipped.

### 6. Verify in the UI

Open a candidate that previously had a CV. Confirm:
- The CV preview renders inline.
- The browser's network tab shows a `307` redirect from
  `/api/v1/applications/<id>/documents/cv` to a `…tigris…` URL.
- `Cache-Control: private, max-age=600` is on the response.

### 7. Tear down

1. **Delete** the `taali-migration-readonly` IAM user in AWS.
2. **Unset** `MIGRATION_SRC_*` env vars from any Railway shell history.
3. After ~1 week of confidence the new bucket is serving everything,
   delete the AWS S3 bucket (or leave it as cold storage).

## What gets migrated

| Table | Column |
|---|---|
| `candidate` | `cv_file_url` |
| `candidate` | `job_spec_file_url` |
| `candidate_application` | `cv_file_url` |
| `role` | `job_spec_file_url` |
| `assessment` | `cv_file_url` |

Cached PDF reports under `cached/reports/<hash>.pdf` are **not**
migrated — they're derived artefacts and regenerate on next download.

## What if a copy fails

Per-row failures are logged with the table + row id + S3 key. The
script keeps going and reports a non-zero exit code at the end with
the list of failed rows. Common causes:

- Source key doesn't exist — file was deleted upstream. The DB row
  still has a stale URL; either re-fetch from Workable (UI: "Fetch
  CVs") or null out the column manually.
- Permission denied on source — the AWS key doesn't have
  `s3:GetObject`. Fix the IAM policy.
- Permission denied on dest — Tigris credentials are wrong. See
  step 2.

After fixing the cause, re-run the script — already-copied rows are
detected via destination `HeadObject` and skipped.
