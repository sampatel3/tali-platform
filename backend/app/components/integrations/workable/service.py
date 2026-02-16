"""Workable ATS integration client."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

import httpx

logger = logging.getLogger(__name__)

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

    def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, json=json, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json() if response.content else {}

    def _request_optional(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        try:
            return self._request(method, path, json=json, params=params)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            if status_code == 429:
                logger.warning("Workable rate limited (429) for %s %s", method, path)
                raise WorkableRateLimitError("Workable API rate limited (429)")
            logger.exception("Workable request failed: %s %s", method, path)
            return {}
        except Exception:
            logger.exception("Workable request failed: %s %s", method, path)
            return {}

    def _download(self, url: str) -> bytes:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url, headers=self.headers)
            # Workable often returns a presigned URL for resumes; these reject extra auth headers.
            if response.status_code in {400, 401, 403}:
                response = client.get(url)
        response.raise_for_status()
        return response.content

    def list_open_jobs(self) -> list[dict]:
        candidates = []
        for params in ({"state": "published"}, {"state": "open"}, {"status": "open"}, {}):
            payload = self._request_optional("GET", "/jobs", params=params)
            jobs = payload.get("jobs")
            if isinstance(jobs, list):
                candidates = jobs
                if candidates:
                    break
            elif isinstance(payload, list):
                candidates = payload
                if candidates:
                    break
        return [job for job in candidates if isinstance(job, dict)]

    def verify_access(self) -> None:
        # A simple authenticated read endpoint to validate token + subdomain.
        self._request("GET", "/jobs", params={"state": "published"})

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
        pages = 0

        while path:
            pages += 1
            payload = self._request_optional("GET", path, params=params)
            if isinstance(payload, dict):
                batch = payload.get("candidates")
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
            parsed_path = parsed.path or ""
            if parsed_path.startswith("/spi/v3"):
                parsed_path = parsed_path[len("/spi/v3"):]
            path = parsed_path if parsed_path else path
            params = dict(parse_qsl(parsed.query))

        return candidates

    def get_job_candidates_page(
        self,
        job_identifier: str,
        *,
        since_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict], str | None, bool]:
        """Fetch a single page of job candidates.

        Returns: (candidates, next_since_id, ok)
        """
        if not job_identifier:
            return [], None, False

        page_limit = self.DEFAULT_PAGE_LIMIT if limit is None else int(limit)
        params: dict[str, str] = {"limit": str(page_limit)}
        if since_id:
            params["since_id"] = str(since_id)

        payload = self._request_optional("GET", f"/jobs/{job_identifier}/candidates", params=params)
        if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
            batch = [item for item in payload.get("candidates") or [] if isinstance(item, dict)]
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            next_url = paging.get("next") if isinstance(paging, dict) else None
            next_since = None
            if isinstance(next_url, str) and next_url:
                try:
                    parsed = urlparse(next_url)
                    next_params = dict(parse_qsl(parsed.query))
                    next_since = (next_params.get("since_id") or "").strip() or None
                except Exception:
                    next_since = None
            return batch, next_since, True

        if isinstance(payload, list):
            batch = [item for item in payload if isinstance(item, dict)]
            return batch, None, True

        return [], None, False

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
