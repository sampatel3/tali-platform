"""Self-contained PDF drawing primitives for client report generation.

A tiny, dependency-free PDF writer (Helvetica/Type1, manual content streams)
extracted verbatim from ``services/candidate_feedback_engine`` so the engine
stays focused on report *content* while these geometry/encoding helpers become
reusable and unit-testable. Names keep their ``_pdf_*`` prefix because they are
internal report helpers shared between builders, not a broad public API.
"""

from __future__ import annotations

import textwrap
import unicodedata
from dataclasses import dataclass

_PDF_BODY_WRAP = 92
_A4_PAGE_WIDTH = 595
_A4_PAGE_HEIGHT = 842
_PDF_BRAND_PURPLE = "#9D00FF"
_PDF_BRAND_PURPLE_SOFT = "#F3E9FF"
_PDF_BORDER_SOFT = "#D8D5E8"
_PDF_TEXT = "#171B2D"
_PDF_MUTED = "#667085"


@dataclass(frozen=True)
class _PdfLine:
    text: str
    font: str = "F1"
    size: int = 11
    leading: int = 14


def _pdf_escape(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    safe = normalized.encode("latin-1", "ignore").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_text(text: str, width: int) -> list[str]:
    raw = str(text or "").rstrip()
    if not raw:
        return [""]

    stripped = raw.lstrip()
    indent = raw[: len(raw) - len(stripped)]
    bullet_prefix = ""
    body = stripped
    subsequent_indent = indent
    if stripped.startswith("- "):
        bullet_prefix = f"{indent}- "
        body = stripped[2:]
        subsequent_indent = f"{indent}  "
    elif stripped.startswith("* "):
        bullet_prefix = f"{indent}* "
        body = stripped[2:]
        subsequent_indent = f"{indent}  "

    wrapped = textwrap.wrap(
        body,
        width=width,
        initial_indent=bullet_prefix or indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [bullet_prefix or indent]


def _append_wrapped_pdf_lines(
    output: list[_PdfLine],
    text: str,
    *,
    font: str = "F1",
    size: int = 11,
    leading: int = 14,
    width: int = _PDF_BODY_WRAP,
) -> None:
    for raw_line in str(text or "").splitlines() or [""]:
        for wrapped in _wrap_pdf_text(raw_line, width):
            output.append(_PdfLine(text=wrapped, font=font, size=size, leading=leading))


def _paginate_pdf_lines_with_bounds(
    lines: list[_PdfLine],
    *,
    top_baseline: float,
    bottom_margin: float,
) -> list[list[tuple[float, _PdfLine]]]:
    pages: list[list[tuple[float, _PdfLine]]] = []
    current_page: list[tuple[float, _PdfLine]] = []
    y = top_baseline

    for line in lines:
        if y - line.leading < bottom_margin and current_page:
            pages.append(current_page)
            current_page = []
            y = top_baseline
        current_page.append((y, line))
        y -= line.leading

    if current_page or not pages:
        pages.append(current_page)
    return pages


def _build_pdf_with_dimensions(
    page_streams: list[bytes],
    *,
    page_width: int,
    page_height: int,
) -> bytes:
    page_count = max(1, len(page_streams))
    font_regular_obj = 3
    font_bold_obj = 4
    next_obj_num = 5
    page_obj_nums: list[int] = []
    content_obj_nums: list[int] = []
    for _ in range(page_count):
        page_obj_nums.append(next_obj_num)
        content_obj_nums.append(next_obj_num + 1)
        next_obj_num += 2

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0] * next_obj_num

    def emit(obj_num: int, payload: bytes) -> None:
        offsets[obj_num] = len(pdf)
        pdf.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        pdf.extend(payload)
        pdf.extend(b"\nendobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{obj_num} 0 R" for obj_num in page_obj_nums)
    emit(2, f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii"))
    emit(font_regular_obj, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    emit(font_bold_obj, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    for page_obj_num, content_obj_num, content in zip(page_obj_nums, content_obj_nums, page_streams, strict=False):
        emit(
            page_obj_num,
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /Font << /F1 {font_regular_obj} 0 R /F2 {font_bold_obj} 0 R >> >> "
                f"/Contents {content_obj_num} 0 R >>"
            ).encode("ascii"),
        )
        emit(
            content_obj_num,
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream",
        )

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {next_obj_num}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for obj_num in range(1, next_obj_num):
        pdf.extend(f"{offsets[obj_num]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(f"trailer << /Size {next_obj_num} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii"))
    return bytes(pdf)


def _rgb_components(hex_color: str, fallback: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    raw = str(hex_color or "").strip().lstrip("#")
    if len(raw) != 6:
        return fallback
    try:
        return tuple(round(int(raw[index : index + 2], 16) / 255.0, 4) for index in (0, 2, 4))
    except ValueError:
        return fallback


def _pdf_color_ops(hex_color: str, *, fill: bool = True) -> str:
    r, g, b = _rgb_components(hex_color)
    operator = "rg" if fill else "RG"
    return f"{r} {g} {b} {operator}"


def _pdf_rect_top(
    x: float,
    top: float,
    width: float,
    height: float,
    *,
    fill_color: str | None = None,
    stroke_color: str | None = None,
    line_width: float = 1.0,
) -> str:
    y = _A4_PAGE_HEIGHT - top - height
    ops: list[str] = []
    if fill_color:
        ops.append(_pdf_color_ops(fill_color, fill=True))
    if stroke_color:
        ops.append(_pdf_color_ops(stroke_color, fill=False))
        ops.append(f"{line_width} w")
    paint = "B" if fill_color and stroke_color else "f" if fill_color else "S"
    ops.append(f"{x:.1f} {y:.1f} {width:.1f} {height:.1f} re {paint}")
    return "\n".join(ops)


def _estimate_wrap_width(width: float, font_size: int) -> int:
    return max(12, int(width / max(font_size * 0.54, 1.0)))


def _truncate_pdf_line(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    trimmed = text[: max_chars - 1].rstrip(" ,.;:-")
    return f"{trimmed}…"


def _wrapped_pdf_lines_for_width(
    text: str,
    *,
    width: float,
    font_size: int,
    max_lines: int | None = None,
) -> list[str]:
    lines: list[str] = []
    wrap_width = _estimate_wrap_width(width, font_size)
    for raw_line in str(text or "").splitlines() or [""]:
        lines.extend(_wrap_pdf_text(raw_line, wrap_width))
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _truncate_pdf_line(lines[-1], max(8, wrap_width - 2))
    return lines or [""]


def _pdf_text_block_ops(
    text: str,
    *,
    x: float,
    top: float,
    width: float,
    font: str = "F1",
    size: int = 11,
    leading: int = 14,
    color: str = _PDF_TEXT,
    max_lines: int | None = None,
) -> tuple[list[str], float]:
    ops: list[str] = []
    lines = _wrapped_pdf_lines_for_width(text, width=width, font_size=size, max_lines=max_lines)
    for index, line in enumerate(lines):
        baseline_y = _A4_PAGE_HEIGHT - top - (index * leading) - size
        ops.append(
            " ".join(
                [
                    "BT",
                    f"/{font}",
                    f"{size}",
                    "Tf",
                    _pdf_color_ops(color, fill=True),
                    "1 0 0 1",
                    f"{x:.1f}",
                    f"{baseline_y:.1f}",
                    "Tm",
                    f"({_pdf_escape(line)})",
                    "Tj ET",
                ]
            )
        )
    return ops, top + (len(lines) * leading)
