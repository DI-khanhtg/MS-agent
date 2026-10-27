from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient
from enterprise_agent.tools.workiq.policy import WorkIQPolicyError


class FakeResult:
    def __init__(self, *, is_error: bool = False) -> None:
        self.isError = is_error

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}], "isError": self.isError}


class FakeSession:
    result = FakeResult()
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeResult:
        self.calls.append((name, arguments))
        return self.result


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "workiq_timeout_seconds": 1,
        "workiq_max_retries": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_mcp_client_sends_bearer_and_initializes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    FakeSession.calls = []
    FakeSession.result = FakeResult()

    @asynccontextmanager
    async def fake_transport(url: str, *, http_client: Any):
        captured["url"] = url
        captured["authorization"] = http_client.headers["Authorization"]
        yield "read", "write", lambda: "session-id"

    monkeypatch.setattr(
        "enterprise_agent.tools.workiq.client.streamable_http_client",
        fake_transport,
    )
    monkeypatch.setattr("enterprise_agent.tools.workiq.client.ClientSession", FakeSession)
    client = WorkIQMCPClient(settings())

    result = await client.call_tool(
        "fetch",
        {"entityUrls": ["/me/messages?$top=1"]},
        access_token="delegated-token",
    )

    assert captured == {
        "url": "https://workiq.svc.cloud.microsoft/mcp",
        "authorization": "Bearer delegated-token",
    }
    assert FakeSession.calls == [("fetch", {"entityUrls": ["/me/messages?$top=1"]})]
    assert result["content"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_mcp_tool_error_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSession.result = FakeResult(is_error=True)

    @asynccontextmanager
    async def fake_transport(url: str, *, http_client: Any):
        yield "read", "write", lambda: "session-id"

    monkeypatch.setattr(
        "enterprise_agent.tools.workiq.client.streamable_http_client",
        fake_transport,
    )
    monkeypatch.setattr("enterprise_agent.tools.workiq.client.ClientSession", FakeSession)

    with pytest.raises(WorkIQClientError, match="returned an error"):
        await WorkIQMCPClient(settings()).call_tool(
            "fetch",
            {"entityUrls": ["/me/messages?$top=1"]},
            access_token="delegated-token",
        )


@pytest.mark.asyncio
async def test_mcp_client_retries_transient_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    client = WorkIQMCPClient(settings(workiq_max_retries=1))
    calls = 0

    async def fake_call_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return {"content": []}

    monkeypatch.setattr(client, "_call_once", fake_call_once)

    result = await client.call_tool("fetch", {}, access_token="delegated-token")

    assert result == {"content": []}
    assert calls == 2


@pytest.mark.asyncio
async def test_ambiguous_write_timeout_is_never_retried(monkeypatch) -> None:
    client = WorkIQMCPClient(settings(workiq_max_retries=3))
    calls = []

    async def timeout(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("destination may have accepted the write")

    monkeypatch.setattr(client, "_call_once", timeout)
    with pytest.raises(WorkIQClientError):
        await client.call_tool("do_action", {}, access_token="token", write=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_mcp_client_rejects_write_tool_before_transport() -> None:
    client = WorkIQMCPClient(settings())

    with pytest.raises(WorkIQPolicyError):
        await client.call_tool("delete_entity", {}, access_token="delegated-token")

    with pytest.raises(WorkIQPolicyError):
        await client.call_tool("delete_entity", {}, access_token="delegated-token", write=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_case", ["valid", "missing_tool", "new_required_field", "different_strategy"]
)
async def test_retrieval_discovers_and_validates_live_schema_before_any_billed_call(
    monkeypatch, schema_case
):
    FakeSession.calls = []
    FakeSession.result = FakeResult()
    listed = []
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "strategy": {"enum": ["grounding", "copilot"]},
            "capabilities": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    if schema_case == "new_required_field":
        schema["required"].append("new_field")
    if schema_case == "different_strategy":
        schema["properties"]["strategy"]["enum"] = ["new-strategy"]

    async def list_tools(self, cursor=None):
        listed.append(cursor)
        return SimpleNamespace(
            tools=[]
            if schema_case == "missing_tool"
            else [SimpleNamespace(name="retrieve", inputSchema=schema)],
            nextCursor=None,
        )

    @asynccontextmanager
    async def transport(url, *, http_client):
        yield "read", "write", lambda: "session-id"

    monkeypatch.setattr(FakeSession, "list_tools", list_tools, raising=False)
    monkeypatch.setattr("enterprise_agent.tools.workiq.client.ClientSession", FakeSession)
    monkeypatch.setattr("enterprise_agent.tools.workiq.client.streamable_http_client", transport)
    client = WorkIQMCPClient(settings())
    arguments = {
        "query": "shared slides",
        "strategy": "grounding",
        "capabilities": [{"name": "OneDriveAndSharePoint"}],
    }
    if schema_case == "valid":
        await client.call_tool("retrieve", arguments, access_token="token")
        assert FakeSession.calls == [("retrieve", arguments)]
    else:
        with pytest.raises(WorkIQClientError):
            await client.call_tool("retrieve", arguments, access_token="token")
        assert FakeSession.calls == []
    assert listed == [None]


@pytest.mark.asyncio
async def test_potentially_billed_retrieval_is_not_automatically_replayed(monkeypatch):
    client = WorkIQMCPClient(settings(workiq_max_retries=3))
    calls = []

    async def timeout(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("request may already have been processed")

    monkeypatch.setattr(client, "_call_once", timeout)
    with pytest.raises(WorkIQClientError):
        await client.call_tool("retrieve", {}, access_token="token")
    assert len(calls) == 1
