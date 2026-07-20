# 7. Agent injection via a synced CLAUDE.md block

- **Status:** Accepted (block content made role-aware by ADR-0008)
- **Date:** 2026-05-29
- **Deciders:** Sam

## Context

The North Star only works if it is actually *in front of the agent* in every repo,
every session. Options considered for getting it there:

- **Git submodule** of the North Star into each repo. Single source of truth, but
  submodules are fiddly (detached checkouts, update friction, easy to forget) — poor
  fit for a solo dev moving fast.
- **A standing MCP server** exposing the model/ADRs on demand. Most "product-like",
  but it is infra to build, run, and keep alive, and it only helps when the agent
  *chooses* to query it — the North Star should be present by default.
- **A synced block in each repo's `CLAUDE.md`.** Claude Code already loads `CLAUDE.md`
  and follows `@path` imports automatically, so a managed block puts the North Star
  in context with zero runtime and no extra moving parts.

## Decision

We will inject the North Star into each target repo via a **managed block in that
repo's `CLAUDE.md`**, written by `agent/sync_north_star.py`:

- The block is delimited by `<!-- NORTH-STAR:BEGIN -->` / `<!-- NORTH-STAR:END -->`
  so the sync is idempotent and never clobbers a repo's own `CLAUDE.md` content.
- The block contains the guardrail convention ("before non-trivial work, read the
  North Star; don't contradict it — raise an ADR") plus `@`-style pointers to the
  North Star files.
- Target repos and the path to the North Star checkout live in `agent/sync.config.json`.
- CI runs the sync in `--check` mode so a stale block fails the build.

We explicitly do **not** build a submodule wiring or an MCP server now. MCP remains a
documented future option (see ADR-0006) if on-demand querying becomes valuable.

## Consequences

- Every session in every target repo starts with the North Star in context, for free.
- The block is generated, so updating guidance is a sync, not a manual edit in N repos.
- The synced block can go stale relative to the source; the `--check` CI gate catches
  that. Each consuming repo must run that check (or accept manual discipline).
- Because the block is delimited, a repo keeps full ownership of the rest of its
  `CLAUDE.md`.
