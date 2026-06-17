"""Document processing service — extracts text from PDF and DOCX files."""

from __future__ import annotations

import io
from datetime import date, datetime
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile

logger = logging.getLogger("taali.documents")

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
_UNSAFE_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    previous_blank = False
    for raw in lines:
        line = str(raw or "")
        is_blank = not line.strip()
        if is_blank:
            if previous_blank:
                continue
            output.append("")
            previous_blank = True
            continue
        output.append(line.strip())
        previous_blank = False
    while output and not output[0]:
        output.pop(0)
    while output and not output[-1]:
        output.pop()
    return output


def _looks_like_pdf_heading(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    alpha = re.sub(r"[^A-Za-z]", "", text)
    if len(text.split()) <= 6 and text.endswith(":"):
        return True
    if alpha and text == text.upper() and len(alpha) >= 4:
        return True
    return False


def _looks_like_contact_line(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False
    return any(token in text for token in ("@", "linkedin.com", "github.com", "http://", "https://"))


def _normalize_pdf_text_layout(text: str) -> str:
    lines = [re.sub(r"\s+", " ", part).strip() for part in str(text or "").replace("\r", "\n").splitlines()]
    lines = _collapse_blank_lines(lines)
    if not lines:
        return ""

    short_lines = [line for line in lines if line and len(line.split()) <= 3]
    if len(short_lines) / max(1, len([line for line in lines if line])) < 0.45:
        return "\n".join(lines).strip()

    output: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        output.append(" ".join(buffer).strip())
        buffer.clear()

    for line in lines:
        if not line:
            flush_buffer()
            output.append("")
            continue
        if _looks_like_pdf_heading(line) or _looks_like_contact_line(line) or line.startswith(("-", "•")):
            flush_buffer()
            output.append(line)
            continue

        buffer.append(line)
        joined = " ".join(buffer)
        if line.endswith((".", "!", "?", ":")) or len(joined) >= 220:
            flush_buffer()

    flush_buffer()
    return "\n".join(_collapse_blank_lines(output)).strip()


def _pdf_text_quality(text: str) -> tuple[float, int]:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return (0.0, 0)
    average_words = sum(len(line.split()) for line in lines) / len(lines)
    return (average_words, len(lines))


def _join_pdf_fragments(fragments: list[tuple[float, str]]) -> str:
    pieces: list[str] = []
    for _, raw in sorted(fragments, key=lambda item: item[0]):
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not text:
            continue
        if not pieces:
            pieces.append(text)
            continue
        if pieces[-1].endswith(("-", "/", "(")) or text.startswith((".", ",", ";", ":", "!", "?", "%", ")", "]")):
            pieces[-1] = f"{pieces[-1]}{text}"
        else:
            pieces.append(text)
    return " ".join(pieces).strip()


def _extract_text_from_pdf_with_layout(content: bytes) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages_text: list[str] = []

    for page in reader.pages:
        fragments: list[tuple[float, float, str]] = []

        def visitor_text(text, _cm, tm, _font_dict, _font_size):
            value = re.sub(r"\s+", " ", str(text or "")).strip()
            if not value:
                return
            fragments.append((float(tm[5]), float(tm[4]), value))

        page.extract_text(visitor_text=visitor_text)
        if not fragments:
            continue

        grouped_lines: list[tuple[float, list[tuple[float, str]]]] = []
        for y, x, text in sorted(fragments, key=lambda item: (-item[0], item[1])):
            if not grouped_lines or abs(grouped_lines[-1][0] - y) > 3.0:
                grouped_lines.append((y, [(x, text)]))
            else:
                grouped_lines[-1][1].append((x, text))

        lines = [_join_pdf_fragments(items) for _, items in grouped_lines]
        page_text = "\n".join(line for line in lines if line)
        if page_text.strip():
            pages_text.append(page_text.strip())

    return "\n\n".join(pages_text).strip()


def _detect_column_split(frags: list[tuple[float, float, str, float]], page_w: float) -> float | None:
    """Return the x of a clean two-column gutter, or None for single-column.

    ``frags`` are ``(x, y, text, font_size)``. A real gutter is an x that
    almost no fragment's horizontal extent crosses, with substantial,
    vertically-broad content on both sides — that rules out a one-off indent
    or a localized two-up list inside an otherwise single-column page.
    """
    if len(frags) < 25:
        return None
    # Estimate each fragment's horizontal extent from font-size-scaled width.
    spans = [(x, x + max(len(t) * sz * 0.5, sz * 0.5)) for (x, _y, t, sz) in frags]
    ys = [f[1] for f in frags]
    full_span = (max(ys) - min(ys)) or 1.0
    lo, hi = page_w * 0.28, page_w * 0.72
    best: tuple[float, int] | None = None
    x = lo
    while x <= hi:
        crossings = sum(1 for a, b in spans if a < x < b)
        left = [f for f, (a, b) in zip(frags, spans) if b <= x]
        right = [f for f, (a, b) in zip(frags, spans) if a >= x]
        if len(left) >= len(frags) * 0.2 and len(right) >= len(frags) * 0.2:
            ly = [f[1] for f in left]
            ry = [f[1] for f in right]
            if (max(ly) - min(ly)) >= full_span * 0.4 and (max(ry) - min(ry)) >= full_span * 0.4:
                if best is None or crossings < best[1]:
                    best = (x, crossings)
        x += 4.0
    if best is None or best[1] > len(frags) * 0.05:
        return None
    return best[0]


def _fragments_to_lines(frags: list[tuple[float, float, str, float]]) -> list[str]:
    """Group fragments into visual lines (by y proximity), each ordered left-to-right."""
    lines: list[str] = []
    cur_y: float | None = None
    cur: list[tuple[float, str]] = []
    for x, y, text, _sz in sorted(frags, key=lambda f: (-f[1], f[0])):
        if cur_y is None or abs(cur_y - y) <= 3.0:
            cur.append((x, text))
            if cur_y is None:
                cur_y = y
        else:
            lines.append(_join_pdf_fragments(cur))
            cur = [(x, text)]
            cur_y = y
    if cur:
        lines.append(_join_pdf_fragments(cur))
    return [ln for ln in lines if ln]


def _extract_text_from_pdf_columnar(content: bytes) -> tuple[str, bool]:
    """Column-aware extraction. Returns ``(text, found_multicolumn)``.

    Multi-column CVs (a skills / education sidebar beside the main column)
    confuse the default top-to-bottom reader: it interleaves the two columns
    line-by-line, scrambling the reading order — which then mixes sections
    (e.g. summary text becomes "skills", certifications absorb hobbies) and
    mis-attributes project bullets to the wrong role. Here we detect a
    vertical gutter and read each column independently, ordering columns so
    the one carrying the title/name block (topmost fragment) comes first.
    """
    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages_text: list[str] = []
    found = False

    for page in reader.pages:
        frags: list[tuple[float, float, str, float]] = []

        def visitor_text(text, _cm, tm, _font_dict, font_size):
            value = re.sub(r"\s+", " ", str(text or "")).strip()
            if not value:
                return
            try:
                size = float(font_size) or 10.0
            except Exception:
                size = 10.0
            frags.append((float(tm[4]), float(tm[5]), value, size))

        page.extract_text(visitor_text=visitor_text)
        if not frags:
            continue

        page_w = float(page.mediabox.width) or 540.0
        split = _detect_column_split(frags, page_w)
        if split is not None:
            found = True
            left = [f for f in frags if f[0] < split]
            right = [f for f in frags if f[0] >= split]
            # Read the column with the topmost fragment first (name/header block).
            columns = sorted([left, right], key=lambda g: -max(f[1] for f in g))
            page_text = "\n".join("\n".join(_fragments_to_lines(col)) for col in columns)
        else:
            page_text = "\n".join(_fragments_to_lines(frags))
        if page_text.strip():
            pages_text.append(page_text.strip())

    return ("\n\n".join(pages_text).strip(), found)


def load_stored_document_bytes(file_url: str | None) -> bytes | None:
    location = str(file_url or "").strip()
    if not location:
        return None

    local_path = Path(location)
    if local_path.exists() and local_path.is_file():
        try:
            return local_path.read_bytes()
        except Exception as exc:
            logger.warning("Failed to read local document bytes from %s: %s", location, exc)
            return None

    from .s3_service import extract_key_from_url, download_from_s3

    bucket_key = extract_key_from_url(location)
    if bucket_key:
        _, key = bucket_key
        try:
            return download_from_s3(key)
        except Exception as exc:
            logger.warning("Failed to download object bytes from %s: %s", location, exc)
            return None

    return None


def stored_document_s3_key(file_url: str | None) -> str | None:
    """Extract the object key from a stored file_url, or None for
    non-object-store URLs (e.g. local filesystem paths).

    Used by download endpoints that redirect to a presigned URL instead
    of streaming bytes back through FastAPI. Recognises both AWS-style
    and Tigris/R2/MinIO endpoint-style URLs via
    ``s3_service.extract_key_from_url``.
    """
    from .s3_service import extract_key_from_url

    bucket_key = extract_key_from_url(file_url or "")
    if bucket_key is None:
        return None
    _, key = bucket_key
    return key


def sanitize_text_for_storage(value: str | None) -> str:
    """Strip NUL and non-printable control characters unsafe for DB text fields."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    return _UNSAFE_CONTROL_CHARS_RE.sub("", text)


def sanitize_json_for_storage(value: Any) -> Any:
    """Recursively sanitize strings inside JSON-like payloads."""
    if isinstance(value, str):
        return sanitize_text_for_storage(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [sanitize_json_for_storage(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json_for_storage(item) for item in value]
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = sanitize_text_for_storage(key) if isinstance(key, str) else key
            out[safe_key] = sanitize_json_for_storage(item)
        return out
    return value


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(content: bytes) -> str:
    """Extract text from PDF bytes using PyPDF2."""
    try:
        # Column-aware first. Multi-column CVs (sidebar + main column) get
        # scrambled by the default top-to-bottom reader. When a clean column
        # layout is detected we use that text directly — it's already in
        # reading order, so we skip the paragraph-merge normalizer (which
        # would re-merge the cleanly separated lines). Single-column CVs
        # detect no gutter and fall through to the original path unchanged.
        try:
            columnar_text, multicolumn = _extract_text_from_pdf_columnar(content)
        except Exception as col_exc:
            logger.warning("Columnar PDF extraction failed: %s", col_exc)
            columnar_text, multicolumn = "", False
        if multicolumn and columnar_text:
            return columnar_text

        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        raw_text = "\n\n".join(pages).strip()
        try:
            layout_text = _extract_text_from_pdf_with_layout(content)
        except Exception as layout_exc:
            logger.warning("Layout-aware PDF extraction failed: %s", layout_exc)
            layout_text = ""

        if _pdf_text_quality(layout_text) > _pdf_text_quality(raw_text):
            raw_text = layout_text
        return _normalize_pdf_text_layout(raw_text)
    except Exception as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        return ""


def extract_text_from_docx(content: bytes) -> str:
    """Extract text from DOCX bytes using python-docx."""
    try:
        from docx import Document

        doc = Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs).strip()
    except Exception as exc:
        logger.warning("DOCX text extraction failed: %s", exc)
        return ""


def extract_text_from_txt(content: bytes) -> str:
    """Extract text from plain text bytes."""
    try:
        return content.decode("utf-8", errors="replace").strip()
    except Exception as exc:
        logger.warning("TXT text extraction failed: %s", exc)
        return ""


def extract_text(content: bytes, extension: str) -> str:
    """Route to the appropriate extractor based on file extension."""
    ext = extension.lower().lstrip(".")
    if ext == "pdf":
        return sanitize_text_for_storage(extract_text_from_pdf(content))
    elif ext == "docx":
        return sanitize_text_for_storage(extract_text_from_docx(content))
    elif ext == "txt":
        return sanitize_text_for_storage(extract_text_from_txt(content))
    return ""


# ---------------------------------------------------------------------------
# Upload processing
# ---------------------------------------------------------------------------

def validate_upload(upload: UploadFile, allowed_extensions: set[str] | None = None) -> tuple[str, str]:
    """Validate an upload file. Returns (filename, extension).

    Raises HTTPException on validation failure.
    """
    exts = allowed_extensions or ALLOWED_EXTENSIONS
    filename = (upload.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in exts:
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(sorted(exts)).upper()} files are allowed",
        )
    return filename, ext


def read_upload_content(upload: UploadFile) -> bytes:
    """Read and validate upload content size. Returns raw bytes."""
    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File must be {MAX_FILE_SIZE // (1024 * 1024)}MB or smaller",
        )
    return content


def save_file_locally(content: bytes, directory: str, prefix: str, ext: str) -> str:
    """Local-disk fallback for development and tests when no S3 creds
    are configured.

    Production deployments must always have ``AWS_*`` env vars set
    (which keeps this path inactive). Files written here live on
    ephemeral disk and will be wiped on container restart — fine for
    local dev / unit tests, dangerous for prod.
    """
    uploads_dir = Path(__file__).resolve().parents[2] / "uploads" / directory
    uploads_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{prefix}-{int(time.time())}.{ext}"
    target_path = uploads_dir / stored_name
    target_path.write_bytes(content)
    return str(target_path)


def _object_storage_is_configured() -> bool:
    """True when the operator has wired up S3 credentials.

    A configured-but-unhealthy store is still considered "configured"
    — production should fail loudly rather than silently fall back to
    ephemeral disk. Tests / local dev typically leave creds blank,
    which keeps the local fallback path active.
    """
    from ..platform.config import settings

    return bool((settings.AWS_ACCESS_KEY_ID or "").strip() and (settings.AWS_SECRET_ACCESS_KEY or "").strip())


def process_document_upload(
    upload: UploadFile,
    entity_id: int,
    doc_type: str,
    allowed_extensions: set[str] | None = None,
) -> Dict[str, Any]:
    """Process a document upload: validate, upload to object storage,
    extract text.

    Bytes go directly to the configured S3-compatible store (Tigris in
    production) — no local-disk hop, no temp files. When storage
    credentials aren't configured (local dev / tests), falls back to
    writing to ``backend/uploads/`` so the test suite doesn't need a
    mocked S3 backend. When credentials are configured but the upload
    fails, raises HTTP 503 so prod misconfigurations surface loudly
    instead of silently corrupting state.

    Args:
        upload: The uploaded file.
        entity_id: ID of the entity (candidate, assessment, etc.).
        doc_type: Type of document ("cv" or "job_spec").
        allowed_extensions: Override allowed extensions.

    Returns:
        Dict with file_url, filename, extracted_text.
    """
    import mimetypes as _mt
    from .s3_service import generate_s3_key, upload_bytes_to_s3

    filename, ext = validate_upload(upload, allowed_extensions)
    content = read_upload_content(upload)

    s3_key = generate_s3_key(doc_type, entity_id, filename)
    content_type = _mt.guess_type(filename)[0] or "application/octet-stream"
    file_url = upload_bytes_to_s3(content, s3_key, content_type=content_type)
    if not file_url:
        if _object_storage_is_configured():
            raise HTTPException(
                status_code=503,
                detail={
                    "reason": "storage_unavailable",
                    "message": (
                        "Object storage is configured but not reachable; cannot save uploads. "
                        "Check AWS_* env vars and the storage health probe at /health."
                    ),
                },
            )
        # Dev/test fallback only — prod should never hit this branch.
        file_url = save_file_locally(
            content=content,
            directory=doc_type,
            prefix=f"{doc_type}-{entity_id}",
            ext=ext,
        )

    extracted_text = sanitize_text_for_storage(extract_text(content, ext))
    text_preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text

    logger.info(
        "Document uploaded: type=%s entity=%s file=%s chars_extracted=%d url=%s",
        doc_type, entity_id, filename, len(extracted_text), file_url,
    )

    return {
        "file_url": file_url,
        "filename": filename,
        "extracted_text": extracted_text,
        "text_preview": text_preview,
    }
