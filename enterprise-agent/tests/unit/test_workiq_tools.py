import base64
from typing import Any
from unittest.mock import AsyncMock

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError, EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.registry import create_default_registry
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError
from enterprise_agent.tools.workiq.client import WorkIQClientError
from enterprise_agent.tools.workiq.tools import WorkIQReadTools


class FakeTokenProvider:
    def __init__(self) -> None:
        self.principal: Principal | None = None

    async def get_token(self, principal: Principal) -> str:
        self.principal = principal
        return "workiq-token"


class FakeWorkIQClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        access_token: str,
    ) -> dict[str, Any]:
        self.calls.append((name, arguments, access_token))
        return self.result


def principal() -> Principal:
    return Principal(
        tenant_id="tenant-a",
        object_id="user-a",
        subject="subject-a",
        username="user@example.com",
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token="incoming-token",
    )


def build_tools(
    result: dict[str, Any],
) -> tuple[WorkIQReadTools, FakeWorkIQClient, FakeTokenProvider]:
    settings = Settings(_env_file=None)
    client = FakeWorkIQClient(result)
    token_provider = FakeTokenProvider()
    tools = WorkIQReadTools(
        client,  # type: ignore[arg-type]
        token_provider,
        EgressGate.from_yaml(settings.egress_policy_path),
    )
    return tools, client, token_provider


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pdf", "docx", "pptx", "xlsx"])
async def test_office_file_content_is_extracted_from_workiq(document_files, kind):
    filename, content, expected = document_files[kind]
    tools, client, provider = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(content).decode()}}
    )
    current = principal()
    context = set_request_context(RequestContext(current))
    try:
        result = await tools.workiq_read_document("/drives/drive/items/item/content", filename)
    finally:
        reset_request_context(context)
    data = result["data"]
    assert expected in data["text"]
    assert data["extraction"]["format"] == kind
    assert data["extraction"]["coverage"] == "text_only"
    assert provider.principal is current
    assert client.calls == [
        ("fetch_blob", {"path": "/drives/drive/items/item/content"}, "workiq-token")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/me/drive/items/item",
        "/me/drive/items/item/",
        "/me/drive/items/content",
        "/me/drive/items/opaque%2Bitem",
        "/drives/drive/items/item",
        "/users/user/drive/items/item",
        "/sites/site/drive/items/item",
    ],
)
async def test_resolved_onedrive_item_is_downloaded_and_pdf_extracted(document_files, path):
    _, content, expected = document_files["pdf"]
    tools, client, _ = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(content).decode()}}
    )
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_read_document(path, "[REDACTED].pdf")
    finally:
        reset_request_context(context)
    assert expected in result["data"]["text"]
    assert result["data"]["extraction"]["format"] == "pdf"
    assert client.calls[0][1]["path"] == path.rstrip("/") + "/content"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/me/drive/items",
        "/me/drive/root/children",
        "/me/messages/item/content",
        "https://evil.example/file",
        "/me/drive/items/%2e%2e",
        "/me/drive/items/item?$skip=1",
        "/me/drive/items/[REDACTED]",
    ],
)
async def test_invalid_document_path_has_actionable_error_without_network(path):
    tools, client, provider = build_tools({})
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_read_document(path, "notes.pdf")
    finally:
        reset_request_context(context)
    assert result["data"]["error"]["code"] == "invalid_document_path"
    assert "resolve" in result["data"]["error"]["message"].lower()
    assert client.calls == []
    assert provider.principal is None


@pytest.mark.asyncio
async def test_corrupt_pdf_returns_safe_extraction_error():
    tools, _, _ = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(b"not a PDF").decode()}}
    )
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_read_document("/me/drive/items/item", "notes.pdf")
    finally:
        reset_request_context(context)
    assert result["data"]["error"]["code"] == "document_extraction_failed"
    assert "re-export" in result["data"]["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "error_code"),
    [
        (WorkIQAuthenticationError("private provider details"), "document_authentication_failed"),
        (WorkIQClientError("private provider details"), "document_download_failed"),
        (TimeoutError("private provider details"), "document_read_timeout"),
    ],
)
async def test_document_upstream_errors_are_actionable_and_do_not_leak_details(failure, error_code):
    tools, _, provider = build_tools({})
    provider.get_token = AsyncMock(side_effect=failure)
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_read_document("/me/drive/items/item", "notes.pdf")
    finally:
        reset_request_context(context)
    assert result["data"]["error"]["code"] == error_code
    assert "private provider details" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra", "error_code"),
    [
        ({"statusCode": 403}, "document_access_denied"),
        ({"statusCode": 404}, "document_not_found"),
        ({"statusCode": 500}, "document_download_failed"),
        ({"sizeBytes": 100}, "invalid_document_response"),
        ({"base64Content": "!invalid"}, "invalid_document_response"),
        ({"base64Content": ""}, "invalid_document_response"),
    ],
)
async def test_document_rejects_failed_or_incomplete_blob_responses(extra, error_code):
    tools, _, _ = build_tools(
        {
            "structuredContent": {
                "base64Content": base64.b64encode(b"actual text").decode(),
                **extra,
            }
        }
    )
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_read_document("/drives/drive/items/item/content", "notes.txt")
    finally:
        reset_request_context(context)
    assert result["data"]["error"]["code"] == error_code
    assert "text" not in result["data"]


