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
from enterprise_agent.tools.fabric.auth import MsalFabricOnBehalfOfTokenProvider
from enterprise_agent.tools.fabric.client import FabricDataAgentClient
from enterprise_agent.tools.fabric.tools import FabricReadTools


@pytest.mark.live
@pytest.mark.asyncio
async def test_published_fabric_data_agent_answers_business_query_as_user() -> None:
    if os.getenv("RUN_FABRIC_LIVE_EVALS", "").casefold() != "true":
        pytest.skip("Set RUN_FABRIC_LIVE_EVALS=true to call the Fabric Data Agent")
    incoming_token = os.getenv("FABRIC_USER_ACCESS_TOKEN", "")
    if not incoming_token:
        pytest.skip("Set FABRIC_USER_ACCESS_TOKEN in the process environment")

    settings = Settings()
    if not settings.auth_enabled or not settings.fabric_obo_is_configured:
        pytest.skip("Enable Entra authentication and configure the Fabric endpoint in .env")
    principal = await EntraTokenValidator(settings).validate(incoming_token)
    tools = FabricReadTools(
        FabricDataAgentClient(settings),
        MsalFabricOnBehalfOfTokenProvider(settings),
        EgressGate.from_yaml(settings.egress_policy_path),
    )
    context_token = set_request_context(RequestContext(principal))
    try:
        result = await tools.fabric_query(
            os.getenv("FABRIC_TEST_QUESTION", "Summarize the latest governed business KPIs")
        )
    finally:
        reset_request_context(context_token)

    assert "data" in result
    assert "_egress" in result
