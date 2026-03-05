# Recruiter Workflow V2 Rollout

This runbook maps directly to the execution plan for **Phase 6 (Hardening + Rollout)**.

## Preconditions

1. Backend migration and compatibility layer deployed.
2. Frontend deployed with V2 routes behind org flag.
3. Legacy routes remain available.
4. Kill switches are configured:
   - Org flag: `recruiter_workflow_v2_enabled`
   - Global force-off: `RECRUITER_WORKFLOW_V2_FORCE_OFF=true`

## Deploy Order

1. Deploy backend:
```bash
./scripts/railway/deploy_backend.sh
```

2. Deploy worker (if enabled):
```bash
RAILWAY_WORKER_SERVICE=<worker-service-name> ./scripts/railway/deploy_worker.sh
```

3. Deploy frontend through normal production path.

4. Start with org-level canary:
```bash
curl -X PATCH "$API_BASE_URL/organizations/me" \
  -H "Authorization: Bearer $TAALI_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recruiter_workflow_v2_enabled": true}'
```

## Monitoring Checks

Use the rollout health script for org-by-org validation:

```bash
TAALI_API_BASE_URL="https://<backend>/api/v1" \
TAALI_BEARER_TOKEN="<token>" \
./scripts/check_recruiter_workflow_v2_rollout.py --org-id <ORG_ID> --hours 24 --sample-roles 5
```

Script output includes:
1. Open stage counts and outcome split.
2. Pipeline event coverage (`pipeline_initialized` gaps).
3. External stage drift rate.
4. `/roles/{id}/pipeline` latency probe (`p95_ms`).
5. Alert summary with non-zero exit code when thresholds are breached.

## Rollback

1. Immediate org rollback:
```bash
curl -X PATCH "$API_BASE_URL/organizations/me" \
  -H "Authorization: Bearer $TAALI_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recruiter_workflow_v2_enabled": false}'
```

2. Global rollback:
- Set `RECRUITER_WORKFLOW_V2_FORCE_OFF=true` in production backend env.
- Redeploy backend:
```bash
./scripts/railway/deploy_backend.sh
```

3. Keep dual-write period active and reconcile before re-enabling.

