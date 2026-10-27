"""Resolve one shared document through delegated Work IQ without indexed retrieval."""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from enterprise_agent.agent.budget import cached_source_read, read_window, register_source_reference
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.recording import record_tool_call
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError, WorkIQTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient
from enterprise_agent.tools.workiq.links import (
    SHARED_ITEM_SELECT,
    SharedLinkError,
    original_shared_url,
    shared_item_path,
    validate_shared_url,
)
from enterprise_agent.tools.workiq.policy import (
    WorkIQPolicyError,
    validate_document_path,
    validate_read_path,
)
from enterprise_agent.tools.workiq.search import search_security_payload


@dataclass(frozen=True)
class ResolvedSharedFile:
    path: str
    filename: str
    web_url: str
    sensitivity_label: dict | str | None
    size_bytes: int | None


class SharedLinkResolver:
    def __init__(
        self,
        client: WorkIQMCPClient,
        provider: WorkIQTokenProvider,
        gate: EgressGate,
        namespace: str,
    ):
        self.client, self.provider, self.gate, self.namespace = client, provider, gate, namespace

    async def resolve(self, url: str = "", link_id: str = "") -> dict:
        try:
            if link_id:
                if url:
                    raise SharedLinkError(
                        "invalid_shared_link", "Use either url or link_id, not both."
                    )
                url = original_shared_url(link_id)
                if url is None:
                    raise SharedLinkError(
                        "link_reference_expired",
                        "Use a link_id from this user turn; "
                        "paste the original Microsoft link again if it expired.",
                    )
            validated = validate_shared_url(url)
            digest = hashlib.sha256(validated.encode()).hexdigest()
            # Never record the full share URL, encoded sharing token or bearer token.
            record_tool_call("workiq_resolve_document_link", {"link_hash": digest[:16]})
            async with read_window("workiq_shared_link"):
                document = await cached_source_read(
                    (self.namespace, "link", digest), lambda: self._fetch(validated)
                )
                reference = register_source_reference(
                    self.namespace,
                    (document.path, document.filename, "shared_link"),
                    document,
                )
                if reference is None:
                    raise SharedLinkError(
                        "link_requires_active_turn",
                        "Resolve the link inside an active authenticated agent turn.",
                    )
                return self._output(
                    {
                        "source": "workiq:shared_link",
                        "document_id": reference,
                        "filename": document.filename,
                        "web_url": document.web_url,
                        "sensitivityLabel": document.sensitivity_label,
                        "content_read": False,
                        "size_bytes": document.size_bytes,
                        "trust": "untrusted_document_data",
                    }
                )
        except SharedLinkError as error:
            return self._output({"error": {"code": error.code, "message": str(error)}})
        except WorkIQAuthenticationError:
            return self._error(
                "link_authentication_failed",
                "Sign in to Microsoft again and verify delegated Work IQ consent.",
            )
        except WorkIQClientError:
            return self._error(
                "link_resolution_unavailable",
                "Work IQ could not resolve this sharing link. Check support for the shared-item "
                "read route, tenant policy and delegated access. "
                "This is not an indexed-search or search-quota failure.",
            )
        except TimeoutError:
            return self._error(
                "link_resolution_timeout",
                "Resolving the shared link exceeded its source time budget; retry in a new turn.",
            )

    async def _fetch(self, url: str) -> ResolvedSharedFile:
        context = get_request_context()
        token = await self.provider.get_token(context.principal)
        path = validate_read_path(shared_item_path(url) + "?$select=" + SHARED_ITEM_SELECT)
        result = await self.client.call_tool("fetch", {"entityUrls": [path]}, access_token=token)
        security = search_security_payload(result)
        decision = self.gate.evaluate(security)
        if not decision.allowed and decision.reason == "prohibited_classification":
            self.gate.enforce(security, source="workiq:shared_link", principal=context.principal)
        payload = result.get("structuredContent", {})
        rows = payload.get("results") if isinstance(payload, dict) else None
        if (
            result.get("isError")
            or not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise SharedLinkError(
                "invalid_link_response", "Work IQ returned invalid shared-item metadata."
            )
        response = rows[0]
        status = response.get("statusCode")
        if status in (401, 403):
            raise SharedLinkError(
                "link_access_denied",
                "Microsoft denied access. Check this user's file permission and application "
                "consent/policy; do not infer that the link is missing.",
            )
        if status in (404, 410):
            raise SharedLinkError(
                "link_not_found",
                "Microsoft could not find this shared item. The link may be invalid, expired "
                "or no longer available to this user.",
            )
        if status == 429:
            raise SharedLinkError(
                "link_throttled",
                "Microsoft throttled link resolution. "
                "Try again later, without repeatedly searching.",
            )
        if status != 200:
            raise SharedLinkError(
                "link_resolution_failed",
                "Microsoft did not return successful shared-item metadata.",
            )
        data = response.get("data")
        if not isinstance(data, dict):
            raise SharedLinkError(
                "invalid_link_response", "Work IQ returned invalid shared-item metadata."
            )
        item = data.get("remoteItem", data)
        if not isinstance(item, dict):
            raise SharedLinkError(
                "invalid_link_response", "Work IQ returned invalid shared-item metadata."
            )
        if "folder" in item:
            raise SharedLinkError(
                "link_not_file",
                "This link points to a folder. Supply the link to the specific PDF or Office file.",
            )
        drive = item.get("parentReference", {})
        drive_id = drive.get("driveId") if isinstance(drive, dict) else None
        item_id = item.get("id")
        name = item.get("name", data.get("name"))
        if (
            not all(
                isinstance(value, str)
                and re.fullmatch(r"[A-Za-z0-9!._~+-]{1,2048}", value)
                and value not in (".", "..")
                for value in (drive_id, item_id)
            )
            or not isinstance(name, str)
            or not name
            or len(name) > 1000
            or not isinstance(item.get("file", data.get("file")), dict)
        ):
            raise SharedLinkError(
                "invalid_link_response",
                "Microsoft did not return a resolved file with its actual name, "
                "drive and item IDs.",
            )
        try:
            resolved_path = validate_document_path(
                f"/drives/{drive_id}/items/{item_id}"
            ).removesuffix("/content")
        except WorkIQPolicyError:
            raise SharedLinkError(
                "invalid_link_response", "Microsoft returned an unsupported file reference."
            ) from None
        canonical = item.get("webUrl", data.get("webUrl", ""))
        try:
            canonical = validate_shared_url(canonical)
            # Do not echo bearer-style sharing links as model-facing citations.
            if urlsplit(canonical).hostname == "1drv.ms" or urlsplit(canonical).path.startswith(
                "/:"
            ):
                canonical = ""
        except SharedLinkError:
            canonical = ""
        size = item.get("size", data.get("size"))
        if size is not None and (type(size) is not int or size < 0):
            raise SharedLinkError(
                "invalid_link_response", "Microsoft returned an invalid file size."
            )
        if size is not None and size > 20_000_000:
            raise SharedLinkError(
                "document_too_large", "Linked documents above 20 MB must be split before reading."
            )
        return ResolvedSharedFile(
            resolved_path,
            name,
            canonical,
            data.get("sensitivityLabel", item.get("sensitivityLabel")),
            size,
        )

    def _output(self, value: dict) -> dict:
        return self.gate.enforce(
            value, source="workiq:shared_link", principal=get_request_context().principal
        )

    def _error(self, code: str, message: str) -> dict:
        return self._output({"error": {"code": code, "message": message}})
