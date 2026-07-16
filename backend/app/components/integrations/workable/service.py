"""Workable ATS integration client."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx

from ....platform.secrets import decrypt_integration_secret
from .error_policy import public_workable_exception
from .url_security import (
    same_https_origin,
    validate_public_download_url,
    validate_workable_api_url,
)

logger = logging.getLogger(__name__)

# Workable rate-limits per OAuth token at 10 requests / 10 seconds
# (https://workable.readme.io/reference/rate-limits). Every outbound call is
# paced through a shared sliding-window limiter (see _WorkableRateLimiter) keyed
# by subdomain, kept one slot under the cap for headroom.
WORKABLE_RATE_WINDOW_SEC = 10.0
WORKABLE_RATE_MAX_REQUESTS = 9
WORKABLE_JOBS_LIMIT = 100

# 429 backoff: honor the server's Retry-After header when present, else
# exponential backoff. Bounded so a wedged token can't hang a sync forever.
WORKABLE_MAX_ATTEMPTS = 4
WORKABLE_BACKOFF_BASE_SEC = 2.0
WORKABLE_BACKOFF_CAP_SEC = 30.0

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class WorkableRateLimitError(RuntimeError):
    """Raised when Workable returns HTTP 429."""


class _WorkableRateLimiter:
    """Process-global sliding-window limiter, one instance per Workable token.

    A single org sync fans out across a prefetch thread-pool, so blind per-call
    sleeps let several threads burst past Workable's 10 req/10s limit and trip
    429s — which in turn starve the user-facing write path sharing the same
    token. This limiter coordinates all in-process callers for a subdomain:
    ``acquire`` blocks until a slot is free within the rolling window.

    The per-org Redis mutex already prevents two *worker tasks* from hitting the
    same token concurrently, so an in-process limiter is enough to cap the burst
    from one task's threads; the headroom slot absorbs the occasional
    cross-process caller (e.g. an API request hitting the same token).
    """

    def __init__(self, max_requests: int, window_sec: float):
        self._max = max(1, int(max_requests))
        self._window = float(window_sec)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self._window
                while self._calls and self._calls[0] <= cutoff:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                wait = self._calls[0] + self._window - now
            if wait > 0:
                time.sleep(wait)


_rate_limiters: dict[str, _WorkableRateLimiter] = {}
_rate_limiters_lock = threading.Lock()


def _get_rate_limiter(subdomain: str) -> _WorkableRateLimiter:
    """Return the shared limiter for a subdomain (one budget per OAuth token)."""
    key = (subdomain or "").strip().lower()
    with _rate_limiters_lock:
        limiter = _rate_limiters.get(key)
        if limiter is None:
            limiter = _WorkableRateLimiter(
                WORKABLE_RATE_MAX_REQUESTS, WORKABLE_RATE_WINDOW_SEC
            )
            _rate_limiters[key] = limiter
        return limiter


def _retry_after_seconds(response: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait before retrying a 429: honor Retry-After, else backoff."""
    header = response.headers.get("Retry-After") if response is not None else None
    if header:
        try:
            return min(float(header), WORKABLE_BACKOFF_CAP_SEC)
        except (TypeError, ValueError):
            pass  # Retry-After may be an HTTP-date — fall through to backoff
    return min(
        WORKABLE_BACKOFF_BASE_SEC * (2 ** max(0, attempt)), WORKABLE_BACKOFF_CAP_SEC
    )



