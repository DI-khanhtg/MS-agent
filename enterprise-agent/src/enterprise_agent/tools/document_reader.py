"""Bounded, offline text extraction for common documents; no OCR or visual analysis."""

import io
from dataclasses import dataclass

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pypdf import PdfReader

from enterprise_agent.tools.document_formats import (
    ADVANCED_FORMATS,
    SUPPORTED,
    DocumentExtractionError,
    DocumentLimits,
)
from enterprise_agent.tools.document_validation import validate_content

_OFFICE = {".docx", ".pptx", ".xlsx"}
_VISUAL_WARNING = (
    "Text only: images, charts and other visual content, if present, are not extracted."
)
_MAX_SOURCE_BYTES = 5_000_000
_MAX_DEPTH = 32


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    format: str
    unit_count: int | None
    warnings: tuple[str, ...]
    headers: dict[str, str] | None = None
    converted_to: str | None = None

    @property
    def metadata(self) -> dict:
        result = {
            "format": self.format,
            "method": "text_extraction",
            "unit_count": self.unit_count,
            "coverage": "text_only",
            "warnings": list(self.warnings),
        }
        if self.headers is not None:
            result["headers"] = self.headers
        if self.converted_to:
            result.update(method="libreoffice_conversion", converted_to=self.converted_to)
        if self.format in {suffix[1:] for suffix in ADVANCED_FORMATS}:
            result["trust"] = "untrusted_document_data"
        return result


class _Text:
    """Account for every separator before retaining another text fragment."""

    def __init__(self, limit: int):
        self.limit = limit
        self.size = 0
        self.parts: list[str] = []

    def write(self, text: str) -> None:
        if self.size + len(text) > self.limit:
            raise DocumentExtractionError(
                "Extracted document exceeds the text limit; split the file"
            )
        if text:
            self.parts.append(text)
            self.size += len(text)

    def line(self, text: str) -> None:
        if text:
            if self.parts:
                self.write("\n")
            self.write(text)

    def result(self) -> str:
        return "".join(self.parts)


def _docx_blocks(container, text: _Text, depth: int = 0, max_depth=_MAX_DEPTH) -> None:
    if depth > max_depth:
        raise DocumentExtractionError("Document nesting exceeds the supported depth; simplify it")
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            text.line(block.text)
        elif isinstance(block, Table):
            for row in block.rows:
                # Merged cells share the same XML; emit each distinct cell once per row.
                seen = set()
                cells = _Text(text.limit - text.size)
                for cell in row.cells:
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    cell_text = _Text(text.limit - text.size)
                    _docx_blocks(cell, cell_text, depth + 1, max_depth)
                    if cells.parts:
                        cells.write(" | ")
                    cells.write(cell_text.result())
                text.line(cells.result())


def _extract_docx(content: bytes, text: _Text, max_depth=_MAX_DEPTH) -> None:
    document = Document(io.BytesIO(content))
    seen = set()
    # Headers and footers live in separate parts; inherited parts are emitted once.
    for section in document.sections:
        for header in (section.header, section.first_page_header, section.even_page_header):
            if header._has_definition and header.part.partname not in seen:
                seen.add(header.part.partname)
                _docx_blocks(header, text, max_depth=max_depth)
    _docx_blocks(document, text, max_depth=max_depth)
    for section in document.sections:
        for footer in (section.footer, section.first_page_footer, section.even_page_footer):
            if footer._has_definition and footer.part.partname not in seen:
                seen.add(footer.part.partname)
                _docx_blocks(footer, text, max_depth=max_depth)


