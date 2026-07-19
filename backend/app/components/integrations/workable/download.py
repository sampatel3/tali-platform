"""Bounded, credential-safe Workable document downloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from urllib.parse import urljoin

import httpx

from ....services.document_service import MAX_FILE_SIZE
from .url_security import same_https_origin, validate_public_download_url

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AUTH_RETRY_STATUSES = frozenset({400, 401, 403})
_READ_CHUNK_BYTES = 64 * 1024


class WorkableDownloadTooLarge(ValueError):
    """The remote document exceeds the platform's existing upload limit."""


def _declared_content_length(response: httpx.Response) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _read_bounded(response: httpx.Response, *, max_bytes: int) -> bytes:
    declared = _declared_content_length(response)
    if declared is not None and declared > max_bytes:
        raise WorkableDownloadTooLarge(
            f"Workable document exceeds {max_bytes}-byte limit"
        )
    content = bytearray()
    for chunk in response.iter_bytes(chunk_size=_READ_CHUNK_BYTES):
        remaining = max_bytes + 1 - len(content)
        if remaining <= 0:
            break
        content.extend(chunk[:remaining])
        if len(content) > max_bytes:
            raise WorkableDownloadTooLarge(
                f"Workable document exceeds {max_bytes}-byte limit"
            )
    return bytes(content)


def _consume_response(
    response: httpx.Response,
    *,
    current_url: str,
    max_bytes: int,
) -> tuple[bytes | None, str | None]:
    if response.status_code in _REDIRECT_STATUSES:
        location = response.headers.get("location")
        if not location:
            raise ValueError("Workable download redirect has no location")
        return None, validate_public_download_url(urljoin(current_url, location))
    response.raise_for_status()
    return _read_bounded(response, max_bytes=max_bytes), None


def download_workable_file(
    url: str,
    *,
    api_hostname: str,
    auth_headers: Mapping[str, str],
    acquire_rate_limit: Callable[[], None],
    should_yield: Callable[[], None],
    max_bytes: int = MAX_FILE_SIZE,
) -> bytes:
    """Stream at most ``max_bytes + 1`` while preserving auth/redirect rules."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    current_url = validate_public_download_url(url)
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        for _ in range(4):
            attach_auth = same_https_origin(current_url, host=api_hostname)
            if attach_auth:
                acquire_rate_limit()
            should_yield()
            retry_without_auth = False
            with client.stream(
                "GET",
                current_url,
                headers=auth_headers if attach_auth else None,
            ) as response:
                if attach_auth and response.status_code in _AUTH_RETRY_STATUSES:
                    retry_without_auth = True
                    content, next_url = None, None
                else:
                    content, next_url = _consume_response(
                        response,
                        current_url=current_url,
                        max_bytes=max_bytes,
                    )
            if retry_without_auth:
                acquire_rate_limit()
                should_yield()
                with client.stream("GET", current_url) as response:
                    content, next_url = _consume_response(
                        response,
                        current_url=current_url,
                        max_bytes=max_bytes,
                    )
            if next_url is not None:
                current_url = next_url
                continue
            return content or b""
    raise ValueError("Too many Workable download redirects")


__all__ = [
    "WorkableDownloadTooLarge",
    "download_workable_file",
]
