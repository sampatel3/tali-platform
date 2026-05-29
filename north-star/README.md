# north-star

A persistent, high-level **architecture reference that keeps AI coding agents aligned**
with the intended system design — across many sessions and across every repo in the
platform. It is built as a product, not a folder of docs: a machine-readable model,
a maintained decision log, agent plumbing that injects it into every session, and
drift detection that keeps it honest.

> New here? Read **`NORTH_STAR.md`** first. The design rationale is in **`DESIGN.md`**.

## What's in here

| Path                              | What it is                                                        |
| --------------------------------- | ---------------------------------------------------------------- |
| `NORTH_STAR.md`                   | The canonical high-level reference agents read first.            |
| `DESIGN.md`                       | Scope sign-off + the design of this thing.                       |
| `architecture/model/model.yaml`  | **Single source of truth** — the C4 model as data, with code maps.|
| `architecture/model/views/`      | Human-facing C4 views (Mermaid, render on GitHub).               |
| `architecture/decisions/`        | ADR log — *why* the settled decisions hold.                      |
| `agent/`                         | The sync plumbing that injects the North Star into each repo.    |
| `scripts/`                       | `validate_model.py` (integrity) + `check_drift.py` (model↔reality).|
| `.github/workflows/`             | CI that runs the above on every PR.                             |

## How it works (the loop)

1. The **model** (`model.yaml`) is the source of truth; **views** and docs derive from it.
2. **ADRs** record why decisions hold, so agents stop re-litigating them.
3. **`agent/sync_north_star.py`** injects a managed block into each repo's `CLAUDE.md`,
   so every Claude Code session starts aligned, with the guardrail: *read the North
   Star; don't contradict it — raise an ADR instead.*
4. **`scripts/check_drift.py`** verifies the model still matches the code. CI runs it.
5. **Self-maintaining rule:** any architectural change updates the model or adds an
   ADR in the same PR.

## Quick start

```bash
pip install pyyaml
python scripts/validate_model.py        # model integrity + ADR linkage
python scripts/check_drift.py           # does the model still match the code?
python agent/sync_north_star.py --print  # preview the block injected into repos
python agent/sync_north_star.py          # write it into the repos in agent/sync.config.json
```

## Adopting in the platform repos

Point each repo's CI at the sync check and add it to `agent/sync.config.json`:

```bash
# in a consuming repo's CI
python /path/to/north-star/agent/sync_north_star.py --check
```

See `agent/README.md` for the full plumbing, and `architecture/decisions/0007-*` for why
it's a synced block rather than a submodule or MCP server.

## Status / roadmap

- **Now:** internal, product-grade, consumed by `mainspring`, `taali`, `cadence`;
  `tali-platform` is legacy and drained over time.
- **Kept open (not built):** extraction into a standalone tool for arbitrary repos;
  a Structurizr DSL + MCP upgrade for on-demand querying (ADR-0006). Both are
  deliberate non-goals today — see `DESIGN.md §7`.
