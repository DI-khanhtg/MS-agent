import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from enterprise_agent.agent.budget import turn_budget
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.knowledge.client import KnowledgeTools, retrieval_arguments


def test_retrieval_schema_contract_and_minimal_reasoning():
    result = retrieval_arguments(
        {"properties": {"request": {"type": "string"}}, "required": ["request"]}, "Policy?"
    )
    request = json.loads(result["request"])
    assert request["messages"][0]["content"][0]["text"] == "Policy?"
    assert request["retrievalReasoningEffort"] == {"kind": "minimal"}
    assert retrieval_arguments({"properties": {"query": {}}, "required": ["query"]}, "Policy?") == {
        "query": "Policy?"
    }
    with pytest.raises(ValueError, match="unsupported parameters"):
        retrieval_arguments({"properties": {"query": {}}, "required": ["query", "admin"]}, "x")


def knowledge_settings(**overrides):
    return Settings(
        _env_file=None,
        foundry_iq_mcp_url="https://sample.search.windows.net/knowledgebases/shared/mcp?api-version=test",
        foundry_iq_auth_mode="api_key",
        foundry_iq_api_key="fake-key",
        **overrides,
    )


@pytest.mark.asyncio
async def test_knowledge_budget_caps_obo_before_network():
    settings = knowledge_settings(foundry_iq_shared_knowledge=True).model_copy(
        update={
            "foundry_iq_auth_mode": "entra",
            "azure_client_id": "api-client",
            "azure_client_secret": SecretStr("client-secret"),
            "entra_api_audience": "api-client",
            "auth_allowed_tenant_ids": "tenant-a",
        }
    )
    tools = KnowledgeTools(settings, EgressGate.from_yaml(settings.egress_policy_path))
    started = asyncio.Event()

    class SlowTokenProvider:
        async def get_token(self, principal):
            started.set()
            await asyncio.sleep(10)

    tools.token_provider = SlowTokenProvider()
    principal = Principal("tenant", "user", "sub", None, frozenset(), "client", "token")
    context = set_request_context(RequestContext(principal))
    try:
        with turn_budget(1, 0.9), pytest.raises(TimeoutError):
            await tools.knowledge_query("Policy?")
        assert started.is_set()
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_private_or_anonymous_knowledge_fails_before_network():
    context = set_request_context(RequestContext(Principal.anonymous()))
    try:
        settings = knowledge_settings()
        tools = KnowledgeTools(settings, EgressGate.from_yaml(settings.egress_policy_path))
        with pytest.raises(ValueError, match="shared knowledge"):
            await tools.knowledge_query("Policy?")
        settings.foundry_iq_shared_knowledge = True
        with pytest.raises(ValueError, match="authenticated user"):
            await tools.knowledge_query("Policy?")
    finally:
        reset_request_context(context)


@pytest.mark.asyncio
async def test_mcp_discovery_and_redaction(monkeypatch):
    calls = []

    @asynccontextmanager
    async def transport(url, *, http_client):
        assert http_client.headers["api-key"] == "fake-key"
        yield None, None, None

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            calls.append("initialize")

        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="knowledge_base_retrieve", inputSchema={"properties": {"query": {}}}
                    )
                ]
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(
                isError=False,
                model_dump=lambda **kwargs: {
                    "structuredContent": {"text": "Contact person@example.com"}
                },
            )

    monkeypatch.setattr("enterprise_agent.tools.knowledge.client.streamable_http_client", transport)
    monkeypatch.setattr("enterprise_agent.tools.knowledge.client.ClientSession", Session)
    settings = knowledge_settings(foundry_iq_shared_knowledge=True)
    tools = KnowledgeTools(settings, EgressGate.from_yaml(settings.egress_policy_path))
    principal = Principal("tenant", "user", "sub", None, frozenset(), "client", "token")
    context = set_request_context(RequestContext(principal))
    try:
        result = await tools.knowledge_query("Policy?")
    finally:
        reset_request_context(context)
    assert calls == ["initialize", ("knowledge_base_retrieve", {"query": "Policy?"})]
    assert result["data"]["structuredContent"]["text"] == "Contact [REDACTED]"
