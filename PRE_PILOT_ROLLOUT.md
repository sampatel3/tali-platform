# TAALI Pre-Pilot Rollout Runbook

Date: 2026-02-13  
Branch: `codex/pre-pilot-hardening`

## 1. Critical Warning

Migration `015_role_first_applications_pause_reset` is intentionally destructive and deletes:
- `assessments`
- `candidate_applications`
- `candidates`

Run only when you are ready to wipe pre-pilot data in that environment.

## 2. Environment Order

1. Local
2. Staging
3. Production

Do not progress until smoke checks pass in the previous environment.

## 3. Pre-Migration Checklist (Each Environment)

1. Confirm branch/commit deployed.
2. Confirm DB backup/snapshot completed.
3. Confirm team acknowledges irreversible reset.
4. Confirm `ANTHROPIC_API_KEY`, `E2B_API_KEY`, and mail settings are present.

## 4. Migration Commands

From backend directory:

```bash
alembic upgrade 015_role_first_applications_pause_reset
alembic upgrade head
```

## 5. Post-Migration Verification

Run SQL checks:

```sql
SELECT COUNT(*) FROM candidates;
SELECT COUNT(*) FROM assessments;
SELECT COUNT(*) FROM candidate_applications;
```

Expected: all `0` immediately after migration.

Confirm schema objects exist:

```sql
\d roles
\d candidate_applications
\d role_tasks
\d assessments
```

## 6. API Smoke Checks

1. `POST /api/v1/roles` (create role)
2. `POST /api/v1/roles/{id}/upload-job-spec`
3. `POST /api/v1/roles/{id}/tasks`
4. `POST /api/v1/roles/{id}/applications`
5. `POST /api/v1/applications/{id}/upload-cv`
6. `POST /api/v1/applications/{id}/assessments`
7. `POST /api/v1/assessments/{id}/claude` with healthy Claude
8. Simulate Claude failure and verify pause behavior:
   - `execute`, `submit`, and CV upload endpoints return `423 ASSESSMENT_PAUSED`
   - `POST /api/v1/assessments/{id}/claude/retry` resumes timer

## 7. UI Smoke Checks

1. Candidates page enforces role-first flow.
2. Application creation is blocked until job spec exists.
3. Assessment creation is blocked until application CV exists.
4. Dashboard filtering/grouping by role works.
5. Direct load `/candidate-detail?assessmentId=<id>` renders correctly.

## 8. Test Commands

Backend:

```bash
cd backend
../.venv/bin/pytest -q
```

Frontend:

```bash
cd frontend
npm test -- --run
npm run build
```

## 9. Rollback Note

Application rollback is possible via deploy rollback.  
Data rollback requires restoring the DB backup/snapshot created before migration.
