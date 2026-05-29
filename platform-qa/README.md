# platform-qa — Tier 2 cross-repo QA

> **This directory is a runnable reference skeleton committed inside
> `tali-platform`. It is meant to be extracted into its own repo,
> `sampatel3/platform-qa`** (see `docs/qa/TWO_TIER_QA_STRATEGY.md`). It lives
> here only because the session that built it could write `tali-platform` but
> not the other repos. Search for `REPLACE-WITH-REAL` to find the seams where
> the example stand-ins are swapped for the real mainspring/brand interfaces.

## Why this exists

Per-repo tests (Tier 1) can't catch the one failure mode that actually bites a
multi-repo platform: **a change in `mainspring` (the substrate) silently breaks
`taali` and/or `cadence`**. mainspring's tests pass; the brands aren't re-run;
the break surfaces in production. Tier 2 is the only layer that watches the
substrate↔brand boundary.

## What's here

```
harness/contract.py     # the compatibility checker (substrate iface vs brand's required subset)
examples/               # runnable reference: a substrate + a brand (stand-ins for mainspring/taali)
contracts/mainspring/   # pinned snapshots of the substrate's published interface
tests/contract/         # substrate↔brand contract tests  (run anywhere, no services)
tests/e2e/              # thin end-to-end vs the assembled platform (needs real Postgres)
conftest.py             # harness: deterministic RNG + throwaway-Postgres fixture
docker-compose.qa.yml   # throwaway Postgres on :55432 (NEVER 5432)
bin/setup.sh            # env bootstrap (worktrees/containers have no .venv)
```

## Run it

```bash
bin/setup.sh                       # builds venv, runs contract tests, (optionally) starts pg
# or, against an existing interpreter:
python -m pytest tests/contract -q # contract tests — no external services needed
```

End-to-end (needs a real throwaway Postgres):

```bash
docker compose -f docker-compose.qa.yml up -d
export QA_DATABASE_URL=postgresql://qa:qa@localhost:55432/qa
python -m pytest tests/e2e -q
```

## How the contract layer works

1. The substrate publishes its public interface as a versioned contract
   (`contracts/mainspring/*.json`; in real life generated from mainspring's
   types).
2. Each brand declares the **subset it consumes** (`examples/brand_*.py` →
   real: a contract the brand owns).
3. `check_compatibility(substrate, brand)` returns every way the substrate
   fails the brand — removed operation/output, type narrowing, a newly-required
   input. **Empty == compatible.** A breaking substrate change fails the gate
   *before merge*, with a message that names the symbol and the brand.

`tests/contract/` asserts both directions: brands are compatible with the
current substrate **and** an injected breaking change is caught.

## Standards for a good Tier 2 test

1. **Deterministic** — no wall-clock, no network, no unseeded randomness.
2. **Isolated** — fresh DB state per test; zero order dependence.
3. **Localizing** — failure names the substrate symbol and the broken brand.
4. **Real datastore** — Postgres (throwaway container), not in-memory shortcuts.
5. **Layered** — contracts first (fast, precise); thin E2E second.

## Triggering (see the strategy doc for detail)

- **PR to mainspring/taali/cadence** → fan-out runs the contract gate on that
  ref; a substrate PR that breaks a brand fails before merge.
- **`platform-qa` main + nightly** → full contract + E2E matrix across `@main`.
- **Pre-release** → E2E smoke as a release gate.

## Relationship to existing per-repo gates

Tier 2 **complements** the metering gate, architecture gates, and alembic
single-head gate — it does not re-implement them.
