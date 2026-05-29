### You are a brand (on top of `mainspring`)

You own your surface and your data — **not** the substrate:

- **Build on the substrate; never fork it.** For anything cross-cutting (AI calls,
  decision writes, billing, auth, integrations) call into `mainspring`. If the
  capability you need isn't there, the fix is to add/extend it in `mainspring` (raise
  an ADR if it's a boundary change) — **not** to implement it locally "just for now".
- **Don't reach into another brand.** No imports from the other brand, no sharing of
  the other brand's migration revisions (ADR-0005). Your Alembic chain is yours alone.
- **Brand-specific is fine here:** UX, routes, copy, brand config, your own data
  model. Brand assumptions must never flow *down* into the substrate.
- **Metering still applies to you.** Even brand-initiated AI features go through the
  substrate's metered client (ADR-0003) — there is no brand-local SDK path.
- **Smell check:** if you're about to copy logic that the other brand also has, stop —
  it belongs in `mainspring`.
