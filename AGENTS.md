# Repository agent instructions

These instructions apply to every task in this repository. User instructions
take precedence.

## Production deployment: default path

Production deploys from `main` automatically. The normal release path is:

1. Fetch the latest `main`.
2. Create a fresh branch from `origin/main`.
3. Make the smallest scoped change.
4. Run checks proportional to the change.
5. Commit and push the branch.
6. Open a ready-for-review PR into `main` when the user asked to deploy.
7. Wait for required PR checks, fix only genuine failures, and merge when green.
8. Verify the automatic deployment for the exact merged `main` SHA.

Typical setup:

```bash
git fetch origin main
git switch -c codex/<short-description> origin/main
git status --short --branch
```

Do not develop a new fix on an old or already-merged feature branch. If the
current worktree contains unrelated changes, preserve them and use a clean
worktree or branch.

## Automatic deploys only

Merging to `main` triggers the configured Vercel and Railway deployments.

- Do not run `vercel --prod`, `railway up`, Railway redeploy APIs, or any other
  manual production rollout unless the user explicitly requests it or the
  automatic deployment has demonstrably failed and the user approves a manual
  recovery.
- Do not start duplicate deployments while an automatic deployment is pending.
- Do not replay approvals, jobs, webhooks, migrations, or queue messages as part
  of deployment verification.
- Use read-only status, log, commit, deployment, and health checks to verify the
  rollout.
- Verify the deployed commit is the exact merge SHA, not merely "the latest"
  deployment.
- For a frontend-only change, Vercel production readiness and the live alias are
  the blocking deployment checks. Note Railway's automatic status, but do not
  hold the frontend handoff open for unrelated services when production `/ready`
  remains healthy.
- For a backend or worker change, verify the affected Railway service, `/ready`,
  and the relevant worker heartbeat or queue capability.

## Proportionate checks

Avoid repeating broad checks after every small edit. Run independent checks in
parallel where practical.

- Documentation-only: `git diff --check` and any repository Markdown check.
  Do not run application test suites unless the documentation changes executable
  configuration.
- Frontend-only: affected Vitest files plus type-check. Run the production build
  when bundling, routing, imports, or environment handling changed.
- Backend-only: affected pytest files plus the relevant lint/type check.
- Cross-cutting or high-risk production behavior: targeted regression tests,
  then one final broader suite if the risk justifies it.
- Let required CI checks be authoritative. Do not rerun an already-green local
  suite without a concrete reason.

For a narrow hotfix, prefer one implementation pass, one targeted verification
pass, one consolidated review pass, and then the PR. Do not create repeated
review/test loops unless a concrete finding requires another change.

## Pull requests and merging

- Keep each PR scoped to one fix or outcome.
- Include the root cause, user impact, changed behavior, and checks in the PR
  body.
- If the user said "deploy", "ship", or equivalent, create a non-draft PR and
  proceed through required checks to merge. Do not stop after merely opening the
  PR unless a check, review requirement, or user decision blocks it.
- Never bypass a failing required check. Diagnose the failure; fix it if caused
  by the branch, otherwise report the external blocker clearly.
- Do not wait for optional checks once all required checks and the affected
  deployment are green.

## Production verification

A deployment task is complete when:

- the PR is merged into `main`;
- `origin/main` resolves to the expected merge SHA;
- the affected automatic deployment reports success for that SHA;
- the live alias or affected health endpoint responds successfully; and
- no manual duplicate rollout or production job replay was triggered.

Report the PR URL, merge SHA, affected deployment result, and any remaining
operational concern. Keep routine progress updates brief and name the real
blocker whenever the straightforward path must be extended.

## When extra time is justified

Spend additional time before merging only when there is concrete production
risk, such as destructive data changes, authentication or authorization changes,
payments, irreversible ATS/provider writes, schema migrations, concurrency or
idempotency defects, or an observed failing check. Explain that reason explicitly
instead of silently expanding a narrow deployment into a broad audit.
