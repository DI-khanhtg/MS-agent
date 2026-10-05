from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.tools.fabric.client import FabricClientError, FabricDataAgentClient

WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
DATA_AGENT_ID = "33333333-3333-3333-3333-333333333333"


class FakeResult:
    def __init__(self, *, is_error: bool = False) -> None:
        self.isError = is_error

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "42"}], "isError": self.isError}


class FakeSession:
    tools = [
        SimpleNamespace(
            name="ask_data_agent",
            inputSchema={"type": "object", "properties": {"question": {"type": "string"}}},
        )
    ]
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

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeResult:
        self.calls.append((name, arguments))
        return self.result


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "fabric_workspace_id": WORKSPACE_ID,
        "fabric_data_agent_id": DATA_AGENT_ID,
        "fabric_timeout_seconds": 1,
        "fabric_max_retries": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_client_discovers_single_tool_and_question_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}
    FakeSession.calls = []
    FakeSession.result = FakeResult()
    FakeSession.tools = [
        SimpleNamespace(
            name="ask_data_agent",
            inputSchema={"properties": {"prompt": {"type": "string"}}},
        )
    ]

    @asynccontextmanager
    async def fake_transport(url: str, *, http_client: Any):
        captured["url"] = url
        captured["authorization"] = http_client.headers["Authorization"]
        yield "read", "write", lambda: "session-id"

    monkeypatch.setattr(
        "enterprise_agent.tools.fabric.client.streamable_http_client", fake_transport
    )
    monkeypatch.setattr("enterprise_agent.tools.fabric.client.ClientSession", FakeSession)

    result = await FabricDataAgentClient(settings()).query(
        "Budget versus actual for Project X", access_token="fabric-token"
    )

    assert captured["url"].endswith(
        f"/workspaces/{WORKSPACE_ID}/dataagents/{DATA_AGENT_ID}/agent"
    )
    assert captured["authorization"] == "Bearer fabric-token"
    assert FakeSession.calls == [
        ("ask_data_agent", {"prompt": "Budget versus actual for Project X"})
    ]
    assert result["content"][0]["text"] == "42"


@pytest.mark.asyncio
async def test_client_rejects_ambiguous_or_failed_mcp_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_transport(url: str, *, http_client: Any):
        yield "read", "write", lambda: "session-id"

    monkeypatch.setattr(
        "enterprise_agent.tools.fabric.client.streamable_http_client", fake_transport
    )
    monkeypatch.setattr("enterprise_agent.tools.fabric.client.ClientSession", FakeSession)
    client = FabricDataAgentClient(settings())

    FakeSession.tools = []
    with pytest.raises(FabricClientError, match="exactly one"):
        await client.query("Revenue this month", access_token="fabric-token")

    FakeSession.tools = [
        SimpleNamespace(name="ask", inputSchema={"properties": {"question": {}}})
    ]
    FakeSession.result = FakeResult(is_error=True)
    with pytest.raises(FabricClientError, match="returned an error"):
        await client.query("Revenue this month", access_token="fabric-token")


@pytest.mark.asyncio
async def test_client_retries_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FabricDataAgentClient(settings(fabric_max_retries=1))
    calls = 0

    async def fake_query_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary")
        return {"content": []}

    monkeypatch.setattr(client, "_query_once", fake_query_once)

    assert await client.query("Utilization", access_token="token") == {"content": []}
    assert calls == 2
