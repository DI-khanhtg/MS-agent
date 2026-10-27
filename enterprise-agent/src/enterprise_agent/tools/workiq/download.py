"""Bounded, delegated Graph downloads above Work IQ's binary limit."""

import logging
import re
from contextvars import ContextVar
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError, WorkIQTokenProvider
from enterprise_agent.tools.workiq.policy import validate_document_path

MAX_CLOUD_BYTES = 20_000_000
_PRIVATE_HTTP = ContextVar("private_microsoft_download", default=False)
_HOST = re.compile(r"(?:[a-z0-9-]+\.sharepoint\.com|[a-z0-9-]+\.files\.1drv\.com)")


class _PrivateHTTPLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _PRIVATE_HTTP.get():
            record.msg, record.args = "Private Microsoft document HTTP request", ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


# Parent filters do not apply to descendant records. DEBUG response headers may
# contain capabilities, so also filter the concrete transport logger names.
for _name in (
    "httpx",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.connection",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_name).addFilter(_PrivateHTTPLogFilter())


class MicrosoftDownloadError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MicrosoftDocumentDownloader:
    def __init__(self, graph_provider: WorkIQTokenProvider | None) -> None:
        self.graph_provider = graph_provider

    async def download(self, path: str, size: int) -> bytes:
        path = validate_document_path(path).removesuffix("/content")
        if type(size) is not int or not 0 < size <= MAX_CLOUD_BYTES:
            raise MicrosoftDownloadError(
                "document_too_large", "Linked cloud files must be nonempty and at most 20 MB."
            )
        if self.graph_provider is None:
            raise MicrosoftDownloadError(
                "document_graph_consent_required",
                "Large documents require delegated Graph Files.Read consent on the backend app.",
            )
        try:
            token = await self.graph_provider.get_token(get_request_context().principal)
        except WorkIQAuthenticationError:
            raise MicrosoftDownloadError(
                "document_graph_consent_required",
                "Configure delegated Graph Files.Read and consent on the backend app, "
                "then sign in again.",
            ) from None
        url = "https://graph.microsoft.com/v1.0" + path + "/content"
        graph_request = True
        private_log = _PRIVATE_HTTP.set(True)
        try:
            async with httpx.AsyncClient(
                timeout=30,
                follow_redirects=False,
                trust_env=False,
                headers={"Accept-Encoding": "identity"},
            ) as client:
                for _ in range(4):
                    if not graph_request:
                        self._validate_redirect(url)
                    headers = {"Authorization": "Bearer " + token} if graph_request else {}
                    client.cookies.clear()
                    async with client.stream("GET", url, headers=headers) as response:
                        if graph_request and response.status_code in (401, 403):
                            raise MicrosoftDownloadError(
                                "document_graph_access_denied",
                                "Graph denied access. Check delegated Files.Read consent on the "
                                "backend app, user file permission and tenant policy.",
                            )
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                raise MicrosoftDownloadError(
                                    "document_download_rejected",
                                    "Microsoft returned an invalid download redirect.",
                                )
                            url = urljoin(url, location)
                            graph_request = False
                            continue
                        if response.status_code != 200:
                            raise MicrosoftDownloadError(
                                "document_download_rejected",
                                "Microsoft rejected the download; "
                                "check access or resolve the file again.",
                            )
                        if (
                            response.headers.get("content-encoding", "identity").casefold()
                            != "identity"
                        ):
                            raise MicrosoftDownloadError(
                                "invalid_document_response", "Unexpected HTTP content encoding."
                            )
                        length = response.headers.get("content-length")
                        if length is not None and (not length.isdecimal() or int(length) != size):
                            raise MicrosoftDownloadError(
                                "invalid_document_response",
                                "File length changed or is invalid; resolve metadata again.",
                            )
                        content = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            if len(content) + len(chunk) > min(size, MAX_CLOUD_BYTES):
                                raise MicrosoftDownloadError(
                                    "document_too_large",
                                    "Download exceeded its declared or allowed size.",
                                )
                            content.extend(chunk)
                        if len(content) != size:
                            raise MicrosoftDownloadError(
                                "invalid_document_response",
                                "Microsoft returned incomplete file content.",
                            )
                        return bytes(content)
                raise MicrosoftDownloadError(
                    "document_download_redirect_limit", "Too many Microsoft download redirects."
                )
        except httpx.TimeoutException:
            raise MicrosoftDownloadError(
                "document_download_timeout", "Microsoft download exceeded its time limit."
            ) from None
        except httpx.HTTPError:
            raise MicrosoftDownloadError(
                "document_download_failed", "Microsoft document download failed."
            ) from None
        finally:
            _PRIVATE_HTTP.reset(private_log)

    @staticmethod
    def _validate_redirect(url: str) -> None:
        try:
            parsed = urlsplit(url) if isinstance(url, str) and len(url) <= 16384 else None
            decoded = unquote(url)
            if (
                parsed is None
                or parsed.scheme != "https"
                or not _HOST.fullmatch(parsed.hostname or "")
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
                or parsed.fragment
                or any(character.isspace() or ord(character) < 32 for character in url)
                or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
                or "[redacted" in decoded.casefold()
                or "[truncated" in decoded.casefold()
            ):
                raise ValueError
        except ValueError:
            raise MicrosoftDownloadError(
                "invalid_download_host", "Microsoft returned an unsupported download destination."
            ) from None