@pytest.mark.asyncio
async def test_document_content_extraction_is_bounded_and_egress_checked():
    text = "person@example.com\n" + "x" * 4000
    tools, client, _ = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(text.encode()).decode()}}
    )
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_read_document("/drives/drive/items/item/content", "note.md")
        invalid = await tools.workiq_read_document("https://evil.example/file", "note.md")
    finally:
        reset_request_context(context)
    assert client.calls == [
        ("fetch_blob", {"path": "/drives/drive/items/item/content"}, "workiq-token")
    ]
    assert "person@example.com" not in result["data"]["text"]
    assert result["data"]["next_offset"] == 3500
    assert result["data"]["trust"] == "untrusted_document_data"
    assert invalid["data"]["error"]["code"] == "invalid_document_path"


@pytest.mark.asyncio
async def test_fetch_uses_verified_identity_obo_and_egress() -> None:
    tools, client, token_provider = build_tools(
        {"structuredContent": {"from": "sender@example.com", "subject": "Update"}}
    )
    current = principal()
    context_token = set_request_context(RequestContext(current))
    try:
        result = await tools.workiq_fetch(["/me/messages?$top=5"])
    finally:
        reset_request_context(context_token)

    assert token_provider.principal is current
    assert client.calls == [("fetch", {"entityUrls": ["/me/messages?$top=5"]}, "workiq-token")]
    assert result["data"]["structuredContent"]["from"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_schema_tool_forces_read_operation() -> None:
    tools, client, _ = build_tools({"content": []})
    context_token = set_request_context(RequestContext(principal()))
    try:
        await tools.workiq_get_schema("/me/messages")
    finally:
        reset_request_context(context_token)

    assert client.calls[0][0] == "get_schema"
    assert client.calls[0][1]["operationType"] == "fetch"


@pytest.mark.asyncio
async def test_secret_in_workiq_result_is_blocked() -> None:
    tools, _, _ = build_tools({"structuredContent": {"access_token": "secret-value"}})
    context_token = set_request_context(RequestContext(principal()))
    try:
        with pytest.raises(EgressBlockedError):
            await tools.workiq_fetch(["/me/messages?$top=1"])
    finally:
        reset_request_context(context_token)


def test_registry_exposes_workiq_read_without_write_tools() -> None:
    tools, _, _ = build_tools({"content": []})
    registry = create_default_registry(tools)

    workiq_names = {tool.__name__ for tool in registry.tools_for_groups({"workiq_read"})}

    assert workiq_names == {
        "workiq_fetch",
        "workiq_get_schema",
        "workiq_search_paths",
        "workiq_read_document",
        "workiq_read_documents",
        "workiq_list_folder",
        "workiq_search_documents",
        "workiq_resolve_document_link",
    }
    assert not workiq_names & {"create_entity", "update_entity", "delete_entity", "do_action"}


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["tenant", "user", "assertion"])
async def test_shared_reference_cannot_be_reused_by_a_different_identity_or_token(changed):
    from dataclasses import replace

    from enterprise_agent.agent.budget import turn_budget

    tools, client, _ = build_tools(retrieval_result(count=1))
    current = principal()
    changes = {
        "tenant": {"tenant_id": "tenant-b"},
        "user": {"object_id": "user-b"},
        "assertion": {"access_token": "new-assertion"},
    }
    context = set_request_context(RequestContext(current))
    try:
        with turn_budget(30, 1):
            result = await tools.workiq_search_documents("shared slides")
            reference = result["data"]["documents"][0]["document_id"]
            other = set_request_context(RequestContext(replace(current, **changes[changed])))
            try:
                result = await tools.workiq_read_document(document_id=reference)
            finally:
                reset_request_context(other)
            assert result["data"]["error"]["code"] == "invalid_document_reference"
            assert len(client.calls) == 1
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (WorkIQAuthenticationError("private-provider-details"), "search_authentication_failed"),
        (WorkIQClientError("private-provider-details"), "search_unavailable"),
        (TimeoutError("private-provider-details"), "search_timeout"),
    ],
)
async def test_search_errors_are_actionable_without_leaking_provider_details(failure, code):
    tools, _, provider = build_tools({})
    provider.get_token = AsyncMock(side_effect=failure)
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_search_documents("shared slides")
        assert result["data"]["error"]["code"] == code
        assert result["data"]["exhaustive"] is False
        assert "private-provider-details" not in str(result)
    finally:
        reset_request_context(context)


