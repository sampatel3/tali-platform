"""PDF text extraction — column-aware reader plus layout normalization.

Extracted from ``document_service`` (the column-aware logic added in PR #659
pushed that module over the file-size gate). These functions form a
self-contained cluster: they depend only on ``io``, ``re``, and PyPDF2
(imported lazily inside the functions that need it).

``document_service.extract_text`` routes PDF bytes to
``extract_text_from_pdf`` here.
"""

from __future__ import annotations

import io
import logging
import re

logger = logging.getLogger("taali.documents")


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
    from pypdf import PdfReader

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
    from pypdf import PdfReader

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
            logger.warning(
                "Columnar PDF extraction failed error_type=%s",
                type(col_exc).__name__,
            )
            columnar_text, multicolumn = "", False
        if multicolumn and columnar_text:
            return columnar_text

        from pypdf import PdfReader

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
            logger.warning(
                "Layout-aware PDF extraction failed error_type=%s",
                type(layout_exc).__name__,
            )
            layout_text = ""

        if _pdf_text_quality(layout_text) > _pdf_text_quality(raw_text):
            raw_text = layout_text
        return _normalize_pdf_text_layout(raw_text)
    except Exception as exc:
        logger.warning("PDF text extraction failed error_type=%s", type(exc).__name__)
        return ""
