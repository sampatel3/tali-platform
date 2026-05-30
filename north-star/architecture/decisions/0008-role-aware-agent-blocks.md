# 8. Role-aware agent blocks (substrate / brand / legacy)

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

ADR-0007 established that the North Star reaches each repo as a managed block in its
`CLAUDE.md`. The first cut injected the *same* block everywhere. But the repos do not
have the same obligations at the architectural boundary — in places they are
**opposites**:

- The **substrate** (`mainspring`, described as the operations runtime brands build
  on) must *own* cross-cutting capability and stay brand-agnostic.
- A **brand** (`taali-brand`, `cadence`) must *not* own cross-cutting capability — it
  must consume the substrate's and never fork it.
- **Legacy** (`tali-platform`) must not grow new architecture at all; it is drained.

A single block that tells a brand repo "you own the cross-cutting logic" would induce
exactly the drift the North Star exists to prevent. Guidance must match the reader's
role.

## Decision

We will make the injected block **role-aware**. Each target in `sync.config.json`
declares a `role` ∈ {`substrate`, `brand`, `legacy`}. The rendered block is a shared
core (the invariants, the guardrail, the self-maintaining rule — identical for
everyone) plus a **role section** from `agent/blocks/<role>.md`:

- `substrate.md` — own the capability, never branch on a brand, you are the invariant
  enforcer, adopt migrating capabilities.
- `brand.md` — build on the substrate, never fork it; don't reach into another brand;
  brand-specific stays local; metering still applies.
- `legacy.md` — don't extend the monolith; migrate capabilities out; keep the drift
  checker's mappings honest; invariants still hold.

The digest (staleness signal) covers the template and all role blocks, so changing any
role's guidance re-stales every consuming repo until re-synced.

## Consequences

- Each repo gets guidance that is correct *for it*, which is the whole point of a
  north star: the substrate is told to absorb cross-cutting logic; brands are told to
  delegate it. The shared invariants stay verbatim everywhere.
- Slightly more to maintain: three role files instead of one block. They are short and
  change rarely.
- Adding a new repo means picking its role; an unknown/missing role fails loudly rather
  than silently shipping the wrong guidance.
- Supersedes the single-block rendering in ADR-0007 (the injection *mechanism* in 0007
  stands; only the block content became role-aware).
