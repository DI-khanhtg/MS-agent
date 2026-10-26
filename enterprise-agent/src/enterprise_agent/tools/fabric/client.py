"""Remote Streamable HTTP MCP client for a published Fabric Data Agent."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from enterprise_agent.config import Settings
from enterprise_agent.telemetry.operations import CircuitBreaker, CircuitOpenError
from enterprise_agent.tools.fabric.policy import validate_fabric_question
from enterprise_agent.tools.mcp_errors import is_retryable_mcp_error


class FabricClientError(RuntimeError):
    """A Fabric Data Agent MCP request failed."""


class FabricDataAgentClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.circuit = CircuitBreaker(
            settings.circuit_failure_threshold, settings.circuit_reset_seconds
        )

    async def query(self, question: str, *, access_token: str) -> dict[str, Any]:
        validated_question = validate_fabric_question(question)
        if not self.settings.fabric_mcp_endpoint:
            raise FabricClientError("Fabric Data Agent MCP endpoint is not configured")
        try:
            with self.circuit.request():
                return await self._query_with_retries(validated_question, access_token=access_token)
        except CircuitOpenError as exc:
            raise FabricClientError(str(exc)) from exc

    async def _query_with_retries(self, question: str, *, access_token: str) -> dict[str, Any]:
        attempts = self.settings.fabric_max_retries + 1
        for attempt in range(attempts):
            try:
                result = await self._query_once(question, access_token=access_token)
                self.circuit.success()
                return result
            except Exception as exc:
                if not is_retryable_mcp_error(exc) or attempt == attempts - 1:
                    self.circuit.failure()
                    if isinstance(exc, FabricClientError):
                        raise
                    raise FabricClientError("Fabric Data Agent MCP request failed") from exc
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
        raise AssertionError("retry loop exited unexpectedly")

    async def _query_once(self, question: str, *, access_token: str) -> dict[str, Any]:
        endpoint = self.settings.fabric_mcp_endpoint
        assert endpoint is not None
        headers = {"Authorization": f"Bearer {access_token}"}
        timeout = self.settings.fabric_timeout_seconds
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as http_client:
                async with streamable_http_client(endpoint, http_client=http_client) as streams:
                    read_stream, write_stream, _ = streams
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=timeout),
                    ) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        if len(tools.tools) != 1:
                            raise FabricClientError(
                                "Published Fabric Data Agent must expose exactly one MCP tool"
                            )
                        tool = tools.tools[0]
                        input_schema = getattr(tool, "inputSchema", None) or getattr(
                            tool, "input_schema", None
                        )
                        properties = input_schema.get("properties", {}) if input_schema else {}
                        question_argument = _question_argument(properties)
                        result = await session.call_tool(tool.name, {question_argument: question})
        if result.isError:
            raise FabricClientError("Fabric Data Agent MCP tool returned an error")
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def _question_argument(properties: dict[str, Any]) -> str:
    if not properties:
        raise FabricClientError("Fabric Data Agent MCP tool has no question argument")
    normalized = {key.casefold(): key for key in properties}
    for candidate in ("question", "query", "prompt"):
        if candidate in normalized:
            return normalized[candidate]
    return next(iter(properties))
