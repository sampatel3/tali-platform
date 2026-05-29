# NORTH STAR

> The canonical, high-level reference for the platform. **Read this before
> non-trivial work.** It is intentionally short and stable. Detail lives in the
> model (`architecture/model/model.yaml`), the views, and the ADRs.
>
> **If your change would contradict anything here, do not just do it. Raise an ADR
> (`architecture/decisions/`) proposing the change.** The North Star is amended by
> decision, not by drift.

---

## One product, one substrate, many brands

There is **one product**, built on **one shared substrate** (`mainspring` — an
operations runtime over a stateful business pipeline, the layer above LLM-chaining
frameworks), exposed through **brands** that sit on top of it. Brands differ in
surface, positioning, and data; they do **not** fork the substrate.

```
        ┌─────────────┐     ┌─────────────┐
        │   taali     │     │   cadence   │     (brands: surface + brand-owned data)
        └──────┬──────┘     └──────┬──────┘
               │                   │
               └─────────┬─────────┘
                         ▼
                 ┌───────────────┐
                 │   mainspring  │            (substrate: shared, cross-cutting)
                 └───────────────┘
```

`tali-platform` is **legacy**. Do not extend it; migrate capabilities out of it into
the substrate/brand model when touched.

## The boundary that matters most: substrate vs brand

- **Substrate (`mainspring`)** owns everything cross-cutting and brand-agnostic:
  framework, shared services, the metered Anthropic client, auth primitives, the
  decision engine, billing plumbing, integration adapters.
- **A brand (`taali`, `cadence`)** owns its surface (UX, routes, copy), its
  brand-specific configuration, and its **own data + migration chain**.
- **Shared logic lives in the substrate. Brand code stays in its brand.** If you find
  yourself copying logic between brands, it belongs in `mainspring`. If you find
  brand-specific assumptions leaking into `mainspring`, that's a bug.

See **ADR-0002**.

## Invariants (do not violate without a superseding ADR)

These are settled. They are CI-gated or convention-gated. Agents must uphold them.

1. **Metering is mandatory.** *Every* call to Anthropic goes through the metered
   client and writes a `UsageEvent`. There is no raw SDK call path. (**ADR-0003**)
2. **Decision writes are serialized per org.** Workable decision writes go through a
   per-org mutex; never write decisions concurrently for the same org. (**ADR-0004**)
3. **Migrations are per-brand.** Each brand owns its Alembic chain. Never cross-import
   or share migration revisions between brands. (**ADR-0005**)
4. **Substrate/brand boundary** (above). (**ADR-0002**)

## Tech shape

- **Languages:** TypeScript (frontend/surface) and Python (backend/substrate).
- **Backend:** FastAPI, PostgreSQL (SQLAlchemy 2 + Alembic), Redis, Celery.
- **AI:** Anthropic Claude, always via the metered client.
- **External:** Stripe (billing), Workable (ATS), E2B (sandboxes).

(The authoritative, structured version of the above — with code-path mappings — is
`architecture/model/model.yaml`. Diagrams are in `architecture/model/views/`.)

## How to use this as an agent

1. **Read this file first.** Then, if relevant, the matching ADR(s) and the model.
2. **Stay inside the boundaries and invariants.** They are not suggestions.
3. **If the right change conflicts with the North Star**, stop and propose an ADR
   instead of silently diverging. A wrong-but-consistent codebase is recoverable; a
   silently-diverged one is not.
4. **Self-maintaining rule:** any architectural change you make must, in the *same
   PR*, either update `model.yaml` (+ affected views) or add an ADR — usually both.

## Where things live

| You want…                              | Go to                                          |
| -------------------------------------- | ---------------------------------------------- |
| The high-level rules (this)            | `NORTH_STAR.md`                                |
| The structured model (source of truth) | `architecture/model/model.yaml`                |
| Rendered C4 views                      | `architecture/model/views/`                    |
| *Why* a decision was made              | `architecture/decisions/`                      |
| How the North Star reaches each repo   | `agent/README.md`                              |
