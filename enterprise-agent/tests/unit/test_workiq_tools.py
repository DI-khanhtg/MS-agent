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
    }
    assert not workiq_names & {"create_entity", "update_entity", "delete_entity", "do_action"}
