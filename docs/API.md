# TAALI API Reference

Base URL: `/api/v1`

Unless a row says otherwise, business endpoints require a Bearer token in the
`Authorization` header. Operator diagnostics use `X-Admin-Secret`; public
liveness, candidate assessment entry points, and signed webhooks have their own
auth contracts.

---

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Cheap public liveness. Returns exactly `{"status": "ok", "service": "taali-api"}` without probing or exposing dependencies. |
| `GET` | `/ready` | No | Redacted critical readiness. Returns only `status` (`healthy` or `degraded`) and `service`, with HTTP 200 or 503. |
| `GET` | `/admin/health` | `X-Admin-Secret` | Authenticated operator diagnostics, including dependency, worker, integration, storage, and usage-meter state. |

---

## Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/auth/register` | No | Register a new user (and optionally create an organization). |
| `POST` | `/api/v1/auth/jwt/login` | No | Authenticate with email/password form data. Returns a JWT access token. |
| `GET` | `/api/v1/users/me` | Yes | Get the currently authenticated user. |

### POST /api/v1/auth/register

**Request body:**

```json
{
  "email": "user@example.com",
  "password": "strongpassword",
  "full_name": "Jane Doe",
  "organization_name": "Acme Corp"
}
```

### POST /api/v1/auth/jwt/login

**Request body** (form data / `application/x-www-form-urlencoded`):

| Field | Description |
|-------|-------------|
| `username` | User email |
| `password` | User password |

**Response:**

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer"
}
```

---

## Assessments

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/assessments/` | Yes | Create a new assessment and send invite to candidate. |
| `GET` | `/api/v1/assessments/` | Yes | List all assessments for the current organization. |
| `GET` | `/api/v1/assessments/{id}` | Yes | Get a single assessment by ID. |
| `POST` | `/api/v1/assessments/token/{token}/start` | No | Candidate starts an assessment via unique token. |
| `POST` | `/api/v1/assessments/{id}/execute` | No | Execute code in the assessment's E2B sandbox. |
| `POST` | `/api/v1/assessments/{id}/claude` | No | Send a message to Claude AI assistant during assessment. |
| `POST` | `/api/v1/assessments/{id}/submit` | No | Submit the assessment for grading. |
| `GET` | `/api/v1/billing/costs` | Yes | Estimated per-assessment and per-tenant infrastructure costs (Claude/E2B/email/storage) with threshold alerts. |

### POST /api/v1/assessments/

**Request body:**

```json
{
  "candidate_email": "candidate@example.com",
  "candidate_name": "John Smith",
  "task_id": 1,
  "duration_minutes": 90
}
```

### POST /api/v1/assessments/token/{token}/start

**Response:**

```json
{
  "assessment_id": 42,
  "sandbox_id": "sbx_abc123",
  "task": {
    "name": "Build a REST API",
    "description": "...",
    "starter_code": "...",
    "duration_minutes": 90
  },
  "time_remaining": 5400
}
```

### POST /api/v1/assessments/{id}/execute

**Request body:**

```json
{
  "code": "print('hello world')"
}
```

### POST /api/v1/assessments/{id}/claude

**Request body:**

```json
{
  "message": "How do I read a CSV file in Python?",
  "conversation_history": []
}
```

### POST /api/v1/assessments/{id}/submit

**Request body:**

```json
{
  "final_code": "def solution():\n    ..."
}
```

**Response:**

```json
{
  "success": true,
  "score": 8.5,
  "tests_passed": 8,
  "tests_total": 10,
  "quality_analysis": "..."
}
```

---

## Organizations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/organizations/me` | Yes | Get the current user's organization. |
| `PATCH` | `/api/v1/organizations/me` | Yes | Update organization settings. |
| `POST` | `/api/v1/organizations/workable/connect` | Yes | Exchange a Workable OAuth code for an access token. |

### PATCH /api/v1/organizations/me

**Request body:**

```json
{
  "name": "Updated Org Name",
  "workable_config": {
    "auto_send_on_stage": true,
    "auto_send_stage": "assessment"
  }
}
```

### POST /api/v1/organizations/workable/connect

**Request body:**

```json
{
  "code": "oauth_authorization_code"
}
```

---

## Workable Sync

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/workable/sync/jobs` | Yes | List selectable Workable roles/jobs for scoped sync. |
| `POST` | `/api/v1/workable/sync` | Yes | Start a background Workable sync run and return `run_id`. |
| `GET` | `/api/v1/workable/sync/status` | Yes | Get run-aware sync status (`run_id`, phase, counters, errors, db snapshot). |
| `POST` | `/api/v1/workable/sync/cancel` | Yes | Request cancellation for a run (`run_id` optional). |

### GET /api/v1/workable/sync/jobs

**Response:**

```json
{
  "total": 2,
  "jobs": [
    {
      "shortcode": "ABC123",
      "id": "123456",
      "identifier": "ABC123",
      "title": "Backend Engineer",
      "state": "published"
    }
  ]
}
```

### POST /api/v1/workable/sync

**Request body:**

```json
{
  "mode": "metadata",
  "job_shortcodes": ["ABC123", "XYZ999"]
}
```

`mode` supports:
- `metadata` (default): roles + candidate/application metadata only.
- `full` (reserved): accepted for forward compatibility, currently executes metadata flow.

`job_shortcodes` is optional:
- Omit it to sync all Workable jobs.
- Provide a shortlist to sync only selected roles.

**Response:**

```json
{
  "status": "started",
  "run_id": 123,
  "mode": "metadata",
  "selected_jobs_count": 2,
  "execution_backend": "celery",
  "message": "Sync started in the background. Poll /workable/sync/status to see progress."
}
```

### GET /api/v1/workable/sync/status

**Query params:**
- `run_id` (optional): if omitted, latest org run is returned.

**Response fields include:**
- `run_id`, `phase`, `jobs_total`, `jobs_processed`
- `candidates_seen`, `candidates_upserted`, `applications_upserted`
- `errors`, `started_at`, `finished_at`, `cancel_requested_at`
- `db_snapshot` (`roles_active`, `applications_active`, `candidates_active`)

### POST /api/v1/workable/sync/cancel

**Request body:**

```json
{
  "run_id": 123
}
```

If `run_id` is omitted, the latest running org sync is targeted.

---

## Tasks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/tasks/` | Yes | Create a new coding task. |
| `GET` | `/api/v1/tasks/` | Yes | List all tasks (org-specific + templates). |
| `GET` | `/api/v1/tasks/{id}` | Yes | Get a single task by ID. |

