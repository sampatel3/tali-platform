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
| Workspace | **One** | A single home for everything. |
| Team | **One**, prefix `PLAT` | Teams model *people/workflows* (cycles, triage queue, ID prefix) — not code. With one developer, four teams = four boards to context-switch between for zero benefit. |
| Project | **One per repo** (`mainspring`, `taali`, `cadence`, `tali-platform`) | Projects give per-repo grouping inside a single unified backlog — matches "one product, four repos." |
| Labels | **Optional** `repo:mainspring` … | Only add if you want cross-cutting filtering on top of Projects. Projects already cover per-repo grouping, so this is redundant unless you find you need it. |

Issue IDs look like `PLAT-42`. Branch names look like `sam/plat-42-short-slug`.

---

## One-time setup checklist

Steps marked **(Sam)** require your Linear login / GitHub OAuth and cannot be
done headless. Steps marked **(Claude)** can be done from this Claude Code
environment via the Linear MCP once you've authenticated it.

### 1. Create the workspace and team — (Sam)
1. Go to <https://linear.app> → **Sign up** (or log in).
2. When prompted to create a workspace, name it for the platform (e.g. `Tali`).
3. Create a team. In **Settings → Teams → New team**:
   - Name: `Platform` (or similar).
   - **Identifier / prefix: `PLAT`** — this is what produces `PLAT-1`, `PLAT-2`…
4. You can disable Cycles initially (optional; solo dev doesn't need sprints).

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
In **Settings → Team (Platform) → Issue statuses & automations → Pull request
and commit automation**:
- Branch created → **In Progress**
- PR opened → **In Review**
- PR merged → **Done**
- **Scope auto-close to the `main` target branch only** (set the branch-specific
  rule so merges into feature/staging branches don't close issues).
- Enable **"Link commits to issues with magic words."**

### 5. Create the four Projects — (Claude, after MCP auth) or (Sam)
One Project per repo: `mainspring`, `taali`, `cadence`, `tali-platform`.
Once the Linear MCP is authenticated (section below), Claude Code can create
these — plus the team and optional labels — programmatically. Just ask.

### 6. Optional `repo:*` labels — (Claude/Sam, optional)
Create `repo:mainspring`, `repo:taali`, `repo:cadence`, `repo:tali-platform`
**only** if you want label-based cross-cutting filters in addition to Projects.

### 7. Connect the Linear MCP to Claude Code — (Sam, one auth click)
The server is already wired in this repo's `.mcp.json`:
```json
{ "mcpServers": { "linear-server": { "type": "http", "url": "https://mcp.linear.app/mcp" } } }
```
To activate it:
- **In any Claude Code session in this repo**, run `/mcp`, select
  `linear-server`, and complete the OAuth flow in the browser (OAuth 2.1 with
  dynamic client registration — no API key to manage).
- Or from a terminal, the equivalent one-liner is:
  ```bash
  claude mcp add --transport http linear-server https://mcp.linear.app/mcp
  ```
- Replicate the same `.mcp.json` (or `claude mcp add`) in the **mainspring**,
  **taali**, and **cadence** repos so Claude Code can manage Linear from each.

After auth, Claude Code can find/create/update Linear issues, projects, and
comments directly from the terminal — this is the highest-value piece for how
Sam works.

---

## Day-to-day workflow

1. **Create an issue** — in Linear, or ask Claude Code (via the MCP):
   *"Create a PLAT issue: <title>, in the taali project."*
2. **Get the branch name** — on the issue press **Cmd/Ctrl + Shift + .** to copy
   a ready-made branch name like `sam/plat-42-fix-login`. Creating the branch
   moves the issue to **In Progress** automatically.
3. **Do the work**, commit, and open a PR. Put a magic word in the **PR body**
   (or commit message):
   - `Fixes PLAT-42` / `Closes PLAT-42` / `Resolves PLAT-42` → auto-links and
     **auto-closes** the issue when the PR merges to `main`.
   - `Ref PLAT-42` / `Related to PLAT-42` → links without closing.
   Opening the PR moves the issue to **In Review**.
4. **Merge to `main`** → the issue moves to **Done** and (with a closing magic
   word) closes automatically.

That's the loop: **issue → branch → PR with `Fixes PLAT-#` → merge auto-closes.**

---

## References
- Linear GitHub integration: <https://linear.app/docs/github>
- Linear conceptual model (teams vs projects): <https://linear.app/docs/conceptual-model>
- Linear MCP server: <https://linear.app/docs/mcp>
- Claude Code MCP: <https://code.claude.com/docs/en/mcp>
