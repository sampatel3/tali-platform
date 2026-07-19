# MCP server

Read-only access to Taali's recruiting data over the [Model Context Protocol](https://modelcontextprotocol.io/).
Mounted on the main FastAPI app at **`/mcp/`**. The endpoint accepts either a
Taali API key or a fastapi-users JWT on the same URL. Use an API key for external
connectors; JWTs are short-lived session credentials intended as a fallback.

## What you can do

Once a supported MCP client is connected, ask things like:

- "Show me every candidate above 70 for the Senior Backend role"
- "List the 5 highest-ranked candidates currently in review"
- "Compare applications 412, 413, and 415 — which should we advance?"
- "Pull up Sam Patel's profile and every role they've applied for"
- "Read me the job spec for `tali://role/12`"

## Tool surface (read-only)

For API keys, every scope shown in a row is required. JWT principals receive
implicit access to all three read scopes.

<!-- public-mcp-tools:start -->

| Tool | Required API-key scope(s) | Cost | Purpose |
|---|---|---|---|
| `list_roles` | `roles:read` | `free` | List roles and lifecycle state; optionally include per-stage application counts. |
| `get_role` | `roles:read` | `free` | Fetch one role's job specification, recruiter criteria, and open pipeline counts. |
| `search_applications` | `applications:read` | `free` | Filter applications by score, stage, outcome, or simple name/email/position text. |
| `get_application` | `applications:read` | `free` | Fetch one application with scores, evidence, rejection context, ATS state, and recruiter notes; CV text is optional. |
| `get_candidate` | `applications:read` | `free` | Fetch a candidate profile and their applications across the organization. |
| `compare_applications` | `applications:read` | `free` | Compare two to five applications on a common scorecard. |
| `nl_search_candidates` | `applications:read` | `paid` | Common deterministic or cached queries can be free. Ambiguous queries may consume organization credits for Sonnet parsing; optional deep verification may consume additional organization credits and is bounded. |
| `graph_search_candidates` | `applications:read` | `free` | Search the organization's temporal candidate graph and return matching facts plus an inline subgraph when available. |
| `get_candidate_cv` | `applications:read` | `free` | Fetch parsed CV sections and raw extracted CV text when exact evidence is necessary. |
| `get_recruiting_overview` | `roles:read`, `applications:read`, `assessments:read` | `free` | Summarize roles, candidates, application funnel, assessment statuses, and attention counts for the organization or one role. |
| `list_assessments` | `assessments:read` | `free` | List a paginated assessment work queue by status, role, or attention condition such as expiring invitations, delivery failures, or scoring failures. |

<!-- public-mcp-tools:end -->

`list_roles` and `get_role` can be called with only `roles:read`, but their
application totals and stage counts are omitted unless the principal also has
`applications:read`.

Clients that support MCP resource mentions can use:

- `tali://role/{role_id}` — role spec as markdown; requires `roles:read`
- `tali://application/{application_id}` — application snapshot as markdown; requires `applications:read`
- `tali://candidate/{candidate_id}/cv` — raw CV text; requires `applications:read`

Every result that names an entity includes a `frontend_url` so Claude can
render a clickable deep-link into the Taali web app.

## Score conventions

| key | source | recommended use |
|---|---|---|
| `taali` | merged primary score (`taali_score_cache_100`) | **Default** for "score above X". |
| `pre_screen` | cheap LLM gating pass (`pre_screen_score_100`) | Volume filtering. |
| `rank` | pairwise rank against role pool (`rank_score`) | Relative ordering only. |
| `cv_match` | CV/job-spec similarity (`cv_match_score`) | Skill-fit signal. |
| `workable` | source ATS score (`workable_score`) | Stored on a 0–10 scale; results also include normalized `workable_score_100`. |
| `assessment` | cached assessment score (`assessment_score_cache_100`) | Completed-assessment signal when present. |
| `role_fit` | cached role-fit score (`role_fit_score_cache_100`) | Full role-fit signal when present. |

