"""Chat attachment model + LLM message assembly for requisition intake.

Split out of ``requisition_chat_service`` to keep that module under the
file-size gate. Owns the ``ChatAttachment`` view the route hands us, the
coarse kind/media detection, transcript/PDF decoding, and the assembly of both
the PERSISTED user message (text + attachment metadata) and the LLM user turn
(text + base64 image blocks for vision). All pure — no DB, no LLM.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger("taali.requisition_chat")

# Extensions we treat as decode-able text/transcripts (appended to the user
# message inline so the model reads them as conversation context).
_TEXT_EXTENSIONS = {"txt", "vtt", "srt", "md", "markdown", "text"}
# Anthropic image block media types we pass through for vision.
_SUPPORTED_IMAGE_MEDIA = {"image/jpeg", "image/png", "image/gif", "image/webp"}


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


def _attachment_kind(att: ChatAttachment) -> str:
    """Coarse kind stored on the persisted message + used to label content."""
    ctype = (att.content_type or "").lower()
    ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
    if ctype.startswith("image/"):
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
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext)


def _decode_text_attachment(att: ChatAttachment) -> Optional[str]:
    try:
        return att.content.decode("utf-8", errors="replace").strip() or None
    except Exception:  # pragma: no cover — defensive
        return None


def _decode_pdf_attachment(att: ChatAttachment) -> Optional[str]:
    """Extract text from a PDF if the repo's extractor is available; else None."""
    try:
        from .document_service import extract_text

        text = extract_text(att.content, "pdf")
        return (text or "").strip() or None
    except Exception as exc:  # pragma: no cover — defensive
        logger.info("requisition chat: PDF extraction failed for %s: %s", att.name, exc)
        return None


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
        "content": text or "",
        "attachments": [
            {"name": a.name, "kind": _attachment_kind(a)} for a in attachments
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


def build_user_turn_content(
    text: str, attachments: list[ChatAttachment]
) -> Any:
    """Build the content for the NEW user turn sent to the model.

    Transcripts/PDFs are decoded and appended to the text labelled
    ``[Attached transcript: <name>]\\n<content>``; images become base64 image
    blocks (vision). Returns a plain string when there are no image blocks (so
    text-only turns stay simple), else a list of content blocks.
    """
    text_parts: list[str] = []
    if (text or "").strip():
        text_parts.append(text.strip())

    image_blocks: list[dict[str, Any]] = []
    for att in attachments:
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
                text_parts.append(f"[Attached image: {att.name} — could not be read]")
        elif kind == "transcript":
            decoded = _decode_text_attachment(att)
            if decoded:
                text_parts.append(f"[Attached transcript: {att.name}]\n{decoded}")
            else:
                text_parts.append(f"[Attached transcript: {att.name} — empty]")
        else:  # file
            ext = att.name.rsplit(".", 1)[-1].lower() if "." in att.name else ""
            ctype = (att.content_type or "").lower()
            if ext == "pdf" or "pdf" in ctype:
                decoded = _decode_pdf_attachment(att)
                if decoded:
                    text_parts.append(f"[Attached document: {att.name}]\n{decoded}")
                else:
                    text_parts.append(
                        f"[Attached document: {att.name} — PDF text could not be extracted]"
                    )
            else:
                text_parts.append(f"[Attached file: {att.name} — unsupported type, skipped]")

    joined = "\n\n".join(text_parts).strip()
    if not image_blocks:
        return joined or "(no message)"
    blocks: list[dict[str, Any]] = []
    if joined:
        blocks.append({"type": "text", "text": joined})
    blocks.extend(image_blocks)
    return blocks
