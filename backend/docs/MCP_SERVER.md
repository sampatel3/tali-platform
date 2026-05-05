# MCP server

Read-only access to Tali's recruiting data over the [Model Context Protocol](https://modelcontextprotocol.io/).
Mounted on the main FastAPI app at **`/mcp`** with the same fastapi-users JWT
auth as the rest of the API.

## What you can do

Once connected, ask Claude things like:

- "Show me every candidate above 70 for the Senior Backend role"
- "List the 5 highest-ranked candidates currently in review"
- "Compare applications 412, 413, and 415 — which should we advance?"
- "Pull up Sam Patel's profile and every role they've applied for"
- "Read me the job spec for `tali://role/12`"

## Tool surface (v1: read-only)

| Tool | Purpose |
|---|---|
| `list_roles` | All active roles for your org. Use first to discover `role_id` values. |
| `get_role` | Job spec, criteria, per-stage open-application counts. |
| `search_applications` | Score / stage / outcome / text-search filters. Default sort is `taali_score desc` over open applications. |
| `get_application` | One application with all four scores, evidence, notes. Optional CV text. |
| `get_candidate` | Cross-role profile + every application that candidate has filed. |
| `compare_applications` | 2–5 applications side-by-side with the full score legend. |

Resources (use as `@`-mentions in claude.ai):

- `tali://role/{role_id}` — role spec as markdown
- `tali://application/{application_id}` — application snapshot as markdown
- `tali://candidate/{candidate_id}/cv` — raw CV text

Every result that names an entity includes a `frontend_url` so Claude can
render a clickable deep-link into the Tali web app.

## Score conventions

| key | source | recommended use |
|---|---|---|
| `taali` | merged primary score (`taali_score_cache_100`) | **Default** for "score above X". |
| `pre_screen` | cheap LLM gating pass (`pre_screen_score_100`) | Volume filtering. |
| `rank` | pairwise rank against role pool (`rank_score`) | Relative ordering only. |
| `cv_match` | CV/job-spec similarity (`cv_match_score`) | Skill-fit signal. |

`min_score` accepts either a 0–10 or a 0–100 threshold; values ≤ 10 are
auto-scaled to 0–100 to match the recruiter UI's behaviour.

## Connecting Claude Desktop

1. Get a JWT by logging into the API:

    ```bash
    TALI_TOKEN=$(curl -s -X POST $TALI_API/api/v1/auth/jwt/login \
      -d "username=$TALI_EMAIL&password=$TALI_PASSWORD" \
      -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)
    ```

   The token's lifetime is `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30 min in
   dev). For long-lived local use, bump that env var or re-issue when it
   expires.

2. Add the connector to `~/Library/Application Support/Claude/claude_desktop_config.json`:

    ```json
    {
      "mcpServers": {
        "tali": {
          "transport": "streamable_http",
          "url": "http://localhost:8000/mcp/",
          "headers": {
            "Authorization": "Bearer YOUR_JWT_HERE"
          }
        }
      }
    }
    ```

3. Restart Claude Desktop. The Tali server should appear in the connector
   list with six tools and three resource templates.

## Connecting claude.ai (Custom Connector)

Same setup — Settings → Connectors → Add custom connector → paste the
`/mcp/` URL and a `Authorization: Bearer …` header. Available on Pro/Team/
Enterprise plans.

For shipping this to other users on your team, swap the bearer-token flow
for an OAuth 2.1 wrapper around `/api/v1/auth/jwt/login` so claude.ai can
mint tokens without a manual paste step. Not implemented yet — flagged for
v2.

## Multi-tenancy

Every tool resolves `current_user` from the bearer token and then filters
every query by `organization_id == current_user.organization_id`. The
cross-org isolation is covered by `tests/test_mcp_server.py::test_get_application_cross_org_404`.

## Not in v1

These are intentionally out of scope and tracked for a later pass:

- Write tools (`advance_stage`, `set_outcome`, `preview_stage_transition`).
- OAuth 2.1 wrapper for claude.ai connectors.
- Streaming progress / long-running tool calls.
- Audit log / usage attribution per MCP call.

## Files

- `app/mcp/server.py` — FastMCP instance, all tools and resources.
- `app/mcp/auth.py` — JWT decode + user load.
- `app/mcp/payloads.py` — thin payload builders.
- `app/mcp/urls.py` — frontend deep-link builders.
- `app/main.py` — mount + lifespan wiring.
- `tests/test_mcp_server.py` — end-to-end tests through the streamable-HTTP transport.
