"""Workable ATS integration client."""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

logger = logging.getLogger(__name__)

# Workable: 10 requests per 10 seconds (https://workable.readme.io/reference/rate-limits)
# Use 0.3s to allow ~3 req/sec while staying under limit for bursts
WORKABLE_THROTTLE_SEC = 0.3
WORKABLE_429_RETRY_AFTER_SEC = 11
WORKABLE_JOBS_LIMIT = 100

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


class WorkableRateLimitError(RuntimeError):
    """Raised when Workable returns HTTP 429."""



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
        self.base_url = f"https://{subdomain}.workable.com/spi/v3"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        self._ratings_supported: bool | None = None

    def _throttle(self) -> None:
        time.sleep(WORKABLE_THROTTLE_SEC)

    def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.request(method, url, json=json, params=params, headers=self.headers)
                response.raise_for_status()
                self._throttle()
                return response.json() if response.content else {}
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429 and attempt == 0:
                    logger.warning("Workable 429, waiting %ss then retry", WORKABLE_429_RETRY_AFTER_SEC)
                    time.sleep(WORKABLE_429_RETRY_AFTER_SEC)
                    continue
                self._throttle()
                raise
        self._throttle()
        return {}

    def _request_optional(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        try:
            return self._request(method, path, json=json, params=params)
        except WorkableRateLimitError:
            raise
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            if status_code == 429:
                raise WorkableRateLimitError("Workable API rate limited (429)")
            logger.exception("Workable request failed: %s %s", method, path)
            self._throttle()
            return {}
        except Exception:
            logger.exception("Workable request failed: %s %s", method, path)
            self._throttle()
            return {}

    def _download(self, url: str) -> bytes:
        self._throttle()
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url, headers=self.headers)
            # Workable often returns a presigned URL for resumes; these reject extra auth headers.
            if response.status_code in {400, 401, 403}:
                response = client.get(url)
        response.raise_for_status()
        return response.content

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
        """Fetch all open/published jobs, with full pagination across all states."""
        seen_shortcodes: set[str] = set()
        all_jobs: list[dict] = []
        params_list = [
            {"state": "published", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"state": "open", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"state": "draft", "limit": str(WORKABLE_JOBS_LIMIT)},
            {"limit": str(WORKABLE_JOBS_LIMIT)},
        ]
        for i, params in enumerate(params_list):
            try:
                payload = self._request("GET", "/jobs", params=params) if i == 0 else self._request_optional("GET", "/jobs", params=params)
            except WorkableRateLimitError:
                raise
            except Exception:
                if i == 0:
                    raise
                continue
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
                self._throttle()
                payload = self._get_next_page(next_url)
            # Do not return early: aggregate jobs from all states
        return all_jobs

    def verify_access(self) -> None:
        # A simple authenticated read endpoint to validate token + subdomain.
        self._request("GET", "/jobs", params={"state": "published"})

    def _get_next_page(self, next_url: str) -> dict:
        """Fetch a single page using the full 'next' URL from Workable (handles absolute URLs)."""
        url = next_url.strip()
        if not url:
            return {}
        self._throttle()
        for attempt in range(2):
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(url, headers=self.headers)
            if response.status_code == 429 and attempt == 0:
                logger.warning("Workable 429 on next page, waiting %ss", WORKABLE_429_RETRY_AFTER_SEC)
                time.sleep(WORKABLE_429_RETRY_AFTER_SEC)
                continue
            if response.status_code == 429:
                raise WorkableRateLimitError("Workable API rate limited (429)")
            if response.status_code != 200:
                logger.warning("Workable next page returned %s for %s", response.status_code, url[:80])
                return {}
            try:
                return response.json() if response.content else {}
            except Exception:
                return {}
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
                    status = exc.response.status_code if exc.response else None
                    err_body = ""
                    if exc.response and exc.response.content:
                        try:
                            err_body = exc.response.text[:200] if exc.response.text else ""
                        except Exception:
                            pass
                    logger.warning(
                        "Workable GET %s returned %s (body=%s). Check token has candidates scope (r_candidates).",
                        path,
                        status,
                        err_body or "(none)",
                    )
                    return []
                except Exception as exc:
                    logger.exception("Workable GET %s failed: %s", path, exc)
                    return []
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

    def get_candidate_ratings(self, candidate_id: str) -> dict:
        if self._ratings_supported is False:
            return {}

        try:
            payload = self._request("GET", f"/candidates/{candidate_id}/ratings")
            self._ratings_supported = True
            return payload if isinstance(payload, dict) else {}
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            if status_code == 429:
                raise WorkableRateLimitError("Workable API rate limited (429)") from exc
            if status_code in {403, 404}:
                self._ratings_supported = False
                logger.info("Workable ratings endpoint unavailable (status=%s); skipping ratings fetches", status_code)
                return {}
            logger.exception("Failed fetching candidate ratings")
            return {}
        except Exception:
            logger.exception("Failed fetching candidate ratings")
            return {}

    def post_candidate_activity(self, candidate_id: str, body: str) -> dict:
        try:
            payload = self._request("POST", f"/candidates/{candidate_id}/activities", json={"body": body})
            return {"success": True, "response": payload}
        except Exception as exc:
            logger.exception("Failed posting candidate activity")
            return {"success": False, "response": {"error": str(exc)}}

    def post_assessment_result(self, candidate_id: str, assessment_data: dict) -> dict:
        score = assessment_data.get("score", 0)
        tests_passed = assessment_data.get("tests_passed", 0)
        tests_total = assessment_data.get("tests_total", 0)
        time_taken = assessment_data.get("time_taken", "N/A")
        results_url = assessment_data.get("results_url", "")
        body = (
            "TAALI Assessment Complete\n\n"
            f"Overall score: {score}/10\n"
            f"Tests passed: {tests_passed}/{tests_total}\n"
            f"Time taken: {time_taken} minutes\n"
            f"Full recruiter report: {results_url}\n\n"
            "This result was posted automatically by TAALI."
        )
        return self.post_candidate_activity(candidate_id, body)

    def update_candidate_stage(self, candidate_id: str, stage: str) -> dict:
        try:
            payload = self._request("PATCH", f"/candidates/{candidate_id}", json={"stage": stage})
            return {"success": True, "response": payload}
        except Exception as exc:
            logger.exception("Failed updating candidate stage")
            return {"success": False, "response": {"error": str(exc)}}

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