def retrieval_result(*, count=2, stopped="completed", markdown="Source evidence [^hit-0]"):
    return {
        "structuredContent": {
            "application/vnd.ms-workiq.retrieval": {
                "markdown": markdown,
                "retrievalHits": [
                    {
                        "id": f"hit-{index}",
                        "driveId": "other-owner-drive",
                        "itemId": f"item-{index}",
                        "entityUrl": f"/drives/other-owner-drive/items/item-{index}",
                        "webUrl": f"https://company.sharepoint.com/slides-{index}.pptx",
                        "resourceMetadata": {
                            "document": {
                                "title": f"slides-{index}.pptx",
                                "authorName": "Shared Owner",
                            }
                        },
                        "sensitivityLabel": {"displayName": "Internal"},
                    }
                    for index in range(count)
                ],
                "resultCount": count,
                "stoppedReason": stopped,
            }
        }
    }


@pytest.mark.asyncio
async def test_shared_document_search_is_indexed_scoped_bounded_and_egress_checked():
    tools, client, provider = build_tools(
        retrieval_result(count=8, markdown="person@example.com " + "x" * 8000)
    )
    current = principal()
    context = set_request_context(RequestContext(current))
    try:
        result = await tools.workiq_search_documents(
            "3d-reconstruction-evolution by Shared Owner", 3
        )
    finally:
        reset_request_context(context)
    data = result["data"]
    assert provider.principal is current
    assert client.calls == [
        (
            "retrieve",
            {
                "query": "3d-reconstruction-evolution by Shared Owner",
                "strategy": "grounding",
                "capabilities": [{"name": "OneDriveAndSharePoint"}],
            },
            "workiq-token",
        )
    ]
    assert len(data["documents"]) == 3
    assert data["documents"][0]["document_path"] == "/drives/other-owner-drive/items/item-0"
    assert data["documents"][0]["sensitivityLabel"]["displayName"] == "Internal"
    assert data["returned_count"] == 3
    assert data["result_count"] == 8
    assert data["truncated"] is True
    assert data["exhaustive"] is False
    assert len(data["grounding"]) <= 3500
    assert "person@example.com" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,limit", [("", 5), ("x" * 1001, 5), ("file", 0), ("file", 11), ("file", True)]
)
async def test_invalid_search_does_not_make_a_network_request(query, limit):
    tools, client, _ = build_tools({})
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_search_documents(query, limit)
    finally:
        reset_request_context(context)
    assert result["data"]["error"]["code"] == "invalid_search"
    assert not client.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("stopped", ["error", "cancelled", "unknown-state"])
async def test_noncompleted_retrieval_is_not_a_successful_empty_search(stopped):
    tools, _, _ = build_tools(retrieval_result(count=0, stopped=stopped))
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_search_documents("shared slides")
    finally:
        reset_request_context(context)
    assert result["data"]["error"]["code"] == "search_incomplete"
    assert result["data"]["exhaustive"] is False


@pytest.mark.asyncio
async def test_empty_retrieval_is_not_proof_of_missing_user_permissions():
    tools, _, _ = build_tools(retrieval_result(count=0, markdown=""))
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_search_documents("shared slides")
    finally:
        reset_request_context(context)
    assert result["data"]["documents"] == []
    assert result["data"]["exhaustive"] is False
    assert "error" not in result["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {},
        {"isError": True},
        {
            "structuredContent": {
                "application/vnd.ms-workiq.retrieval": {
                    "stoppedReason": "completed",
                    "retrievalHits": "invalid",
                }
            }
        },
    ],
)
async def test_invalid_retrieval_returns_safe_diagnostic(result):
    tools, _, _ = build_tools(result)
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_search_documents("shared slides")
    finally:
        reset_request_context(context)
    assert "error" in result["data"]


