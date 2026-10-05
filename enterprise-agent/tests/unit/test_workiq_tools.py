from typing import Any

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
    assert client.calls == [
        ("fetch", {"entityUrls": ["/me/messages?$top=5"]}, "workiq-token")
    ]
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
    }
    assert not workiq_names & {"create_entity", "update_entity", "delete_entity", "do_action"}
