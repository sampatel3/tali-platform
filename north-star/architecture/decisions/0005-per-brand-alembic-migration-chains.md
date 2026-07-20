# 5. Per-brand Alembic migration chains

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

Each brand owns brand-specific data. If brands shared a single Alembic migration
chain (or cross-imported each other's revisions), a migration for one brand would
couple to another's schema, deployments would interlock, and revision history would
become a shared bottleneck. Agents adding a model field often reach for "the"
migrations directory without realising there is one per brand.

## Decision

We will give **each brand its own Alembic chain.** Brands never cross-import or share
migration revisions. Substrate-owned schema (if any) is migrated independently of any
brand. A brand's migrations are part of that brand's repo.

Current (legacy) home: `backend/alembic` (single legacy chain). Target: per-brand
chains in each brand repo as capabilities migrate out of legacy.

## Consequences

- Brands evolve their schemas independently and deploy without interlocking.
- There is no single global migration ordering — intentional. Shared/substrate schema
  changes are coordinated explicitly, not implicitly via a shared chain.
- A migration must be added to the *correct* brand's chain; cross-importing a revision
  from another brand is prohibited.
- Modelled as `invariants[].migrations-per-brand` and on the `brand-migrations`
  component in `model.yaml`.
