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
from .errors import (
    BullhornApiError,
    BullhornAuthError,
    BullhornProviderYielded,
    BullhornRateLimitError,
    safe_request_operation,
)
from .ratelimit import (
    BULLHORN_MAX_ATTEMPTS,
    CircuitBreaker,
    TokenBucket,
    get_breaker,
    get_bucket,
    retry_after_seconds,
)
from .service_paging import (
    COMPLETE_SNAPSHOT_ROW_GUARD,
    SEARCH_PAGE_CAP,
    paged,
)
from .service_exact import BullhornExactReadsMixin
from .service_files import (
    BullhornFilesMixin,
    execute_response_request,
    validate_raw_byte_limit,
)

__all__ = ["COMPLETE_SNAPSHOT_ROW_GUARD", "SEARCH_PAGE_CAP"]

logger = logging.getLogger(__name__)

# Entities we page for reads. Named constants avoid stringly-typed drift.
_ENTITY_JOB_ORDER = "JobOrder"
_ENTITY_CANDIDATE = "Candidate"
_ENTITY_JOB_SUBMISSION = "JobSubmission"
_ENTITY_JOB_SUBMISSION_HISTORY = "JobSubmissionHistory"
_ENTITY_NOTE = "Note"

# The three categorization settings that classify a per-org free-text status.
_CATEGORIZATION_SETTINGS = (
    "interviewScheduledJobResponseStatus",
    "confirmedJobResponseStatus",
    "rejectedJobResponseStatus",
)


