"""Bounded Bullhorn file-response handling and typed file methods."""

from __future__ import annotations

from typing import Any

import httpx

from .errors import BullhornFileTooLargeError

_RAW_READ_CHUNK_BYTES = 64 * 1024
_FILE_TOO_LARGE = "Bullhorn file exceeds the accepted size limit"


def validate_raw_byte_limit(max_bytes: int | None) -> None:
    """Reject invalid limits before auth, rate-limit, or network side effects."""
    if max_bytes is not None and (
        isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0
    ):
        raise ValueError("max_raw_bytes must be a non-negative integer or None")


def _read_raw_response(response: httpx.Response, *, max_bytes: int | None) -> bytes:
    if max_bytes is None:
        return response.read()
    declared_length = response.headers.get("content-length")
    if (
        declared_length is not None
        and declared_length.isdigit()
        and int(declared_length) > max_bytes
    ):
        raise BullhornFileTooLargeError(_FILE_TOO_LARGE)

    chunks: list[bytes] = []
    received = 0
    chunk_size = max(1, min(_RAW_READ_CHUNK_BYTES, max_bytes + 1))
    for chunk in response.iter_bytes(chunk_size=chunk_size):
        received += len(chunk)
        if received > max_bytes:
            raise BullhornFileTooLargeError(_FILE_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def execute_response_request(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict,
    json_body: dict | None,
    files: dict | None,
    raw: bool,
    max_raw_bytes: int | None,
) -> Any:
    """Perform one HTTP attempt; the caller retains retry/error policy."""
    if raw:
        with client.stream(
            method,
            url,
            params=params,
            json=json_body,
            files=files,
        ) as response:
            response.raise_for_status()
            return _read_raw_response(response, max_bytes=max_raw_bytes)

    response = client.request(
        method,
        url,
        params=params,
        json=json_body,
        files=files,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


class BullhornFilesMixin:
    """Typed attachment/download/convert operations for ``BullhornService``."""

    def list_file_attachments(
        self,
        *,
        candidate_id: str | int,
        fields: str,
    ) -> list[dict]:
        """GET /entity/Candidate/{id}/fileAttachments (metadata only)."""
        payload = self._request(  # type: ignore[attr-defined]
            "GET",
            f"entity/Candidate/{int(candidate_id)}/fileAttachments",
            params={"fields": fields},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        return (
            [item for item in data if isinstance(item, dict)]
            if isinstance(data, list)
            else []
        )

    def get_file_raw(
        self,
        *,
        candidate_id: str | int,
        file_id: str | int,
        max_bytes: int | None = None,
    ) -> bytes:
        """GET /file/Candidate/{id}/{fileId}/raw -> file bytes."""
        return self._request(  # type: ignore[attr-defined,no-any-return]
            "GET",
            f"file/Candidate/{int(candidate_id)}/{int(file_id)}/raw",
            raw=True,
            max_raw_bytes=max_bytes,
        )

    def convert_resume_to_text(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> str:
        """POST /resume/convertToText (multipart) -> extracted text.

        This is the fallback CV path when a resume-typed file attachment does
        not yield usable text locally.
        """
        payload = self._request(  # type: ignore[attr-defined]
            "POST",
            "resume/convertToText",
            files={"file": (filename, content, content_type)},
        )
        if isinstance(payload, dict):
            text = payload.get("convertedText") or payload.get("text")
            if isinstance(text, str):
                return text
        return ""


__all__ = [
    "BullhornFilesMixin",
    "execute_response_request",
    "validate_raw_byte_limit",
]
