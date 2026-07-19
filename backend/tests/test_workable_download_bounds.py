from __future__ import annotations

import httpx
import pytest

from app.components.integrations.workable import download
from app.services.document_service import MAX_FILE_SIZE


class _UnreadStream(httpx.SyncByteStream):
    def __iter__(self):
        raise AssertionError("oversized declared content must not be read")


class _CountingStream(httpx.SyncByteStream):
    def __init__(self, *, chunk_size: int, chunk_count: int):
        self.chunk_size = chunk_size
        self.chunk_count = chunk_count
        self.yielded = 0

    def __iter__(self):
        for _ in range(self.chunk_count):
            self.yielded += 1
            yield b"x" * self.chunk_size


def _patch_client(monkeypatch, handler) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        download.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(download, "validate_public_download_url", lambda value: value)


def _download(
    *,
    acquire_rate_limit=lambda: None,
    should_yield=lambda: None,
) -> bytes:
    return download.download_workable_file(
        "https://tenant.workable.com/cv.pdf",
        api_hostname="tenant.workable.com",
        auth_headers={"Authorization": "Bearer secret"},
        acquire_rate_limit=acquire_rate_limit,
        should_yield=should_yield,
    )


@pytest.mark.parametrize("invalid_limit", [True, -1, 1.5, "1"])
def test_invalid_limit_is_rejected_before_provider_side_effects(
    monkeypatch, invalid_limit
):
    monkeypatch.setattr(
        download,
        "validate_public_download_url",
        lambda _value: pytest.fail("invalid limit reached URL validation"),
    )

    with pytest.raises(ValueError, match="max_bytes"):
        download.download_workable_file(
            "https://tenant.workable.com/cv.pdf",
            api_hostname="tenant.workable.com",
            auth_headers={"Authorization": "Bearer secret"},
            acquire_rate_limit=lambda: pytest.fail("invalid limit used rate capacity"),
            should_yield=lambda: pytest.fail("invalid limit reached provider lease"),
            max_bytes=invalid_limit,
        )


def test_declared_oversize_is_rejected_before_body_read(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            headers={"Content-Length": str(MAX_FILE_SIZE + 1)},
            stream=_UnreadStream(),
        )

    _patch_client(monkeypatch, handler)

    with pytest.raises(download.WorkableDownloadTooLarge):
        _download()


def test_chunked_oversize_stops_after_limit_instead_of_draining_body(monkeypatch):
    stream = _CountingStream(chunk_size=64 * 1024, chunk_count=160)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    _patch_client(monkeypatch, handler)

    with pytest.raises(download.WorkableDownloadTooLarge):
        _download()

    assert stream.yielded < stream.chunk_count
    assert stream.yielded <= (MAX_FILE_SIZE // stream.chunk_size) + 1


def test_redirect_never_forwards_workable_bearer_to_object_host(monkeypatch):
    observed: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((request.url.host, request.headers.get("Authorization")))
        if request.url.host == "tenant.workable.com":
            return httpx.Response(
                302,
                headers={"Location": "https://objects.example.test/cv.pdf"},
            )
        return httpx.Response(200, content=b"resume")

    _patch_client(monkeypatch, handler)

    assert _download() == b"resume"
    assert observed == [
        ("tenant.workable.com", "Bearer secret"),
        ("objects.example.test", None),
    ]


def test_same_origin_redirect_and_unauth_retry_each_acquire_rate_capacity(
    monkeypatch,
):
    events: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(("request", request.headers.get("Authorization")))
        if request.url.path == "/cv.pdf":
            return httpx.Response(302, headers={"Location": "/protected.pdf"})
        if request.url.path == "/protected.pdf" and request.headers.get(
            "Authorization"
        ):
            return httpx.Response(401)
        if request.url.path == "/protected.pdf":
            return httpx.Response(302, headers={"Location": "/signed.pdf"})
        return httpx.Response(200, content=b"resume")

    _patch_client(monkeypatch, handler)

    assert _download(
        acquire_rate_limit=lambda: events.append(("limit", None))
    ) == b"resume"
    assert events == [
        ("limit", None),
        ("request", "Bearer secret"),
        ("limit", None),
        ("request", "Bearer secret"),
        ("limit", None),
        ("request", None),
        ("limit", None),
        ("request", "Bearer secret"),
    ]


def test_external_redirect_requests_do_not_consume_workable_rate_capacity(
    monkeypatch,
):
    requests: list[str] = []
    limiter_calls: list[str] = []
    yields: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.host)
        if request.url.host == "tenant.workable.com":
            return httpx.Response(
                302,
                headers={"Location": "https://objects.example.test/one"},
            )
        if request.url.path == "/one":
            return httpx.Response(302, headers={"Location": "/two"})
        return httpx.Response(200, content=b"resume")

    _patch_client(monkeypatch, handler)

    assert _download(
        acquire_rate_limit=lambda: limiter_calls.append("limit"),
        should_yield=lambda: yields.append("yield"),
    ) == b"resume"
    assert requests == [
        "tenant.workable.com",
        "objects.example.test",
        "objects.example.test",
    ]
    assert limiter_calls == ["limit"]
    assert yields == ["yield", "yield", "yield"]
