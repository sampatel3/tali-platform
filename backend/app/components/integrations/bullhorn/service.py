"""Bullhorn REST client — transport + typed methods.

Pairs with :mod:`auth` (discovery/OAuth/session) and :mod:`ratelimit` (token
bucket + 429 breaker). This module owns the request loop shared by every typed
method: pace via the bucket, attach the live BhRestToken, back off on 429 (both
flavors), reauth-once on 401, and enforce verb discipline
(PUT=create, POST=update) so callers never pick a verb.

Nothing here logs a token, secret, corpToken, or a full URL that would carry one
— log lines carry method + relative path only.

The typed methods (search_job_orders, search_candidates, query_job_submissions,
get_job_submission_history, get_status_list, update_job_submission_status,
create_note, list_file_attachments, get_file_raw, convert_resume_to_text,
get_entitlements, create_subscription, poll_events, refetch_events,
delete_subscription) are exactly the surface PR-5's sync/write-back needs.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx

from .auth import BullhornAuth, quiet_httpx
from .errors import BullhornApiError, BullhornAuthError, BullhornRateLimitError
from .ratelimit import (
    BULLHORN_MAX_ATTEMPTS,
    CircuitBreaker,
    TokenBucket,
    get_breaker,
    get_bucket,
    retry_after_seconds,
)

logger = logging.getLogger(__name__)

# /search hard page cap we page defensively against (real Bullhorn caps at 500).
SEARCH_PAGE_CAP = 500
# Entities we page for reads. Named constants avoid stringly-typed drift.
_ENTITY_JOB_ORDER = "JobOrder"
_ENTITY_CANDIDATE = "Candidate"
_ENTITY_JOB_SUBMISSION = "JobSubmission"
_ENTITY_JOB_SUBMISSION_HISTORY = "JobSubmissionHistory"

# The three categorization settings that classify a per-org free-text status.
_CATEGORIZATION_SETTINGS = (
    "interviewScheduledJobResponseStatus",
    "confirmedJobResponseStatus",
    "rejectedJobResponseStatus",
)


class BullhornService:
    """Typed Bullhorn REST client. One instance per org, per unit of work.

    ``auth`` carries the session + token-rotation invariant; this class never
    touches refresh tokens directly. ``time_sleep`` is injectable so the 429
    backoff is testable without wall-clock waits.
    """

    def __init__(
        self,
        auth: BullhornAuth,
        *,
        client_id: str,
        bucket: TokenBucket | None = None,
        breaker: CircuitBreaker | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        time_sleep: Any = None,
    ):
        self._auth = auth
        self._bucket = bucket if bucket is not None else get_bucket(client_id)
        self._breaker = breaker if breaker is not None else get_breaker(client_id)
        self._transport = transport
        self._timeout = timeout
        if time_sleep is None:
            import time as _time

            time_sleep = _time.sleep
        self._sleep = time_sleep

    # --- core request loop --------------------------------------------------

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        raw: bool = False,
        files: dict | None = None,
    ) -> Any:
        """Paced, session-authed request with 429 backoff + 401 reauth-once.

        ``path`` is relative to the live rest url (joined per-call so a reauth
        that swaps the corpToken url is picked up). ``raw`` returns bytes.
        """
        # ``attempt`` counts only 429-backoff retries against the budget; a 401
        # reauth is a one-shot that does NOT consume the budget, so a 401 landing
        # on the last 429 attempt still gets its single retried call.
        reauthed = False
        attempt = 0
        while True:
            if self._breaker.is_open():
                raise BullhornRateLimitError(
                    "Bullhorn 429 circuit breaker open — backing off to protect the API user"
                )
            session = self._auth.ensure_session()
            url = urljoin(session.rest_url, path.lstrip("/"))
            call_params = dict(params or {})
            call_params["BhRestToken"] = session.bh_rest_token
            self._bucket.acquire()
            try:
                with quiet_httpx(), self._client() as client:
                    resp = client.request(
                        method, url, params=call_params, json=json, files=files
                    )
                resp.raise_for_status()
                if raw:
                    return resp.content
                return resp.json() if resp.content else {}
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 429:
                    self._breaker.record_429()
                    if attempt < BULLHORN_MAX_ATTEMPTS - 1 and not self._breaker.is_open():
                        wait = retry_after_seconds(exc.response, attempt)
                        logger.warning(
                            "Bullhorn 429 on %s %s; waiting %.1fs then retry (attempt %d/%d)",
                            method, path, wait, attempt + 1, BULLHORN_MAX_ATTEMPTS,
                        )
                        self._sleep(wait)
                        attempt += 1
                        continue
                    raise BullhornRateLimitError("Bullhorn API rate limited (429)") from exc
                if status == 401 and not reauthed:
                    # Session expired — refresh (rotation invariant) + re-login
                    # exactly once, then retry this call. A second 401 falls
                    # through to a typed auth error.
                    logger.info("Bullhorn 401 on %s %s; reauthenticating once", method, path)
                    self._auth.reauthenticate()
                    reauthed = True
                    continue
                if status == 401:
                    raise BullhornAuthError(
                        f"Bullhorn still 401 after reauth on {method} {path}"
                    ) from exc
                body = None
                if exc.response is not None and exc.response.content:
                    try:
                        body = exc.response.text[:500]
                    except Exception:
                        body = None
                raise BullhornApiError(
                    f"Bullhorn API error on {method} {path}", status_code=status, body=body
                ) from exc

    # --- verb discipline ----------------------------------------------------

    def _create(self, entity: str, data: dict) -> dict:
        """CREATE = PUT /entity/{entity}. Callers never choose the verb."""
        return self._request("PUT", f"entity/{entity}", json=data)

    def _update(self, entity: str, entity_id: str | int, data: dict) -> dict:
        """UPDATE = POST /entity/{entity}/{id}. Callers never choose the verb."""
        return self._request("POST", f"entity/{entity}/{entity_id}", json=data)

    # --- paged reads --------------------------------------------------------

    def _search(
        self, entity: str, *, fields: str, query: str = "", count: int = SEARCH_PAGE_CAP
    ) -> list[dict]:
        """GET /search/{entity} with mandatory ``fields``, paged to exhaustion.

        Treats ``SEARCH_PAGE_CAP`` as the hard page size and walks ``start`` until
        a short page (or ``total``) says we're done.
        """
        return self._paged("search", entity, fields=fields, selector=query, count=count)

    def _query(
        self, entity: str, *, fields: str, where: str = "", count: int = SEARCH_PAGE_CAP
    ) -> list[dict]:
        """GET /query/{entity} (JPQL) with mandatory ``fields``, paged to exhaustion."""
        return self._paged("query", entity, fields=fields, selector=where, count=count)

    def _paged(
        self, kind: str, entity: str, *, fields: str, selector: str, count: int
    ) -> list[dict]:
        if not fields:
            # fields= is MANDATORY: omitting it returns only ids. A caller
            # reaching here without fields is a bug, not a silent id-only read.
            raise ValueError(f"fields= is mandatory for {kind}/{entity}")
        page = min(int(count), SEARCH_PAGE_CAP)
        selector_key = "query" if kind == "search" else "where"
        out: list[dict] = []
        start = 0
        while True:
            params = {"fields": fields, "start": start, "count": page}
            if selector:
                params[selector_key] = selector
            payload = self._request("GET", f"{kind}/{entity}", params=params)
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
            out.extend(rows)
            total = payload.get("total") if isinstance(payload, dict) else None
            if len(rows) < page:
                break
            if isinstance(total, int) and start + len(rows) >= total:
                break
            start += page
            if start > 100_000:  # absolute guard against a runaway pager
                logger.warning("Bullhorn %s/%s pagination guard hit at start=%d", kind, entity, start)
                break
        return out

    # --- typed reads --------------------------------------------------------

    def search_job_orders(self, *, fields: str, query: str = "isOpen:true") -> list[dict]:
        return self._search(_ENTITY_JOB_ORDER, fields=fields, query=query)

    def search_candidates(self, *, fields: str, query: str = "") -> list[dict]:
        return self._search(_ENTITY_CANDIDATE, fields=fields, query=query)

    def query_job_submissions(self, *, fields: str, where: str = "") -> list[dict]:
        return self._query(_ENTITY_JOB_SUBMISSION, fields=fields, where=where)

    def get_job_submission_history(self, *, job_submission_id: str | int, fields: str) -> list[dict]:
        """JobSubmissionHistory for one submission (JPQL /query, per fact sheet)."""
        where = f"jobSubmission.id={int(job_submission_id)}"
        return self._query(_ENTITY_JOB_SUBMISSION_HISTORY, fields=fields, where=where)

    def get_status_list(self) -> dict[str, Any]:
        """Per-org free-text status list + the 3 categorization settings.

        Returns ``{"statuses": [...], "categorization": {setting: value|None}}``.
        Never hardcodes status strings.
        """
        payload = self._request("GET", "settings/jobResponseStatusList")
        statuses = payload.get("jobResponseStatusList") if isinstance(payload, dict) else None
        statuses = [s for s in statuses if isinstance(s, str)] if isinstance(statuses, list) else []
        categorization: dict[str, Any] = {}
        for name in _CATEGORIZATION_SETTINGS:
            got = self._request("GET", f"settings/{name}")
            categorization[name] = got.get(name) if isinstance(got, dict) else None
        return {"statuses": statuses, "categorization": categorization}

    def get_entitlements(self, entity: str) -> list[str]:
        """GET /entitlements/{entity} -> the allowed verbs list for the API user."""
        payload = self._request("GET", f"entitlements/{entity}")
        if isinstance(payload, list):
            return [str(v) for v in payload]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [str(v) for v in data]
        return []

    def ping(self) -> dict:
        """GET /ping -> {sessionExpires}. Healthcheck; establishes a session."""
        return self._request("GET", "ping")

    # --- typed writes -------------------------------------------------------

    def update_job_submission_status(self, *, job_submission_id: str | int, status: str) -> dict:
        """Move a JobSubmission to a (per-org, validated upstream) status string."""
        return self._update(_ENTITY_JOB_SUBMISSION, job_submission_id, {"status": status})

    def create_note(
        self,
        *,
        comments: str,
        person_reference_id: str | int | None = None,
        job_order_id: str | int | None = None,
        action: str = "Other",
    ) -> dict:
        """Create a Note (PUT /entity/Note). Links to a person + optional job.

        ``person_reference_id`` is the Candidate/ClientContact the note is about;
        Bullhorn attributes authorship to the API user's session.
        """
        data: dict[str, Any] = {"action": action, "comments": comments}
        if person_reference_id is not None:
            data["personReference"] = {"id": int(person_reference_id)}
        if job_order_id is not None:
            data["jobOrder"] = {"id": int(job_order_id)}
        return self._create("Note", data)

    # --- files --------------------------------------------------------------

    def list_file_attachments(self, *, candidate_id: str | int, fields: str) -> list[dict]:
        """GET /entity/Candidate/{id}/fileAttachments (metadata only)."""
        payload = self._request(
            "GET", f"entity/Candidate/{int(candidate_id)}/fileAttachments", params={"fields": fields}
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []

    def get_file_raw(self, *, candidate_id: str | int, file_id: str | int) -> bytes:
        """GET /file/Candidate/{id}/{fileId}/raw -> file bytes."""
        return self._request(
            "GET", f"file/Candidate/{int(candidate_id)}/{int(file_id)}/raw", raw=True
        )

    def convert_resume_to_text(self, *, filename: str, content: bytes, content_type: str) -> str:
        """POST /resume/convertToText (multipart) -> extracted text.

        Fallback CV path when no resume-typed fileAttachment yields usable text.
        """
        payload = self._request(
            "POST",
            "resume/convertToText",
            files={"file": (filename, content, content_type)},
        )
        if isinstance(payload, dict):
            text = payload.get("convertedText") or payload.get("text")
            if isinstance(text, str):
                return text
        return ""

    # --- event subscriptions ------------------------------------------------

    def create_subscription(
        self,
        *,
        subscription_id: str,
        entity_names: list[str],
        event_types: tuple[str, ...] = ("INSERTED", "UPDATED", "DELETED"),
    ) -> dict:
        """PUT /event/subscription/{id} -> create/replace an entity subscription."""
        return self._request(
            "PUT",
            f"event/subscription/{subscription_id}",
            params={
                "type": "entity",
                "names": ",".join(entity_names),
                "eventTypes": ",".join(event_types),
            },
        )

    def poll_events(self, *, subscription_id: str, max_events: int = 100) -> dict:
        """GET /event/subscription/{id}?maxEvents= — a DESTRUCTIVE queue drain.

        Returns ``{"requestId", "events": [...]}``. The caller MUST checkpoint
        ``requestId`` BEFORE processing so a crash can replay via
        :meth:`refetch_events` instead of losing the batch.
        """
        return self._request(
            "GET", f"event/subscription/{subscription_id}", params={"maxEvents": int(max_events)}
        )

    def refetch_events(self, *, subscription_id: str, request_id: str | int, max_events: int = 100) -> dict:
        """Re-fetch the last drained batch by ``requestId`` (crash replay, non-destructive)."""
        return self._request(
            "GET",
            f"event/subscription/{subscription_id}",
            params={"maxEvents": int(max_events), "requestId": request_id},
        )

    def delete_subscription(self, *, subscription_id: str) -> dict:
        """DELETE /event/subscription/{id}."""
        return self._request("DELETE", f"event/subscription/{subscription_id}")
