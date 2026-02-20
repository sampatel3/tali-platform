# RALPH Task Board (Reset)

Status: Active
Updated: 2026-02-20

## Operating Contract
- Workstream model: `M0..M6` executed as independent modules.
- Branch rule: every module must use its own branch (`codex/<module-name>`).
- PR rule: one module per PR; include tests/smoke evidence.
- Merge rule: merge to `main` only when explicitly requested.
- Deploy rule: Railway CLI scripts in `scripts/railway/` are the default backend deploy path.
- Test account rule: production smoke uses `sampatel@deeplight.ae` only.

## Module Registry

| Module | Branch | Owner | Depends On | Scope | Test Gate | Rollout Gate | Status |
|---|---|---|---|---|---|---|---|
| M0 Deployment Alignment | `codex/m0-railway-alignment` | Unassigned | None | Railway wrapper scripts + deploy contract docs | `bash -n scripts/railway/*.sh` and `scripts/railway/check_status.sh` | Backend deploy from repo root succeeds via wrapper | In progress |
| M1 Workable Stability Hotfix | `codex/m1-workable-stability-hotfix` | Unassigned | M0 | Sanitization, metadata-only sync path, progress/cancel robustness | `cd backend && pytest -q backend/tests/test_workable_sync_service.py backend/tests/test_api_workable_sync.py` | Production sync no NUL rollback + completes without manual cancellation | In progress |
| M2 Workable V2 Rebuild | `codex/m2-workable-v2-rebuild` | Unassigned | M1 | Run-state table/API (`run_id`, phase/counters/errors), resumable-friendly architecture | `cd backend && pytest -q backend/tests/test_api_workable_sync.py` | Large org sync observable/cancellable via run-aware status | In progress |
| M3 Candidates UX Cleanup | `codex/m3-candidates-ux-cleanup` | Unassigned | M1 | Remove Workable AI score UI, TAALI-first filters/sorts, cleaner sync messaging | `cd frontend && npm test -- --run` and `cd frontend && npm run build` | Candidate workflows clear, score semantics unambiguous | In progress |
| M4 Model Unification | `codex/m4-model-unification` | Unassigned | M1 | Single model resolver (`CLAUDE_MODEL`), legacy mismatch fail-fast, settings visibility | `cd backend && pytest -q backend/tests/test_unit_config_costs.py` | Production model reports Haiku and is consistent across app calls | In progress |
| M5 Prod-Account QA Automation | `codex/m5-prod-account-qa` | Unassigned | M0, M1, M4 | Production smoke scripts for Workable + model checks | `bash -n scripts/qa/*.sh` + scripted run with env secrets | One-command smoke detects failures via exit codes | In progress |
| M6 Task File Reset + Agent Board | `codex/m6-ralph-task-reset` | Unassigned | None | Archive old plan, publish modular board with PR template + merge policy | File review only | Agents can pick module independently without overlap | Completed |

## Execution Order
1. M0
2. M1
3. M4 (can run parallel after M1 starts)
4. M2
5. M3
6. M5
7. M6

## PR Checklist Template

Use this template in each module PR:

- Module: `M#`
- Branch: `codex/<module-name>`
- Scope confirmation: only module-specific files changed
- Test evidence:
  - Backend:
  - Frontend:
  - Smoke:
- API changes (if any):
- Railway impact:
- Risks + rollback:
- Ready for merge to `main`: `Yes/No` (merge only on explicit request)

## Agent Pickup Protocol
- Pick one `M#` row only.
- Set Owner from `Unassigned` to your handle.
- Move Status: `Queued -> In progress -> In review -> Done`.
- Do not edit another module's scope in the same branch/PR.

