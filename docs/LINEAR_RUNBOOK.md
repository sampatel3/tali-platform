# Linear Runbook

How we track work across the platform. One product, four repos, one solo
developer (Sam) working almost entirely through Claude Code.

- **mainspring** — shared substrate / framework
- **taali** — brand built on the substrate
- **cadence** — brand built on the substrate
- **tali-platform** — legacy (this repo)

## Structure (and why)

| Linear concept | Our choice | Why |
| --- | --- | --- |
| Workspace | **One** (`taali`) | A single home for everything. |
| Team | **One**: `Taali`, prefix `TAA` | Teams model *people/workflows* (cycles, triage, ID prefix) — not code. With one developer, four teams = four boards to context-switch between for zero benefit. |
| Project | **One per repo** (`mainspring`, `taali`, `cadence`, `tali-platform`) | Projects give per-repo grouping inside a single unified backlog — matches "one product, four repos." |
| Labels | **Optional** `repo:mainspring` … | Only add if you want cross-cutting filtering on top of Projects. Projects already cover per-repo grouping, so this is redundant unless you find you need it. |

Issue IDs look like `TAA-42`. Branch names look like `sam/taa-42-short-slug`.

> Note: there is both a **team** named `Taali` and a **project** named `taali`
> (the brand/repo). The team is the org-level container; the project is one of
> the four repos inside it.

---

## Setup status

| # | Step | Owner | Status |
| --- | --- | --- | --- |
| 1 | Workspace `taali` + team `Taali` (key `TAA`) | Sam | ✅ done |
| 2 | Connect GitHub org + all 4 repos | Sam | ⬜ to do |
| 3 | Enable webhook + agent session events | Sam | ⬜ to do |
| 4 | Workflow automations (scoped to `main`) | Sam | ⬜ to do |
| 5 | Four Projects (one per repo) | Claude (via MCP) | ✅ done |
| 6 | Optional `repo:*` labels | — | ⬜ skipped (Projects suffice) |
| 7 | Linear MCP wired in `.mcp.json` + authenticated | Claude + Sam | ✅ done |
| 8 | This runbook + CLAUDE.md note | Claude | ✅ done |

Steps below marked **(Sam)** require your Linear login / GitHub OAuth and can't
be done headless. Steps marked **(Claude)** can be driven from Claude Code via
the Linear MCP — just ask.

### 1. Workspace + team — (Sam) ✅
Workspace `taali` and team `Taali` (identifier **`TAA`**) exist. Cycles can stay
off — a solo dev doesn't need sprints.

### 2. Connect GitHub (org + all 4 repos) — (Sam)
1. **Settings → Features → Integrations → GitHub → Enable**.
2. Authorize the GitHub **organization** that owns the repos.
3. Select **all four** repos: `mainspring`, `taali`, `cadence`, `tali-platform`.
4. Authenticate your personal GitHub account when prompted.
   > One Linear workspace can link to multiple GitHub repos. Issue *linking*
   > (branches/PRs/commits → issues) works for all connected repos. Note
   > GitHub **Issues two-way sync** is limited to one repo at a time — we
   > don't rely on that; we link via branches/PRs/magic words.

### 3. Enable webhook + agent session events — (Sam)
- In the GitHub integration settings, ensure the **webhook** is enabled
  (Linear provides the Payload URL + Secret automatically).
- Enable **Agent session events** — required for any AI/agent features.

### 4. Configure workflow automations — (Sam)
In **Settings → Team (Taali) → Issue statuses & automations → Pull request and
commit automation**:
- Branch created → **In Progress**
- PR opened → **In Review**
- PR merged → **Done**
- **Scope auto-close to the `main` target branch only** (set the branch-specific
  rule so merges into feature/staging branches don't close issues).
- Enable **"Link commits to issues with magic words."**

### 5. Four Projects — (Claude) ✅
Created, one per repo, all attached to the `Taali` team:
- `mainspring` · `taali` · `cadence` · `tali-platform`

### 6. Optional `repo:*` labels — (Claude/Sam, optional, skipped)
Skipped for now — Projects already give per-repo grouping. Create
`repo:mainspring` … only if you later want label-based cross-cutting filters.

### 7. Linear MCP — (Claude + Sam) ✅
The server is wired in this repo's `.mcp.json`:
```json
{ "mcpServers": { "linear-server": { "type": "http", "url": "https://mcp.linear.app/mcp" } } }
```
To (re)activate in a fresh session: run `/mcp`, select `linear-server`, complete
the browser OAuth (OAuth 2.1 with dynamic client registration — no API key).
Terminal equivalent:
```bash
claude mcp add --transport http linear-server https://mcp.linear.app/mcp
```
Replicate the same `.mcp.json` (or `claude mcp add`) in the **mainspring**,
**taali**, and **cadence** repos so Claude Code can drive Linear from each.

After auth, Claude Code can find/create/update Linear issues, projects, and
comments directly from the terminal — the highest-value piece for how Sam works.

---

## Day-to-day workflow

1. **Create an issue** — in Linear, or ask Claude Code (via the MCP):
   *"Create a TAA issue: <title>, in the taali project."*
2. **Get the branch name** — on the issue press **Cmd/Ctrl + Shift + .** to copy
   a ready-made branch name like `sam/taa-42-fix-login`. Creating the branch
   moves the issue to **In Progress** automatically.
3. **Do the work**, commit, and open a PR. Put a magic word in the **PR body**
   (or commit message):
   - `Fixes TAA-42` / `Closes TAA-42` / `Resolves TAA-42` → auto-links and
     **auto-closes** the issue when the PR merges to `main`.
   - `Ref TAA-42` / `Related to TAA-42` → links without closing.
   Opening the PR moves the issue to **In Review**.
4. **Merge to `main`** → the issue moves to **Done** and (with a closing magic
   word) closes automatically.

That's the loop: **issue → branch → PR with `Fixes TAA-#` → merge auto-closes.**

---

## References
- Linear GitHub integration: <https://linear.app/docs/github>
- Linear conceptual model (teams vs projects): <https://linear.app/docs/conceptual-model>
- Linear MCP server: <https://linear.app/docs/mcp>
- Claude Code MCP: <https://code.claude.com/docs/en/mcp>
