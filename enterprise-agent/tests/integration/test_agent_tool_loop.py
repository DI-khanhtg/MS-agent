import json
from typing import Any

import httpx2
import pytest
from agent_framework.openai import OpenAIChatClient
from openai import AsyncOpenAI

from enterprise_agent.agent.factory import create_enterprise_agent
from enterprise_agent.agent.runtime import AgentResult, MicrosoftAgentRuntime
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import Principal, RequestContext
from enterprise_agent.tools.recording import record_tool_call
from enterprise_agent.tools.registry import ToolRegistry, create_default_registry
from enterprise_agent.tools.workiq.tools import WorkIQReadTools


def _response(output: list[dict[str, Any]], response_number: int) -> dict[str, Any]:
    return {
        "id": f"resp_{response_number}",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "background": False,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": "deepseek-flash",
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": None,
        "store": False,
        "temperature": 1,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 15,
        },
    }


def _function_call(name: str, arguments: dict[str, Any], number: int) -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": f"fc_{number}",
        "call_id": f"call_{number}",
        "name": name,
        "arguments": json.dumps(arguments),
        "status": "completed",
    }


def _message(text: str, number: int) -> dict[str, Any]:
    return {
        "type": "message",
        "id": f"msg_{number}",
        "status": "completed",
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
        ],
    }


async def _run_script(
    outputs: list[list[dict[str, Any]]],
    *,
    registry: ToolRegistry | None = None,
    enabled_tool_groups: str = "mock_read",
    context: RequestContext | None = None,
) -> tuple[AgentResult, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        output = outputs[len(requests) - 1]
        return httpx2.Response(200, json=_response(output, len(requests)))

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    openai_client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        http_client=http_client,
    )
    framework_client = OpenAIChatClient(async_client=openai_client, model="deepseek-flash")
    settings = Settings(
        _env_file=None,
        deepseek_api_key="test-key",
        deepseek_max_retries=0,
        enabled_tool_groups=enabled_tool_groups,
    )
    agent = create_enterprise_agent(
        settings,
        registry or create_default_registry(),
        client=framework_client,
    )
    runtime = MicrosoftAgentRuntime(settings, agent=agent)
    try:
        result = await runtime.chat("scripted test", "scripted-session", context)
    finally:
        await openai_client.close()
    return result, requests


@pytest.mark.asyncio
async def test_single_tool_call_round_trip() -> None:
    result, requests = await _run_script(
        [
            [_function_call("get_project_status", {"project_id": "alpha"}, 1)],
            [_message("Project Alpha is at risk.", 2)],
        ]
    )

    assert result.text == "Project Alpha is at risk."
    assert [call["name"] for call in result.tool_calls] == ["get_project_status"]
    assert len(requests) == 2
    assert requests[0]["model"] == "deepseek-flash"
    assert requests[0]["tools"]
    assert requests[1]["input"][0]["type"] == "function_call_output"


@pytest.mark.asyncio
async def test_multiple_tools_can_run_in_one_model_turn() -> None:
    result, requests = await _run_script(
        [
            [
                _function_call("get_project_status", {"project_id": "alpha"}, 1),
                _function_call("search_mock_documents", {"query": "Alpha"}, 2),
            ],
            [_message("Combined project summary.", 3)],
        ]
    )

    assert {call["name"] for call in result.tool_calls} == {
        "get_project_status",
        "search_mock_documents",
    }
    assert len(requests[1]["input"]) == 2


@pytest.mark.asyncio
async def test_direct_answer_avoids_wrong_tools() -> None:
    result, requests = await _run_script([[_message("Hello!", 1)]])

    assert result.text == "Hello!"
    assert result.tool_calls == []
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_invalid_arguments_are_returned_to_model() -> None:
    result, requests = await _run_script(
        [
            [_function_call("get_mock_calendar", {"calendar_date": "13/09/2026"}, 1)],
            [_message("I could not use that calendar argument.", 2)],
        ]
    )

    assert result.text == "I could not use that calendar argument."
    assert [call["name"] for call in result.tool_calls] == ["get_mock_calendar"]
    tool_output = requests[1]["input"][0]
    assert tool_output["type"] == "function_call_output"
    assert tool_output["output"] == "Error: Function failed."


