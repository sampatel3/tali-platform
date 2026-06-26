"""GitHub credential health verification.

Deliberately a tiny standalone module (not a method on the ~500-LOC
``assessment_repository_service``) so the provisioning watchdog stays decoupled
from the heavy repo manager and the arch file-size gate stays green.

An expired/revoked ``GITHUB_TOKEN`` returns 401 and silently blocks EVERY
candidate from starting an assessment — repo provisioning runs at both send and
start — which is exactly how the 2026-06-25 zero-traction incident happened. The
``assessment_provisioning_healthcheck`` beat polls this and alerts on failure.
"""
import os
from typing import Any, Dict

import httpx


def verify_github_credentials(
    *,
    org: str | None = None,
    token: str | None = None,
    api_base: str | None = None,
    mock_mode: bool | None = None,
    timeout_seconds: float = 20.0,
) -> Dict[str, Any]:
    """Non-raising check that the GitHub token is valid.

    ``GET /rate_limit`` validates auth without consuming the rate-limit budget.
    Returns ``{ok, status_code, detail, org}`` (``ok=False`` on 401 / missing
    token / unreachable). Validates auth only — does not prove the token still
    carries repo-create scope. Args default to the ``GITHUB_*`` env/settings.
    """
    org = org if org is not None else os.getenv("GITHUB_ORG", "taali-assessments")
    token = (token if token is not None else os.getenv("GITHUB_TOKEN", "") or "").strip()
    api_base = (api_base or os.getenv("GITHUB_API_BASE_URL", "https://api.github.com")).rstrip("/")
    if mock_mode is None:
        env = os.getenv("GITHUB_MOCK_MODE")
        mock_mode = env.lower() in {"1", "true", "yes"} if env is not None else False

    if mock_mode:
        return {"ok": True, "mock": True, "detail": "GITHUB_MOCK_MODE", "org": org}
    if not token:
        return {"ok": False, "status_code": None, "detail": "GITHUB_TOKEN not set", "org": org}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = httpx.get(f"{api_base}/rate_limit", headers=headers, timeout=timeout_seconds)
    except Exception as exc:  # network/DNS/timeout — surface, never raise
        return {"ok": False, "status_code": None, "detail": f"github unreachable: {exc!r}"[:300], "org": org}

    ok = resp.status_code == 200
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "detail": "ok" if ok else (resp.text or "")[:300],
        "org": org,
    }
