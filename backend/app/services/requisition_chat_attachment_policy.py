"""Shared upload policy for recruiter and public requisition chat.

The browser's ``accept`` attribute is only a picker hint and can be bypassed by
direct API callers.  Keep the authoritative extension/MIME coherence check in
this pure module so both chat surfaces reject unsupported content before file
bytes are read or a metered model call is made.
"""

from __future__ import annotations


MAX_REQUISITION_CHAT_FILES = 6
MAX_REQUISITION_CHAT_FILE_BYTES = 15 * 1024 * 1024

SUPPORTED_REQUISITION_ATTACHMENT_EXTENSIONS = frozenset(
    {
        "txt",
        "text",
        "vtt",
        "srt",
        "md",
        "markdown",
        "pdf",
        "docx",
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
    }
)

SUPPORTED_REQUISITION_ATTACHMENT_MIME_TYPES = frozenset(
    {
        "text/plain",
        "text/vtt",
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)

_GENERIC_MIME_TYPES = frozenset({"", "application/octet-stream"})
_TEXT_EXTENSIONS = frozenset({"txt", "text", "vtt", "srt", "md", "markdown"})
_ALTERNATE_TEXT_MIME_TYPES = frozenset(
    {"application/markdown", "application/srt", "application/x-subrip"}
)
_IMAGE_MIME_BY_EXTENSION = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def _extension(filename: str | None) -> str:
    name = str(filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def is_supported_requisition_attachment(
    filename: str | None,
    content_type: str | None,
) -> bool:
    """Return whether filename and browser-provided MIME type are coherent.

    Empty or generic MIME metadata is accepted only when an allow-listed
    extension supplies the missing signal.  A concrete MIME type must agree
    with its extension so renamed HEIC/SVG/PDF data is not sent through the
    wrong decoder or declared to the vision provider as a different format.
    """

    extension = _extension(filename)
    mime_type = str(content_type or "").lower()
    if not extension:
        return mime_type in SUPPORTED_REQUISITION_ATTACHMENT_MIME_TYPES
    if extension not in SUPPORTED_REQUISITION_ATTACHMENT_EXTENSIONS:
        return False
    if mime_type in _GENERIC_MIME_TYPES:
        return True
    if extension in _TEXT_EXTENSIONS:
        return mime_type.startswith("text/") or mime_type in _ALTERNATE_TEXT_MIME_TYPES
    if extension == "pdf":
        return mime_type == "application/pdf"
    if extension == "docx":
        return (
            mime_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    return _IMAGE_MIME_BY_EXTENSION.get(extension) == mime_type


def requisition_attachment_image_media_type(
    filename: str | None,
    content_type: str | None,
) -> str | None:
    """Return the image format selected by a coherent filename/MIME pair."""

    extension = _extension(filename)
    if extension in _IMAGE_MIME_BY_EXTENSION:
        return _IMAGE_MIME_BY_EXTENSION[extension]
    mime_type = str(content_type or "").lower()
    return mime_type if mime_type in _IMAGE_MIME_BY_EXTENSION.values() else None


def has_valid_requisition_image_signature(
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> bool:
    """Check reliable magic bytes for an allowed image; ignore non-images."""

    media_type = requisition_attachment_image_media_type(filename, content_type)
    if media_type is None:
        return True
    if media_type == "image/jpeg":
        return content.startswith(b"\xff\xd8")
    if media_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if media_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


__all__ = [
    "MAX_REQUISITION_CHAT_FILE_BYTES",
    "MAX_REQUISITION_CHAT_FILES",
    "SUPPORTED_REQUISITION_ATTACHMENT_EXTENSIONS",
    "SUPPORTED_REQUISITION_ATTACHMENT_MIME_TYPES",
    "has_valid_requisition_image_signature",
    "is_supported_requisition_attachment",
    "requisition_attachment_image_media_type",
]