@pytest.mark.asyncio
async def test_tool_failure_can_trigger_replan() -> None:
    result, requests = await _run_script(
        [
            [
                _function_call(
                    "get_project_status",
                    {"project_id": "simulate-failure"},
                    1,
                )
            ],
            [_function_call("search_mock_documents", {"query": "Alpha status"}, 2)],
            [_message("The status service failed; document evidence is limited.", 3)],
        ]
    )

    assert [call["name"] for call in result.tool_calls] == [
        "get_project_status",
        "search_mock_documents",
    ]
    assert len(requests) == 3
    assert "failed" in result.text


@pytest.mark.asyncio
async def test_workiq_result_is_redacted_before_second_model_request() -> None:
    class FakeTokenProvider:
        async def get_token(self, principal: Principal) -> str:
            return "delegated-token"

    class FakeWorkIQClient:
        async def call_tool(
            self,
            name: str,
            arguments: dict[str, Any],
            *,
            access_token: str,
        ) -> dict[str, Any]:
            return {"structuredContent": {"sender": "sensitive@example.com"}}

    settings = Settings(_env_file=None)
    tools = WorkIQReadTools(
        FakeWorkIQClient(),  # type: ignore[arg-type]
        FakeTokenProvider(),
        EgressGate.from_yaml(settings.egress_policy_path),
    )
    registry = create_default_registry(tools)
    principal = Principal(
        tenant_id="tenant-a",
        object_id="user-a",
        subject="subject-a",
        username=None,
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token="incoming-token",
    )

    _, requests = await _run_script(
        [
            [_function_call("workiq_fetch", {"entity_urls": ["/me/messages?$top=1"]}, 1)],
            [_message("The sender was redacted.", 2)],
        ],
        registry=registry,
        enabled_tool_groups="workiq_read",
        context=RequestContext(principal),
    )

    second_request = json.dumps(requests[1])
    assert "sensitive@example.com" not in second_request
    assert "[REDACTED]" in second_request


@pytest.mark.asyncio
async def test_multi_source_workflow_retrieves_both_sources_before_artifact() -> None:
    class ScriptedWorkIQTools:
        async def workiq_fetch(self, entity_urls: list[str]) -> dict[str, Any]:
            record_tool_call("workiq_fetch", {"entity_urls": entity_urls})
            return {"communications": ["Project X delivery risk"]}

        async def workiq_get_schema(self, entity_url: str) -> dict[str, Any]:
            return {}

        async def workiq_search_paths(self, query: str) -> dict[str, Any]:
            return {}

    class ScriptedFabricTools:
        async def fabric_query(self, question: str) -> dict[str, Any]:
            record_tool_call("fabric_query", {"question": question})
            return {"budget": 100, "actual": 115, "currency": "USD"}

    class ScriptedArtifactTools:
        async def create_artifact(self, spec: dict[str, Any]) -> dict[str, Any]:
            record_tool_call("create_artifact", {"spec": spec})
            return {"artifact_id": "report-1", "format": "docx"}

    registry = create_default_registry(
        workiq_tools=ScriptedWorkIQTools(),  # type: ignore[arg-type]
        fabric_tools=ScriptedFabricTools(),  # type: ignore[arg-type]
        artifact_tools=ScriptedArtifactTools(),  # type: ignore[arg-type]
    )
    result, requests = await _run_script(
        [
            [
                _function_call(
                    "workiq_fetch", {"entity_urls": ["/me/messages?$top=5"]}, 1
                ),
                _function_call(
                    "fabric_query", {"question": "Project X budget versus actual"}, 2
                ),
            ],
            [
                _function_call(
                    "create_artifact",
                    {
                        "spec": {
                            "format": "docx",
                            "title": "Project X status",
                            "sections": [],
                            "sources": [],
                        }
                    },
                    3,
                )
            ],
            [_message("The grounded report is ready.", 4)],
        ],
        registry=registry,
        enabled_tool_groups="workiq_read,fabric_read,artifact_generation",
        context=RequestContext(
            Principal(
                tenant_id="tenant-a",
                object_id="user-a",
                subject="subject-a",
                username=None,
                scopes=frozenset({"access_as_user"}),
                client_id="frontend",
                access_token="incoming-token",
            )
        ),
    )

    assert result.text == "The grounded report is ready."
    assert [call["name"] for call in result.tool_calls] == [
        "workiq_fetch",
        "fabric_query",
        "create_artifact",
    ]
    assert len(requests) == 3
