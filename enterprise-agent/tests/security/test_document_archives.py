import io
import struct
from zipfile import ZipFile

import pytest

from enterprise_agent.tools.document_formats import DocumentExtractionError, DocumentLimits
from enterprise_agent.tools.document_validation import validate_content
from tests.fixtures.document_corpus import compound_file


def test_forged_entry_count_cannot_bypass_central_directory_limit(monkeypatch):
    from enterprise_agent.tools import document_validation

    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for index in range(25):
            archive.writestr(f"{index}.xml", b"<x/>")
    content = bytearray(output.getvalue())
    end = content.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", content, end + 8, 1, 1)
    allocated = []

    def observed_zip(*args, **kwargs):
        allocated.append(True)
        return ZipFile(*args, **kwargs)

    monkeypatch.setattr(document_validation, "ZipFile", observed_zip)
    with pytest.raises(DocumentExtractionError, match="archive|limit"):
        validate_content(bytes(content), ".docx", limits=DocumentLimits(max_archive_entries=10))
    assert not allocated, "Reject forged directories before allocating ZipInfo objects"


def test_xml_entity_expansion_is_rejected_before_parsing():
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY y "boom">]><x>&y;</x>')
    with pytest.raises(DocumentExtractionError, match="entities|DTD"):
        validate_content(output.getvalue(), ".docx")


def test_binary_office_macros_and_wrong_compound_type_are_rejected(tmp_path):
    from enterprise_agent.tools.document_worker import process_document

    for streams in ({"WordDocument": b"data", "Macros": b"macro"}, {"Workbook": b"data"}):
        with pytest.raises(DocumentExtractionError):
            process_document(compound_file(streams), ".doc", DocumentLimits(), tmp_path)


def test_malformed_zip_and_oversized_input_are_rejected():
    for content in (b"PK\x03\x04broken", b"a" * 1025):
        with pytest.raises(DocumentExtractionError):
            validate_content(content, ".docx", limits=DocumentLimits(max_source_bytes=1024))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
@pytest.mark.parametrize(
    "payload",
    [
        '<!DOCTYPE x [<!ENTITY y "expanded">]><x>&y;</x>',
        '<office:scripts xmlns:office="urn:test"><office:event-listener/></office:scripts>',
    ],
)
def test_xml_security_checks_do_not_depend_on_byte_encoding(encoding, payload):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr(
            "content.xml",
            (f'<?xml version="1.0" encoding="{encoding}"?>' + payload).encode(encoding),
        )
    with pytest.raises(DocumentExtractionError):
        validate_content(output.getvalue(), ".odt")