`min_score` accepts either a 0–10 or a 0–100 threshold; values ≤ 10 are
auto-scaled to 0–100. The SQL edge converts a `workable` threshold back to its
stored 0–10 scale.

## Connecting a static-header MCP client

1. An organization owner creates a key at **Settings → Developers**. Choose
   the minimum scopes the connector needs. When scopes are omitted through the
   API, the read-only default is `roles:read`, `applications:read`, and
   `assessments:read`. Copy the plaintext key when it is shown; Taali stores only
   its hash and cannot display it again.

2. Configure the client's Streamable HTTP endpoint as
   `https://<your-taali-host>/mcp/` and provide the key through the client's
   secret store as `X-API-Key`. `Authorization: Bearer <key>` is also accepted.
   Do not put the key directly in a shell command, process argument, or tracked
   config file. Exact configuration is client-specific; only use a client that
   documents support for static request headers.

3. The server advertises 11 tools and three resource templates after the
   client authenticates.

### Short-lived JWT fallback

JWTs expire after `ACCESS_TOKEN_EXPIRE_MINUTES` (30 minutes by default), so do
not increase the deployment-wide lifetime for a local connector. Re-issue the
session token when needed. The following flow keeps the email, password, and
token out of command arguments, command substitution, and terminal output. All
credential-bearing files are created with mode `0600` and removed when the
shell exits:

```bash
set -euo pipefail
set +x
umask 077
: "${TALI_API:=http://localhost:8000}"
AUTH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tali-mcp-auth.XXXXXX")"
cleanup() {
  rm -rf -- "$AUTH_DIR" || true
  unset TALI_EMAIL TALI_PASSWORD TALI_MCP_URL AUTH_DIR
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

export TALI_API AUTH_DIR
python3 <<'PY'
import os
from urllib.parse import urlsplit

raw = os.environ["TALI_API"]
if not raw or any(ord(character) < 32 or ord(character) == 127 for character in raw):
    raise SystemExit("TALI_API must be a valid HTTPS URL")

try:
    parsed = urlsplit(raw)
    parsed.port
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
except ValueError:
    raise SystemExit("TALI_API must be a valid HTTPS URL") from None
local_http_hosts = {"localhost", "127.0.0.1", "::1"}
if (
    not host
    or parsed.username is not None
    or parsed.password is not None
    or parsed.query
    or parsed.fragment
    or (scheme == "http" and host not in local_http_hosts)
    or scheme not in {"http", "https"}
):
    raise SystemExit(
        "TALI_API must use HTTPS; HTTP is allowed only for "
        "localhost, 127.0.0.1, or [::1] local development"
    )
PY

EMAIL_FILE="$AUTH_DIR/email.raw"
PASSWORD_FILE="$AUTH_DIR/password.raw"
: > "$EMAIL_FILE"
: > "$PASSWORD_FILE"
chmod 600 "$EMAIL_FILE" "$PASSWORD_FILE"

printf 'Taali email: '
IFS= read -r TALI_EMAIL
printf '%s' "$TALI_EMAIL" > "$EMAIL_FILE"
printf 'Taali password: '
IFS= read -r -s TALI_PASSWORD
printf '\n'
printf '%s' "$TALI_PASSWORD" > "$PASSWORD_FILE"
unset TALI_EMAIL TALI_PASSWORD

python3 <<'PY'
import json
import os
from pathlib import Path
from urllib.parse import urlencode

root = Path(os.environ["AUTH_DIR"])
api = os.environ["TALI_API"].rstrip("/")
email_file = root / "email.raw"
password_file = root / "password.raw"

def write_private(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    path.chmod(0o600)

form = root / "login.form"
headers = root / "login.headers"
response = root / "login.response.json"
config = root / "login.curl"
write_private(form, urlencode({
    "username": email_file.read_text(encoding="utf-8"),
    "password": password_file.read_text(encoding="utf-8"),
}))
email_file.unlink()
password_file.unlink()
write_private(headers, "Content-Type: application/x-www-form-urlencoded\n")
write_private(response, "")
write_private(config, "\n".join([
    f"url = {json.dumps(api + '/api/v1/auth/jwt/login')}",
    'request = "POST"',
    f"header = {json.dumps('@' + str(headers))}",
    f"data = {json.dumps('@' + str(form))}",
    f"output = {json.dumps(str(response))}",
    "connect-timeout = 5",
    "max-time = 20",
    'proto = "=https,http"',
    "fail-with-body",
    "silent",
    "show-error",
    "",
]))
PY

curl --config "$AUTH_DIR/login.curl"
chmod 600 "$AUTH_DIR/login.response.json"
export TALI_MCP_URL="${TALI_API%/}/mcp/"

python3 <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["AUTH_DIR"])
payload = json.loads((root / "login.response.json").read_text(encoding="utf-8"))
token = payload.get("access_token")
if not isinstance(token, str) or not token:
    raise SystemExit("Login response did not contain an access token")

destination = root / "jwt.headers.json"
descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump({"Authorization": f"Bearer {token}"}, handle, indent=2)
    handle.write("\n")
destination.chmod(0o600)
print(f"Private header fragment for {os.environ['TALI_MCP_URL']} written to {destination}")
PY
```

