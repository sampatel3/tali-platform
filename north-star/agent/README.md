# Agent plumbing — how the North Star reaches every repo

The North Star only works if it's in front of the agent in every repo, every session.
We do that with a **managed block in each repo's `CLAUDE.md`** (ADR-0007) — not a
submodule, not a standing MCP server.

## How it works

1. `sync.config.json` lists the target repos and where each one's `CLAUDE.md` lives,
   plus `northStarRef` (the pointer embedded in the block so agents know where the
   full North Star is).
2. `sync_north_star.py` renders a block from `NORTH_STAR.block.md`, stamps it with a
   **digest** over the North Star source files (`NORTH_STAR.md`, `model.yaml`, all
   ADRs), and splices it into each target `CLAUDE.md` between markers:

   ```
   <!-- NORTH-STAR:BEGIN -->
   ... generated guidance + guardrail + digest ...
   <!-- NORTH-STAR:END -->
   ```

   The markers make the sync **idempotent** and **non-destructive**: the repo keeps
   full ownership of the rest of its `CLAUDE.md`.

## Commands

```bash
python agent/sync_north_star.py          # write/update the block in all targets
python agent/sync_north_star.py --check  # CI mode: exit 1 if any block is missing/stale
python agent/sync_north_star.py --print  # preview the block, write nothing
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

1. Add the repo to `targets` in `sync.config.json`.
2. Run `python agent/sync_north_star.py`.
3. Add `python /path/to/north-star/agent/sync_north_star.py --check` to that repo's CI.
