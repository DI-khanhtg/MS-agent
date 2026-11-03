"""Bounded email extraction for use inside the document sandbox; no attachment saving."""

import re
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser

from enterprise_agent.tools.document_formats import DocumentExtractionError

_BLOCKED = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "svg",
    "math",
    "template",
    "head",
    "form",
}
_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "wbr",
}
_LINES = {"p", "div", "br", "hr", "li", "tr", "h1", "h2", "h3", "h4", "pre", "blockquote"}


class _ReadableHTML(HTMLParser):
    def __init__(self, max_chars, max_depth):
        super().__init__(convert_charrefs=True)
        self.max_chars, self.max_depth = max_chars, max_depth
        self.stack = []
        self.parts, self.size = [], 0

    def add(self, value):
        if self.size + len(value) > self.max_chars:
            raise DocumentExtractionError("Email HTML exceeds the text limit")
        self.parts.append(value)
        self.size += len(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = (
            tag in _BLOCKED
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or "display:none" in (attrs.get("style") or "").replace(" ", "").lower()
        )
        suppressed = hidden or (self.stack and self.stack[-1][1])
        if tag not in _VOID:
            self.stack.append((tag, bool(suppressed)))
            if len(self.stack) > self.max_depth:
                raise DocumentExtractionError("Email HTML nesting exceeds the depth limit")
        if not suppressed and tag in _LINES:
            self.add("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        suppressed = self.stack and self.stack[-1][1]
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not suppressed and tag in _LINES:
            self.add("\n")

    def handle_data(self, value):
        if not self.stack or not self.stack[-1][1]:
            self.add(value)


def html_to_text(value: str, *, max_chars: int, max_depth: int) -> str:
    parser = _ReadableHTML(max_chars, max_depth)
    parser.feed(value)
    parser.close()
    return "\n".join(
        line for raw in "".join(parser.parts).splitlines() if (line := " ".join(raw.split()))
    )


@dataclass(frozen=True)
class EmailExtraction:
    text: str
    headers: dict[str, str]
    warnings: tuple[str, ...] = ("Email attachments and nested messages are not extracted.",)


def _clean_header(value) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()
    if len(value) > 2048:
        raise DocumentExtractionError("Email header exceeds the text limit")
    return value


def _result(headers, body, max_chars):
    lines = [f"{key.title()}: {value}" for key, value in headers.items() if value]
    text = "\n".join([*lines, "", body.strip()]).strip()
    if len(text) > max_chars:
        raise DocumentExtractionError("Extracted email exceeds the text limit")
    return EmailExtraction(text, headers)


def extract_email(
    content: bytes, suffix: str, *, max_chars=100_000, max_depth=32, max_parts=256
) -> EmailExtraction:
    try:
        if suffix == ".eml":
            message = BytesParser(policy=policy.default).parsebytes(content)
            headers = {
                key: _clean_header(message.get(key, ""))
                for key in ("from", "to", "cc", "subject", "date")
            }
            # Validate the complete MIME tree before selecting a body, without recursion.
            pending, count = [(message, 0)], 0
            while pending:
                part, depth = pending.pop()
                count += 1
                if depth > max_depth or count > max_parts:
                    raise DocumentExtractionError("Email MIME nesting or part limit exceeded")
                if part.is_multipart():
                    pending.extend((child, depth + 1) for child in part.get_payload())

            def body(part):
                if (
                    part.get_content_disposition() == "attachment"
                    or part.get_filename()
                    or part.get_content_maintype() == "message"
                ):
                    return ""
                if part.is_multipart():
                    children = list(part.iter_parts())
                    if part.get_content_subtype() == "alternative":
                        children.sort(key=lambda child: child.get_content_type() != "text/plain")
                        return next((value for child in children if (value := body(child))), "")
                    return "\n".join(value for child in children if (value := body(child)))
                if part.get_content_type() not in {"text/plain", "text/html"}:
                    return ""
                value = part.get_content()
                if not isinstance(value, str) or len(value) > max_chars:
                    raise DocumentExtractionError("Email body exceeds the text limit")
                return (
                    html_to_text(value, max_chars=max_chars, max_depth=max_depth)
                    if part.get_content_type() == "text/html"
                    else value.strip()
                )

            return _result(headers, body(message), max_chars)
        if suffix != ".msg":
            raise DocumentExtractionError("Unsupported email format")
        import extract_msg

        with extract_msg.openMsg(content, delayAttachments=True) as message:
            headers = {
                "from": _clean_header(message.sender),
                "to": _clean_header(message.to),
                "cc": _clean_header(message.cc),
                "subject": _clean_header(message.subject),
                "date": _clean_header(message.header.get("date") or message.date),
            }
            body = (message.body or "").rstrip("\0")
            if not body and message.htmlBody:
                body = html_to_text(
                    message.htmlBody.rstrip(b"\0").decode("utf-8", errors="replace"),
                    max_chars=max_chars,
                    max_depth=max_depth,
                )
            return _result(headers, body, max_chars)
    except DocumentExtractionError:
        raise
    except ImportError:
        raise DocumentExtractionError("Email parser dependency is unavailable") from None
    except Exception:
        raise DocumentExtractionError(
            "Email could not be extracted; malformed or unsupported content"
        ) from None
