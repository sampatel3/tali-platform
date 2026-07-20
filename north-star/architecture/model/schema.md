# `model.yaml` schema

`model.yaml` is the single source of truth for the architecture. It is plain YAML so
it is hand-editable and diffable, and machine-readable so views and drift-checks can
be derived from it. It uses **C4 vocabulary** (Context → Container → Component); it is
*not* Structurizr DSL (see ADR-0006 for why).

## Top-level keys

| Key             | Purpose                                                                 |
| --------------- | ----------------------------------------------------------------------- |
| `metadata`      | Name, schema/model versions, last-updated date, vocabulary.             |
| `repos`         | Ownership domains. Each has a `role` (substrate/brand/legacy).          |
| `context`       | C4 L1: the system, people (actors), and external systems.               |
| `containers`    | C4 L2: runtime/deployable units, each owned by a `repo`.                |
| `components`    | C4 L3: notable pieces, especially **invariant-bearing** ones.          |
| `relationships` | Directed edges between containers/external systems (for views).         |
| `boundaries`    | Architectural boundaries as data, each linked to an ADR.                |
| `invariants`    | Non-negotiable rules as data, each linked to an ADR.                    |

## `repos[]`

```yaml
- id: mainspring          # stable identifier referenced by containers/components
  role: substrate         # substrate | brand | legacy
  description: ...
  localPathEnv: MAINSPRING_PATH   # env var the drift checker reads to find a checkout
```

`localPathEnv` is how drift detection works across repos this checkout can't see: if
the env var is unset (and no default applies), that repo's `implementation` mappings
are reported **unverifiable** rather than failing. `tali-platform` defaults to `.`.

## `implementation` block (on containers/components)

```yaml
implementation:
  repo: tali-platform       # must match a repos[].id
  paths:                    # files OR directories, relative to that repo's root
    - backend/app/services/metered_anthropic_client.py
    - backend/alembic
```

`check_drift.py` resolves `repo` → local path (via `localPathEnv`), then asserts each
`path` exists. Missing paths in a verifiable repo = drift (failure). Paths in an
unverifiable repo = skipped with a note.

## Invariant linkage

Components and containers that carry an invariant set `invariant: ADR-NNNN`.
`boundaries[]` and `invariants[]` each set `adr: ADR-NNNN`. `validate_model.py` checks
every referenced ADR id resolves to a file in `architecture/decisions/`.

## Conventions

- `id`s are kebab-case, stable, and unique within their list.
- Bump `metadata.modelVersion` when the model changes meaningfully.
- A component migrating from legacy to substrate sets `migratesTo: <repo id>` and
  keeps its `implementation` pointing at the *current* (verifiable) home until moved.
