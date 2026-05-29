## Project tracking — Linear

We track work in **Linear**: one workspace (`taali`), one team **Taali**
(prefix **`TAA`**), one **Project per repo** (`mainspring`, `taali`, `cadence`,
`tali-platform`).

The **Linear MCP** is wired in `.mcp.json` (`linear-server`,
`https://mcp.linear.app/mcp`). Run `/mcp` to authenticate, then you can
find/create/update Linear issues from here.

**Workflow:** issue → branch (`sam/taa-#-...`, copy with `Cmd/Ctrl+Shift+.`)
→ PR with **`Fixes TAA-#`** in the body → merge to `main` auto-closes the issue.
Branch created → In Progress, PR opened → In Review, PR merged → Done.
