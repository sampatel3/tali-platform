# 6. North Star model format

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

The North Star needs a canonical architecture model. The format choice was made
deliberately, *not* by reaching for the most familiar tool. The brief was explicit:
research and reason before picking Mermaid/MCP/Structurizr.

Key facts that shaped the decision:

- **The primary consumer is an AI agent reading text**, not a human admiring a
  rendered diagram. So unambiguous structured text, zero toolchain friction, and a
  *machine-readable* model (for drift detection) matter more than render fidelity.
- **C4 is the right vocabulary** (Context → Container → Component → Code) regardless
  of tool. Adopting the vocabulary is the real win.
- **Structurizr DSL** is the strongest "single source of truth → many views" model
  and has MCP servers that let agents read/validate/render it — but those servers are
  community/early-stage, and the toolchain (JVM/CLI/workspace) is heavy. For a solo
  founder this is a real risk of becoming "the framework nobody maintains."
- **Mermaid C4** renders natively on GitHub, but its C4-specific syntax
  (`C4Context`/`C4Container`) is officially **experimental** with weak layout. Plain
  Mermaid `flowchart`/`graph` renders reliably.
- A folder of prose Markdown is easy but **not machine-readable**, so it can't power
  drift detection — the feature that makes this a product, not a doc.

## Decision

We will make the canonical model a **machine-readable `model.yaml` in C4 vocabulary**
(the single source of truth), and **render human-facing views with plain Mermaid
`flowchart`** (not experimental C4 syntax). Specifically:

- `architecture/model/model.yaml` is authoritative. It carries the C4 elements *and*
  `implementation` mappings from elements to repo + code paths.
- Views in `architecture/model/views/*` are derived from the model and use plain
  Mermaid so they render everywhere with no toolchain.
- We adopt C4 *vocabulary* throughout; we do **not** adopt the Structurizr DSL or its
  ecosystem now.
- We keep a documented, low-effort **upgrade path to Structurizr DSL + MCP** for the
  day an MCP-driven, on-demand query workflow proves its worth. `model.yaml`'s
  structure maps cleanly onto Structurizr concepts, so the migration is mechanical.

## Consequences

- Zero toolchain to keep alive: YAML + Mermaid + a little stdlib Python.
- The model is queryable by scripts and agents, enabling `validate_model.py` and
  `check_drift.py` — the product features.
- We give up Structurizr's automatic multi-view layout; views are hand-kept in sync
  with the model (CI nudges this). Accepted: views are few and the model is small.
- If/when we adopt Structurizr+MCP, this ADR will be superseded, not contradicted —
  the C4 vocabulary and the model's shape carry over.
