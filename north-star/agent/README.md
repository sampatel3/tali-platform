# Agent plumbing — how the North Star reaches every repo

The North Star only works if it's in front of the agent in every repo, every session.
We do that with a **managed block in each repo's `CLAUDE.md`** (ADR-0007) — not a
submodule, not a standing MCP server.

## The platform topology this targets

| Repo            | Role        | Default branch | What the block tells it                       |
| --------------- | ----------- | -------------- | --------------------------------------------- |
| `mainspring`    | substrate   | `master`       | Own cross-cutting capability; stay brand-agnostic; enforce invariants. |
| `taali-brand`   | brand       | `master`       | Build on the substrate, never fork it; don't reach into another brand. |
| `cadence`       | brand       | `master`       | (same as taali-brand)                         |
| `tali-platform` | legacy      | `main`         | Don't extend the monolith; migrate capability out; keep drift mappings honest. |

`mainspring` is the operations runtime the brands build on, so it is the **substrate**;
`taali-brand` and `cadence` are **brands**; `tali-platform` is **legacy** (the source
that capabilities are drained from). These roles drive the block content.

## How it works (role-aware — ADR-0008)

1. `sync.config.json` lists the target repos: each entry has a `repo`, a
   **`role`** (`substrate` | `brand` | `legacy`), and where its `CLAUDE.md` lives,
   plus `northStarRef` (the pointer embedded in the block).
2. `sync_north_star.py` renders a block = **shared core** (`NORTH_STAR.block.md`:
   invariants + guardrail + self-maintaining rule, identical everywhere) **+ the role
   section** from `blocks/<role>.md`. It stamps the block with a **digest** over the
   North Star source files (`NORTH_STAR.md`, `model.yaml`, the template, all role
   blocks, all ADRs), and splices it into each target `CLAUDE.md` between markers:

   ```
   <!-- NORTH-STAR:BEGIN -->
   ... generated guidance + guardrail + digest ...
   <!-- NORTH-STAR:END -->
   ```

   The markers make the sync **idempotent** and **non-destructive**: the repo keeps
   full ownership of the rest of its `CLAUDE.md`.

## Commands

```bash
python agent/sync_north_star.py             # write/update the block in all targets
python agent/sync_north_star.py --check      # CI mode: exit 1 if any block is missing/stale
python agent/sync_north_star.py --print substrate   # preview the substrate block
python agent/sync_north_star.py --print brand        # preview a brand block
python agent/sync_north_star.py --print legacy       # preview the legacy block
```

The digest is the staleness signal: change the North Star, and every target's block
no longer matches → `--check` fails until you re-sync. Each consuming repo runs
`--check` in its own CI (or you run it centrally before pushing North Star changes).

## Why not a submodule or an MCP server?

See ADR-0007. Short version: submodules are fiddly for a solo dev; an MCP server is
runtime to keep alive and only helps when the agent *chooses* to query it. A synced
block is present by default, has zero runtime, and is trivial to debug. MCP remains a
documented future upgrade (ADR-0006) if on-demand querying becomes worth it.

## Adopting in a new repo

1. Add the repo to `targets` in `sync.config.json` with its `role`
   (`substrate` | `brand` | `legacy`).
2. Run `python agent/sync_north_star.py`.
3. Add `python /path/to/north-star/agent/sync_north_star.py --check` to that repo's CI.

## Operating model across the 4 repos

The North Star repo is the hub; the four repos are spokes. Because this session can
only see `tali-platform`, the brand/substrate `CLAUDE.md` files are written by running
the sync **from a machine that has all repos checked out as siblings** of `north-star`
(the `../<repo>/CLAUDE.md` paths in the config assume that layout). Typical loop:

1. Make an architectural change in `mainspring`/a brand → update `model.yaml` or add an
   ADR (the self-maintaining rule).
2. From the `north-star` checkout: `python agent/sync_north_star.py` → refreshes every
   repo's block (digest changes).
3. Commit each repo's updated `CLAUDE.md`; each repo's CI runs `--check` and stays green.
