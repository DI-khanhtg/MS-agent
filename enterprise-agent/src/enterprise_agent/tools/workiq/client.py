"""Remote Streamable HTTP MCP client for Microsoft Work IQ."""

import asyncio
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from enterprise_agent.config import Settings
from enterprise_agent.tools.workiq.policy import validate_tool_name, validate_write_tool_name


class WorkIQClientError(RuntimeError):
    """A Work IQ MCP request failed."""


class WorkIQMCPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
        attempts = self.settings.workiq_max_retries + 1
        for attempt in range(attempts):
            try:
                return await self._call_once(name, arguments, access_token=access_token)
            except Exception as exc:
                if not _is_retryable(exc) or attempt == attempts - 1:
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


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx.NetworkError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
        return True
    message = str(exc).casefold()
    return "429" in message or "throttl" in message or "server error" in message
