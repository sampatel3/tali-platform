# Architecture Decision Records

ADRs capture *why* a significant architectural decision was made — its context, the
decision, and its consequences. They are the highest-leverage context for AI agents:
they stop agents re-litigating settled decisions.

Format: the lightweight one popularised by Michael Nygard. (Same convention as
`tali-platform/docs/adr`, intentionally, so they read the same.)

## Index

| ADR  | Title                                              | Status   |
| ---- | -------------------------------------------------- | -------- |
| 0001 | Record architecture decisions                      | Accepted |
| 0002 | Substrate-vs-brand ownership boundaries            | Accepted |
| 0003 | Metering invariant: every Anthropic call writes a UsageEvent | Accepted |
| 0004 | Decision-write serialization (per-org mutex)       | Accepted |
| 0005 | Per-brand Alembic migration chains                 | Accepted |
| 0006 | North Star model format                            | Accepted |
| 0007 | Agent injection via a synced CLAUDE.md block       | Accepted |

## Conventions

- Files: `NNNN-short-title.md` (zero-padded sequence).
- Status: Proposed | Accepted | Superseded | Deprecated.
- One decision per ADR. Link superseding/superseded ADRs to each other.
- IDs referenced from `model.yaml` (e.g. `ADR-0003`) must resolve to a file here;
  `scripts/validate_model.py` enforces this.

## Creating a new ADR

Copy `0000-template.md`, take the next number, fill it in, add a row above. **Raising
an ADR is the sanctioned way to change the North Star** — if a needed change conflicts
with `NORTH_STAR.md` or the model, propose it here rather than diverging silently.
