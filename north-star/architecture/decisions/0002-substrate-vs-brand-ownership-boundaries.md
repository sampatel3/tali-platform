# 2. Substrate-vs-brand ownership boundaries

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

One product is delivered through multiple brands (`taali`, `cadence`) on a shared
substrate (`mainspring`). Without a hard ownership rule, two failure modes appear:
logic gets copy-pasted between brands (divergence, double maintenance), or brand-
specific assumptions leak into the substrate (coupling, brittle shared code). Agents,
lacking the whole-system picture, are especially prone to both.

## Decision

We will treat the substrate/brand boundary as a first-class invariant:

- **Shared, cross-cutting, brand-agnostic logic lives in `mainspring`.** This includes
  the metered Anthropic client, the decision engine, billing plumbing, auth
  primitives, and integration adapters.
- **Brand-specific code stays in its brand** — surface (UX, routes, copy), brand
  configuration, and the brand's own data and migration chain.
- Brands build *on* the substrate; they never fork it. The substrate makes no
  assumptions about a specific brand.

The rule is encoded as data in `model.yaml` (`boundaries[].substrate-vs-brand`).

## Consequences

- Adding a brand is cheap; shared behaviour changes in one place.
- Requires judgement at the seam: "is this cross-cutting or brand-specific?" When
  logic is duplicated across brands, it belongs in the substrate; when substrate code
  branches on brand identity, that is a smell to fix.
- `tali-platform` predates this split and is legacy — capabilities migrate out of it
  into the substrate/brand model when touched, never the reverse.
