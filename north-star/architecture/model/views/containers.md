# View — Containers (C4 L2)

> Derived from `../model.yaml` (`containers`, `relationships`). Shows the
> substrate/brand topology and repo ownership. Refresh when the model changes.

```mermaid
flowchart TB
    subgraph brands [Brands]
        taali[Taali Surface<br/><i>repo: taali</i>]
        cadence[Cadence Surface<br/><i>repo: cadence</i>]
    end

    mainspring[Mainspring Substrate<br/><i>repo: mainspring</i><br/>invariant-bearing components live here]

    legacy[Legacy Backend<br/><i>repo: tali-platform</i><br/>migrate out when touched]

    anthropic[[Anthropic]]
    stripe[[Stripe]]
    workable[[Workable]]
    e2b[[E2B]]

    taali -->|builds on| mainspring
    cadence -->|builds on| mainspring
    legacy -.->|capabilities migrate to| mainspring

    mainspring -->|metered only| anthropic
    mainspring -->|bills via| stripe
    mainspring -->|serialized writes| workable
    mainspring -->|sandboxes| e2b
```

**Reading guide:** brands build *on* the substrate — they never fork it. The substrate
owns everything cross-cutting, including the components that carry invariants. The
legacy backend is a source to drain into the substrate/brand model, not to extend.
