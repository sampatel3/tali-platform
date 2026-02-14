# FastAPI-Users Migration Summary

Auth has been migrated from custom code to **FastAPI-Users**.

## What Changed

### Backend

1. **Dependencies** (`requirements.txt`)
   - Added: `asyncpg`, `fastapi-users[sqlalchemy]==15.0.4`

2. **Database** (`app/platform/database.py`)
   - Added async engine and `get_async_db` for FastAPI-Users
   - Kept sync engine for existing routes (assessments, candidates, etc.)

3. **User model** (`app/models/user.py`)
   - Now extends `SQLAlchemyBaseUserTable[int]` from FastAPI-Users
   - Added: `full_name`, `organization_id`, `created_at`, `updated_at`
   - Uses `is_verified` (renamed from `is_email_verified`)
   - Dropped: `password_reset_token`, `password_reset_expires`, `email_verification_token`, `email_verification_sent_at` (FastAPI-Users uses JWT tokens)

4. **Auth module** (`app/api/v1/users_fastapi.py`)
   - User manager with Resend hooks (verification, forgot password)
   - Custom `UserCreate` with `full_name`, `organization_name`
   - Organization creation during registration
   - JWT auth backend

5. **Auth routes** (replaced `app/api/v1/auth.py`)
   - `POST /auth/jwt/login` – login (form: username, password)
   - `POST /auth/register` – register (JSON: email, password, full_name?, organization_name?)
   - `POST /auth/forgot-password` – forgot password (JSON: email)
   - `POST /auth/reset-password` – reset (JSON: token, password)
   - `POST /auth/request-verify-token` – resend verification (JSON: email)
   - `POST /auth/verify` – verify email (JSON: token)
   - `GET /users/me` – current user

6. **Dependencies** (`app/deps.py`)
   - `get_current_user` now uses FastAPI-Users `current_active_user`
   - All protected routes use this for auth

### Frontend

1. **API** (`frontend/src/shared/api/*`)
   - Login: `/auth/login` → `/auth/jwt/login`
   - Me: `/auth/me` → `/users/me`
   - Verify: `GET /auth/verify-email?token=` → `POST /auth/verify` with `{token}`
   - Resend: `/auth/resend-verification` → `/auth/request-verify-token`
   - Reset: `new_password` → `password` in payload

### Alembic

- **Migration 009** (`alembic/versions/009_fastapi_users_schema.py`)
  - Renames `is_email_verified` → `is_verified`
  - Drops password reset and verification token columns

## Before Running

1. **Install deps**
   ```bash
   cd backend && pip install -r requirements.txt
   ```

2. **Run migration**
   ```bash
   cd backend && alembic upgrade head
   ```

3. **Environment**
   - `RESEND_API_KEY` – required for verification and reset emails
   - `SECRET_KEY` – used by FastAPI-Users for JWT

## Testing

- Existing auth tests will need updates for new endpoints and response shapes.
- Manual checks: register → verify → login → forgot password → reset → login.
