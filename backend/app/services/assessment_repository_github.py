"""GitHub API transport and repository-discovery helpers.

The mixin deliberately has no constructor.  ``AssessmentRepositoryService``
continues to own configuration and the public API, while these transport
methods remain available on the same service instance for compatibility.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import httpx

from .assessment_repository_types import AssessmentRepositoryError


class AssessmentRepositoryGitHubMixin:
    github_org: str
    github_token: str
    api_base: str
    http_timeout_seconds: float

    def _require_token(self) -> str:
        token = (self.github_token or "").strip()
        if not token:
            raise AssessmentRepositoryError(
                "GITHUB_TOKEN is required when GITHUB_MOCK_MODE is false"
            )
        return token

    def _headers(self) -> Dict[str, str]:
        token = self._require_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code == 403:
            if response.headers.get("X-RateLimit-Remaining") == "0":
                return True
            body = (response.text or "").lower()
            return "rate limit" in body or "secondary rate" in body
        return False

    def _rate_limit_delay(self, response: httpx.Response, attempt: int) -> float:
        """Honor Retry-After / X-RateLimit-Reset where present, capped."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 15.0)
            except ValueError:
                pass
        reset = response.headers.get("X-RateLimit-Reset")
        if reset:
            try:
                delta = float(reset) - time.time()
                if delta > 0:
                    return min(delta, 15.0)
            except ValueError:
                pass
        return float(min(2 ** attempt, 8))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Dict[str, Any] | None = None,
        expected_statuses: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        url = f"{self.api_base}{path}"
        attempts = 4
        response: httpx.Response | None = None
        for attempt in range(attempts):
            try:
                response = httpx.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_payload,
                    timeout=self.http_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                raise AssessmentRepositoryError(
                    f"GitHub API request failed for {path}: {exc}"
                ) from exc

            # Back off on GitHub primary/secondary rate limits rather than
            # surfacing an opaque error when many candidates start at once.
            if (
                self._is_rate_limited(response)
                and response.status_code not in expected_statuses
                and attempt < attempts - 1
            ):
                time.sleep(self._rate_limit_delay(response, attempt))
                continue
            break

        assert response is not None  # loop always assigns or raises
        if response.status_code not in expected_statuses:
            detail = response.text.strip()[:500]
            raise AssessmentRepositoryError(
                f"GitHub API {method} {path} returned {response.status_code}: {detail}"
            )
        return response

    def _ensure_repo_exists(self, repo_name: str) -> None:
        check = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}",
            expected_statuses=(200, 404),
        )
        if check.status_code == 200:
            payload = check.json() if check.content else {}
            if payload.get("private") is not True:
                hardened = self._request(
                    "PATCH",
                    f"/repos/{self.github_org}/{repo_name}",
                    json_payload={"private": True, "visibility": "private"},
                    expected_statuses=(200,),
                )
                hardened_payload = hardened.json() if hardened.content else {}
                if hardened_payload.get("private") is not True:
                    raise AssessmentRepositoryError(
                        f"Repository {self.github_org}/{repo_name} could not be made private"
                    )
            return

        create = self._request(
            "POST",
            f"/orgs/{self.github_org}/repos",
            json_payload={
                "name": repo_name,
                "private": True,
                "auto_init": False,
                "has_issues": False,
                "has_wiki": False,
                "has_projects": False,
            },
            expected_statuses=(201, 422),
        )
        if create.status_code == 201:
            return

        # 422 can happen due to an existing repo in races/retries; verify it.
        verify = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}",
            expected_statuses=(200, 404),
        )
        if verify.status_code == 200:
            return
        detail = create.text.strip()[:500]
        raise AssessmentRepositoryError(
            f"Unable to create repository {repo_name}: {detail}"
        )

    def _main_head_sha(self, repo_name: str) -> str:
        ref = self._request(
            "GET",
            f"/repos/{self.github_org}/{repo_name}/git/ref/heads/main",
            expected_statuses=(200, 404),
        )
        if ref.status_code == 404:
            raise AssessmentRepositoryError(
                f"Repository {repo_name} has no main branch"
            )
        payload = ref.json() if ref.content else {}
        sha = (payload.get("object") or {}).get("sha")
        if not sha:
            raise AssessmentRepositoryError(
                f"Unable to resolve main branch SHA for {repo_name}"
            )
        return str(sha)

    @staticmethod
    def _response_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text or ""
        message = payload.get("message")
        if isinstance(message, str):
            return message
        return str(payload)


__all__ = ["AssessmentRepositoryGitHubMixin"]
