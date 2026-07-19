"""Typed errors for the Bullhorn client.

Mirrors how the Workable client types ``WorkableRateLimitError`` — but Bullhorn
has three distinct failure classes the callers (sync/write-back/op_runner) must
tell apart, so they live in one module rather than inline:

* :class:`BullhornAuthError` — auth/session is unrecoverable (discovery failed,
  refresh-token rotation stranded, re-login after 401 still 401). op_runner
  surfaces this rather than retrying blindly.
* :class:`BullhornRateLimitError` — a 429 survived the client's backoff, or the
  circuit breaker tripped. Retryable later.
* :class:`BullhornApiError` — any other non-2xx from a REST call.
* :class:`BullhornFileTooLargeError` — a deterministic local file-size
  rejection; callers can continue metadata sync without retrying the provider.
"""

from __future__ import annotations

import httpx


_KNOWN_ENTITIES = {
    "Candidate",
    "JobOrder",
    "JobSubmission",
    "JobSubmissionHistory",
    "Note",
}
_KNOWN_SETTINGS = {
    "jobResponseStatusList",
    "interviewScheduledJobResponseStatus",
    "confirmedJobResponseStatus",
    "rejectedJobResponseStatus",
}


def safe_request_operation(path: str) -> str:
    """Describe a request without retaining provider IDs or tokens."""
    parts = [part for part in path.strip("/").split("/") if part]
    if not parts:
        return "request"
    root = parts[0]
    if root in {"search", "query"}:
        entity = parts[1] if len(parts) > 1 and parts[1] in _KNOWN_ENTITIES else "entity"
        return f"{root}/{entity}"
    if root == "entity":
        entity = parts[1] if len(parts) > 1 and parts[1] in _KNOWN_ENTITIES else "entity"
        suffix = "/fileAttachments" if parts[-1:] == ["fileAttachments"] else ""
        return f"entity/{entity}{suffix}"
    if root == "file":
        entity = parts[1] if len(parts) > 1 and parts[1] in _KNOWN_ENTITIES else "entity"
        return f"file/{entity}/raw"
    if root == "event":
        return "event/subscription"
    if root == "settings":
        setting = parts[1] if len(parts) > 1 and parts[1] in _KNOWN_SETTINGS else "setting"
        return f"settings/{setting}"
    if root == "entitlements":
        entity = parts[1] if len(parts) > 1 and parts[1] in _KNOWN_ENTITIES else "entity"
        return f"entitlements/{entity}"
    if root == "resume":
        return "resume/convertToText"
    return root if root == "ping" else "request"


# Bullhorn passes the access_token (on /login) and the BhRestToken (on every REST
# call) in the URL QUERY STRING. httpx's exception string embeds the full request
# URL — query string included — so ``str(HTTPStatusError)`` on a failed /login
# carries a LIVE access token. Those exceptions are wrapped into BullhornAuthError
# and handed to callers. Strip every query string out of
# the request URL before it can reach a log line or the DB, so a rotated/expired
# token can never be surfaced. Keeps only the diagnostic error type/status.
def redact_exc(exc: BaseException) -> str:
    """Render an exception without retaining a token-bearing request URL.

    httpx exceptions retain their full Request object, including Bullhorn's
    access/session tokens and corp-token REST base. For those errors, expose
    only the type and status. Other exception messages can also echo injected
    callback/response values, so their type is the only safe generic detail.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        return (
            f"{type(exc).__name__} (status {status})"
            if status is not None
            else type(exc).__name__
        )
    if isinstance(exc, httpx.RequestError):
        return type(exc).__name__
    return type(exc).__name__


class BullhornError(RuntimeError):
    """Base for all Bullhorn client errors."""


class BullhornAuthError(BullhornError):
    """Auth or session establishment failed unrecoverably."""


class BullhornRateLimitError(BullhornError):
    """A 429 survived backoff, or the 429 circuit breaker is open."""


class BullhornProviderYielded(BullhornError):
    """The caller lost authority before the next provider HTTP attempt."""


class BullhornApiError(BullhornError):
    """A REST call returned a non-2xx status other than 429/401-auth."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class BullhornFileTooLargeError(BullhornApiError):
    """A downloaded attachment exceeded the caller's accepted byte ceiling."""
