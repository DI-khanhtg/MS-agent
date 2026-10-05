from typing import Any

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.fabric.policy import FabricPolicyError, validate_fabric_question
from enterprise_agent.tools.fabric.tools import FabricReadTools
from enterprise_agent.tools.recording import (
    begin_tool_recording,
    end_tool_recording,
    get_recorded_tool_calls,
)


class FakeTokenProvider:
    async def get_token(self, principal: Principal) -> str:
        return "fabric-token"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def query(self, question: str, *, access_token: str) -> dict[str, Any]:
        self.calls.append((question, access_token))
        return {"structuredContent": {"amount": 42, "owner": "owner@example.com"}}


def principal() -> Principal:
    return Principal(
        tenant_id="tenant-a",
        object_id="user-a",
        subject="subject-a",
        username=None,
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token="incoming-token",
    )


@pytest.mark.asyncio
async def test_fabric_tool_uses_user_token_egress_and_records_call() -> None:
    client = FakeClient()
    settings = Settings(_env_file=None)
    tools = FabricReadTools(
        client,  # type: ignore[arg-type]
        FakeTokenProvider(),
        EgressGate.from_yaml(settings.egress_policy_path),
    )
    context_token = set_request_context(RequestContext(principal()))
    recording_token = begin_tool_recording()
    try:
        result = await tools.fabric_query("Budget versus actual for Project X")
        calls = get_recorded_tool_calls()
    finally:
        end_tool_recording(recording_token)
        reset_request_context(context_token)

    assert client.calls == [("Budget versus actual for Project X", "fabric-token")]
    assert result["data"]["structuredContent"]["owner"] == "[REDACTED]"
    assert calls == [
        {"name": "fabric_query", "arguments": {"question": "Budget versus actual for Project X"}}
    ]


def test_fabric_question_policy_rejects_blank_and_oversized_input() -> None:
    with pytest.raises(FabricPolicyError):
        validate_fabric_question("   ")
    with pytest.raises(FabricPolicyError):
        validate_fabric_question("x" * 4_001)