### POST /api/v1/tasks/

### Task payload notes

`POST /api/v1/tasks/` and `PATCH /api/v1/tasks/{id}` support repository-context metadata used in assessment sessions:
- `task_key` (or alias `task_id`), `role`, `scenario`, `repo_structure`, `evaluation_rubric`, `extra_data`.
- `expected_insights` and `valid_solutions` are also accepted and merged into `extra_data`.
- On create/update, TAALI recreates the task's canonical local `main` repository snapshot from `repo_structure`.

**Request body:**

```json
{
  "name": "Build a REST API",
  "description": "Build a simple REST API with CRUD operations...",
  "starter_code": "from fastapi import FastAPI\napp = FastAPI()\n",
  "test_code": "def test_health():\n    ...",
  "duration_minutes": 90,
  "difficulty": "medium"
}
```

---

## Webhooks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/webhooks/workable` | Signature | Reserved endpoint: verifies configured signatures, then returns `501` until durable inbound processing is implemented. |
| `POST` | `/api/v1/webhooks/stripe` | Signature | Receives Stripe events; `checkout.session.completed` idempotently grants one-time top-up credits. |

Both endpoints verify request signatures. Stripe events are processed. The
Workable endpoint must not be registered with Workable yet because valid events
are intentionally rejected rather than falsely acknowledged and dropped.

---

## MCP (agent-native surface)

Taali exposes a curated read-only recruiting surface over the [Model Context Protocol](https://modelcontextprotocol.io) so agents (e.g. Claude) can query roles, applications, candidates, and assessments directly. These public tools are the shared read subset of the broader catalogue used by the in-product copilot; Taali Chat also has chat-only tools.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/mcp/` | Yes | Streamable-HTTP MCP endpoint (JSON-RPC over SSE). |

### Auth

The endpoint accepts **either** credential type on the same URL:

- A **Taali API key** (`tali_live_…` / `tali_test_…`) — the public, machine-to-machine path. Send it as `Authorization: Bearer <key>` or `X-API-Key: <key>`.
- A **fastapi-users JWT** (from `POST /api/v1/auth/jwt/login`) as `Authorization: Bearer <token>` — the session path used inside the app.

Both resolve to the caller's organization; every tool is org-scoped, so a key only ever sees its own org's data.

**Scopes (API keys only).** A key is gated on its scopes; JWT sessions have implicit full read access.

| Scope | Grants |
|-------|--------|
| `roles:read` | Role tools and `tali://role/{role_id}`; also one of the three grants required by `get_recruiting_overview` |
| `applications:read` | Application/candidate tools and resources; also one of the three grants required by `get_recruiting_overview` |
| `assessments:read` | `list_assessments`; also one of the three grants required by `get_recruiting_overview` |

A key minted without explicit scopes gets all three read scopes by default. A
call missing any required scope returns a tool error. `list_roles` and
`get_role` require only `roles:read`, but application totals and stage counts
are omitted unless the principal also has `applications:read`.

### Tools

Every API-key scope shown in a row is required. JWT sessions have implicit full
read access.

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

All public MCP tools are read-only. There are no public write tools. Resources
return markdown/plain-text snapshots for @-mention context:

- `tali://role/{role_id}` requires `roles:read`.
- `tali://application/{application_id}` requires `applications:read`.
- `tali://candidate/{candidate_id}/cv` requires `applications:read`.

### Header-capable client setup

- Streamable HTTP endpoint: `https://<your-taali-host>/mcp/`
- Private request header: `X-API-Key: tali_live_…`

Create the key in **Settings → Developers**, copy it when it is shown once,
and load it through the client's secret store. Do not place it in a shell
argument or tracked config. API keys are preferred for machine-to-machine
clients because they can be independently scoped, expired, and revoked.

Claude's remote custom-connector UI does not document arbitrary static request
headers, and `claude_desktop_config.json` does not directly connect remote MCP
URLs. Direct Claude connection is therefore unavailable until Taali implements
the planned OAuth 2.1 wrapper. See [the MCP server guide](../backend/docs/MCP_SERVER.md)
for the current limitation and the secret-safe short-lived JWT fallback for
other static-header clients.

---

## Interactive Docs

Outside production, Swagger UI is available at:

```
GET /api/docs
```

Outside production, the OpenAPI JSON schema is available at:

```
GET /api/openapi.json
```