@pytest.mark.asyncio
async def test_search_inspects_security_metadata_before_discarding_fields_or_hits():
    response = retrieval_result(count=8)
    response["structuredContent"]["application/vnd.ms-workiq.retrieval"]["retrievalHits"][-1][
        "access_token"
    ] = "private-secret"
    tools, _, _ = build_tools(response)
    context = set_request_context(RequestContext(principal()))
    try:
        with pytest.raises(EgressBlockedError):
            await tools.workiq_search_documents("shared slides", 1)
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_document_search_and_read_are_cached_only_within_the_current_turn(monkeypatch):
    from enterprise_agent.agent.budget import turn_budget
    from enterprise_agent.tools.workiq import tools as module

    tools, client, _ = build_tools(retrieval_result())
    extraction_calls = []
    original = module.extract_document

    def extract(*args):
        extraction_calls.append(True)
        return original(*args)

    monkeypatch.setattr(module, "extract_document", extract)
    text = "a" * 20000
    context = set_request_context(RequestContext(principal()))
    try:
        with turn_budget(30, 1):
            await tools.workiq_search_documents(" shared   slides ", 1)
            await tools.workiq_search_documents("shared slides", 5)
            assert len(client.calls) == 1
            client.result = {
                "structuredContent": {"base64Content": base64.b64encode(text.encode()).decode()}
            }
            first = await tools.workiq_read_document(
                "/drives/other/items/slide", "slides.txt", max_chars=14000
            )
            second = await tools.workiq_read_document(
                "/drives/other/items/slide/content", "slides.txt", offset=14000, max_chars=14000
            )
            assert first["data"]["next_offset"] == 14000
            assert first["data"]["text"] + "".join(first["data"]["continuation"]) == text[:14000]
            assert second["data"]["next_offset"] is None
            assert second["data"]["text"] + "".join(second["data"]["continuation"]) == text[14000:]
            assert len(client.calls) == 2
            assert len(extraction_calls) == 1
        with turn_budget(30, 1):
            await tools.workiq_read_document("/drives/other/items/slide", "slides.txt")
            assert len(client.calls) == 3
            assert len(extraction_calls) == 2
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["tenant", "user", "assertion"])
async def test_document_cache_cannot_cross_verified_identity_or_assertion(changed):
    from dataclasses import replace

    from enterprise_agent.agent.budget import turn_budget

    tools, client, _ = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(b"private").decode()}}
    )
    current = principal()
    changes = {
        "tenant": {"tenant_id": "tenant-b"},
        "user": {"object_id": "user-b"},
        "assertion": {"access_token": "new-assertion"},
    }
    context = set_request_context(RequestContext(current))
    try:
        with turn_budget(30, 1):
            await tools.workiq_read_document("/drives/d/items/i", "notes.txt")
            other = set_request_context(RequestContext(replace(current, **changes[changed])))
            try:
                await tools.workiq_read_document("/drives/d/items/i", "notes.txt")
            finally:
                reset_request_context(other)
        assert len(client.calls) == 2
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_search_cost_budget_caps_distinct_queries_without_charging_cache_hits():
    from enterprise_agent.agent.budget import turn_budget

    tools, client, _ = build_tools(retrieval_result())
    context = set_request_context(RequestContext(principal()))
    try:
        with turn_budget(30, 1):
            await tools.workiq_search_documents("shared slides")
            await tools.workiq_search_documents("shared slides")
            await tools.workiq_search_documents("slides by owner")
            result = await tools.workiq_search_documents("another search")
        assert len(client.calls) == 2
        assert result["data"]["error"]["code"] == "search_budget_exhausted"
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_restricted_label_in_an_omitted_hit_still_blocks_search_output():
    response = retrieval_result(count=8)
    response["structuredContent"]["application/vnd.ms-workiq.retrieval"]["retrievalHits"][-1][
        "sensitivityLabel"
    ] = {"displayName": "Highly Restricted"}
    tools, _, _ = build_tools(response)
    context = set_request_context(RequestContext(principal()))
    try:
        with pytest.raises(EgressBlockedError):
            await tools.workiq_search_documents("shared slides", 1)
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_missing_ids_and_malicious_paths_are_not_guessed_or_followed():
    response = retrieval_result()
    hits = response["structuredContent"]["application/vnd.ms-workiq.retrieval"]["retrievalHits"]
    hits[0]["entityUrl"] = "https://external.example/steal"
    hits[1].pop("entityUrl")
    hits[1].pop("driveId")
    tools, client, _ = build_tools(response)
    context = set_request_context(RequestContext(principal()))
    try:
        result = await tools.workiq_search_documents("shared slides")
    finally:
        reset_request_context(context)
    assert all("document_path" not in hit for hit in result["data"]["documents"])
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_large_unicode_read_window_respects_egress_bytes_without_skipping_text():
    from enterprise_agent.agent.budget import turn_budget

    text = "🧪" * 14000
    tools, client, _ = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(text.encode()).decode()}}
    )
    context = set_request_context(RequestContext(principal()))
    try:
        with turn_budget(30, 1):
            first = (
                await tools.workiq_read_document("/drives/d/items/i", "note.txt", max_chars=14000)
            )["data"]
            assert 0 < first["next_offset"] < len(text)
            parts = [first["text"], *first["continuation"]]
            offset = first["next_offset"]
            while offset is not None:
                chunk = (
                    await tools.workiq_read_document(
                        "/drives/d/items/i", "note.txt", offset=offset, max_chars=14000
                    )
                )["data"]
                parts.extend([chunk["text"], *chunk["continuation"]])
                assert chunk["next_offset"] is None or chunk["next_offset"] > offset
                offset = chunk["next_offset"]
            assert "".join(parts) == text
            assert len(client.calls) == 1
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_redaction_expansion_does_not_drop_non_sensitive_document_text():
    from enterprise_agent.agent.budget import turn_budget

    text = ("a@b.co MARK " * 1400)[:14000]
    tools, _, _ = build_tools(
        {"structuredContent": {"base64Content": base64.b64encode(text.encode()).decode()}}
    )
    context = set_request_context(RequestContext(principal()))
    parts = []
    try:
        with turn_budget(30, 1):
            offset = 0
            while offset is not None:
                result = await tools.workiq_read_document(
                    "/drives/d/items/i", "note.txt", offset=offset, max_chars=14000
                )
                assert result["_egress"]["truncated"] is False
                data = result["data"]
                parts.extend([data["text"], *data["continuation"]])
                assert data["next_offset"] is None or data["next_offset"] > offset
                offset = data["next_offset"]
        assert "".join(parts).count("MARK") == text.count("MARK")
        assert "a@b.co" not in "".join(parts)
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_shared_document_opaque_reference_survives_numeric_id_redaction(document_files):
    from enterprise_agent.agent.budget import turn_budget

    response = retrieval_result(count=1)
    hit = response["structuredContent"]["application/vnd.ms-workiq.retrieval"]["retrievalHits"][0]
    path = "/drives/1234567890123456/items/9876543210"
    hit["entityUrl"] = path
    hit["resourceMetadata"]["document"]["title"] = "123456789012.pdf"
    tools, client, _ = build_tools(response)
    context = set_request_context(RequestContext(principal()))
    try:
        with turn_budget(30, 1):
            search = await tools.workiq_search_documents("shared PDF")
            document_id = search["data"]["documents"][0]["document_id"]
            assert "[REDACTED]" not in document_id
            _, content, expected = document_files["pdf"]
            client.result = {
                "structuredContent": {"base64Content": base64.b64encode(content).decode()}
            }
            read = await tools.workiq_read_document(document_id=document_id)
            assert expected in read["data"]["text"]
            assert client.calls[-1][1] == {"path": path + "/content"}
        with turn_budget(30, 1):
            stale = await tools.workiq_read_document(document_id=document_id, filename="note.txt")
            assert stale["data"]["error"]["code"] == "invalid_document_reference"
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_reference_with_extensionless_title_resolves_metadata_without_a_model_round_trip(
    document_files,
):
    from enterprise_agent.agent.budget import turn_budget

    response = retrieval_result(count=1)
    hit = response["structuredContent"]["application/vnd.ms-workiq.retrieval"]["retrievalHits"][0]
    path = "/drives/1234567890123456/items/9876543210"
    hit["entityUrl"] = path
    hit["resourceMetadata"]["document"]["title"] = "shared slide title"
    tools, client, _ = build_tools(response)
    _, content, expected = document_files["pptx"]

    async def upstream(name, arguments, *, access_token):
        client.calls.append((name, arguments, access_token))
        if name == "retrieve":
            return response
        if name == "fetch":
            assert arguments == {"entityUrls": [path + "?$select=name"]}
            return {
                "structuredContent": {
                    "results": [{"statusCode": 200, "data": {"name": "real-slides.pptx"}}]
                }
            }
        assert name == "fetch_blob"
        return {"structuredContent": {"base64Content": base64.b64encode(content).decode()}}

    client.call_tool = upstream
    context = set_request_context(RequestContext(principal()))
    try:
        with turn_budget(30, 1):
            search = await tools.workiq_search_documents("shared slides")
            reference = search["data"]["documents"][0]["document_id"]
            first = await tools.workiq_read_document(document_id=reference)
            second = await tools.workiq_read_document(document_id=reference)
            assert expected in first["data"]["text"]
            assert expected in second["data"]["text"]
        assert [name for name, _, _ in client.calls] == ["retrieve", "fetch", "fetch_blob"]
    finally:
        reset_request_context(context)
