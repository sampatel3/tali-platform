# TALI API Reference

Base URL: `/api/v1`

All endpoints (except Health, assessment start, and webhooks) require a Bearer token in the `Authorization` header.

---

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Health check. Returns `{"status": "healthy", "service": "tali-api"}`. |

---

## Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/auth/register` | No | Register a new user (and optionally create an organization). |
| `POST` | `/api/v1/auth/login` | No | Authenticate with email/password. Returns a JWT access token. |
| `GET` | `/api/v1/auth/me` | Yes | Get the currently authenticated user. |

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

### POST /api/v1/auth/login

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

## Tasks

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/v1/tasks/` | Yes | Create a new coding task. |
| `GET` | `/api/v1/tasks/` | Yes | List all tasks (org-specific + templates). |
| `GET` | `/api/v1/tasks/{id}` | Yes | Get a single task by ID. |

### POST /api/v1/tasks/

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
| `POST` | `/api/v1/webhooks/workable` | Signature | Receives Workable webhook events (e.g., `candidate_stage_changed`). |
| `POST` | `/api/v1/webhooks/stripe` | Signature | Receives Stripe webhook events (e.g., `payment_intent.succeeded`). |

Both endpoints verify request signatures. Do not call these directly — they are meant to be called by the respective third-party services.

---

## Interactive Docs

Swagger UI is available at:

```
GET /api/docs
```

The OpenAPI JSON schema is available at:

```
GET /api/openapi.json
```
