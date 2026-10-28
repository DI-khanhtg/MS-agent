"""Agent-facing Work IQ read-only functions."""

import asyncio
import base64
import binascii
from pathlib import PurePosixPath
from typing import Any

from enterprise_agent.agent.budget import (
    cached_source_read,
    read_window,
    register_source_reference,
    reserve_source_call,
    resolve_source_reference,
)
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.security.redaction import SanitizationStats, sanitize_copy
from enterprise_agent.telemetry.operations import audit_event, identity_hash
from enterprise_agent.tools.document_reader import (
    SUPPORTED,
    DocumentExtractionError,
    ExtractedDocument,
    extract_document,
)
from enterprise_agent.tools.recording import record_tool_call
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError, WorkIQTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient
from enterprise_agent.tools.workiq.download import (
    MAX_CLOUD_BYTES,
    MicrosoftDocumentDownloader,
    MicrosoftDownloadError,
)
from enterprise_agent.tools.workiq.folder import (
    MAX_DEPTH,
    FolderListError,
    MicrosoftFolderLister,
)
from enterprise_agent.tools.workiq.link_resolver import (
    ResolvedSharedFile,
    ResolvedSharedFolder,
    SharedLinkResolver,
)
from enterprise_agent.tools.workiq.policy import (
    WorkIQPolicyError,
    validate_document_path,
    validate_entity_urls,
    validate_path_filter,
    validate_schema_request,
)
from enterprise_agent.tools.workiq.search import (
    DocumentSearchError,
    project_search,
    search_security_payload,
    validate_search,
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
        *,
        graph_token_provider: WorkIQTokenProvider | None = None,
    ) -> None:
        self.client = client
        self.token_provider = token_provider
        self.egress_gate = egress_gate
        self.link_resolver = SharedLinkResolver(
            client, token_provider, egress_gate, f"workiq:{id(self)}"
        )
        self.document_downloader = MicrosoftDocumentDownloader(graph_token_provider)
        self.folder_lister = MicrosoftFolderLister(client, token_provider, graph_token_provider)

    async def workiq_resolve_document_link(self, url: str = "", link_id: str = "") -> dict:
        """Resolve a user-provided Microsoft sharing link without indexed search.

        Prefer link_id from the CURRENT user message; it privately retains the exact URL.
        Alternatively provide an intact organizational SharePoint/OneDrive HTTPS url.
        Pass returned document_id to workiq_read_document to read actual PDF/Office text.
        Works independently of the search quota. Inspect errors; never guess masked URLs.
        """
        return await self.link_resolver.resolve(url, link_id)

    async def workiq_read_document(
        self,
        path: str = "",
        filename: str = "",
        offset: int = 0,
        max_chars: int = 3500,
        document_id: str = "",
    ) -> dict:
        """Read PDF, DOCX, PPTX, XLSX or text from a resolved drive item; follow offsets.

        Prefer document_id returned by workiq_search_documents in THIS turn; it resolves
        private paths even when data protection masks numeric IDs or filenames.
        Alternatively use /me/drive/items/{id} or /drives/{drive-id}/items/{item-id}, optionally
        ending in /content. Resolve IDs with workiq_fetch first; never guess IDs.
        filename must retain the original extension. Use max_chars=14000 for a whole-file
        summary with fewer calls; read text plus continuation, then follow next_offset.
        Download/extraction is reused only in this turn. Inspect errors before retrying.
        Offsets and total_chars index sanitized_text, after whole-document PII masking.
        Omit filename with a document_id; missing extensions are resolved from metadata.
        """
        return await self._read_document(path, filename, offset, max_chars, document_id)

    async def _read_document(
        self,
        path: str = "",
        filename: str = "",
        offset: int = 0,
        max_chars: int = 3500,
        document_id: str = "",
        *,
        record: bool = True,
    ) -> dict:
        try:
            large_file = False
            source_size = None
            if document_id:
                reference = resolve_source_reference(f"workiq:{id(self)}", document_id)
                if not reference or path:
                    raise _DocumentReadError(
                        "invalid_document_reference",
                        "Use a document_id returned by search or link resolution this turn, "
                        "without path. "
                        "Search again for stale, masked or unknown references.",
                    )
                if isinstance(reference, ResolvedSharedFolder):
                    raise _DocumentReadError(
                        "reference_is_folder",
                        "This reference is a folder. Call workiq_list_folder with it, then read "
                        "the listed files by their own document_id.",
                    )
                if isinstance(reference, ResolvedSharedFile):
                    path, original_name = reference.path, reference.filename
                    source_size = reference.size_bytes
                    large_file = (
                        reference.size_bytes is not None and reference.size_bytes > 4_000_000
                    )
                else:
                    path, original_name = reference
                filename = filename or original_name
            validated = validate_document_path(path)
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not 0 <= offset <= 1_000_000
            ):
                raise _DocumentReadError(
                    "invalid_offset", "Use a sanitized text offset from 0 to 1000000."
                )
            if (
                isinstance(max_chars, bool)
                or not isinstance(max_chars, int)
                or not 1 <= max_chars <= 14000
            ):
                raise _DocumentReadError("invalid_read_window", "Use max_chars from 1 to 14000.")
            suffix = PurePosixPath(filename).suffix.lower()
            if suffix not in SUPPORTED and not (document_id and not suffix):
                raise _DocumentReadError(
                    "unsupported_document_type",
                    "Use PDF, DOCX, PPTX, XLSX or UTF-8 text; convert legacy Office files first.",
                )
            if record:
                record_tool_call("workiq_read_document", {"path": validated, "offset": offset})
            async with read_window("workiq_document"):
                if not suffix:
                    filename = await cached_source_read(
                        (id(self), "filename", validated),
                        lambda: self._document_filename(validated.removesuffix("/content")),
                    )
                    suffix = PurePosixPath(filename).suffix.lower()
                    if suffix not in SUPPORTED:
                        raise _DocumentReadError(
                            "unsupported_document_type",
                            "The source filename has an unsupported extension; "
                            "convert legacy Office files first.",
                        )
                document = await cached_source_read(
                    (id(self), "document", validated, suffix, large_file),
                    lambda: self._download_document(validated, suffix, large_file, source_size),
                )
                return self._document_window(document, validated, offset, max_chars)
        except WorkIQPolicyError:
            return self._document_error(
                "invalid_document_path",
                "Resolve the file's actual drive and item IDs with workiq_fetch. Use "
                "/me/drive/items/{id} or /drives/{drive-id}/items/{item-id}, optionally "
                "ending in /content, without query parameters. Do not guess masked IDs.",
            )
        except _DocumentReadError as exc:
            return self._document_error(exc.code, str(exc))
        except MicrosoftDownloadError as exc:
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

    async def _download_document(
        self, validated: str, suffix: str, large_file: bool = False, source_size: int | None = None
    ) -> ExtractedDocument:
        if large_file:
            content = await self.document_downloader.download(
                validated.removesuffix("/content"), source_size
            )
            return await asyncio.to_thread(
                extract_document, content, suffix, max_source_bytes=MAX_CLOUD_BYTES
            )
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
        return await asyncio.to_thread(extract_document, content, suffix)

    async def _document_filename(self, path: str) -> str:
        context = get_request_context()
        token = await self.token_provider.get_token(context.principal)
        result = await self.client.call_tool(
            "fetch", {"entityUrls": [path + "?$select=name"]}, access_token=token
        )
        decision = self.egress_gate.evaluate(result)
        if not decision.allowed and decision.reason == "prohibited_classification":
            self.egress_gate.enforce(result, source="workiq:document", principal=context.principal)
        payload = result.get("structuredContent", {})
        if not isinstance(payload, dict) or result.get("isError"):
            raise _DocumentReadError(
                "invalid_document_metadata", "Work IQ returned invalid file metadata."
            )
        rows = payload.get("results", [payload])
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise _DocumentReadError(
                "invalid_document_metadata", "Work IQ returned invalid file metadata."
            )
        item = rows[0]
        if item.get("statusCode", 200) in (401, 403):
            raise _DocumentReadError(
                "document_access_denied", "Work IQ denied access to file metadata."
            )
        data = item.get("data")
        name = data.get("name") if isinstance(data, dict) else None
        if item.get("statusCode", 200) != 200 or not isinstance(name, str) or not name:
            raise _DocumentReadError(
                "invalid_document_metadata", "Resolve the file's current metadata again."
            )
        return name

    def _document_window(
        self, document: ExtractedDocument, validated: str, offset: int, max_chars: int
    ) -> dict:
        policy = self.egress_gate.policy
        principal = get_request_context().principal
        # Classify the complete original before masking or limiting it. Sanitize before
        # splitting so PII across a chunk boundary cannot leak or expand past that chunk.
        original = self.egress_gate.evaluate(document.text)
        if not original.allowed and original.reason == "prohibited_classification":
            self.egress_gate.enforce(document.text, source="workiq:document", principal=principal)
        stats = SanitizationStats()
        text = sanitize_copy(
            document.text,
            pii_patterns=policy.pii_patterns,
            replacement=policy.replacement,
            max_list_items=policy.max_list_items,
            max_string_chars=1_000_000,
            max_depth=policy.max_depth,
            stats=stats,
        )
        if stats.truncated:
            raise _DocumentReadError(
                "document_redaction_limit", "Redacted text exceeds the read limit."
            )
        if stats.redaction_count:
            audit_event(
                "document_text_redaction",
                source="workiq:document",
                subject_hash=identity_hash(principal.isolation_key),
                redaction_count=stats.redaction_count,
            )
        segment = min(3500, policy.max_string_chars)
        characters = min(max_chars, segment * (policy.max_list_items + 1))
        # Leave room for JSON, extraction metadata and redaction expansion. UTF-8 bytes
        # can exceed character counts; offsets always advance by actual retained text.
        byte_limit = min(36000, policy.max_payload_bytes // 2)
        window = (
            text[offset : offset + characters]
            .encode("utf-8")[:byte_limit]
            .decode("utf-8", errors="ignore")
        )
        chunks = []
        retained = 0
        while retained < len(window) and len(chunks) < policy.max_list_items + 1:
            chunk = window[retained : retained + segment]
            while chunk:
                decision = self.egress_gate.evaluate(chunk)
                if not decision.truncated and decision.reason != "payload_too_large":
                    break
                chunk = chunk[: len(chunk) // 2]
            if not chunk:
                raise _DocumentReadError(
                    "document_window_too_small", "Egress limits cannot represent this text segment."
                )
            chunks.append(chunk)
            retained += len(chunk)

        def payload():
            end = offset + sum(map(len, chunks))
            return {
                "source": "workiq:" + validated,
                "text": chunks[0] if chunks else "",
                "continuation": chunks[1:],
                "total_chars": len(text),
                "offset_basis": "sanitized_text",
                "redactions_before_chunking": stats.redaction_count,
                "next_offset": end if end < len(text) else None,
                "empty": not text.strip(),
                "extraction": document.metadata,
                "trust": "untrusted_document_data",
            }

        result = payload()
        # Redaction can expand data beyond the byte cap too. Trim the last raw
        # segment and recompute offsets; never advance over text the model didn't get.
        while self.egress_gate.evaluate(result).reason == "payload_too_large" and chunks:
            tail = chunks.pop()
            if len(tail) > 1:
                chunks.append(tail[: len(tail) // 2])
            result = payload()
        if text[offset:] and not chunks:
            raise _DocumentReadError(
                "document_window_too_small", "Egress limits cannot represent this text segment."
            )
        output = self.egress_gate.enforce(
            result,
            source="workiq:document",
            principal=principal,
        )
        if stats.redaction_count:
            output["_egress"]["redactions"] += stats.redaction_count
            output["_egress"]["classifications"] = sorted(
                set(output["_egress"]["classifications"]) | {"pii"}
            )
        return output

    async def workiq_list_folder(self, folder_id: str, max_depth: int = 3, offset: int = 0) -> dict:
        """List the files AND subfolders inside a Microsoft 365 folder, recursively.

        folder_id comes from workiq_resolve_document_link (item_type=folder) or from a folder
        entry returned by this tool in THIS turn. max_depth is the number of folder levels
        listed (1 = only this folder, up to 5). Entries come in pages of 25: follow next_offset
        until it is null. Each file has a document_id for workiq_read_documents or
        workiq_read_document; unsupported or oversized files have readable=false.
        Folders with expanded=false were not opened; list them with their own document_id.
        Inspect truncated/truncation_reasons and report incomplete coverage honestly.
        """
        try:
            reference = resolve_source_reference(f"workiq:{id(self)}", folder_id)
            if not isinstance(reference, ResolvedSharedFolder):
                raise FolderListError(
                    "invalid_folder_reference",
                    "Use a folder_id returned by link resolution or folder listing this turn.",
                )
            if (
                isinstance(max_depth, bool)
                or not isinstance(max_depth, int)
                or not 1 <= max_depth <= MAX_DEPTH
            ):
                raise FolderListError("invalid_depth", f"Use max_depth from 1 to {MAX_DEPTH}.")
            if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1000:
                raise FolderListError("invalid_offset", "Use an offset from 0 to 1000.")
            record_tool_call(
                "workiq_list_folder",
                {"path": reference.path, "max_depth": max_depth, "offset": offset},
            )
            async with read_window("workiq_folder"):
                listing = await cached_source_read(
                    (id(self), "folder", reference.path, max_depth),
                    lambda: self.folder_lister.list_tree(reference.path, max_depth=max_depth),
                )
                return self._folder_output(reference, listing, offset)
        except FolderListError as exc:
            return self._folder_error(exc.code, str(exc))
        except WorkIQAuthenticationError:
            return self._folder_error(
                "folder_authentication_failed",
                "Microsoft delegated authentication failed. Sign in again; check Work IQ consent.",
            )
        except WorkIQClientError:
            return self._folder_error(
                "folder_list_failed",
                "Work IQ could not list this folder. Verify it still exists and is accessible.",
            )
        except TimeoutError:
            return self._folder_error(
                "folder_list_timeout",
                "Listing exceeded the source time budget; report incomplete coverage.",
            )

    def _folder_output(self, root: ResolvedSharedFolder, listing: Any, offset: int) -> dict:
        page = listing.entries[offset : offset + 25]
        namespace = f"workiq:{id(self)}"
        entries = []
        for entry in page:
            item: dict[str, Any] = {
                "path": entry.relative_path,
                "type": "folder" if entry.is_folder else "file",
            }
            if entry.is_folder:
                item["expanded"] = entry.expanded
                value: Any = ResolvedSharedFolder(entry.path, entry.name, "", None)
            else:
                suffix = PurePosixPath(entry.name).suffix.lower()
                item["size_bytes"] = entry.size_bytes
                item["readable"] = suffix in SUPPORTED and (
                    entry.size_bytes is None or entry.size_bytes <= MAX_CLOUD_BYTES
                )
                value = ResolvedSharedFile(entry.path, entry.name, "", None, entry.size_bytes)
            reference = register_source_reference(
                namespace, (entry.path, entry.name, "folder_entry"), value
            )
            if reference is None:
                raise FolderListError(
                    "folder_requires_active_turn", "List folders inside an active agent turn."
                )
            item["document_id"] = reference
            entries.append(item)
        end = offset + len(page)
        files = sum(1 for entry in listing.entries if not entry.is_folder)
        return self.egress_gate.enforce(
            {
                "source": "workiq:folder",
                "folder_name": root.filename,
                "total_entries": len(listing.entries),
                "total_files": files,
                "total_folders": len(listing.entries) - files,
                "offset": offset,
                "entries": entries,
                "next_offset": end if end < len(listing.entries) else None,
                "truncated": bool(listing.reasons),
                "truncation_reasons": sorted(listing.reasons),
                "incomplete_folders": listing.incomplete_folders[:10],
                "coverage": "Names and sizes only; file content has not been read.",
                "trust": "untrusted_document_data",
            },
            source="workiq:folder",
            principal=get_request_context().principal,
        )

    def _folder_error(self, code: str, message: str) -> dict:
        return self.egress_gate.enforce(
            {
                "source": "workiq:folder",
                "error": {"code": code, "message": message},
                "trust": "untrusted_document_data",
            },
            source="workiq:folder",
            principal=get_request_context().principal,
        )

    async def workiq_read_documents(
        self, document_ids: list[str], max_chars_each: int = 3000
    ) -> dict:
        """Read the beginning of several listed files in one call (up to 6 per call).

        Use document_id values from workiq_list_folder (readable=true) or search/link results
        of THIS turn. Returns one result per file with text, total_chars and next_offset; use
        workiq_read_document(document_id, offset=next_offset) to continue one file. Prefer
        this over many single reads when summarizing or comparing a folder. A failed file
        reports its own error and does not stop the others. Select relevant files first.
        """
        if (
            not isinstance(document_ids, list)
            or not 1 <= len(document_ids) <= 6
            or not all(isinstance(value, str) and value for value in document_ids)
            or isinstance(max_chars_each, bool)
            or not isinstance(max_chars_each, int)
            or not 500 <= max_chars_each <= 8000
        ):
            return self._document_error(
                "invalid_batch_request",
                "Send 1 to 6 document_id strings and max_chars_each from 500 to 8000.",
            )
        unique = list(dict.fromkeys(document_ids))
        record_tool_call("workiq_read_documents", {"count": len(unique)})
        semaphore = asyncio.Semaphore(3)

        async def read(document_id: str) -> dict:
            async with semaphore:
                return await self._read_document(
                    document_id=document_id, max_chars=max_chars_each, record=False
                )

        results = await asyncio.gather(*(read(value) for value in unique))
        return {
            "source": "workiq:documents",
            "documents": [
                {"document_id": value, "result": result}
                for value, result in zip(unique, results, strict=True)
            ],
            "trust": "untrusted_document_data",
        }

    async def workiq_search_documents(self, query: str, limit: int = 5) -> dict:
        """Find personal AND shared OneDrive/SharePoint documents with indexed search.

        Search the specific filename/topic and owner once, before listing folders. Returns
        bounded source excerpts/references, not a complete file or exhaustive inventory.
        Resolve missing filename/IDs with workiq_fetch; read actual content with
        workiq_read_document. No recursive scan, OCR or embedding. May use Copilot Credits.
        """
        try:
            normalized = validate_search(query, limit)
            record_tool_call("workiq_search_documents", {"query": normalized, "limit": limit})
            async with read_window("workiq_search"):
                result = await cached_source_read(
                    (id(self), "search", normalized), lambda: self._search_documents(normalized)
                )
                # Projection never mutates a cached value.
                documents = []
                for hit in result["documents"][:limit]:
                    hit = dict(hit)
                    path = hit.get("document_path")
                    if path:
                        name = hit.get("resourceMetadata", {}).get("document", {}).get("title", "")
                        reference = register_source_reference(
                            f"workiq:{id(self)}", (path, name), (path, name)
                        )
                        if reference:
                            hit["document_id"] = reference
                    documents.append(hit)
                result = {**result, "documents": documents}
                result["returned_count"] = len(result["documents"])
                result["truncated"] |= result["returned_count"] < result["result_count"]
                return self._search_output(result)
        except DocumentSearchError as exc:
            return self._search_error(exc.code, str(exc))
        except WorkIQAuthenticationError:
            return self._search_error(
                "search_authentication_failed",
                "Sign in to Microsoft again; check delegated Work IQ consent if it persists.",
            )
        except WorkIQClientError:
            return self._search_error(
                "search_unavailable",
                "Indexed document search is unavailable. Check retrieve tool availability, "
                "Work IQ consent, user access, tenant policy and Copilot Credits billing. "
                "This is not evidence that the file is absent. "
                "Do not retry the same search repeatedly.",
            )
        except TimeoutError:
            return self._search_error(
                "search_timeout", "Search exceeded its time budget; report incomplete coverage."
            )

    async def _search_documents(self, query: str) -> dict:
        if not reserve_source_call((id(self), "search"), 2):
            raise DocumentSearchError(
                "search_budget_exhausted",
                "The two indexed searches allowed in this turn are used. Report incomplete "
                "coverage and ask for a specific file reference or continue in a new turn.",
            )
        context = get_request_context()
        token = await self.token_provider.get_token(context.principal)
        result = await self.client.call_tool(
            "retrieve",
            {
                "query": query,
                "strategy": "grounding",
                "capabilities": [{"name": "OneDriveAndSharePoint"}],
            },
            access_token=token,
        )
        # Inspect all security metadata, including fields/hits omitted from the compact result.
        security = search_security_payload(result)
        decision = self.egress_gate.evaluate(security)
        if not decision.allowed and decision.reason == "prohibited_classification":
            self.egress_gate.enforce(
                security, source="workiq:document_search", principal=context.principal
            )
        return project_search(result)

    def _search_error(self, code: str, message: str) -> dict:
        return self._search_output(
            {"error": {"code": code, "message": message}, "exhaustive": False}
        )

    def _search_output(self, payload: dict) -> dict:
        return self.egress_gate.enforce(
            payload, source="workiq:document_search", principal=get_request_context().principal
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
