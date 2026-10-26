"""Agent-facing Work IQ read-only functions."""

import asyncio
import base64
import binascii
from pathlib import PurePosixPath
from typing import Any

from enterprise_agent.agent.budget import read_window
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.document_reader import (
    SUPPORTED,
    DocumentExtractionError,
    extract_document,
)
from enterprise_agent.tools.recording import record_tool_call
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError, WorkIQTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient
from enterprise_agent.tools.workiq.policy import (
    WorkIQPolicyError,
    validate_document_path,
    validate_entity_urls,
    validate_path_filter,
    validate_schema_request,
)


class _DocumentReadError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkIQReadTools:
    def __init__(
        self,
        client: WorkIQMCPClient,
        token_provider: WorkIQTokenProvider,
        egress_gate: EgressGate,
    ) -> None:
        self.client = client
        self.token_provider = token_provider
        self.egress_gate = egress_gate

    async def workiq_read_document(self, path: str, filename: str, offset: int = 0) -> dict:
        """Read PDF, DOCX, PPTX, XLSX or text from a resolved drive item; follow offsets.

        Use /me/drive/items/{id} or /drives/{drive-id}/items/{item-id}, optionally
        ending in /content. Resolve IDs with workiq_fetch first; never guess IDs.
        filename must retain the original extension. Inspect error codes before retrying.
        """
        try:
            validated = validate_document_path(path)
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not 0 <= offset <= 100_000
            ):
                raise _DocumentReadError("invalid_offset", "Use a text offset from 0 to 100000.")
            suffix = PurePosixPath(filename).suffix.lower()
            if suffix not in SUPPORTED:
                raise _DocumentReadError(
                    "unsupported_document_type",
                    "Use PDF, DOCX, PPTX, XLSX or UTF-8 text; convert legacy Office files first.",
                )
            record_tool_call("workiq_read_document", {"path": validated, "offset": offset})
            async with read_window("workiq_document"):
                return await self._read_document(validated, suffix, offset)
        except WorkIQPolicyError:
            return self._document_error(
                "invalid_document_path",
                "Resolve the file's actual drive and item IDs with workiq_fetch. Use "
                "/me/drive/items/{id} or /drives/{drive-id}/items/{item-id}, optionally "
                "ending in /content, without query parameters. Do not guess masked IDs.",
            )
        except _DocumentReadError as exc:
            return self._document_error(exc.code, str(exc))
        except DocumentExtractionError as exc:
            # The shared reader deliberately emits fixed diagnostics without document data.
            return self._document_error("document_extraction_failed", str(exc))
        except WorkIQAuthenticationError:
            return self._document_error(
                "document_authentication_failed",
                "Microsoft delegated authentication failed. Sign in again; if it persists, "
                "check backend Work IQ consent and credentials.",
            )
        except WorkIQClientError:
            return self._document_error(
                "document_download_failed",
                "Work IQ could not download this file. Verify the resolved item still exists "
                "and the signed-in user can open it; retry if the service is unavailable.",
            )
        except TimeoutError:
            return self._document_error(
                "document_read_timeout",
                "The document read exceeded the source time budget. Report incomplete coverage "
                "and retry this file in a new request.",
            )

    def _document_error(self, code: str, message: str) -> dict:
        # Known safe diagnostics reach the model instead of the framework's generic exception.
        return self.egress_gate.enforce(
            {
                "source": "workiq:document",
                "error": {"code": code, "message": message},
                "trust": "untrusted_document_data",
            },
            source="workiq:document",
            principal=get_request_context().principal,
        )

    async def _read_document(self, validated: str, suffix: str, offset: int) -> dict:
        context = get_request_context()
        token = await self.token_provider.get_token(context.principal)
        result = await self.client.call_tool("fetch_blob", {"path": validated}, access_token=token)
        payload = result.get("structuredContent", {})
        if not isinstance(payload, dict):
            raise _DocumentReadError(
                "invalid_document_response", "Work IQ returned invalid document metadata."
            )
        if payload.get("statusCode", 200) != 200:
            status = payload["statusCode"]
            if status in (401, 403):
                raise _DocumentReadError(
                    "document_access_denied",
                    "Work IQ denied access to this file. Check the user's file permissions "
                    "and delegated Work IQ consent.",
                )
            if status == 404:
                raise _DocumentReadError(
                    "document_not_found",
                    "The file was not found. Resolve its current drive and item IDs again.",
                )
            raise _DocumentReadError(
                "document_download_failed", "Work IQ did not return successful document content."
            )
        encoded = payload.get("base64Content", "")
        if not isinstance(encoded, str) or len(encoded) > 7_000_000:
            raise _DocumentReadError(
                "invalid_document_response",
                "Document payload is invalid or exceeds the size limit.",
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise _DocumentReadError(
                "invalid_document_response", "Work IQ returned invalid base64 document content."
            ) from None
        if not content or len(content) > 5_000_000:
            raise _DocumentReadError(
                "invalid_document_response",
                "Document content is missing or exceeds the 5 MB size limit.",
            )
        if "sizeBytes" in payload and payload["sizeBytes"] != len(content):
            raise _DocumentReadError(
                "invalid_document_response",
                "Work IQ returned incomplete document content; retry the download.",
            )
        document = await asyncio.to_thread(extract_document, content, suffix)
        text = document.text
        return self.egress_gate.enforce(
            {
                "source": "workiq:" + validated,
                "text": text[offset : offset + 3500],
                "total_chars": len(text),
                "next_offset": offset + 3500 if offset + 3500 < len(text) else None,
                "empty": not text.strip(),
                "extraction": document.metadata,
                "trust": "untrusted_document_data",
            },
            source="workiq:document",
            principal=context.principal,
        )

    async def workiq_fetch(self, entity_urls: list[str]) -> dict[str, Any]:
        """Read Microsoft 365 entities from allowlisted relative paths.

        Examples include /me/messages for email, /me/events for calendar,
        /me/chats/{id}/messages for Teams, /me/drive/root/children for OneDrive,
        /me/people for people, and /sites/{id}/drive for SharePoint. Add a bounded
        $top and narrow $select whenever possible. Multiple paths can be fetched together.
        """
        validated_urls = validate_entity_urls(entity_urls)
        record_tool_call("workiq_fetch", {"entity_urls": validated_urls})
        return await self._call("fetch", {"entityUrls": validated_urls})

    async def workiq_get_schema(
        self,
        path: str,
        operation_type: str = "fetch",
    ) -> dict[str, Any]:
        """Get a Work IQ JSON schema for an allowlisted read operation."""
        validated_path, validated_operation = validate_schema_request(path, operation_type)
        arguments = {
            "path": validated_path,
            "operationType": validated_operation,
            "format": "jsonschema",
        }
        record_tool_call("workiq_get_schema", arguments)
        return await self._call("get_schema", arguments)

    async def workiq_search_paths(self, filter: str) -> dict[str, Any]:
        """Discover Work IQ resource paths by a short prefix or regular-expression filter."""
        validated_filter = validate_path_filter(filter)
        arguments = {"filter": validated_filter}
        record_tool_call("workiq_search_paths", arguments)
        return await self._call("search_paths", arguments)

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with read_window("workiq"):
            context = get_request_context()
            token = await self.token_provider.get_token(context.principal)
            result = await self.client.call_tool(name, arguments, access_token=token)
            return self.egress_gate.enforce(
                result,
                source=f"workiq:{name}",
                principal=context.principal,
            )