def _pptx_shapes(shapes, text: _Text, depth: int = 0, max_depth=_MAX_DEPTH) -> None:
    if depth > max_depth:
        raise DocumentExtractionError("Slide nesting exceeds the supported depth; simplify it")
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _pptx_shapes(shape.shapes, text, depth + 1, max_depth)
        elif shape.has_table:
            for row in shape.table.rows:
                row_text = _Text(text.limit - text.size)
                has_text = False
                for index, cell in enumerate(row.cells):
                    if index:
                        row_text.write(" | ")
                    if not cell.is_spanned:
                        row_text.write(cell.text)
                        has_text |= bool(cell.text.strip())
                if has_text:
                    text.line(row_text.result())
        elif shape.has_text_frame:
            for paragraph in shape.text_frame.paragraphs:
                if paragraph.text.strip():
                    text.line(paragraph.text)


def _extract_pptx(
    content: bytes, text: _Text, warnings: list[str], max_units=200, max_depth=_MAX_DEPTH
) -> int:
    deck = Presentation(io.BytesIO(content))
    if len(deck.slides) > max_units:
        raise DocumentExtractionError(
            f"Presentations over {max_units} slides are unsupported; split the file"
        )
    empty = 0
    for index, slide in enumerate(deck.slides, 1):
        slide_text = _Text(text.limit - text.size)
        _pptx_shapes(slide.shapes, slide_text, max_depth=max_depth)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                slide_text.line("Speaker notes:")
                for paragraph in notes.paragraphs:
                    if paragraph.text.strip():
                        slide_text.line(paragraph.text)
        if slide_text.parts:
            text.line(f"Slide {index}")
            text.line(slide_text.result())
        else:
            empty += 1
    if empty:
        warnings.append(
            f"{empty} of {len(deck.slides)} PowerPoint slides have no readable text; "
            "images and charts are not read."
        )
    return len(deck.slides)


