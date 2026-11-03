"""Deterministic, self-authored readable fixtures and independent reference text."""

import io
import json
import struct
from dataclasses import dataclass
from email.message import EmailMessage
from zipfile import ZipFile

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen.canvas import Canvas


@dataclass(frozen=True)
class Fixture:
    name: str
    content: bytes
    reference: str
    converted: bool = False


def compound_file(streams):
    """Small CFB v3 writer for synthetic, macro-free MAPI validation fixtures.

    Streams use regular FAT sectors and at least 4096 bytes. No MiniFAT, executable
    objects, external files or third-party document content is embedded.
    """
    free, end, fat_sector = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
    names = sorted(streams, key=lambda name: (len(name), name.upper()))
    data, starts, chains = bytearray(), {}, []
    for name in names:
        content = streams[name].ljust(4096, b"\0")
        size = (len(content) + 511) // 512
        start = len(data) // 512
        starts[name] = (start, len(content))
        data.extend(content.ljust(size * 512, b"\0"))
        chains.extend(range(start + 1, start + size))
        chains.append(end)
    directory_start = len(data) // 512
    count = len(names) + 1
    directory_sectors = (count + 3) // 4
    tree = {}

    def build(indices):
        if not indices:
            return free
        middle = len(indices) // 2
        number = indices[middle]
        tree[number] = (build(indices[:middle]), build(indices[middle + 1 :]))
        return number

    child = build(list(range(1, count)))

    def entry(name, kind, left=free, right=free, child_id=free, start=end, size=0):
        value = bytearray(128)
        encoded = (name + "\0").encode("utf-16le")
        value[: len(encoded)] = encoded
        struct.pack_into("<HBBIII", value, 64, len(encoded), kind, 1, left, right, child_id)
        struct.pack_into("<IQ", value, 116, start, size)
        return value

    directory = entry("Root Entry", 5, child_id=child)
    for number, name in enumerate(names, 1):
        left, right = tree[number]
        start, size = starts[name]
        directory.extend(entry(name, 2, left, right, start=start, size=size))
    data.extend(directory.ljust(directory_sectors * 512, b"\0"))
    chains.extend(range(directory_start + 1, directory_start + directory_sectors))
    chains.append(end)
    fat_start = len(data) // 512
    chains.append(fat_sector)
    assert len(chains) <= 128
    data.extend(struct.pack("<128I", *(chains + [free] * (128 - len(chains)))))
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, directory_start, 0, 4096, end, 0, end, 0)
    struct.pack_into("<109I", header, 76, fat_start, *([free] * 108))
    return bytes(header + data)


def msg_fixture(subject, body, *, html=False):
    headers = (
        f"From: sender@example.test\r\nTo: reader@example.test\r\nSubject: {subject}\r\n"
        "Date: Fri, 30 Oct 2026 08:00:00 +0700\r\n\r\n"
    )
    unicode_property = struct.pack("<IIII", 0x340D0003, 6, 0x40000, 0)
    streams = {
        "__properties_version1.0": b"\0" * 32 + unicode_property * 254,
        "__substg1.0_001A001F": "IPM.Note".encode("utf-16le"),
        "__substg1.0_0037001F": subject.encode("utf-16le"),
        "__substg1.0_007D001F": headers.encode("utf-16le"),
    }
    streams["__substg1.0_10130102" if html else "__substg1.0_1000001F"] = body.encode(
        "utf-8" if html else "utf-16le"
    )
    return compound_file(streams)


def readable_corpus():
    fixtures = []
    for index, title in enumerate(("Project Alpha", "Quarterly Review"), 1):
        body = f"{title}\nReadable document number {index}."
        for suffix in ("txt", "md", "csv", "json"):
            source = body
            if suffix == "md":
                source = "# " + body
            elif suffix == "csv":
                source = f"title,number\n{title},{index}\n"
            elif suffix == "json":
                source = json.dumps({"title": title, "number": index}, indent=2)
            fixtures.append(Fixture(f"{title}.{suffix}", source.encode(), source))
        document = Document()
        document.add_paragraph(title)
        document.add_paragraph(f"Readable document number {index}.")
        stream = io.BytesIO()
        document.save(stream)
        fixtures.append(Fixture(f"{title}.docx", stream.getvalue(), body))
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(2)).text = body
        stream = io.BytesIO()
        deck.save(stream)
        fixtures.append(Fixture(f"{title}.pptx", stream.getvalue(), "Slide 1\n" + body))
        book = Workbook()
        book.active.title = "Report"
        book.active.append([title])
        book.active.append([f"Readable document number {index}."])
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        fixtures.append(Fixture(f"{title}.xlsx", stream.getvalue(), "Sheet: Report\n" + body))
        stream = io.BytesIO()
        canvas = Canvas(stream)
        for line, y in zip(body.splitlines(), (720, 700), strict=True):
            canvas.drawString(60, y, line)
        canvas.save()
        fixtures.append(Fixture(f"{title}.pdf", stream.getvalue(), "Page 1\n" + body))
    for index, html in enumerate((False, True, False), 1):
        message = EmailMessage()
        message["From"] = "sender@example.test"
        message["To"] = "reader@example.test"
        message["Subject"] = f"Readable email {index}"
        message["Date"] = "Fri, 30 Oct 2026 08:00:00 +0700"
        body = f"Email body {index}.\nSecond paragraph."
        if index == 3:
            body += "\nCafé résumé — Unicode text."
        message.set_content(
            "<p>" + body.replace("\n", "</p><p>") + "</p>" if html else body,
            subtype="html" if html else "plain",
        )
        reference = (
            f"From: sender@example.test\nTo: reader@example.test\nSubject: Readable email {index}\n"
            "Date: Fri, 30 Oct 2026 08:00:00 +0700\n\n" + body
        )
        fixtures.append(Fixture(f"mail-{index}.eml", message.as_bytes(), reference))
        msg_body = "<p>" + body.replace("\n", "</p><p>") + "</p>" if html else body
        fixtures.append(
            Fixture(
                f"mail-{index}.msg",
                msg_fixture(f"Readable email {index}", msg_body, html=html),
                reference,
            )
        )
    for index in (1, 2):
        body = f"Legacy readable report {index}."
        fixtures.append(
            Fixture(f"legacy-{index}.rtf", ("{\\rtf1\\ansi " + body + "}").encode(), body, True)
        )
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            archive.writestr(
                "content.xml",
                '<?xml version="1.0"?><office:document-content '
                'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
                f"<office:body><office:text><text:p>{body}</text:p></office:text></office:body></office:document-content>",
            )
            archive.writestr(
                "META-INF/manifest.xml",
                '<?xml version="1.0"?><manifest:manifest '
                'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
                'manifest:version="1.2">'
                '<manifest:file-entry manifest:full-path="/" '
                'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                '<manifest:file-entry manifest:full-path="content.xml" '
                'manifest:media-type="text/xml"/></manifest:manifest>',
            )
        fixtures.append(Fixture(f"legacy-{index}.odt", stream.getvalue(), body, True))
    return fixtures
