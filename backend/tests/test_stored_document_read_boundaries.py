from __future__ import annotations

from app.services import document_service, s3_service


class _ChunkedBody:
    def __init__(self, content: bytes, *, chunk_size: int | None = None) -> None:
        self._content = content
        self._offset = 0
        self._chunk_size = chunk_size
        self.read_sizes: list[int | None] = []
        self.closed = False

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        if self._offset >= len(self._content):
            return b""
        accepted = len(self._content) - self._offset if size is None else size
        if self._chunk_size is not None:
            accepted = min(accepted, self._chunk_size)
        end = min(self._offset + accepted, len(self._content))
        chunk = self._content[self._offset:end]
        self._offset = end
        return chunk

    def close(self) -> None:
        self.closed = True

    @property
    def bytes_read(self) -> int:
        return self._offset


class _ObjectClient:
    def __init__(self, body: _ChunkedBody, *, content_length: int | None) -> None:
        self.body = body
        self.content_length = content_length

    def get_object(self, **_kwargs):
        response = {"Body": self.body}
        if self.content_length is not None:
            response["ContentLength"] = self.content_length
        return response


def test_s3_download_rejects_declared_oversize_without_reading(
    monkeypatch,
) -> None:
    body = _ChunkedBody(b"not-read")
    client = _ObjectClient(body, content_length=11)
    monkeypatch.setattr(s3_service, "_get_client", lambda: (client, "bucket"))

    assert s3_service.download_from_s3("documents/one", max_bytes=10) is None
    assert body.read_sizes == []
    assert body.closed is True


def test_s3_download_bounds_stream_when_length_is_missing_or_wrong(
    monkeypatch,
) -> None:
    body = _ChunkedBody(b"01234567890", chunk_size=3)
    client = _ObjectClient(body, content_length=2)
    monkeypatch.setattr(s3_service, "_get_client", lambda: (client, "bucket"))

    assert s3_service.download_from_s3("documents/two", max_bytes=10) is None
    assert body.bytes_read == 11
    assert body.closed is True


def test_s3_download_preserves_complete_bounded_content(monkeypatch) -> None:
    body = _ChunkedBody(b"complete", chunk_size=2)
    client = _ObjectClient(body, content_length=8)
    monkeypatch.setattr(s3_service, "_get_client", lambda: (client, "bucket"))

    assert s3_service.download_from_s3("documents/three", max_bytes=8) == b"complete"
    assert body.closed is True


def test_stored_local_document_reads_only_existing_upload_limit(tmp_path) -> None:
    document = tmp_path / "oversized.pdf"
    document.write_bytes(b"x" * (document_service.MAX_FILE_SIZE + 1))

    assert document_service.load_stored_document_bytes(str(document)) is None


def test_stored_object_document_passes_existing_upload_limit(monkeypatch) -> None:
    observed: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        s3_service,
        "extract_key_from_url",
        lambda _url: ("bucket", "documents/five"),
    )
    monkeypatch.setattr(
        s3_service,
        "download_from_s3",
        lambda key, *, max_bytes=None: observed.append((key, max_bytes)) or b"valid",
    )

    assert (
        document_service.load_stored_document_bytes(
            "https://bucket.s3.amazonaws.com/documents/five"
        )
        == b"valid"
    )
    assert observed == [("documents/five", document_service.MAX_FILE_SIZE)]