def _extract_pdf(content: bytes, text: _Text, warnings: list[str], max_units=200) -> int:
    document = PdfReader(io.BytesIO(content))
    if document.is_encrypted:
        raise DocumentExtractionError("Encrypted PDFs are unsupported; provide an unencrypted copy")
    count = len(document.pages)
    if count > max_units:
        raise DocumentExtractionError(
            f"PDFs over {max_units} pages are unsupported; split the file"
        )
    empty = 0
    for index, page in enumerate(document.pages, 1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            text.line(f"Page {index}")
            text.line(page_text)
        else:
            empty += 1
    if empty:
        warnings.append(
            f"{empty} of {count} PDF pages have no selectable text; "
            "scanned or image-only pages require OCR, which is not performed."
        )
    return count


def _extract_xlsx(
    content: bytes, text: _Text, warnings: list[str], max_cells=100_000, max_units=200
) -> int:
    warnings.append(
        "Only cached formula values are read; unavailable cached values are omitted. "
        "Formulas are not evaluated."
    )
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        if len(workbook.worksheets) > max_units:
            raise DocumentExtractionError("Workbook worksheet count exceeds the configured limit")
        empty = 0
        for sheet in workbook:
            if (sheet.max_row or 0) * (sheet.max_column or 0) > max_cells:
                raise DocumentExtractionError("Worksheet cell/text limit exceeded; split the file")
            sheet_text = _Text(text.limit - text.size)
            has_values = False
            for row in sheet.iter_rows(values_only=True):
                row_text = _Text(sheet_text.limit - sheet_text.size)
                for index, value in enumerate(row):
                    if index:
                        row_text.write(" | ")
                    if value is not None:
                        value_text = str(value)
                        row_text.write(value_text)
                        has_values |= bool(value_text.strip())
                # Account for empty rows too, bounding worksheets with inflated dimensions.
                sheet_text.write("\n")
                sheet_text.write(row_text.result())
            if has_values:
                text.line(f"Sheet: {sheet.title}")
                text.write(sheet_text.result())
            else:
                empty += 1
        if empty:
            warnings.append(
                f"{empty} of {len(workbook.worksheets)} worksheets have no readable cached "
                "values; they may be empty or contain formulas without cached results."
            )
        return len(workbook.worksheets)
    finally:
        workbook.close()


def extract_document(
    content: bytes,
    suffix: str,
    max_chars: int | None = None,
    *,
    max_source_bytes: int | None = None,
    settings=None,
    content_type: str | None = None,
) -> ExtractedDocument:
    """Extract selectable text without executing macros or fetching external resources.

    Source files default to 5 MB (explicit cloud reads may allow up to 20 MB),
    expanded Office ZIPs at 50 MB, PDF/PPTX at
    200 pages/slides, and text is rejected as soon as the requested limit is exceeded.
    Advanced formats always require a verified sandbox; converted Office output reuses
    the same native parsers. No unsafe host conversion fallback is provided.
    """
    max_chars = (
        max_chars
        if max_chars is not None
        else (settings.document_max_chars if settings else 100_000)
    )
    max_source_bytes = (
        max_source_bytes
        if max_source_bytes is not None
        else (settings.document_max_source_bytes if settings else _MAX_SOURCE_BYTES)
    )
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise DocumentExtractionError("max_chars must be a positive integer")
    if type(max_source_bytes) is not int or not 0 < max_source_bytes <= 20_000_000:
        raise DocumentExtractionError("max_source_bytes must be from 1 to 20000000")
    suffix = suffix.lower()
    if suffix not in SUPPORTED:
        if suffix in {".docm", ".xlsm", ".pptm"}:
            raise DocumentExtractionError("Macro-enabled document formats are unsupported")
        raise DocumentExtractionError("Unsupported document format")
    if len(content) > max_source_bytes:
        raise DocumentExtractionError(
            "Source document exceeds the configured size limit; split the file"
        )
    if settings and suffix not in settings.document_formats:
        raise DocumentExtractionError("Document format is disabled by configuration")
    limits = (
        DocumentLimits.from_settings(
            settings, max_source_bytes=max_source_bytes, max_chars=max_chars
        )
        if settings
        else DocumentLimits(
            max_source_bytes=max_source_bytes,
            max_chars=max_chars,
        )
    )
    validate_content(content, suffix, limits=limits, content_type=content_type)
    if suffix in ADVANCED_FORMATS or (
        settings and settings.document_sandbox_enabled and settings.document_sandbox_all_formats
    ):
        from enterprise_agent.tools.document_sandbox import SandboxClient

        return SandboxClient(settings).extract(content, suffix, limits)
    return extract_native(content, suffix, limits)


def extract_native(content: bytes, suffix: str, limits: DocumentLimits) -> ExtractedDocument:
    """Existing native parsers, also reused by the isolated converter worker."""
    text = _Text(limits.max_chars)
    warnings: list[str] = []
    count = None
    try:
        validate_content(content, suffix, limits=limits)
        if suffix == ".docx":
            _extract_docx(content, text, limits.max_depth)
        elif suffix == ".pptx":
            count = _extract_pptx(content, text, warnings, limits.max_units, limits.max_depth)
        elif suffix == ".pdf":
            count = _extract_pdf(content, text, warnings, limits.max_units)
        elif suffix == ".xlsx":
            count = _extract_xlsx(content, text, warnings, limits.max_cells, limits.max_units)
        else:
            decoded = content.decode("utf-8-sig")
            if any(ord(char) < 32 and char not in "\t\n\r" for char in decoded):
                raise DocumentExtractionError(
                    "Text documents must contain UTF-8 text, not binary data"
                )
            text.write(decoded)
    except DocumentExtractionError:
        raise
    except Exception:
        raise DocumentExtractionError(
            "Document could not be extracted; check that it is a valid, unencrypted "
            "file matching its extension, or re-export it in a supported format"
        ) from None
    if suffix in _OFFICE or suffix == ".pdf":
        warnings.append(_VISUAL_WARNING)
    return ExtractedDocument(text.result(), suffix[1:], count, tuple(warnings))


def extract_text(content: bytes, suffix: str, max_chars: int = 100_000) -> str:
    """Compatibility wrapper for callers which only need the extracted text."""
    return extract_document(content, suffix, max_chars).text
