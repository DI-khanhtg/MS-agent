import os

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.security.token_validation import EntraTokenValidator
from enterprise_agent.tools.workiq.auth import (
    MsalGraphFileTokenProvider,
    MsalOnBehalfOfTokenProvider,
)
from enterprise_agent.tools.workiq.client import WorkIQMCPClient
from enterprise_agent.tools.workiq.tools import WorkIQReadTools


@pytest.mark.live
@pytest.mark.asyncio
async def test_shared_document_link_reads_all_text_as_the_signed_in_user():
    if os.getenv("RUN_WORKIQ_LINK_LIVE_EVALS", "").casefold() != "true":
        pytest.skip("Enable RUN_WORKIQ_LINK_LIVE_EVALS for the explicitly selected shared file")
    incoming = os.getenv("WORKIQ_USER_ACCESS_TOKEN", "")
    url = os.getenv("WORKIQ_SHARED_DOCUMENT_URL", "")
    if not incoming or not url:
        pytest.skip("Provide backend user token and document URL in process environment only")
    from enterprise_agent.agent.budget import turn_budget

    settings = Settings()
    principal = await EntraTokenValidator(settings).validate(incoming)
    tools = WorkIQReadTools(
        WorkIQMCPClient(settings),
        MsalOnBehalfOfTokenProvider(settings),
        EgressGate.from_yaml(settings.egress_policy_path),
        graph_token_provider=MsalGraphFileTokenProvider(settings),
    )
    context = set_request_context(RequestContext(principal))
    try:
        with turn_budget(
            settings.agent_turn_timeout_seconds, settings.agent_synthesis_reserve_seconds
        ):
            metadata = (await tools.workiq_resolve_document_link(url=url))["data"]
            assert "error" not in metadata, metadata.get("error", {}).get("code")
            offset, total = 0, 0
            while offset is not None:
                data = (
                    await tools.workiq_read_document(
                        document_id=metadata["document_id"], offset=offset, max_chars=14000
                    )
                )["data"]
                assert "error" not in data, data.get("error", {}).get("code")
                total += len(data["text"]) + sum(map(len, data["continuation"]))
                assert data["next_offset"] is None or data["next_offset"] > offset
                offset = data["next_offset"]
            assert total > 0
    finally:
        reset_request_context(context)


@pytest.mark.live
@pytest.mark.asyncio
async def test_workiq_reads_five_m365_workloads_as_signed_in_user() -> None:
    if os.getenv("RUN_WORKIQ_LIVE_EVALS", "").casefold() != "true":
        pytest.skip("Set RUN_WORKIQ_LIVE_EVALS=true to call the tenant's Work IQ MCP service")
    incoming_token = os.getenv("WORKIQ_USER_ACCESS_TOKEN", "")
    if not incoming_token:
        pytest.skip("Set WORKIQ_USER_ACCESS_TOKEN in the process environment")

    settings = Settings()
    if not settings.auth_enabled or not settings.workiq_obo_is_configured:
        pytest.skip("Enable and configure Entra authentication in .env")
    principal = await EntraTokenValidator(settings).validate(incoming_token)
    tools = WorkIQReadTools(
        WorkIQMCPClient(settings),
        MsalOnBehalfOfTokenProvider(settings),
        EgressGate.from_yaml(settings.egress_policy_path),
    )
    workload_paths = {
        "email": "/me/messages?$top=1&$select=id,subject,receivedDateTime",
        "calendar": "/me/events?$top=1&$select=id,subject,start,end",
        "files": "/me/drive/root/children?$top=1&$select=id,name,webUrl",
        "teams": "/me/chats?$top=1&$select=id,topic,chatType",
        "people": "/me/people?$top=1&$select=id,displayName,userPrincipalName",
    }
    context_token = set_request_context(RequestContext(principal))
    try:
        for workload, path in workload_paths.items():
            result = await tools.workiq_fetch([path])
            assert "data" in result, f"No sanitized result returned for {workload}"
            assert "_egress" in result
    finally:
        reset_request_context(context_token)
