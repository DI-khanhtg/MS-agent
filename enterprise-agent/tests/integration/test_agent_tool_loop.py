import base64
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from openai import AsyncOpenAI

from enterprise_agent.agent.factory import create_enterprise_agent, create_gemini_client
from enterprise_agent.agent.runtime import AgentResult, MicrosoftAgentRuntime
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.storage.database import Database
from enterprise_agent.tools.local.documents import LocalDocumentTools
from enterprise_agent.tools.recording import get_stream_result, record_tool_call
from enterprise_agent.tools.registry import ToolRegistry, create_default_registry
from enterprise_agent.tools.workiq.tools import WorkIQReadTools


def _response(output: list[dict[str, Any]], response_number: int) -> dict[str, Any]:
    calls = [
        {
            "id": item["call_id"],
            "type": "function",
            "function": {"name": item["name"], "arguments": item["arguments"]},
            "extra_content": {"google": {"thought_signature": f"signature-{item['call_id']}"}},
        }
        for item in output
        if item["type"] == "function_call"
    ]
    text = "".join(
        part["text"] for item in output if item["type"] == "message" for part in item["content"]
    )
    return {
        "id": f"chatcmpl_{response_number}",
        "object": "chat.completion",
        "created": 0,
        "model": "gemini-3.8-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text or None, "tool_calls": calls},
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
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
        "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
    }


async def _run_script(
    outputs: list[list[dict[str, Any]]],
    *,
    registry: ToolRegistry | None = None,
    enabled_tool_groups: str = "mock_read",
    context: RequestContext | None = None,
    message: str = "scripted test",
) -> tuple[AgentResult, list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        output = outputs[len(requests) - 1]
        return httpx2.Response(200, json=_response(output, len(requests)))

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    openai_client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        http_client=http_client,
    )
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        gemini_max_retries=0,
        enabled_tool_groups=enabled_tool_groups,
    )
    with patch("enterprise_agent.agent.factory.AsyncOpenAI", return_value=openai_client):
        framework_client = create_gemini_client(settings)
    agent = create_enterprise_agent(
        settings,
        registry or create_default_registry(),
        client=framework_client,
    )
    runtime = MicrosoftAgentRuntime(settings, agent=agent)
    try:
        result = await runtime.chat(message, "scripted-session", context)
    finally:
        await openai_client.close()
    return result, requests


@pytest.mark.asyncio
async def test_real_framework_turn_can_resume_after_database_restart(tmp_path, monkeypatch):
    requests = []
    outputs = [
        [_function_call("get_project_status", {"project_id": "alpha"}, 1)],
        [_message("First grounded answer", 2)],
        [_message("Resumed answer", 3)],
    ]

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=_response(outputs[len(requests) - 1], len(requests)))

    openai_client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    settings = Settings(
        _env_file=None,
        gemini_api_key="test-key",
        gemini_max_retries=0,
        enabled_tool_groups="mock_read",
        database_path=tmp_path / "state.db",
    )
    with patch("enterprise_agent.agent.factory.AsyncOpenAI", return_value=openai_client):
        framework_client = create_gemini_client(settings)
    monkeypatch.setattr(
        "enterprise_agent.agent.runtime.create_gemini_client", lambda settings: framework_client
    )
    database = Database(settings)
    first = MicrosoftAgentRuntime(settings, database=database)
    try:
        await first.chat("First turn", "resumed")
        saved = database.get("framework_sessions", "anonymous:anonymous:resumed", "runtime")
        assert saved is not None
        restarted = MicrosoftAgentRuntime(settings, database=database)
        result = await restarted.chat("Continue the prior analysis", "resumed")
        assert result.text == "Resumed answer"
        assert result.usage["input_token_count"] == 10
        assert "First grounded answer" in json.dumps(requests[-1])
        assert "signature-call_1" in json.dumps(requests[-1])
    finally:
        await openai_client.close()
        database.engine.dispose()


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
    assert requests[0]["model"] == "gemini-3.8-flash"
    assert requests[0]["tools"]
    instructions = requests[0]["messages"][0]["content"]
    assert "get_project_status" in instructions
    assert "workiq_fetch" not in instructions
    assert "fabric_query" not in instructions
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert requests[1]["messages"][-2]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "signature-call_1"}
    }


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
    assert len([m for m in requests[1]["messages"] if m["role"] == "tool"]) == 2