def _normalize_score(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except Exception:
        return None
    if numeric < 0:
        return None
    if numeric <= 1:
        return round(numeric * 10.0, 2)
    if numeric <= 10:
        return round(numeric, 2)
    if numeric <= 100:
        return round(numeric / 10.0, 2)
    return None


class WorkableService:
    """Service for interacting with the Workable API (SPI v3)."""

    SCORE_KEYWORDS = ("score", "rating", "match")
    DEFAULT_PAGE_LIMIT = 100

    def __init__(self, access_token: str, subdomain: str):
        access_token = decrypt_integration_secret(access_token, allow_plaintext=True)
        if not access_token:
            raise ValueError("Workable access token is unavailable")
        normalized_subdomain = str(subdomain or "").strip().lower()
        if not _SUBDOMAIN_RE.fullmatch(normalized_subdomain):
            raise ValueError("Invalid Workable subdomain")
        self._hostname = f"{normalized_subdomain}.workable.com"
        self.base_url = f"https://{self._hostname}/spi/v3"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._ratings_supported: bool | None = None
        self._rate_limiter = _get_rate_limiter(subdomain)

    def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(WORKABLE_MAX_ATTEMPTS):
            self._rate_limiter.acquire()
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.request(method, url, json=json, params=params, headers=self.headers)
                response.raise_for_status()
                return response.json() if response.content else {}
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt < WORKABLE_MAX_ATTEMPTS - 1:
                    wait = _retry_after_seconds(exc.response, attempt)
                    logger.warning(
                        "Workable 429 on %s %s; waiting %.1fs then retry (attempt %d/%d)",
                        method, path, wait, attempt + 1, WORKABLE_MAX_ATTEMPTS,
                    )
                    time.sleep(wait)
                    continue
                raise
        return {}

    def _request_optional(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        try:
            return self._request(method, path, json=json, params=params)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                raise WorkableRateLimitError("Workable API rate limited (429)")
            if status_code == 404:
                logger.info("Optional Workable endpoint not found: %s %s", method, path)
                return {}
            raise

    def _download(self, url: str) -> bytes:
        current_url = validate_public_download_url(url)
        self._rate_limiter.acquire()
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            for _ in range(4):
                attach_auth = same_https_origin(current_url, host=self._hostname)
                response = client.get(
                    current_url,
                    headers=self.headers if attach_auth else None,
                )
                # Some Workable endpoints return a presigned object directly and
                # reject extra headers. Retrying without auth is safe; the
                # inverse (sending auth to the object host) is never attempted.
                if attach_auth and response.status_code in {400, 401, 403}:
                    response = client.get(current_url)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Workable download redirect has no location")
                    current_url = validate_public_download_url(
                        urljoin(current_url, location)
                    )
                    continue
                response.raise_for_status()
                return response.content
        raise ValueError("Too many Workable download redirects")

    def _parse_jobs_response(self, payload: dict | list) -> list[dict]:
        """Extract list of job dicts from Workable API response."""
        if isinstance(payload, list):
            return [j for j in payload if isinstance(j, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("jobs", "data", "results"):
            jobs = payload.get(key)
            if isinstance(jobs, list):
                out = [j for j in jobs if isinstance(j, dict)]
                if out:
                    return out
            if isinstance(jobs, dict):
                inner = jobs.get("jobs") or jobs.get("data")
                if isinstance(inner, list):
                    out = [j for j in inner if isinstance(j, dict)]
                    if out:
                        return out
        return []

    def list_open_jobs(self) -> list[dict]:
        """Fetch all jobs across every Workable state, with full pagination."""
        seen_shortcodes: set[str] = set()
        all_jobs: list[dict] = []
        # Workable's valid states are: published, draft, archived, closed.
        # The no-state call returns jobs across all states for the token
        # we use, but we keep per-state calls as a defensive fallback in
        # case the no-state default changes for some token scopes.
        params_list = [
            {"state": "published", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"state": "draft", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"state": "archived", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"state": "closed", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"limit": str(WORKABLE_JOBS_LIMIT)},
        ]
        for i, params in enumerate(params_list):
            payload = (
                self._request("GET", "/jobs", params=params)
                if i == 0
                else self._request_optional("GET", "/jobs", params=params)
            )
            while True:
                jobs = self._parse_jobs_response(payload)
                if not jobs and isinstance(payload, dict) and not all_jobs and i == 0:
                    logger.info(
                        "Workable /jobs state=%s returned 0 jobs (response keys=%s)",
                        params.get("state", "none"),
                        list(payload.keys())[:15],
                    )
                for j in jobs:
                    if not isinstance(j, dict):
                        continue
                    shortcode = (j.get("shortcode") or j.get("id") or "").strip()
                    if shortcode and shortcode not in seen_shortcodes:
                        seen_shortcodes.add(shortcode)
                        all_jobs.append(j)
                paging = payload.get("paging") if isinstance(payload, dict) else None
                next_url = paging.get("next") if isinstance(paging, dict) else None
                if not isinstance(next_url, str) or not next_url.strip():
                    break
                payload = self._get_next_page(next_url)
            # Do not return early: aggregate jobs from all states
        return all_jobs

    def verify_access(self) -> None:
        # A simple authenticated read endpoint to validate token + subdomain.
        self._request("GET", "/jobs", params={"state": "published"})

    def _get_next_page(self, next_url: str) -> dict:
        """Fetch a single page using the full 'next' URL from Workable (handles absolute URLs)."""
        url = validate_workable_api_url(
            next_url,
            expected_host=self._hostname,
            base_url=self.base_url,
        )
        if not url:
            return {}
        for attempt in range(WORKABLE_MAX_ATTEMPTS):
            self._rate_limiter.acquire()
            with httpx.Client(timeout=30.0, follow_redirects=False) as client:
                response = client.get(url, headers=self.headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                raise ValueError("Workable pagination redirects are not allowed")
            if response.status_code == 429:
                if attempt < WORKABLE_MAX_ATTEMPTS - 1:
                    wait = _retry_after_seconds(response, attempt)
                    logger.warning(
                        "Workable 429 on next page; waiting %.1fs (attempt %d/%d)",
                        wait, attempt + 1, WORKABLE_MAX_ATTEMPTS,
                    )
                    time.sleep(wait)
                    continue
                raise WorkableRateLimitError("Workable API rate limited (429)")
            response.raise_for_status()
            return response.json() if response.content else {}
        return {}

    def list_job_candidates(
        self,
        job_identifier: str,
        *,
        paginate: bool = False,
        max_pages: int | None = None,
    ) -> list[dict]:
        if not job_identifier:
            return []

        candidates: list[dict] = []
        seen_ids: set[str] = set()
        path = f"/jobs/{job_identifier}/candidates"
        params: dict[str, str] | None = {"limit": str(self.DEFAULT_PAGE_LIMIT)}
        next_page_url: str | None = None
        pages = 0

        while True:
            pages += 1
            if next_page_url:
                payload = self._get_next_page(next_page_url)
                next_page_url = None
            else:
                try:
                    payload = self._request("GET", path, params=params)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    if status == 404:
                        logger.info("Workable candidate list not found for job path=%s", path)
                        return []
                    raise
                if not payload and not isinstance(payload, dict):
                    payload = {}

            if isinstance(payload, dict):
                batch = (
                    payload.get("candidates")
                    or payload.get("data")
                    or payload.get("results")
                    or payload.get("applicants")
                )
                if isinstance(batch, dict):
                    batch = (
                        batch.get("candidates")
                        or batch.get("data")
                        or batch.get("results")
                        or batch.get("applicants")
                        or []
                    )
                if not isinstance(batch, list):
                    batch = []
                if pages == 1 and len(candidates) == 0 and len(batch) == 0 and payload:
                    logger.info(
                        "Workable GET %s returned 0 candidates. Response keys=%s",
                        path,
                        list(payload.keys())[:15],
                    )
                paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
                next_url = paging.get("next") if isinstance(paging, dict) else None
            elif isinstance(payload, list):
                batch = payload
                next_url = None
            else:
                batch = []
                next_url = None

            if isinstance(batch, list):
                for candidate in batch:
                    if not isinstance(candidate, dict):
                        continue
                    candidate_id = str(candidate.get("id") or "").strip()
                    if candidate_id and candidate_id in seen_ids:
                        continue
                    if candidate_id:
                        seen_ids.add(candidate_id)
                    candidates.append(candidate)

            if not paginate:
                break
            if max_pages is not None and pages >= max_pages:
                break
            if not isinstance(next_url, str) or not next_url:
                break
            parsed = urlparse(next_url)
            if parsed.scheme and parsed.netloc and "workable.com" in parsed.netloc:
                next_page_url = next_url
            else:
                parsed_path = parsed.path or ""
                if parsed_path.startswith("/spi/v3"):
                    parsed_path = parsed_path[len("/spi/v3"):]
                path = parsed_path or path
                params = dict(parse_qsl(parsed.query))

        return candidates

    def get_job_details(self, job_identifier: str) -> dict:
        if not job_identifier:
            return {}
        payload = self._request_optional("GET", f"/jobs/{job_identifier}")
        return payload if isinstance(payload, dict) else {}

    def get_candidate(self, candidate_id: str) -> dict:
        payload = self._request_optional("GET", f"/candidates/{candidate_id}")
        if not isinstance(payload, dict):
            return {}
        wrapped = payload.get("candidate")
        if isinstance(wrapped, dict):
            return wrapped
        return payload

    def get_candidate_files(self, candidate_id: str) -> list[dict]:
        """Fetch files for a candidate via GET /candidates/:id/files (Workable API)."""
        if not candidate_id:
            return []
        payload = self._request_optional("GET", f"/candidates/{candidate_id}/files")
        if not isinstance(payload, dict):
            return []
        files = payload.get("files") or payload.get("data") or payload.get("attachments")
        if isinstance(files, list):
            return [f for f in files if isinstance(f, dict)]
        return []

    # Workable's activities endpoint paginates at 50 entries by default
    # (limit can be raised but max is 100). For candidates with long
    # histories — assessments + interviews + multiple recruiter comments
    # — older entries (including recruiter comments carrying salary /
    # notice-period context) would silently fall off the first page,
    # which defeats the purpose of feeding this data into pre-screen.
    # ``MAX_ACTIVITIES_PAGES`` caps total pages walked so a runaway feed
    # can't drain our Workable rate budget on one candidate.
    _ACTIVITIES_PAGE_LIMIT = 100
    _ACTIVITIES_MAX_PAGES = 10

    def get_candidate_activities(self, candidate_id: str) -> list[dict] | None:
        """Fetch the full paginated activity log via
        GET /candidates/:id/activities.

        Workable's activity feed is the authoritative source for both
        the timeline (stage transitions, assessment/interview/message
        events) AND recruiter comments — entries carry ``action ==
        "comment"`` with ``body`` + ``member`` set. Workable's public
        API does not expose a ``GET`` on ``/candidates/:id/comments``
        (only ``POST`` for creation), so the activities feed is the
        only way to ingest recruiter comments.

        Walks pagination via the ``paging.next`` link until exhausted
        or ``_ACTIVITIES_MAX_PAGES`` is hit. Returns ``None`` if the
        first page fails so callers can distinguish "no activities"
        from "couldn't reach the endpoint" and preserve stored data.
        Partial failures (page 1 succeeds, page 2 errors) return what
        we got — a partial timeline is better than wiping stored data.
        """
        if not candidate_id:
            return None
        payload = self._request_optional(
            "GET",
            f"/candidates/{candidate_id}/activities",
            params={"limit": str(self._ACTIVITIES_PAGE_LIMIT)},
        )
        if not isinstance(payload, dict):
            return None
        if "activities" not in payload and "data" not in payload:
            return None

        def _extract(p: dict) -> list[dict]:
            items = p.get("activities") or p.get("data") or []
            return [a for a in items if isinstance(a, dict)] if isinstance(items, list) else []

        results: list[dict] = _extract(payload)
        pages_walked = 1
        while pages_walked < self._ACTIVITIES_MAX_PAGES:
            paging = payload.get("paging") if isinstance(payload, dict) else None
            next_url = paging.get("next") if isinstance(paging, dict) else None
            if not isinstance(next_url, str) or not next_url.strip():
                break
            try:
                payload = self._get_next_page(next_url)
            except WorkableRateLimitError:
                raise
            if not isinstance(payload, dict) or not payload:
                break
            results.extend(_extract(payload))
            pages_walked += 1
        if pages_walked >= self._ACTIVITIES_MAX_PAGES:
            logger.warning(
                "Workable activities pagination hit cap (%d pages) for candidate=%s — older entries may be truncated",
                self._ACTIVITIES_MAX_PAGES,
                candidate_id,
            )
        return results

    def get_candidate_ratings(self, candidate_id: str) -> dict:
        if self._ratings_supported is False:
            return {}

        try:
            payload = self._request("GET", f"/candidates/{candidate_id}/ratings")
            self._ratings_supported = True
            return payload if isinstance(payload, dict) else {}
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 429:
                raise WorkableRateLimitError("Workable API rate limited (429)") from exc
            if status_code == 404:
                self._ratings_supported = False
                logger.info("Workable ratings endpoint unavailable (status=%s); skipping ratings fetches", status_code)
                return {}
            raise

    def post_candidate_comment(self, candidate_id: str, member_id: str, body: str) -> dict:
        # Workable's only candidate write-back for free-text notes is
        # POST /candidates/{id}/comments — it requires a member_id to
        # attribute the comment. (/activities is read-only and 404s on POST.)
        mid = str(member_id or "").strip()
        if not mid:
            return {
                "success": False,
                "error": "member_id is required to post a candidate comment",
                "status_code": None,
                "response": {"error": "member_id is required to post a candidate comment"},
            }
        try:
            payload = self._request(
                "POST",
                f"/candidates/{candidate_id}/comments",
                json={"member_id": mid, "comment": {"body": body}},
            )
            return {"success": True, "response": payload}
        except Exception as exc:
            logger.exception("Failed posting candidate comment")
            return self._failure_result(exc)

    def post_assessment_result(self, candidate_id: str, member_id: str, assessment_data: dict) -> dict:
        score = assessment_data.get("score", 0)
        tests_passed = assessment_data.get("tests_passed", 0)
        tests_total = assessment_data.get("tests_total", 0)
        time_taken = assessment_data.get("time_taken", "N/A")
        results_url = assessment_data.get("results_url", "")
        body = (
            "Taali assessment complete\n\n"
            f"Overall score: {score}/10\n"
            f"Tests passed: {tests_passed}/{tests_total}\n"
            f"Time taken: {time_taken} minutes\n"
            f"Full recruiter report: {results_url}\n\n"
            "Posted automatically by Taali."
        )
        return self.post_candidate_comment(candidate_id, member_id, body)

    def move_candidate(
        self,
        *,
        candidate_id: str,
        member_id: str,
        target_stage: str,
    ) -> dict:
        payload: dict[str, Any] = {
            "member_id": str(member_id),
            "target_stage": str(target_stage),
        }
        try:
            response = self._request("POST", f"/candidates/{candidate_id}/move", json=payload)
            return {"success": True, "response": response}
        except Exception as exc:
            logger.exception("Failed moving candidate")
            return self._failure_result(exc)

    def update_candidate_stage(self, candidate_id: str, stage: str, member_id: str | None = None) -> dict:
        if not str(member_id or "").strip():
            return {
                "success": False,
                "error": "member_id is required to move candidate stage",
                "status_code": None,
                "response": {"error": "member_id is required to move candidate stage"},
            }
        return self.move_candidate(
            candidate_id=candidate_id,
            member_id=str(member_id).strip(),
            target_stage=stage,
        )

    def _extract_collection(self, payload: Any, *, keys: tuple[str, ...]) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def list_members(self, *, limit: int = 100, shortcode: str | None = None) -> list[dict]:
        params: dict[str, str] = {
            "limit": str(max(1, min(int(limit or 100), 100))),
            "status": "all",
        }
        if shortcode:
            params["shortcode"] = str(shortcode)
        payload = self._request_optional("GET", "/members", params=params)
        return self._extract_collection(payload, keys=("members", "data", "results"))

    def list_disqualification_reasons(self) -> list[dict]:
        payload = self._request_optional("GET", "/disqualification_reasons")
        return self._extract_collection(payload, keys=("disqualification_reasons", "reasons", "data", "results"))

    def list_stages(self) -> list[dict]:
        payload = self._request_optional("GET", "/stages")
        return self._extract_collection(payload, keys=("stages", "data", "results"))

    def list_job_stages(self, shortcode: str) -> list[dict]:
        payload = self._request_optional("GET", f"/jobs/{shortcode}/stages")
        return self._extract_collection(payload, keys=("stages", "data", "results"))

    def disqualify_candidate(
        self,
        *,
        candidate_id: str,
        member_id: str,
        disqualify_reason_id: str | None = None,
        disqualify_note: str | None = None,
        withdrew: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            "member_id": str(member_id),
            "withdrew": bool(withdrew),
        }
        reason_id = str(disqualify_reason_id or "").strip()
        if reason_id:
            payload["disqualify_reason_id"] = reason_id
        note = str(disqualify_note or "").strip()
        if note:
            payload["disqualify_note"] = note[:256]
        try:
            response = self._request("POST", f"/candidates/{candidate_id}/disqualify", json=payload)
            return {"success": True, "response": response}
        except Exception as exc:
            logger.exception("Failed disqualifying candidate")
            return self._failure_result(exc)

    def revert_candidate_disqualification(
        self,
        *,
        candidate_id: str,
        member_id: str,
    ) -> dict:
        payload: dict[str, Any] = {
            "member_id": str(member_id),
        }
        try:
            response = self._request("POST", f"/candidates/{candidate_id}/revert", json=payload)
            return {"success": True, "response": response}
        except Exception as exc:
            logger.exception("Failed reverting candidate disqualification")
            return self._failure_result(exc)

    def download_candidate_resume(self, candidate_payload: dict) -> tuple[str, bytes] | None:
        resume_url = candidate_payload.get("resume_url")
        if isinstance(resume_url, str) and resume_url.strip():
            metadata = candidate_payload.get("resume_metadata")
            filename = None
            if isinstance(metadata, dict):
                filename = metadata.get("filename") or metadata.get("name")
            if not filename:
                parsed = urlparse(resume_url)
                url_name = (parsed.path or "").rsplit("/", 1)[-1]
                if url_name and "." in url_name:
                    filename = url_name
            filename = str(filename or "resume.pdf")
            try:
                content = self._download(resume_url)
                if content:
                    return filename, content
            except Exception:
                logger.exception("Failed downloading candidate resume_url")

        attachments = []
        for key in ("attachments", "files", "documents"):
            value = candidate_payload.get(key)
            if isinstance(value, list):
                attachments.extend(item for item in value if isinstance(item, dict))
        resume = candidate_payload.get("resume")
        if isinstance(resume, dict):
            attachments.insert(0, resume)
        for item in attachments:
            url = (
                item.get("download_url")
                or item.get("url")
                or item.get("file_url")
                or item.get("href")
            )
            if not url:
                continue
            filename = (
                item.get("filename")
                or item.get("name")
                or item.get("title")
                or "resume.pdf"
            )
            try:
                content = self._download(url)
            except Exception:
                logger.exception("Failed downloading candidate attachment")
                continue
            if content:
                return str(filename), content

        # Workable API: GET /candidates/:id/files returns files attached to the candidate
        candidate_id = str(candidate_payload.get("id") or "").strip()
        if candidate_id:
            api_files = self.get_candidate_files(candidate_id)
            for item in api_files:
                url = (
                    item.get("download_url")
                    or item.get("url")
                    or item.get("file_url")
                    or item.get("href")
                    or item.get("source")
                )
                if not url:
                    continue
                filename = (
                    item.get("filename")
                    or item.get("name")
                    or item.get("title")
                    or item.get("file_name")
                    or "resume.pdf"
                )
                try:
                    content = self._download(url)
                except Exception:
                    logger.exception("Failed downloading candidate file from /files")
                    continue
                if content:
                    return str(filename), content
        return None

    def extract_workable_score(self, *, candidate_payload: dict, ratings_payload: dict | None = None) -> tuple[float | None, float | None, str | None]:
        candidates = []
        self._collect_score_candidates(candidate_payload, prefix="candidate", output=candidates)
        if isinstance(ratings_payload, dict):
            self._collect_score_candidates(ratings_payload, prefix="ratings", output=candidates)

        if not candidates:
            return None, None, None

        def priority(path: str) -> int:
            ordered = (
                "ai_rating",
                "match_score",
                "overall_score",
                "average_score",
                "score",
                "rating",
            )
            for idx, token in enumerate(ordered):
                if token in path:
                    return idx
            return len(ordered)

        raw_value, source_path = sorted(candidates, key=lambda item: priority(item[1]))[0]
        normalized = _normalize_score(raw_value)
        return float(raw_value), normalized, source_path

    def _collect_score_candidates(self, payload: Any, *, prefix: str, output: list[tuple[float, str]]) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                path = f"{prefix}.{key}"
                key_lower = str(key).lower()
                if isinstance(value, (int, float)) and any(token in key_lower for token in self.SCORE_KEYWORDS):
                    output.append((float(value), path))
                elif isinstance(value, str) and any(token in key_lower for token in self.SCORE_KEYWORDS):
                    candidate = value.strip()
                    if candidate and _NUMERIC_RE.match(candidate):
                        try:
                            output.append((float(candidate), path))
                        except Exception:
                            pass
                self._collect_score_candidates(value, prefix=path, output=output)
        elif isinstance(payload, list):
            for idx, value in enumerate(payload):
                self._collect_score_candidates(value, prefix=f"{prefix}[{idx}]", output=output)

    def _failure_result(self, exc: Exception) -> dict:
        status_code = None
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code if exc.response is not None else None
        error_message = public_workable_exception(exc)
        error_code = error_message.split(":", 1)[0]
        return {
            "success": False,
            "error": error_message,
            "error_code": error_code,
            "status_code": status_code,
            "response": {
                "error": error_message,
                "error_code": error_code,
                "status_code": status_code,
            },
        }
