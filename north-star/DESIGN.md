# North Star — Design & Scope Sign-off

> **Status:** Accepted (scope confirmed with Sam, 2026-05-29)
> **Owner:** Sam (solo founder)
> **Destined home:** a new standalone repo `north-star` (see "Repo & extraction" below)

This document is the design record for the **North Star**: a persistent, high-level
architecture reference whose job is to keep AI coding agents (Claude Code) aligned
with the intended system design across many sessions and across all platform repos.

It is deliberately short. The detail lives in the model, the ADRs, and the code.

---

## 1. The problem (why this exists)

Sam builds one product across four repos, almost entirely through Claude Code:

| Repo             | Role                                                          |
| ---------------- | ------------------------------------------------------------ |
| `mainspring`     | shared substrate / framework (cross-cutting architecture)    |
| `taali`          | a brand on the substrate                                     |
| `cadence`        | a brand on the substrate                                     |
| `tali-platform`  | legacy                                                        |

Agents are stateless across sessions. Without a durable reference they:

- re-litigate decisions that are already settled,
- drift from the intended substrate/brand boundaries,
- generate code that is locally reasonable but architecturally wrong.

There is **no off-the-shelf "north star for AI agents"** product. The state of the
art is roll-your-own. This package is that, built properly.

## 2. Scope (confirmed with Sam)

Four scope questions were put to Sam. His answers:

1. **Audience / "as a product":** *Internal now, extractable later.* Build a
   robustly-engineered system for the platform's own repos, but keep a clean seam
   so it could be extracted into a standalone tool later without a rewrite.
2. **Canonical home:** *A new repo* (not `tali-platform`, not folded into
   `mainspring`). The North Star is its own thing that the other repos consume.
3. **Model format:** *Research and reason first — do not reflexively pick
   Mermaid/MCP.* See ADR-0006 for the reasoned decision.
4. **Agent plumbing:** *Sync script + CLAUDE.md block* (not git submodule, not a
   bespoke MCP server to start).

### Non-goals (explicitly out of scope for now)

- A generic, externally-distributable installer for arbitrary repos. (We keep the
  seam for it; we don't build it.)
- A standing MCP server. (Documented as a future upgrade path in ADR-0006; not
  built — sync covers the need at far lower maintenance cost.)
- Replacing existing per-repo `CLAUDE.md` files. The North Star *augments* them via
  a managed block; it does not own them.

## 3. Repo & extraction (session constraint)

The canonical home is a **new repo**. This build session, however, can only push to
`sampatel3/tali-platform`. So the entire package is authored here, self-contained,
under `north-star/`, laid out exactly as it will live as its own repo.

To lift it into a real repo (one command, preserving nothing tali-platform-specific):

```bash
# from a clone of tali-platform, on this branch
git subtree split --prefix=north-star -b north-star-export
# then, in an empty new repo:
git pull <path-to-tali-platform> north-star-export
# or simply: cp -r north-star/* /path/to/new/north-star-repo && git init
```

Nothing in this package hard-codes `tali-platform`. The target repos are listed in
`agent/sync.config.json`, which the consumer edits.

## 4. What "a product, not a doc" means here

Four properties separate this from a folder of Markdown:

1. **A machine-readable model** (`architecture/model/model.yaml`) — the single
   source of truth, in C4 vocabulary, with each element mapped to the repo/path
   that implements it. Views are derived from it; docs reference it.
2. **A maintained ADR log** — the *why* behind settled decisions, so agents stop
   re-litigating them. Seeded with Sam's real decisions (see §6).
3. **Agent plumbing** — a sync step injects a managed North Star block into every
   target repo's `CLAUDE.md`, so every session starts aligned, with a guardrail
   convention ("read the North Star; don't contradict it — raise an ADR instead").
4. **Drift detection** — `scripts/check_drift.py` verifies the model still matches
   reality (the code paths it claims exist actually exist). This is the feature
   that makes it self-maintaining rather than rot-prone. CI runs it.

A self-maintaining rule ties it together: **any architectural change must update the
model or add an ADR in the same PR** (enforced softly by CI + the agent guardrail).

## 5. Architecture of the North Star itself

```
north-star/
  NORTH_STAR.md                 # the canonical high-level reference agents read first
  DESIGN.md                     # this file
  README.md                     # what it is, how to adopt, how to extract
  CLAUDE.md                     # how agents work IN this repo
  architecture/
    model/
      model.yaml                # SINGLE SOURCE OF TRUTH (C4 model as data + code mappings)
      schema.md                 # documents model.yaml
      views/                    # human-facing C4 views (Mermaid), derived from the model
        context.md
        containers.md
        components-substrate.md
    decisions/                  # ADR log (Nygard format)
      README.md  0000-template.md  0001..0007-*.md
  agent/
    NORTH_STAR.block.md         # the managed block injected into each repo's CLAUDE.md
    sync_north_star.py          # the sync script
    sync.config.json            # which repos to sync into
    README.md                   # how the plumbing works
  scripts/
    validate_model.py           # model + ADR index integrity
    check_drift.py              # model vs reality
  .github/workflows/
    north-star-ci.yml           # validate + drift + sync-freshness on every PR
```

Dependency direction: **views and docs depend on the model; the model depends on
nothing.** Edit the model first; regenerate/refresh views; record the *why* in an ADR.

## 6. Seeded ADRs (Sam's real decisions)

These existed informally (e.g. the golden rules in `tali-platform/docs/claude/README.md`)
and convert almost 1:1 into ADRs:

- **0002** Substrate-vs-brand ownership boundaries — shared logic in `mainspring`,
  brand code stays in its brand.
- **0003** Metering invariant — every Anthropic call goes through the metered client
  and writes a `UsageEvent`; CI-gated.
- **0004** Decision-write serialization — Workable decision writes are serialized
  per-org via a mutex.
- **0005** Per-brand Alembic migration chains — each brand owns its chain; no
  cross-importing migrations.

Plus two ADRs about the North Star itself: **0006** (model format) and **0007**
(agent injection via sync block). And **0001** records the practice of keeping ADRs.

## 7. Deliberate non-over-engineering

Things we *chose not to build*, and why:

- **No Structurizr/JVM toolchain.** The model is plain YAML + Mermaid. (ADR-0006)
- **No standing MCP server.** Sync into `CLAUDE.md` gets the context in front of the
  agent for free, in-context, with zero runtime. MCP is the documented next step
  only if an on-demand query workflow is actually needed.
- **No git submodules.** A sync script with a CI freshness check is easier for a solo
  dev to reason about and debug. (ADR-0007)
- **Stdlib-first scripts.** `validate_model.py` and `check_drift.py` need only Python
  3.11+ and PyYAML — no bespoke framework.
