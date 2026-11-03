import io
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from enterprise_agent.tools.document_reader import DocumentExtractionError, extract_document
from enterprise_agent.tools.document_validation import validate_content


@pytest.mark.parametrize("suffix", [".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm"])
def test_macro_enabled_extensions_are_always_rejected(suffix):
    with pytest.raises(DocumentExtractionError, match="[Mm]acro"):
        validate_content(b"anything", suffix)


@pytest.mark.parametrize(
    "suffix,content",
    [
        (".pdf", b"not a pdf"),
        (".doc", b"plain text"),
        (".rtf", b"<html>not RTF</html>"),
        (".msg", b"From: a@example.test\n\nbody"),
        (".eml", b"plain text without mail headers"),
        (".txt", b"%PDF-1.4\n"),
    ],
)
def test_extension_and_actual_content_must_match(suffix, content):
    with pytest.raises(DocumentExtractionError):
        validate_content(content, suffix)


def test_ooxml_package_type_and_macros_are_checked(document_files):
    content = document_files["docx"][1]
    with pytest.raises(DocumentExtractionError, match="match|type"):
        validate_content(content, ".xlsx")
    output = io.BytesIO()
    with ZipFile(io.BytesIO(content)) as source, ZipFile(output, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(DocumentExtractionError, match="[Mm]acro"):
        validate_content(output.getvalue(), ".docx")


def test_zip_bomb_and_path_traversal_are_rejected():
    for name, data in [("word/document.xml", b"x" * 1_000_000), ("../escape", b"x")]:
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(name, data)
        with pytest.raises(DocumentExtractionError):
            validate_content(output.getvalue(), ".docx")


def test_new_formats_fail_closed_without_a_sandbox():
    with pytest.raises(DocumentExtractionError, match="[Ss]andbox"):
        extract_document(b"From: sender@example.test\nSubject: Test\n\nBody", ".eml")
