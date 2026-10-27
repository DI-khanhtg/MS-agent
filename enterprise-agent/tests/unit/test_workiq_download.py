import importlib
import io
import logging
from unittest.mock import AsyncMock
from zipfile import ZIP_STORED, ZipFile

import httpx
import pytest

from enterprise_agent.tools.document_reader import DocumentExtractionError, extract_document
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError

PATH = "/drives/drive/items/item"
CAPABILITY = "https://company.sharepoint.com/_layouts/15/download.aspx?tempauth=privateCapability"


async def download(monkeypatch, responses, *, size=5, provider=None, caplog=None):
    module = importlib.import_module("enterprise_agent.tools.workiq.download")
    provider = provider or AsyncMock()
    provider.get_token.return_value = "graph-delegated-token"
    requests = []
    current = importlib.import_module("enterprise_agent.security.identity")
    token = current.set_request_context(current.RequestContext(current.Principal.anonymous()))

    def handler(request):
        requests.append(request)
        response = responses[min(len(requests) - 1, len(responses) - 1)]
        logging.getLogger("httpcore.http11").debug("response headers: %r", response.headers)
        return response

    actual = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: actual(transport=httpx.MockTransport(handler), **kwargs),
    )
    try:
        result = await module.MicrosoftDocumentDownloader(provider).download(PATH, size)
        return result, requests
    finally:
        current.reset_request_context(token)


@pytest.mark.asyncio
async def test_graph_bearer_is_sent_only_to_the_pinned_graph_content_endpoint(monkeypatch):
    content, requests = await download(monkeypatch, [httpx.Response(200, content=b"hello")])
    assert content == b"hello"
    assert str(requests[0].url) == "https://graph.microsoft.com/v1.0" + PATH + "/content"
    assert requests[0].headers["authorization"] == "Bearer graph-delegated-token"


@pytest.mark.asyncio
async def test_graph_redirect_drops_bearer_and_redacts_httpx_and_httpcore_logs(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG):
        content, requests = await download(
            monkeypatch,
            [
                httpx.Response(302, headers={"location": CAPABILITY}),
                httpx.Response(200, content=b"hello"),
            ],
        )
    assert content == b"hello" and len(requests) == 2
    assert "authorization" not in requests[1].headers
    assert "cookie" not in requests[1].headers
    assert "privateCapability" not in caplog.text
    assert "graph-delegated-token" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://company.sharepoint.com/file",
        "https://evil.example/file",
        "https://127.0.0.1/file",
        "https://company.sharepoint.com.evil.example/file",
        "https://user:pass@company.sharepoint.com/file",
        "https://company.sharepoint.com:444/file",
        "https://graph.microsoft.com/v1.0/me",
        "https://company.sharepoint.com/[REDACTED]",
    ],
)
async def test_arbitrary_redirects_are_rejected_before_request(monkeypatch, url):
    module = importlib.import_module("enterprise_agent.tools.workiq.download")
    with pytest.raises(module.MicrosoftDownloadError):
        await download(monkeypatch, [httpx.Response(302, headers={"location": url})])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_missing_graph_consent_or_file_access_is_actionable(monkeypatch, status):
    module = importlib.import_module("enterprise_agent.tools.workiq.download")
    with pytest.raises(module.MicrosoftDownloadError) as error:
        await download(
            monkeypatch,
            [httpx.Response(status, json={"error": {"message": "private provider details"}})],
        )
    assert error.value.code == "document_graph_access_denied"
    assert "Files.Read" in str(error.value)
    assert "private provider details" not in str(error.value)


@pytest.mark.asyncio
async def test_graph_obo_failure_does_not_leak_details(monkeypatch):
    module = importlib.import_module("enterprise_agent.tools.workiq.download")
    provider = AsyncMock()
    provider.get_token.side_effect = WorkIQAuthenticationError("private token details")
    with pytest.raises(module.MicrosoftDownloadError) as error:
        await download(monkeypatch, [httpx.Response(200, content=b"hello")], provider=provider)
    assert error.value.code == "document_graph_consent_required"
    assert "private token details" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,size",
    [
        (httpx.Response(404), 5),
        (httpx.Response(200, content=b"short"), 10),
        (httpx.Response(200, headers={"content-length": "25000000"}), 5),
        (httpx.Response(200, content=b"hello"), 25_000_000),
        (httpx.Response(200, content=b"hello!"), 5),
    ],
)
async def test_failed_truncated_or_oversized_downloads_fail_closed(monkeypatch, response, size):
    module = importlib.import_module("enterprise_agent.tools.workiq.download")
    with pytest.raises(module.MicrosoftDownloadError):
        await download(monkeypatch, [response], size=size)


@pytest.mark.asyncio
async def test_redirect_loops_are_bounded(monkeypatch):
    module = importlib.import_module("enterprise_agent.tools.workiq.download")
    with pytest.raises(module.MicrosoftDownloadError) as error:
        await download(monkeypatch, [httpx.Response(302, headers={"location": CAPABILITY})])
    assert error.value.code == "document_download_redirect_limit"


def test_large_office_file_needs_explicit_cloud_limit(document_files):
    _, content, expected = document_files["pptx"]
    buffer = io.BytesIO(content)
    with ZipFile(buffer, "a", compression=ZIP_STORED) as package:
        package.writestr("ppt/media/unused-test.bin", b"x" * 5_000_001)
    content = buffer.getvalue()
    with pytest.raises(DocumentExtractionError, match="size limit"):
        extract_document(content, ".pptx")
    assert expected in extract_document(content, ".pptx", max_source_bytes=20_000_000).text


@pytest.mark.parametrize("limit", [True, 0, -1, 20_000_001])
def test_cloud_parser_limits_cannot_be_unbounded(limit):
    with pytest.raises(DocumentExtractionError):
        extract_document(b"text", ".txt", max_source_bytes=limit)