Load the generated `jwt.headers.json` through a static-header client's private
configuration, then leave the shell; the trap removes the raw credential files,
form, headers, curl config, response, and generated fragment. Never paste the
response JSON or token into a shell command.

## Claude remote connector status

Direct Claude remote-connector setup is **not available in v1**. Anthropic's
[current custom-connector instructions](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
say that remote connections originate from Anthropic's cloud, require a
publicly reachable MCP URL, and use the connector authentication flow. The
documented UI does not provide arbitrary static-header configuration.

Claude Desktop also does not connect directly to a remote Streamable HTTP URL
from `claude_desktop_config.json`; that file is for locally launched MCP
servers. Do not paste either a Taali API key or JWT into the unsupported remote
JSON shape previously shown here. Taali needs the planned OAuth 2.1 wrapper
before its public `/mcp/` endpoint can be added through Claude's remote
connector UI.

## Multi-tenancy

Every call resolves an authenticated `Principal` from the API key or bearer JWT
and filters every query by that principal's `organization_id`. The cross-org
isolation is covered by
`tests/test_mcp_server.py::test_get_application_cross_org_404`.

## Internal use: Taali Chat

The 11 public read tools are a shared subset of the catalogue used by the
**Taali Chat** in-product chat UI (`/api/v1/taali-chat/*`). Chat also exposes
chat-only tools, including governed mutations, and invokes shared handlers
in-process rather than making an MCP HTTP round trip. See
[TAALI_CHAT.md](TAALI_CHAT.md) for the chat-specific endpoint and frontend
integration.

## Not in v1

These are intentionally out of scope and tracked for a later pass:

- Write tools (`advance_stage`, `set_outcome`, `preview_stage_transition`).
- OAuth 2.1 wrapper for claude.ai connectors.
- Streaming progress / long-running tool calls over MCP transport.
- Audit log / usage attribution per MCP call (Taali Chat does have this).

## Files

- `app/mcp/server.py` — FastMCP instance, all tools and resources.
- `app/mcp/catalog.py` — canonical tool names, descriptions, exposures, and scopes.
- `app/mcp/auth.py` — API-key/JWT authentication, principal resolution, and scope checks.
- `app/mcp/payloads.py` — thin payload builders.
- `app/mcp/urls.py` — frontend deep-link builders.
- `app/main.py` — mount + lifespan wiring.
- `tests/test_mcp_server.py` — end-to-end tests through the streamable-HTTP transport.
