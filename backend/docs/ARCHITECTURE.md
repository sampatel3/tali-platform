# Backend Architecture

## Overview

The TALI backend follows a **component-based modular monolith** architecture.
Code is organised into self-contained components that communicate through
well-defined service interfaces rather than reaching into each other's internals.

## Directory Layout

```
backend/app/
  platform/           # Cross-cutting infrastructure (config, DB, security, middleware, logging)
  components/         # Feature-aligned modules
    auth/             # Authentication & user management
    assessments/      # Assessment lifecycle (create → start → submit → score)
    scoring/          # Heuristic & V2 scoring engine
    candidates/       # Candidate profiles
    tasks/            # Coding task definitions
    organizations/    # Organisation settings, analytics
    notifications/    # Email sending (Resend, templates, Celery tasks)
    integrations/     # External service wrappers
      e2b/            # E2B sandbox management
      claude/         # Anthropic Claude chat
      workable/       # Workable ATS (MVP-disabled)
      stripe/         # Stripe billing (MVP-disabled)
    team/             # Team member management
  shared/             # Utilities shared across components (_utcnow, helpers)
  core/               # **Deprecated** re-export shims (will be removed)
  services/           # **Deprecated** re-export shims (will be removed)
  models/             # **Deprecated** re-export shims (will be removed)
  schemas/            # **Deprecated** re-export shims (will be removed)
```

## Principles

1. **Component ownership** – each component owns its models, schemas, service
   logic, and API routes.
2. **No direct cross-component DB access** – components expose service
   functions; callers never import another component's models to query directly.
3. **Stable external API** – API endpoint paths and response shapes do not
   change during restructuring. The frontend is unaffected.
4. **Feature flags** – MVP-disabled features (Stripe, Workable, Celery,
   calibration, proctoring, Claude-based scoring) are controlled via
   `platform/config.py` settings.
5. **Re-export shims** – during migration, old import paths (`core.*`,
   `services.*`, `models.*`) continue to work via thin re-export modules.
   These will be removed once all consumers are updated.

## Migration Strategy

The restructure is executed in 6 incremental PRs, each deployable independently:

| PR | Scope | Key Files |
|----|-------|-----------|
| 1 | Platform layer scaffold | `platform/*`, `core/*` shims |
| 2 | Scoring component | `components/scoring/*` |
| 3 | Integrations | `components/integrations/*` |
| 4 | Notifications | `components/notifications/*` |
| 5 | Assessment service layer | `components/assessments/*` |
| 6 | Remaining components + cleanup | `components/{auth,candidates,tasks,organizations,team}/*` |
