from email.message import EmailMessage

import pytest

from enterprise_agent.tools.document_email import extract_email, html_to_text


def test_email_headers_body_and_unicode_are_extracted_as_data():
    message = EmailMessage()
    message["From"] = "Sender <sender@example.test>"
    message["To"] = "Reader <reader@example.test>"
    message["Subject"] = "Project café"
    message["Date"] = "Fri, 30 Oct 2026 08:00:00 +0700"
    message.set_content("Readable body.\nSecond paragraph.")
    result = extract_email(message.as_bytes(), ".eml", max_chars=10000)
    assert result.headers["subject"] == "Project café"
    assert "Readable body." in result.text
    assert "sender@example.test" in result.text


def test_html_is_plain_text_without_active_or_hidden_content():
    value = html_to_text(
        "<script>steal()</script><style>hidden</style><p>Hello <b>reader</b></p>"
        '<iframe src="https://evil.test">secret</iframe><img src="https://evil.test/pixel">'
        '<p onclick="steal()">Second &amp; final</p>',
        max_chars=1000,
        max_depth=32,
    )
    assert value == "Hello reader\nSecond & final"


def test_attachments_and_nested_messages_do_not_become_body():
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Report"
    message.set_content("Actual body")
    message.add_attachment(b"DO NOT READ", maintype="text", subtype="plain", filename="secret.txt")
    result = extract_email(message.as_bytes(), ".eml", max_chars=1000)
    assert "Actual body" in result.text
    assert "DO NOT READ" not in result.text


def test_email_nesting_and_html_limits_are_enforced():
    with pytest.raises(ValueError, match="depth|nesting"):
        html_to_text("<div>" * 40 + "body" + "</div>" * 40, max_chars=1000, max_depth=16)


def test_mime_depth_and_part_limits_are_enforced():
    message = EmailMessage()
    message.set_content("Readable body")
    for _ in range(8):
        parent = EmailMessage()
        parent.make_mixed()
        parent.attach(message)
        message = parent
    with pytest.raises(ValueError, match="nesting|part limit"):
        extract_email(message.as_bytes(), ".eml", max_depth=4)
    with pytest.raises(ValueError, match="nesting|part limit"):
        extract_email(message.as_bytes(), ".eml", max_depth=32, max_parts=3)


def test_parser_crash_cannot_echo_private_content(monkeypatch):
    from enterprise_agent.tools import document_email

    class BrokenParser:
        def parsebytes(self, content):
            raise RuntimeError("PRIVATE DOCUMENT CONTENT")

    monkeypatch.setattr(document_email, "BytesParser", lambda **kwargs: BrokenParser())
    with pytest.raises(ValueError) as error:
        extract_email(b"private", ".eml")
    assert "PRIVATE" not in str(error.value)
