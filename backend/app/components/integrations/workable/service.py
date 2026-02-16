"""Workable ATS integration client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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

    def __init__(self, access_token: str, subdomain: str):
        self.base_url = f"https://{subdomain}.workable.com/spi/v3"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, json=json, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json() if response.content else {}

    def _request_optional(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None) -> dict:
        try:
            return self._request(method, path, json=json, params=params)
        except Exception:
            logger.exception("Workable request failed: %s %s", method, path)
            return {}

    def _download(self, url: str) -> bytes:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self.headers)
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

    def list_job_candidates(self, job_identifier: str) -> list[dict]:
        payload = self._request_optional("GET", f"/jobs/{job_identifier}/candidates")
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            return [candidate for candidate in candidates if isinstance(candidate, dict)]
        if isinstance(payload, list):
            return [candidate for candidate in payload if isinstance(candidate, dict)]
        return []

    def get_candidate(self, candidate_id: str) -> dict:
        payload = self._request_optional("GET", f"/candidates/{candidate_id}")
        return payload if isinstance(payload, dict) else {}

    def get_candidate_ratings(self, candidate_id: str) -> dict:
        payload = self._request_optional("GET", f"/candidates/{candidate_id}/ratings")
        return payload if isinstance(payload, dict) else {}

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
                self._collect_score_candidates(value, prefix=path, output=output)
        elif isinstance(payload, list):
            for idx, value in enumerate(payload):
                self._collect_score_candidates(value, prefix=f"{prefix}[{idx}]", output=output)
