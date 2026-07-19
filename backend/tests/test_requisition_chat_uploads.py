"""Shared bounded requisition-chat upload assembly tests."""

import pytest
from fastapi import HTTPException

from app.services.requisition_chat_attachment_policy import (
    MAX_REQUISITION_CHAT_FILE_BYTES,
)
from app.services.requisition_chat_uploads import read_requisition_chat_attachments


class _Upload:
    def __init__(self, filename, content_type, content):
        self.filename = filename
        self.content_type = content_type
        self.content = content
        self.read_sizes = []

    async def read(self, size=-1):
        self.read_sizes.append(size)
        return self.content[:size]


@pytest.mark.asyncio
async def test_upload_reader_caps_read_at_one_byte_beyond_limit():
    upload = _Upload(
        "brief.txt",
        "text/plain",
        b"x" * (MAX_REQUISITION_CHAT_FILE_BYTES + 1),
    )

    with pytest.raises(HTTPException) as exc_info:
        await read_requisition_chat_attachments([upload])

    assert exc_info.value.status_code == 413
    assert upload.read_sizes == [MAX_REQUISITION_CHAT_FILE_BYTES + 1]


@pytest.mark.asyncio
async def test_upload_reader_validates_every_type_before_reading_any_bytes():
    valid = _Upload("notes.txt", "text/plain", b"usable")
    invalid = _Upload("diagram.svg", "image/svg+xml", b"<svg />")

    with pytest.raises(HTTPException) as exc_info:
        await read_requisition_chat_attachments([valid, invalid])

    assert exc_info.value.status_code == 415
    assert valid.read_sizes == []
    assert invalid.read_sizes == []


@pytest.mark.asyncio
async def test_upload_reader_returns_small_allowed_attachment():
    upload = _Upload("brief.docx", "application/octet-stream", b"small")

    attachments = await read_requisition_chat_attachments([upload])

    assert len(attachments) == 1
    assert attachments[0].name == "brief.docx"
    assert attachments[0].content == b"small"
    assert upload.read_sizes == [MAX_REQUISITION_CHAT_FILE_BYTES + 1]


@pytest.mark.asyncio
async def test_upload_reader_rejects_forged_image_content():
    upload = _Upload("forged.png", "image/png", b"not a png")

    with pytest.raises(HTTPException) as exc_info:
        await read_requisition_chat_attachments([upload])

    assert exc_info.value.status_code == 415
    assert "does not match" in exc_info.value.detail
