"""Bounded provider-read helpers used by the Workable pull sync."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ....models.role import Role
from .service import WorkableRateLimitError, WorkableService
from .sync_lease import raise_if_sync_should_yield

logger = logging.getLogger(__name__)

_PREFETCH_WORKERS = 3


def job_identifiers(job: dict, role: Role | None = None) -> list[str]:
    identifiers: list[str] = []
    # SPI v3 resolves job details/candidates by shortcode in most accounts.
    for value in (job.get("shortcode"), role.workable_job_id if role else None):
        identifier = str(value or "").strip()
        if identifier and identifier not in identifiers:
            identifiers.append(identifier)
    # Some payloads expose a numeric code in application_url (/jobs/<code>).
    match = re.search(r"/jobs/([0-9]+)", str(job.get("application_url") or ""))
    if match and match.group(1) not in identifiers:
        identifiers.append(match.group(1))
    # Last fallback for accounts that resolve endpoints by id.
    raw_id = str(job.get("id") or "").strip()
    if raw_id and raw_id not in identifiers:
        identifiers.append(raw_id)
    return identifiers


def list_job_candidates(
    client: WorkableService,
    *,
    job: dict,
    role: Role | None,
    should_yield: Callable[[], bool] | None = None,
) -> list[dict]:
    """Fetch all candidates for the job, paginating through every page."""
    for identifier in job_identifiers(job, role):
        raise_if_sync_should_yield(should_yield)
        candidates = client.list_job_candidates(identifier, paginate=True, max_pages=None)
        raise_if_sync_should_yield(should_yield)
        if candidates:
            return candidates
    return []


def prefetch_full_candidate_payloads(
    client: WorkableService,
    candidate_refs: list[dict],
    *,
    is_terminal: Callable[[dict], bool],
    should_yield: Callable[[], bool] | None = None,
) -> dict[str, dict]:
    """Fan out candidate reads; failures fall back to each list payload."""
    ids = [
        str(ref.get("id") or "").strip()
        for ref in candidate_refs
        if str(ref.get("id") or "").strip() and not is_terminal(ref)
    ]
    if not ids:
        return {}

    def fetch(candidate_id: str) -> tuple[str, dict | None]:
        raise_if_sync_should_yield(should_yield)
        try:
            return candidate_id, client.get_candidate(candidate_id)
        except WorkableRateLimitError:
            raise
        except Exception as exc:
            logger.debug(
                "Prefetch candidate failed id=%s error_type=%s",
                candidate_id,
                type(exc).__name__,
            )
            return candidate_id, None

    payloads: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS) as pool:
        futures = [pool.submit(fetch, candidate_id) for candidate_id in ids]
        for future in as_completed(futures):
            candidate_id, payload = future.result()
            if isinstance(payload, dict) and payload:
                payloads[candidate_id] = payload
    return payloads


def prefetch_candidate_resumes(
    client: WorkableService,
    payloads_by_id: dict[str, dict],
    *,
    should_yield: Callable[[], bool] | None = None,
) -> dict[str, tuple[str, bytes]]:
    """Fan out resume downloads; failures fall back to the sequential flow."""
    if not payloads_by_id:
        return {}

    def download(
        candidate_id: str,
        payload: dict,
    ) -> tuple[str, tuple[str, bytes] | None]:
        raise_if_sync_should_yield(should_yield)
        try:
            return candidate_id, client.download_candidate_resume(payload)
        except WorkableRateLimitError:
            raise
        except Exception as exc:
            logger.debug(
                "Prefetch resume failed id=%s error_type=%s",
                candidate_id,
                type(exc).__name__,
            )
            return candidate_id, None

    downloads: dict[str, tuple[str, bytes]] = {}
    with ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS) as pool:
        futures = [pool.submit(download, candidate_id, payload) for candidate_id, payload in payloads_by_id.items()]
        for future in as_completed(futures):
            candidate_id, result = future.result()
            if result:
                downloads[candidate_id] = result
    return downloads


def job_details_for_role(
    client: WorkableService,
    cache: dict[str, dict],
    *,
    job: dict,
    role: Role | None = None,
    should_yield: Callable[[], bool] | None = None,
) -> dict:
    for identifier in job_identifiers(job, role):
        if identifier in cache:
            cached = cache.get(identifier) or {}
            if cached:
                return cached
            continue
        raise_if_sync_should_yield(should_yield)
        details = client.get_job_details(identifier)
        raise_if_sync_should_yield(should_yield)
        cache[identifier] = details or {}
        if details:
            return details
    return {}
