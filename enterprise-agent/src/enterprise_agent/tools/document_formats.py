"""Document format routing and limits shared by the reader and isolated worker."""

from dataclasses import asdict, dataclass


class DocumentExtractionError(ValueError):
    """Fixed, content-free extraction diagnostics suitable for API/tool responses."""


NATIVE_FORMATS = frozenset({".txt", ".md", ".csv", ".json", ".docx", ".pptx", ".pdf", ".xlsx"})
CONVERSIONS = {".doc": ".docx", ".ppt": ".pptx", ".xls": ".xlsx", ".rtf": ".docx", ".odt": ".docx"}
ADVANCED_FORMATS = frozenset({*CONVERSIONS, ".msg", ".eml"})
SUPPORTED = NATIVE_FORMATS | ADVANCED_FORMATS
MACRO_FORMATS = frozenset({".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm", ".xlam", ".ppam"})
MIME_TYPES = {
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".csv": {"text/csv", "text/plain", "application/vnd.ms-excel"},
    ".json": {"application/json", "text/plain"},
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".ppt": {"application/vnd.ms-powerpoint"},
    ".xls": {"application/vnd.ms-excel"},
    ".rtf": {"application/rtf", "text/rtf"},
    ".odt": {"application/vnd.oasis.opendocument.text"},
    ".msg": {"application/vnd.ms-outlook"},
    ".eml": {"message/rfc822"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
}


@dataclass(frozen=True)
class DocumentLimits:
    max_source_bytes: int = 5_000_000
    max_expanded_bytes: int = 50_000_000
    max_archive_entries: int = 2000
    max_compression_ratio: int = 200
    max_depth: int = 32
    max_units: int = 200
    max_cells: int = 100_000
    max_mail_parts: int = 256
    max_chars: int = 100_000

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_settings(cls, settings, *, max_source_bytes=None, max_chars=None):
        return cls(
            max_source_bytes=max_source_bytes or settings.document_max_source_bytes,
            max_expanded_bytes=settings.document_max_expanded_bytes,
            max_archive_entries=settings.document_max_archive_entries,
            max_compression_ratio=settings.document_max_compression_ratio,
            max_depth=settings.document_max_depth,
            max_units=settings.document_max_units,
            max_cells=settings.document_max_cells,
            max_mail_parts=settings.document_max_mail_parts,
            max_chars=max_chars or settings.document_max_chars,
        )


def enabled_formats(settings=None):
    if settings is None:
        return NATIVE_FORMATS
    configured = settings.document_formats
    if not settings.document_conversion_enabled:
        configured = configured - CONVERSIONS.keys()
    return configured if settings.document_sandbox_enabled else configured & NATIVE_FORMATS
