"""Bounded HTTP upload assembly shared by both requisition-chat routes."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

from .requisition_chat_attachment_policy import (
    MAX_REQUISITION_CHAT_FILE_BYTES,
    MAX_REQUISITION_CHAT_FILES,
    has_valid_requisition_image_signature,
    is_supported_requisition_attachment,
)
from .requisition_chat_service import ChatAttachment


async def read_requisition_chat_attachments(
    files: list[UploadFile] | None,
) -> list[ChatAttachment]:
    """Validate and read one turn's uploads with a hard per-file read bound."""

    uploads = list(files or [])
    if len(uploads) > MAX_REQUISITION_CHAT_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"At most {MAX_REQUISITION_CHAT_FILES} files per turn",
        )

    # Validate every item before reading any bytes. This keeps a mixed valid +
    # invalid request all-or-nothing and avoids work before a deterministic 415.
    unsupported = next(
        (
            upload
            for upload in uploads
            if not is_supported_requisition_attachment(
                upload.filename, upload.content_type
            )
        ),
        None,
    )
    if unsupported is not None:
        raise HTTPException(
            status_code=415,
            detail=(
                f"{unsupported.filename or 'That file'} isn't supported. "
                "Attach a PDF, DOCX, text/Markdown file, or a JPG, PNG, GIF, "
                "or WebP image."
            ),
        )

    attachments: list[ChatAttachment] = []
    read_limit = MAX_REQUISITION_CHAT_FILE_BYTES + 1
    for upload in uploads:
        # Never materialize an arbitrarily large direct-API upload merely to
        # discover that it exceeds the limit.
        content = await upload.read(read_limit)
        if len(content) > MAX_REQUISITION_CHAT_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename or 'file'} exceeds the 15 MB per-file limit",
            )
        if not has_valid_requisition_image_signature(
            upload.filename, upload.content_type, content
        ):
            raise HTTPException(
                status_code=415,
                detail=(
                    f"{upload.filename or 'That file'} content does not match "
                    "its declared image format."
                ),
            )
        attachments.append(
            ChatAttachment(
                name=(upload.filename or "attachment"),
                content_type=upload.content_type,
                content=content,
            )
        )
    return attachments


__all__ = ["read_requisition_chat_attachments"]
