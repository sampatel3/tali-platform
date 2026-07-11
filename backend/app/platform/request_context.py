from contextvars import ContextVar
from typing import Optional

_request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def set_request_id(request_id: str):
    return _request_id_ctx.set(request_id)


def get_request_id() -> Optional[str]:
    return _request_id_ctx.get()


# Client IP + user agent for the current request, set by RequestLoggingMiddleware.
# Lets deep call sites (e.g. the auth audit trail inside UserManager.authenticate,
# which never sees the Request object) attribute events without plumbing the
# request through every layer.
_client_meta_ctx: ContextVar[Optional[dict]] = ContextVar("client_meta", default=None)


def set_client_meta(ip: Optional[str], user_agent: Optional[str]):
    return _client_meta_ctx.set({"ip": ip, "user_agent": user_agent})


def get_client_meta() -> dict:
    return _client_meta_ctx.get() or {}

