"""Query one administrator-configured Azure AI Search knowledge base via MCP."""

import asyncio
import json
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from enterprise_agent.agent.budget import read_window
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import Principal, get_request_context
from enterprise_agent.tools.recording import record_tool_call
from enterprise_agent.tools.workiq.auth import MsalOnBehalfOfTokenProvider


class KnowledgeTools:
    def __init__(self, settings: Settings, gate: EgressGate) -> None:
        self.settings, self.gate = settings, gate
        self.token_provider = MsalOnBehalfOfTokenProvider(
            settings.model_copy(update={"workiq_scope": "https://search.azure.com/.default"})
        )

    async def knowledge_query(self, query: str) -> dict:
        """Retrieve authoritative policy/document passages from the configured Foundry IQ KB."""
        if not query.strip() or len(query) > 4000:
            raise ValueError("Query must contain 1..4000 characters")
        if not self.settings.foundry_iq_is_configured:
            raise ValueError("Foundry IQ endpoint and retrieval credential are not configured")
        if not self.settings.foundry_iq_shared_knowledge:
            raise ValueError(
                "Approve a shared knowledge base with FOUNDRY_IQ_SHARED_KNOWLEDGE=true"
            )
        url = urlsplit(self.settings.foundry_iq_mcp_url)
        if (
            url.scheme != "https"
            or not (url.hostname or "").endswith(".search.windows.net")
            or url.username
            or url.password
            or url.port not in {None, 443}
            or not url.path.startswith("/knowledgebases/")
            or not url.path.endswith("/mcp")
        ):
            raise ValueError("Configure an Azure AI Search knowledge base MCP endpoint")
        principal = get_request_context().principal
        if not principal.authenticated:
            raise ValueError("Knowledge retrieval requires an authenticated user")
        record_tool_call("knowledge_query", {"query": query})
        async with read_window("knowledge"):
            return await self._retrieve(query, principal)

    async def _retrieve(self, query: str, principal: Principal) -> dict:
        if self.settings.foundry_iq_auth_mode == "api_key":
            if self.settings.app_env != "development":
                raise ValueError("Foundry IQ API-key authentication is development-only")
            headers = {"api-key": self.settings.foundry_iq_api_key.get_secret_value()}
        else:
            token = await self.token_provider.get_token(principal)
            headers = {"Authorization": f"Bearer {token}"}
        async with asyncio.timeout(60):
            async with httpx.AsyncClient(
                headers=headers,
                timeout=60,
                follow_redirects=False,
            ) as http:
                async with streamable_http_client(
                    self.settings.foundry_iq_mcp_url, http_client=http
                ) as (read, write, _):
                    async with ClientSession(
                        read, write, read_timeout_seconds=timedelta(seconds=60)
                    ) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        tool = next(
                            (t for t in tools.tools if t.name == "knowledge_base_retrieve"), None
                        )
                        if tool is None:
                            raise ValueError("Knowledge base retrieval tool is unavailable")
                        result = await session.call_tool(
                            tool.name, retrieval_arguments(tool.inputSchema, query)
                        )
                        if result.isError:
                            raise RuntimeError("Knowledge base retrieval failed")
        return self.gate.enforce(
            result.model_dump(mode="json", by_alias=True, exclude_none=True),
            source="foundry:knowledge",
            principal=principal,
        )


def retrieval_arguments(schema: dict, query: str) -> dict:
    """Honor the discovered contract and reject unknown required parameters."""
    properties = schema.get("properties", {})
    payload = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": query}]}],
        "retrievalReasoningEffort": {"kind": "minimal"},
    }
    if "request" in properties:
        value = json.dumps(payload) if properties["request"].get("type") == "string" else payload
        arguments = {"request": value}
    elif "query" in properties:
        arguments = {"query": query}
    else:
        raise ValueError("Unsupported knowledge retrieval input schema")
    if set(schema.get("required", [])) - set(arguments):
        raise ValueError("Knowledge retrieval schema requires unsupported parameters")
    return arguments
