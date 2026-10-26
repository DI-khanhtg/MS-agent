"""Transport failures must retry reads without replaying writes."""

import httpx
import pytest

from enterprise_agent.config import Settings
from enterprise_agent.tools.fabric.client import FabricClientError, FabricDataAgentClient
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["fabric", "workiq"])
@pytest.mark.parametrize("status_code", [429, 503, 401, 403])
async def test_nested_mcp_http_failures_retry_only_transient_reads(
    monkeypatch, provider, status_code
):
    settings = Settings(
        _env_file=None,
        fabric_workspace_id="22222222-2222-2222-2222-222222222222",
        fabric_data_agent_id="33333333-3333-3333-3333-333333333333",
        fabric_max_retries=1,
        workiq_max_retries=1,
    )
    request = httpx.Request("POST", "https://example.test/mcp")
    failure = httpx.HTTPStatusError(
        "upstream failed",
        request=request,
        response=httpx.Response(status_code, request=request),
    )
    calls = 0

    async def transport(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExceptionGroup("task group", [ExceptionGroup("transport", [failure])])
        return {"content": []}

    if provider == "fabric":
        client = FabricDataAgentClient(settings)
        monkeypatch.setattr(client, "_query_once", transport)
        operation = client.query("Revenue", access_token="test-token")
        error_type = FabricClientError
    else:
        client = WorkIQMCPClient(settings)
        monkeypatch.setattr(client, "_call_once", transport)
        operation = client.call_tool("fetch", {}, access_token="test-token")
        error_type = WorkIQClientError

    if status_code in {429, 503}:
        assert await operation == {"content": []}
        assert calls == 2
    else:
        with pytest.raises(error_type):
            await operation
        assert calls == 1


@pytest.mark.asyncio
async def test_nested_mcp_write_failure_is_never_retried(monkeypatch):
    client = WorkIQMCPClient(Settings(_env_file=None, workiq_max_retries=3))
    calls = 0

    async def transport(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ExceptionGroup("transport", [TimeoutError("write may have succeeded")])

    monkeypatch.setattr(client, "_call_once", transport)
    with pytest.raises(WorkIQClientError):
        await client.call_tool("do_action", {}, access_token="test-token", write=True)
    assert calls == 1
