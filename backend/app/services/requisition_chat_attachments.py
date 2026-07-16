"""Attachment decoding and model-content assembly for requisition chat.

The prompt module re-exports the established entry points so callers retain
their existing import paths.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Optional

from pydantic import BaseModel

from .document_service import sanitize_text_for_storage

logger = logging.getLogger("taali.requisition_chat")

# Extensions we treat as decode-able text/transcripts (appended to the user
# message inline so the model reads them as conversation context).
_TEXT_EXTENSIONS = {"txt", "vtt", "srt", "md", "markdown", "text"}
_DOCUMENT_EXTENSIONS = {"pdf", "docx"}
# Anthropic image block media types we pass through for vision.
_SUPPORTED_IMAGE_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_IMAGE_EXTENSION_MEDIA = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}
_MAX_USER_TEXT_CHARS = 30_000
_MAX_EXTRACTED_TEXT_PER_FILE = 30_000
_MAX_EXTRACTED_TEXT_PER_TURN = 50_000
_TRUNCATION_MARKER = "\n\n[...content truncated for safe processing...]\n\n"


# --------------------------------------------------------------------------- #
# Attachment metadata (what we persist on the message) + the upload view the
# route hands us (decoupled from FastAPI's UploadFile so the assembly logic is
# unit-testable with plain objects).
# --------------------------------------------------------------------------- #
class ChatAttachment(BaseModel):
    """One uploaded file, already read into memory by the route."""

    name: str
    content_type: Optional[str] = None
    content: bytes = b""


def _safe_attachment_name(att: ChatAttachment) -> str:
    return sanitize_text_for_storage(att.name).strip() or "attachment"


def _bounded_text(value: str, limit: int) -> str:
    """Keep useful context from both ends without exceeding model/storage limits."""
    text = sanitize_text_for_storage(value)
    if len(text) <= limit:
        return text
    marker = _TRUNCATION_MARKER
    if limit <= len(marker):
        return text[:limit]
    available = max(0, limit - len(marker))
    head = round(available * 0.7)
    tail = available - head
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}"


def _attachment_kind(att: ChatAttachment) -> str:
    """Coarse kind stored on the persisted message + used to label content."""
    ctype = (att.content_type or "").lower()
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    if ctype.startswith("image/") or ext in _IMAGE_EXTENSION_MEDIA:
        return "image"
    if ctype.startswith("text/") or ext in _TEXT_EXTENSIONS:
        return "transcript"
    return "file"


def _image_media_type(att: ChatAttachment) -> Optional[str]:
    ctype = (att.content_type or "").lower().split(";")[0].strip()
    if ctype in _SUPPORTED_IMAGE_MEDIA:
        return ctype
    # Fall back to extension for clients that send octet-stream.
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    return _IMAGE_EXTENSION_MEDIA.get(ext)


def _decode_text_attachment(att: ChatAttachment) -> Optional[str]:
    try:
        decoded = att.content.decode("utf-8", errors="replace")
        return sanitize_text_for_storage(decoded).strip() or None
    except Exception:  # pragma: no cover — defensive
        return None


def _document_extension(att: ChatAttachment) -> Optional[str]:
    """Return a supported document extension from filename or MIME type."""
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    if ext in _DOCUMENT_EXTENSIONS:
        return ext
    ctype = (att.content_type or "").lower()
    if "pdf" in ctype:
        return "pdf"
    if "wordprocessingml" in ctype or "msword" in ctype:
        return "docx"
    return None


def _decode_document_attachment(att: ChatAttachment) -> Optional[str]:
    """Extract text from a supported PDF/DOCX document; return None on failure."""
    extension = _document_extension(att)
    if extension is None:
        return None
    try:
        from .document_service import extract_text

        text = extract_text(att.content, extension)
        return (text or "").strip() or None
    except Exception as exc:  # pragma: no cover — defensive
        logger.info(
            "requisition chat: %s extraction failed for %s: %s",
            extension.upper(),
            _safe_attachment_name(att),
            exc,
        )
        return None


def build_recoverable_source_material(
    attachments: list[ChatAttachment],
) -> str:
    """Return successfully decoded textual attachments with filename markers.

    The result is plain text so the turn engine can persist it on the brief and
    include it in later prompts. Images stay vision-only because this layer has
    no durable OCR result for them.
    """
    _content, source_material = prepare_user_turn_content("", attachments)
    return source_material


# --------------------------------------------------------------------------- #
# Persisted-message + LLM-input assembly.
# --------------------------------------------------------------------------- #
def build_persisted_user_message(
    text: str, attachments: list[ChatAttachment]
) -> dict[str, Any]:
    """The user turn we store on ``brief.messages`` (text + attachment metadata,
    NOT the raw bytes)."""
    return {
        "role": "user",
        "content": _bounded_text(text or "", _MAX_USER_TEXT_CHARS),
        "attachments": [
            {"name": _safe_attachment_name(a), "kind": _attachment_kind(a)}
            for a in attachments
        ],
    }


def _history_for_llm(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map persisted messages → Anthropic message dicts (text only). The newest
    user turn is rebuilt separately so attachments become real content blocks."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            content = str(content or "")
        out.append({"role": role, "content": content})
    return out


def prepare_user_turn_content(
    text: str, attachments: list[ChatAttachment]
) -> tuple[Any, str]:
    """Build model content and recoverable source text in one extraction pass.

    Transcripts/PDFs/DOCX are decoded and appended to the text labelled
    ``[Attached transcript: <name>]\\n<content>``; images become base64 image
    blocks (vision). The second tuple item contains only successfully decoded
    textual attachments, ready for durable storage on the brief.
    """
    text_parts: list[str] = []
    source_parts: list[str] = []
    safe_text = _bounded_text(text or "", _MAX_USER_TEXT_CHARS)
    if safe_text.strip():
        text_parts.append(safe_text.strip())

    image_blocks: list[dict[str, Any]] = []
    for att in attachments:
        name = _safe_attachment_name(att)
        kind = _attachment_kind(att)
        if kind == "image":
            media_type = _image_media_type(att)
            if media_type and att.content:
                image_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(att.content).decode("ascii"),
                        },
                    }
                )
            else:
                text_parts.append(f"[Attached image: {name} — could not be read]")
        elif kind == "transcript":
            decoded = _decode_text_attachment(att)
            if decoded:
                used = sum(len(part) for part in source_parts)
                allowance = min(
                    _MAX_EXTRACTED_TEXT_PER_FILE,
                    max(0, _MAX_EXTRACTED_TEXT_PER_TURN - used),
                )
                if allowance:
                    part = (
                        f"[Attached transcript: {name}]\n"
                        f"{_bounded_text(decoded, allowance)}"
                    )
                    text_parts.append(part)
                    source_parts.append(part)
                else:
                    text_parts.append(
                        f"[Attached transcript: {name} — skipped because this turn's extracted-text limit was reached]"
                    )
            else:
                text_parts.append(f"[Attached transcript: {name} — empty]")
        else:  # file
            document_ext = _document_extension(att)
            if document_ext is not None:
                decoded = _decode_document_attachment(att)
                if decoded:
                    used = sum(len(part) for part in source_parts)
                    allowance = min(
                        _MAX_EXTRACTED_TEXT_PER_FILE,
                        max(0, _MAX_EXTRACTED_TEXT_PER_TURN - used),
                    )
                    if allowance:
                        part = (
                            f"[Attached document: {name}]\n"
                            f"{_bounded_text(decoded, allowance)}"
                        )
                        text_parts.append(part)
                        source_parts.append(part)
                    else:
                        text_parts.append(
                            f"[Attached document: {name} — skipped because this turn's extracted-text limit was reached]"
                        )
                else:
                    text_parts.append(
                        f"[Attached document: {name} — {document_ext.upper()} text could not be extracted]"
                    )
            else:
                text_parts.append(f"[Attached file: {name} — unsupported type, skipped]")

    joined = "\n\n".join(text_parts).strip()
    source_material = "\n\n".join(source_parts).strip()
    if not image_blocks:
        return (joined or "(no message)"), source_material
    blocks: list[dict[str, Any]] = []
    if joined:
        blocks.append({"type": "text", "text": joined})
    blocks.extend(image_blocks)
    return blocks, source_material


def build_user_turn_content(
    text: str, attachments: list[ChatAttachment]
) -> Any:
    """Backward-compatible content-only view of ``prepare_user_turn_content``."""
    content, _source_material = prepare_user_turn_content(text, attachments)
    return content


def attachment_content_has_warning(content: Any) -> bool:
    """Whether prepared model content reports any unreadable/skipped file."""

    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        text = str(content or "")
    return any(
        marker in text
        for marker in (
            "could not be read]",
            "could not be extracted]",
            " — empty]",
            "unsupported type, skipped]",
            "extracted-text limit was reached]",
        )
    )