@pytest.mark.asyncio
async def test_direct_answer_avoids_wrong_tools() -> None:
    result, requests = await _run_script([[_message("Hello!", 1)]])

    assert result.text == "Hello!"
    assert result.tool_calls == []
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_gemini_stream_keeps_parallel_tool_signatures_and_usage() -> None:
    requests = []
    settings = Settings(_env_file=None, gemini_api_key="test-key", enabled_tool_groups="mock_read")

    async def handler(request):
        assert str(request.url) == (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        )
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["stream"] is True
        if len(requests) == 1:
            deltas = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_project_status", "arguments": ""},
                        },
                        {
                            "index": 1,
                            "id": "call_2",
                            "type": "function",
                            "function": {"name": "search_mock_documents", "arguments": ""},
                        },
                    ],
                },
                {
                    "tool_calls": [
                        {
                            "index": 1,
                            "function": {"arguments": '{"query":"Alpha"}'},
                            "extra_content": {
                                "google": {"thought_signature": "stream-signature-2"}
                            },
                        },
                        {
                            "index": 0,
                            "function": {"arguments": '{"project_id":"alpha"}'},
                            "extra_content": {
                                "google": {"thought_signature": "stream-signature-1"}
                            },
                        },
                    ]
                },
            ]
        else:
            calls = next(m["tool_calls"] for m in payload["messages"] if m.get("tool_calls"))
            assert {c["id"]: c["extra_content"] for c in calls} == {
                "call_1": {"google": {"thought_signature": "stream-signature-1"}},
                "call_2": {"google": {"thought_signature": "stream-signature-2"}},
            }
            assert len([m for m in payload["messages"] if m["role"] == "tool"]) == 2
            deltas = [{"role": "assistant", "content": "Grounded "}, {"content": "answer."}]
        chunks = []
        for delta in deltas:
            chunks.append(
                {
                    "id": f"chunk-{len(requests)}",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "gemini-3.8-flash",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
            )
        chunks.append(
            {
                "id": f"chunk-{len(requests)}",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gemini-3.8-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if len(requests) == 1 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }
        )
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    openai_client = AsyncOpenAI(
        api_key="test-key",
        base_url=settings.gemini_base_url,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with patch("enterprise_agent.agent.factory.AsyncOpenAI", return_value=openai_client):
        framework_client = create_gemini_client(settings)
    agent = create_enterprise_agent(settings, create_default_registry(), client=framework_client)
    runtime = MicrosoftAgentRuntime(settings, agent=agent)
    try:
        parts = [part async for part in runtime.stream_chat("Summarize Alpha", "stream-session")]
        assert "".join(parts) == "Grounded answer."
        assert len(requests) == 2
        assert get_stream_result()["usage"] == {"input_token_count": 20, "output_token_count": 10}
        assert {call["name"] for call in get_stream_result()["tool_calls"]} == {
            "get_project_status",
            "search_mock_documents",
        }
    finally:
        await openai_client.close()


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
    tool_output = requests[1]["messages"][-1]
    assert tool_output["role"] == "tool"
    assert tool_output["content"] == "Error: Function failed."


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
@pytest.mark.parametrize("lookup", ["search", "link", "large_link"])
async def test_shared_pptx_search_read_and_word_export_through_real_framework(
    document_files, tmp_path, monkeypatch, lookup
):
    import io
    from types import SimpleNamespace
    from zipfile import ZIP_STORED, ZipFile

    import httpx
    from docx import Document

    from enterprise_agent.artifacts.renderers import create_renderer_registry
    from enterprise_agent.artifacts.service import ArtifactService
    from enterprise_agent.artifacts.store import ArtifactStore
    from enterprise_agent.artifacts.templates import TemplateCatalog
    from enterprise_agent.artifacts.tools import ArtifactTools

    filename, content, expected = document_files["pptx"]
    if lookup == "large_link":
        buffer = io.BytesIO(content)
        with ZipFile(buffer, "a", compression=ZIP_STORED) as package:
            package.writestr("ppt/media/test-unused.bin", b"x" * 5_000_001)
        content = buffer.getvalue()
    path = "/drives/1234567890123456/items/9876543210"
    reference = "wiq_" + "a" * 32
    monkeypatch.setattr(
        "enterprise_agent.agent.budget.uuid4", lambda: SimpleNamespace(hex="a" * 32)
    )
    source = "https://company.sharepoint.com/shared/objectives.pptx"
    sharing_url = (
        "https://company-my.sharepoint.com/:p:/g/personal/owner/123456789012?e=privateShareToken"
    )
    calls = []

    class Client:
        async def call_tool(self, name, arguments, *, access_token):
            calls.append((name, arguments))
            if name == "fetch":
                assert lookup in ("link", "large_link")
                assert arguments["entityUrls"][0].startswith("/shares/u!")
                return {
                    "structuredContent": {
                        "results": [
                            {
                                "statusCode": 200,
                                "data": {
                                    "id": "9876543210",
                                    "parentReference": {"driveId": "1234567890123456"},
                                    "name": filename,
                                    "file": {},
                                    "webUrl": source,
                                    "size": len(content),
                                },
                            }
                        ]
                    }
                }
            if name == "retrieve":
                return {
                    "structuredContent": {
                        "application/vnd.ms-workiq.retrieval": {
                            "markdown": "Relevant shared slide [^h1]",
                            "retrievalHits": [
                                {
                                    "id": "h1",
                                    "entityUrl": path,
                                    "webUrl": source,
                                    "resourceMetadata": {
                                        "document": {
                                            "title": filename,
                                            "authorName": "Shared Owner",
                                        }
                                    },
                                }
                            ],
                            "resultCount": 1,
                            "stoppedReason": "completed",
                        }
                    }
                }
            assert name == "fetch_blob"
            assert arguments == {"path": path + "/content"}
            return {"structuredContent": {"base64Content": base64.b64encode(content).decode()}}

    provider = AsyncMock()
    provider.get_token.return_value = "delegated-token"
    settings = Settings(_env_file=None)
    graph_provider = AsyncMock()
    graph_provider.get_token.return_value = "graph-only-token"
    reader = WorkIQReadTools(
        Client(),
        provider,
        EgressGate.from_yaml(settings.egress_policy_path),
        graph_token_provider=graph_provider,
    )
    if lookup == "large_link":
        actual_http_client = httpx.AsyncClient

        def graph_handler(request):
            assert request.url.host == "graph.microsoft.com"
            assert request.headers["authorization"] == "Bearer graph-only-token"
            return httpx.Response(200, content=content)

        class GraphHTTPClient(actual_http_client):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx.MockTransport(graph_handler), **kwargs)

        monkeypatch.setattr(
            "enterprise_agent.tools.workiq.download.httpx.AsyncClient", GraphHTTPClient
        )
    store = ArtifactStore(tmp_path / "artifacts")
    artifacts = ArtifactTools(
        ArtifactService(
            store,
            TemplateCatalog.from_yaml(
                settings.artifact_template_config_path, settings.artifact_template_root
            ),
            create_renderer_registry(
                node_executable="node", artifact_tool_entrypoint=None, timeout_seconds=30
            ),
        )
    )
    current = Principal(
        "tenant-a",
        "user-a",
        "subject-a",
        None,
        frozenset({"access_as_user"}),
        "frontend",
        "incoming-token",
    )
    result, requests = await _run_script(
        [
            [
                _function_call(
                    "workiq_search_documents"
                    if lookup == "search"
                    else "workiq_resolve_document_link",
                    {"query": "objectives.pptx shared by Shared Owner"}
                    if lookup == "search"
                    else {"link_id": reference},
                    1,
                )
            ],
            [
                _function_call(
                    "workiq_read_document",
                    {"document_id": reference, "max_chars": 14000},
                    2,
                )
            ],
            [
                _function_call(
                    "create_artifact",
                    {
                        "spec": {
                            "artifact_type": "docx",
                            "title": "Shared slide summary",
                            "sections": [{"heading": "Summary", "body": expected}],
                            "sources": [{"label": filename, "reference": source}],
                        }
                    },
                    3,
                )
            ],
            [_message("The Word summary is ready.", 4)],
        ],
        registry=create_default_registry(reader, artifact_tools=artifacts),
        enabled_tool_groups="workiq_read,artifact_generation",
        context=RequestContext(current),
        message="Summarize this PowerPoint and create Word: " + sharing_url
        if lookup != "search"
        else "scripted test",
    )
    search = json.loads(next(m["content"] for m in requests[1]["messages"] if m["role"] == "tool"))[
        "data"
    ]
    assert (
        search["documents"][0]["document_id"] if lookup == "search" else search["document_id"]
    ) == reference
    assert path not in json.dumps(requests)
    if lookup != "search":
        assert sharing_url not in json.dumps(requests)
        assert "privateShareToken" not in json.dumps(requests)
        assert "link_id=" + reference in json.dumps(requests[0])
    read = json.loads([m["content"] for m in requests[2]["messages"] if m["role"] == "tool"][-1])[
        "data"
    ]
    assert expected in read["text"]
    assert read["extraction"]["format"] == "pptx"
    assert [name for name, _ in calls] == (
        ["fetch"]
        if lookup == "large_link"
        else [
            "retrieve" if lookup == "search" else "fetch",
            "fetch_blob",
        ]
    )
    assert "graph-only-token" not in json.dumps(requests)
    records = store.list(current)
    assert len(records) == 1
    _, output_path = store.get(records[0].artifact_id, current)
    paragraphs = "\n".join(paragraph.text for paragraph in Document(output_path).paragraphs)
    assert expected in paragraphs
    assert source in paragraphs
    assert [call["name"] for call in result.tool_calls] == [
        "workiq_search_documents" if lookup == "search" else "workiq_resolve_document_link",
        "workiq_read_document",
        "create_artifact",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["pdf", "access_denied", "invalid_path", "corrupt_pdf"])
async def test_document_result_reaches_model_through_real_framework(document_files, case):
    _, content, expected = document_files["pdf"]
    client, provider = AsyncMock(), AsyncMock()
    provider.get_token.return_value = "delegated-token"
    payload = {"base64Content": base64.b64encode(content).decode()}
    if case == "access_denied":
        payload["statusCode"] = 403
    elif case == "corrupt_pdf":
        payload["base64Content"] = base64.b64encode(b"invalid pdf").decode()
    client.call_tool.return_value = {"structuredContent": payload}
    tools = WorkIQReadTools(
        client, provider, EgressGate.from_yaml(Settings(_env_file=None).egress_policy_path)
    )
    principal = Principal(
        tenant_id="tenant-a",
        object_id="user-a",
        subject="subject-a",
        username=None,
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token="incoming-token",
    )
    path = "/me/drive/items" if case == "invalid_path" else "/me/drive/items/item"
    _, requests = await _run_script(
        [
            [
                _function_call(
                    "workiq_read_document", {"path": path, "filename": "[REDACTED].pdf"}, 1
                )
            ],
            [_message("Document result received.", 2)],
        ],
        registry=create_default_registry(tools),
        enabled_tool_groups="workiq_read",
        context=RequestContext(principal),
    )
    messages = [m["content"] for m in requests[1]["messages"] if m["role"] == "tool"]
    assert len(messages) == 1
    data = json.loads(messages[0])["data"]
    if case == "pdf":
        assert expected in data["text"]
        assert data["extraction"]["format"] == "pdf"
    else:
        assert (
            data["error"]["code"]
            == {
                "access_denied": "document_access_denied",
                "invalid_path": "invalid_document_path",
                "corrupt_pdf": "document_extraction_failed",
            }[case]
        )
        assert "text" not in data
    assert "Function failed" not in messages[0]
    if case == "invalid_path":
        client.call_tool.assert_not_called()
    else:
        client.call_tool.assert_awaited_once_with(
            "fetch_blob", {"path": "/me/drive/items/item/content"}, access_token="delegated-token"
        )


@pytest.mark.asyncio
async def test_local_inventory_reference_reads_numeric_pdf_through_framework(
    tmp_path, document_files
):
    _, content, expected = document_files["pdf"]
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        local_documents_root=tmp_path,
        enabled_tool_groups="local_read",
    )
    reader = LocalDocumentTools(settings, EgressGate.from_yaml(settings.egress_policy_path))
    context = RequestContext(
        Principal("tenant", "reader", "reader", None, frozenset(), "frontend", "test-token")
    )
    token = set_request_context(context)
    try:
        reader.import_documents([("import-1234567890/1571309665.pdf", content)])
        reference = reader.manifest()["files"][0]["document_id"]
    finally:
        reset_request_context(token)
    _, requests = await _run_script(
        [
            [_function_call("list_local_documents", {}, 1)],
            [_function_call("read_local_document", {"document_id": reference}, 2)],
            [_message("PDF read successfully.", 3)],
        ],
        registry=create_default_registry(local_tools=reader),
        enabled_tool_groups="local_read",
        context=context,
    )
    inventory = json.loads(
        next(m["content"] for m in requests[1]["messages"] if m["role"] == "tool")
    )["data"]
    assert inventory["files"][0]["document_id"] == reference
    assert "[REDACTED]" in inventory["files"][0]["path"]
    read_messages = [
        json.loads(m["content"])["data"] for m in requests[2]["messages"] if m["role"] == "tool"
    ]
    assert expected in read_messages[-1]["text"]
    assert read_messages[-1]["extraction"]["unit_count"] == 1
    assert "1571309665" not in json.dumps(read_messages)


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
                _function_call("workiq_fetch", {"entity_urls": ["/me/messages?$top=5"]}, 1),
                _function_call("fabric_query", {"question": "Project X budget versus actual"}, 2),
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