class BullhornService(BullhornExactReadsMixin, BullhornFilesMixin):
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

    def _raise_if_provider_should_yield(self) -> None:
        observer = getattr(self, "_sync_lease_observer", None)
        if observer is not None and observer():
            raise BullhornProviderYielded()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        raw: bool = False,
        max_raw_bytes: int | None = None,
        files: dict | None = None,
    ) -> Any:
        """Paced, session-authed request with 429 backoff + 401 reauth-once.

        ``path`` is relative to the live rest url (joined per-call so a reauth
        that swaps the corpToken url is picked up). ``raw`` returns bytes.
        """
        validate_raw_byte_limit(max_raw_bytes)
        operation = safe_request_operation(path)
        # ``attempt`` counts only 429-backoff retries against the budget; a 401
        # reauth is a one-shot that does NOT consume the budget, so a 401 landing
        # on the last 429 attempt still gets its single retried call.
        reauthed = False
        attempt = 0
        while True:
            self._raise_if_provider_should_yield()
            if self._breaker.is_open():
                raise BullhornRateLimitError(
                    "Bullhorn 429 circuit breaker open — backing off to protect the API user"
                )
            session = self._auth.ensure_session()
            url = urljoin(session.rest_url, path.lstrip("/"))
            call_params = dict(params or {})
            call_params["BhRestToken"] = session.bh_rest_token
            self._bucket.acquire()
            # The token bucket may block. Recheck at the final boundary before
            # every actual REST attempt, including 429/401 retry attempts.
            self._raise_if_provider_should_yield()
            try:
                with quiet_httpx(), self._client() as client:
                    return execute_response_request(
                        client,
                        method,
                        url,
                        params=call_params,
                        json_body=json,
                        files=files,
                        raw=raw,
                        max_raw_bytes=max_raw_bytes,
                    )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 429:
                    self._breaker.record_429()
                    if attempt < BULLHORN_MAX_ATTEMPTS - 1 and not self._breaker.is_open():
                        wait = retry_after_seconds(exc.response, attempt)
                        logger.warning(
                            "Bullhorn 429 on %s %s; waiting %.1fs then retry (attempt %d/%d)",
                            method, operation, wait, attempt + 1, BULLHORN_MAX_ATTEMPTS,
                        )
                        self._sleep(wait)
                        attempt += 1
                        continue
                    raise BullhornRateLimitError(
                        "Bullhorn API rate limited (429)"
                    ) from None
                if status == 401 and not reauthed:
                    # Session expired — refresh (rotation invariant) + re-login
                    # exactly once, then retry this call. A second 401 falls
                    # through to a typed auth error.
                    logger.info("Bullhorn 401 on %s %s; reauthenticating once", method, operation)
                    self._auth.reauthenticate()
                    reauthed = True
                    continue
                if status == 401:
                    raise BullhornAuthError(
                        f"Bullhorn still 401 after reauth on {method} {operation}"
                    ) from None
                raise BullhornApiError(
                    f"Bullhorn API error on {method} {operation}",
                    status_code=status,
                ) from None
            except BullhornApiError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalize tokenized URLs
                # Connection/timeout/decoder errors also retain the full
                # httpx Request URL. Never let that raw exception reach a
                # exception traceback.
                raise BullhornApiError(
                    f"Bullhorn request failed on {method} {operation}: "
                    f"{type(exc).__name__}"
                ) from None

    # --- verb discipline ----------------------------------------------------

    def _create(self, entity: str, data: dict) -> dict:
        """CREATE = PUT /entity/{entity}. Callers never choose the verb."""
        return self._request("PUT", f"entity/{entity}", json=data)

    def _update(self, entity: str, entity_id: str | int, data: dict) -> dict:
        """UPDATE = POST /entity/{entity}/{id}. Callers never choose the verb."""
        return self._request("POST", f"entity/{entity}/{entity_id}", json=data)

    # --- paged reads --------------------------------------------------------

    def _search(
        self, entity: str, *, fields: str, query: str = "", count: int = SEARCH_PAGE_CAP,
        limit: int | None = None,
    ) -> list[dict]:
        """GET /search/{entity} with mandatory ``fields``, paged to exhaustion.

        Treats ``SEARCH_PAGE_CAP`` as the hard page size and walks ``start`` until
        a short page (or ``total``) says we're done.
        """
        return self._paged("search", entity, fields=fields, selector=query, count=count, limit=limit)

    def _query(
        self, entity: str, *, fields: str, where: str = "", count: int = SEARCH_PAGE_CAP,
        limit: int | None = None,
    ) -> list[dict]:
        """GET /query/{entity} (JPQL) with mandatory ``fields``, paged to exhaustion."""
        return self._paged("query", entity, fields=fields, selector=where, count=count, limit=limit)

    def _paged(
        self,
        kind: str,
        entity: str,
        *,
        fields: str,
        selector: str,
        count: int,
        limit: int | None = None,
        require_complete: bool = False,
    ) -> list[dict]:
        return paged(
            self._request,
            kind,
            entity,
            fields=fields,
            selector=selector,
            count=count,
            limit=limit,
            require_complete=require_complete,
        )

    # --- typed reads --------------------------------------------------------

    def search_job_orders(self, *, fields: str, query: str = "isOpen:true", limit: int | None = None) -> list[dict]:
        return self._search(_ENTITY_JOB_ORDER, fields=fields, query=query, limit=limit)

    def search_open_job_orders_complete(self, *, fields: str) -> list[dict]:
        """Return a proven-complete, paginated snapshot of every open JobOrder.

        This is the only read safe to feed into missing-ID closure repair.  It
        requires both identity and lifecycle fields, a stable remote ``total``,
        and exact pagination to that total; any uncertainty raises so callers
        make no destructive local changes.
        """
        requested_fields = {field.strip() for field in fields.split(",")}
        if not {"id", "isOpen"}.issubset(requested_fields):
            raise ValueError("complete open JobOrder snapshots require id,isOpen fields")
        rows = self._paged(
            "search",
            _ENTITY_JOB_ORDER,
            fields=fields,
            selector="isOpen:true",
            count=SEARCH_PAGE_CAP,
            require_complete=True,
        )
        for row in rows:
            if type(row.get("isOpen")) is not bool or row.get("isOpen") is not True:
                raise BullhornApiError(
                    "Bullhorn complete open JobOrder snapshot contained invalid lifecycle state"
                )
        return rows

    def search_candidates(self, *, fields: str, query: str = "", limit: int | None = None) -> list[dict]:
        return self._search(_ENTITY_CANDIDATE, fields=fields, query=query, limit=limit)

    def query_job_submissions(self, *, fields: str, where: str = "", limit: int | None = None) -> list[dict]:
        return self._query(_ENTITY_JOB_SUBMISSION, fields=fields, where=where, limit=limit)

    def query_job_submissions_complete(
        self,
        *,
        job_order_id: str | int,
        fields: str,
        modified_since_millis: int | None = None,
        include_deleted: bool = False,
    ) -> list[dict]:
        """Return a proven-complete, uniquely identified submission snapshot."""
        if isinstance(job_order_id, bool) or not str(job_order_id).isdigit():
            raise ValueError("complete JobSubmission snapshots require a job order id")
        normalized_job_id = str(int(job_order_id))
        if normalized_job_id == "0":
            raise ValueError("complete JobSubmission snapshots require a job order id")
        requested_fields = {field.strip() for field in fields.split(",")}
        if not {"id", "jobOrder", "isDeleted"}.issubset(requested_fields):
            raise ValueError(
                "complete JobSubmission snapshots require id,jobOrder,isDeleted fields"
            )
        if type(include_deleted) is not bool:
            raise ValueError("include-deleted must be a boolean")
        where = f"jobOrder.id={normalized_job_id}"
        if not include_deleted:
            where += " AND isDeleted=false"
        if modified_since_millis is not None:
            if type(modified_since_millis) is not int or modified_since_millis < 0:
                raise ValueError("modified-since watermark must be a nonnegative integer")
            where += f" AND dateLastModified>={modified_since_millis}"
        rows = self._paged(
            "query",
            _ENTITY_JOB_SUBMISSION,
            fields=fields,
            selector=where,
            count=SEARCH_PAGE_CAP,
            require_complete=True,
        )
        seen: set[str] = set()
        for row in rows:
            raw_id = row.get("id")
            if (
                isinstance(raw_id, bool)
                or not isinstance(raw_id, (str, int))
                or not str(raw_id).isdigit()
                or int(raw_id) <= 0
            ):
                raise BullhornApiError(
                    "Bullhorn complete JobSubmission snapshot contained an invalid id"
                )
            submission_id = str(int(raw_id))
            parent = row.get("jobOrder")
            parent_id = parent.get("id") if isinstance(parent, dict) else None
            if (
                isinstance(parent_id, bool)
                or not isinstance(parent_id, (str, int))
                or not str(parent_id).isdigit()
                or str(int(parent_id)) != normalized_job_id
                or type(row.get("isDeleted")) is not bool
                or (not include_deleted and row.get("isDeleted") is not False)
            ):
                raise BullhornApiError(
                    "Bullhorn complete JobSubmission snapshot violated its parent scope"
                )
            if submission_id in seen:
                raise BullhornApiError(
                    "Bullhorn complete JobSubmission snapshot contained a duplicate id"
                )
            seen.add(submission_id)
        return rows

    def get_job_submission(
        self,
        job_submission_id: str | int,
        *,
        fields: str = "id,status,isDeleted,dateLastModified",
    ) -> dict:
        """Return one exact JobSubmission or an empty mapping."""

        exact_id = str(int(job_submission_id))
        rows = self.query_job_submissions(fields=fields, where=f"id={exact_id}")
        return next(
            (dict(row) for row in rows if str(row.get("id")) == exact_id),
            {},
        )

    def get_job_submission_history(self, *, job_submission_id: str | int, fields: str, limit: int | None = None) -> list[dict]:
        """JobSubmissionHistory for one submission (JPQL /query, per fact sheet)."""
        where = f"jobSubmission.id={int(job_submission_id)}"
        return self._query(_ENTITY_JOB_SUBMISSION_HISTORY, fields=fields, where=where, limit=limit)

    def query_notes(self, *, candidate_id: str | int, fields: str, limit: int | None = None) -> list[dict]:
        """Notes ABOUT one candidate (JPQL /query).

        A Bullhorn Note links to the people it concerns via the ``personReference``
        association; ``personReference.id`` selects the notes about this candidate.
        Read-only — the write side is :meth:`create_note`.
        """
        where = f"personReference.id={int(candidate_id)}"
        return self._query(_ENTITY_NOTE, fields=fields, where=where, limit=limit)

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
        """Destructively drain one event batch; caller must use durable intent."""
        return self._request(
            "GET", f"event/subscription/{subscription_id}", params={"maxEvents": int(max_events)}
        )

    def get_last_request_id(self, *, subscription_id: str) -> dict:
        """Read the last consumed request id without draining another batch."""
        return self._request("GET", f"event/subscription/{subscription_id}/lastRequestId")

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
