"""Remote Streamable HTTP MCP client for Microsoft Work IQ."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from enterprise_agent.config import Settings
from enterprise_agent.telemetry.operations import CircuitBreaker, CircuitOpenError
from enterprise_agent.tools.mcp_errors import is_retryable_mcp_error
from enterprise_agent.tools.workiq.policy import validate_tool_name, validate_write_tool_name


class WorkIQClientError(RuntimeError):
    """A Work IQ MCP request failed."""


class WorkIQMCPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.circuit = CircuitBreaker(
            settings.circuit_failure_threshold, settings.circuit_reset_seconds
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        access_token: str,
        write: bool = False,
    ) -> dict[str, Any]:
        if write:
            validate_write_tool_name(name)
        else:
            validate_tool_name(name)
        try:
            with self.circuit.request():
                return await self._call_with_retries(
                    name, arguments, access_token=access_token, write=write
                )
        except CircuitOpenError as exc:
            raise WorkIQClientError(str(exc)) from exc

    async def _call_with_retries(
        self, name: str, arguments: dict[str, Any], *, access_token: str, write: bool,
    ) -> dict[str, Any]:
        # A timeout after a write can mean it already happened. Never replay mutations.
        attempts = 1 if write else self.settings.workiq_max_retries + 1
        for attempt in range(attempts):
            try:
                result = await self._call_once(name, arguments, access_token=access_token)
                self.circuit.success()
                return result
            except Exception as exc:
                if not is_retryable_mcp_error(exc) or attempt == attempts - 1:
                    self.circuit.failure()
                    if isinstance(exc, WorkIQClientError):
                        raise
                    raise WorkIQClientError("Work IQ MCP request failed") from exc
                await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
        raise AssertionError("retry loop exited unexpectedly")

    async def _call_once(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        access_token: str,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        timeout = self.settings.workiq_timeout_seconds
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as http_client:
                async with streamable_http_client(
                    self.settings.workiq_mcp_url,
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=timeout),
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(name, arguments)
        if result.isError:
            raise WorkIQClientError("Work IQ MCP tool returned an error")
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)
