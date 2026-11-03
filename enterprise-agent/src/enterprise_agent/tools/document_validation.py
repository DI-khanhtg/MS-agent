"""Bounded format/container validation before selecting any document parser."""

import io
import re
import stat
import struct
from pathlib import PurePosixPath
from xml.parsers import expat
from zipfile import ZipFile

from enterprise_agent.tools.document_formats import (
    MACRO_FORMATS,
    MIME_TYPES,
    SUPPORTED,
    DocumentExtractionError,
    DocumentLimits,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_PARTS = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml"}


def _xml(content: bytes, limits: DocumentLimits, *, reject_scripts=False):
    # Parser callbacks see decoded XML, including UTF-16 declarations and attributes.
    # No DOM is built, and declarations are rejected before entity expansion.
    parser = expat.ParserCreate()
    depth = 0

    def forbidden(*args):
        raise DocumentExtractionError("XML entities and DTD declarations are unsupported")

    def start(name, attributes):
        nonlocal depth
        depth += 1
        if depth > limits.max_depth * 3:
            raise DocumentExtractionError("Document XML nesting exceeds the depth limit")
        if reject_scripts and name.rsplit(":", 1)[-1].lower() in {"scripts", "event-listener"}:
            raise DocumentExtractionError("OpenDocument macros and event scripts are unsupported")
        if any(
            any(word in value.lower() for word in ("macroenabled", "vbaproject", "macrosheet"))
            for value in attributes.values()
        ):
            raise DocumentExtractionError("Macro-enabled document content is unsupported")

    def end(name):
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.Parse(content, True)


def validate_archive(
    content: bytes, suffix: str, limits: DocumentLimits, *, nesting=0, budget=None
):
    if nesting > min(4, limits.max_depth):
        raise DocumentExtractionError("Nested document archives exceed the depth limit")
    budget = budget if budget is not None else [0, 0]
    # Reject central-directory bombs before ZipFile allocates a ZipInfo per entry.
    end = content.rfind(b"PK\x05\x06", max(0, len(content) - 65557))
    if end < 0 or end + 22 > len(content):
        raise DocumentExtractionError("Malformed document archive")
    disk, directory_disk, on_disk, entries = struct.unpack_from("<4H", content, end + 4)
    directory_size, directory_offset = struct.unpack_from("<II", content, end + 12)
    cursor, actual_entries = directory_offset, 0
    if directory_offset + directory_size > end:
        raise DocumentExtractionError("Malformed document archive directory")
    while cursor < directory_offset + directory_size:
        if content[cursor : cursor + 4] != b"PK\x01\x02" or cursor + 46 > end:
            raise DocumentExtractionError("Malformed document archive directory")
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", content, cursor + 28)
        cursor += 46 + name_size + extra_size + comment_size
        actual_entries += 1
        if actual_entries > limits.max_archive_entries:
            raise DocumentExtractionError("Document archive entry limit exceeded")
    if actual_entries != entries or cursor != directory_offset + directory_size:
        raise DocumentExtractionError("Malformed document archive entry count")
    if (
        disk
        or directory_disk
        or entries != on_disk
        or entries == 65535
        or entries > limits.max_archive_entries
    ):
        raise DocumentExtractionError("Document archive entry limit exceeded")
    with ZipFile(io.BytesIO(content)) as package:
        names = set()
        for item in package.infolist():
            name = item.filename
            path = PurePosixPath(name)
            if (
                name in names
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or len(path.parts) > limits.max_depth
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise DocumentExtractionError("Unsafe document archive path or nesting")
            names.add(name)
            lower = name.casefold()
            if (
                "vbaproject" in lower
                or "vba/" in lower
                or lower.startswith(("basic/", "scripts/"))
                or "macrosheet" in lower
            ):
                raise DocumentExtractionError(
                    "Macros and embedded executable objects are unsupported"
                )
            if item.flag_bits & 1:
                raise DocumentExtractionError("Encrypted Office documents are unsupported")
            budget[0] += item.file_size
            budget[1] += 1
            if budget[0] > limits.max_expanded_bytes:
                raise DocumentExtractionError("Expanded Office document exceeds the size limit")
            if budget[1] > limits.max_archive_entries:
                raise DocumentExtractionError("Document archive entry limit exceeded")
            if item.file_size > max(1, item.compress_size) * limits.max_compression_ratio:
                raise DocumentExtractionError(
                    "Document archive compression ratio exceeds the limit"
                )
            if item.filename.endswith((".xml", ".rels")):
                data = package.read(item)
                _xml(data, limits, reject_scripts=suffix == ".odt")
                if suffix == ".odt" and re.search(
                    rb"<(?:[\w.-]+:)?(?:scripts|event-listener)\b", data, re.I
                ):
                    raise DocumentExtractionError(
                        "OpenDocument macros and event scripts are unsupported"
                    )
                if (
                    b"macroenabled" in data.lower()
                    or b"vbaproject" in data.lower()
                    or b"macrosheet" in data.lower()
                ):
                    raise DocumentExtractionError("Macro-enabled document content is unsupported")
            if "/embeddings/" in lower and not item.is_dir():
                embedded_suffix = path.suffix.lower()
                if embedded_suffix not in _PARTS:
                    raise DocumentExtractionError("Embedded executable objects are unsupported")
                validate_archive(
                    package.read(item), embedded_suffix, limits, nesting=nesting + 1, budget=budget
                )
        if suffix in _PARTS:
            if (
                _PARTS[suffix] not in names
                or "[Content_Types].xml" not in names
                or any(part in names for ext, part in _PARTS.items() if ext != suffix)
            ):
                raise DocumentExtractionError("Document package type does not match its extension")
        elif suffix == ".odt":
            if (
                "mimetype" not in names
                or "content.xml" not in names
                or package.read("mimetype") != b"application/vnd.oasis.opendocument.text"
            ):
                raise DocumentExtractionError("OpenDocument content does not match its extension")


def validate_content(
    content: bytes,
    suffix: str,
    *,
    limits: DocumentLimits | None = None,
    content_type: str | None = None,
):
    limits = limits or DocumentLimits()
    suffix = suffix.lower()
    if suffix in MACRO_FORMATS:
        raise DocumentExtractionError("Macro-enabled document formats are unsupported")
    if suffix not in SUPPORTED:
        raise DocumentExtractionError("Unsupported document format")
    if not isinstance(content, bytes) or not content or len(content) > limits.max_source_bytes:
        raise DocumentExtractionError(
            "Source document is empty or exceeds the configured size limit"
        )
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared and declared != "application/octet-stream" and declared not in MIME_TYPES[suffix]:
        raise DocumentExtractionError("Declared content type does not match the file extension")
    try:
        if suffix in {*_PARTS, ".odt"}:
            validate_archive(content, suffix, limits)
        elif suffix in {".doc", ".ppt", ".xls", ".msg"}:
            if not content.startswith(OLE_MAGIC):
                raise DocumentExtractionError(
                    "Compound document content does not match its extension"
                )
        elif suffix == ".pdf":
            if not content.lstrip().startswith(b"%PDF-"):
                raise DocumentExtractionError(
                    "PDF content does not match its extension; re-export the file"
                )
        elif suffix == ".rtf":
            if not re.match(rb"\{\\rtf[0-9]", content.lstrip()):
                raise DocumentExtractionError("RTF content does not match its extension")
            if re.search(rb"\\(?:object|objdata|objclass|bin|field)\b", content, re.I):
                raise DocumentExtractionError(
                    "RTF embedded objects and active fields are unsupported"
                )
        elif suffix == ".eml":
            headers = re.split(rb"\r?\n\r?\n", content, maxsplit=1)
            if len(headers) != 2 or not re.search(
                rb"^(?:From|To|Subject):", headers[0], re.M | re.I
            ):
                raise DocumentExtractionError("Email content does not match its extension")
        else:
            if content.lstrip(b"\xef\xbb\xbf \r\n").startswith(
                (b"%PDF-", b"PK\x03\x04", OLE_MAGIC, b"MZ", b"\x7fELF", b"{\\rtf")
            ):
                raise DocumentExtractionError("Binary content does not match a text extension")
            decoded = content.decode("utf-8-sig")
            if any(ord(char) < 32 and char not in "\t\n\r" for char in decoded):
                raise DocumentExtractionError(
                    "Text documents must contain UTF-8 text, not binary data"
                )
    except DocumentExtractionError:
        raise
    except Exception:
        raise DocumentExtractionError("Malformed document content or archive") from None


def validate_compound(content: bytes, suffix: str, limits: DocumentLimits):
    """Inspect legacy Office/MAPI streams only inside the isolated worker."""
    import olefile

    with olefile.OleFileIO(content) as compound:
        paths = compound.listdir()
        if len(paths) > limits.max_archive_entries or any(
            len(path) > limits.max_depth for path in paths
        ):
            raise DocumentExtractionError("Compound document nesting or entry limit exceeded")
        names = {path[0] for path in paths}
        required = {
            ".doc": {"WordDocument"},
            ".ppt": {"PowerPoint Document"},
            ".xls": {"Workbook", "Book"},
            ".msg": {"__properties_version1.0"},
        }[suffix]
        if not required & names:
            raise DocumentExtractionError("Compound document type does not match its extension")
        for path in paths:
            if any(
                part.casefold() in {"vba", "macros", "_vba_project", "objectpool"} for part in path
            ):
                raise DocumentExtractionError("Macros and embedded objects are unsupported")
            if compound.get_size(path) > limits.max_expanded_bytes:
                raise DocumentExtractionError("Compound stream exceeds the expanded size limit")
        if suffix == ".xls":
            data = compound.openstream(next(iter(required & names))).read(
                limits.max_expanded_bytes + 1
            )
            position = 0
            while position + 4 <= len(data):
                kind, length = struct.unpack_from("<HH", data, position)
                if position + 4 + length > len(data):
                    raise DocumentExtractionError("Malformed spreadsheet stream")
                if kind == 0x85 and length >= 6 and data[position + 9] in {1, 6}:
                    raise DocumentExtractionError("Excel macro sheets are unsupported")
                position += 4 + length
