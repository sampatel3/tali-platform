### You are the substrate (`mainspring`)

You are the operations runtime every brand builds on. With that comes the duty to
**keep yourself brand-agnostic**:

- **Own the cross-cutting capability, not the brand.** The metered Anthropic client,
  the decision engine + per-org serialization, billing/credit plumbing, auth
  primitives, and integration adapters live here — as the canonical, single
  implementation. When a brand needs cross-cutting behaviour, it consumes yours; it
  does not reimplement it.
- **Never branch on a specific brand.** No `if brand == "taali"`, no taali/cadence
  imports, no brand-specific schema assumptions. If you find yourself reaching for a
  brand's name, the design is wrong — expose a capability/extension point instead.
- **You are the invariant enforcer.** ADR-0003/0004/0005 are upheld *here*. If a brand
  could bypass metering or write decisions concurrently, that is a substrate gap, not
  a brand problem.
- **Capabilities migrating out of `tali-platform` land here first.** When you adopt
  one, update the North Star model's `implementation` mapping to point at the new
  `mainspring` home (and set `migratesTo` done).
