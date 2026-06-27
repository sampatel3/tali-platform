# `.claude/` — Claude Code configuration

This directory configures [Claude Code](https://code.claude.com/docs) (and
compatible AI coding agents) for the Tali platform. It is committed so the whole
team shares the same agent setup. Personal/secret overrides stay out of git (see
**What's ignored** below).

## Layout

| Path | Purpose |
| --- | --- |
| `../CLAUDE.md` | Project memory loaded into every session — stack, commands, conventions, CI gates. Kept short on purpose. |
| `settings.json` | Team-shared settings: secret-file `deny` rules and a `SessionStart` hook. Committed. |
| `settings.local.json` | Personal settings/permissions for one machine. **Gitignored.** |
| `hooks/session-start.sh` | Ensures the backend venv and frontend `node_modules` exist so tests/linters run in a fresh (e.g. web) session. Best-effort, never fails the session. |
| `skills/` | Multi-step procedures invokable as `/run-tests`, `/new-migration`, `/local-ci`. Claude can also auto-invoke them when relevant. |
| `rules/` | Path-scoped conventions that load only when matching files are opened (`backend.md`, `frontend.md`). |

## Skills

Each skill is a directory with a `SKILL.md` (YAML frontmatter + instructions).
The directory name is the command:

- **`/run-tests`** — run backend pytest and/or frontend Vitest correctly.
- **`/new-migration`** — create an Alembic migration while keeping a single head.
- **`/local-ci`** — run the exact gates from `.github/workflows/ci.yml` before pushing.

## Rules vs CLAUDE.md vs skills

- **`CLAUDE.md`** — always-loaded facts/conventions. Keep it concise.
- **`rules/*.md`** — path-scoped guidance loaded on demand via `paths:` frontmatter,
  so backend rules don't cost context while working on the frontend.
- **`skills/`** — multi-step procedures and reference material loaded only when invoked.

## What's ignored

The repo `.gitignore` commits the shared config above but keeps these out of git:
`settings.local.json`, `CLAUDE.local.md`, and `agent-memory-local/`. Put personal
permission grants or machine-specific tweaks in `settings.local.json`.

## Extending

- Add a skill: create `skills/<name>/SKILL.md` with `name` + `description`
  frontmatter.
- Add team permission grants (e.g. pre-approving `Bash(pytest*)`): add an
  `allow` list under `permissions` in `settings.json`, or keep them personal in
  `settings.local.json`.
- See the Claude Code docs for the full settings/skills/hooks schema.
