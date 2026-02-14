# ADR Policy

Use this directory for Architecture Decision Records that change cross-domain interfaces.

## Required ADR cases

Create an ADR before merging any change that:
- modifies a contract between backend domains in `backend/app/domains/*`
- changes public HTTP API behavior under `/api/v1/*`
- changes shared frontend DTO/client contracts under `frontend/src/shared/api/*`
- introduces or removes integration adapter interfaces (Stripe/Workable/Email/Claude/Sandbox)

## ADR format

Minimum sections:
1. Context
2. Decision
3. Consequences
4. Rollout / deprecation plan (with concrete dates)
