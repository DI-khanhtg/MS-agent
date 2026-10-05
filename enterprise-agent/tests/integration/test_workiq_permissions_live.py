import os

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.token_validation import EntraTokenValidator
from enterprise_agent.tools.workiq.auth import MsalOnBehalfOfTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient


@pytest.mark.live
@pytest.mark.asyncio
async def test_user_b_cannot_read_user_a_private_entity() -> None:
    if os.getenv("RUN_WORKIQ_PERMISSION_EVAL", "").casefold() != "true":
        pytest.skip("Set RUN_WORKIQ_PERMISSION_EVAL=true for the two-user permission test")
    token_a = os.getenv("WORKIQ_USER_A_ACCESS_TOKEN", "")
    token_b = os.getenv("WORKIQ_USER_B_ACCESS_TOKEN", "")
    private_path = os.getenv("WORKIQ_USER_A_PRIVATE_ENTITY_URL", "")
    if not token_a or not token_b or not private_path:
        pytest.skip("Two user tokens and USER_A_PRIVATE_ENTITY_URL are required")

    settings = Settings()
    if not settings.workiq_obo_is_configured:
        pytest.skip("Configure Entra OBO settings in .env")
    validator = EntraTokenValidator(settings)
    principal_a = await validator.validate(token_a)
    principal_b = await validator.validate(token_b)
    assert principal_a.isolation_key != principal_b.isolation_key

    token_provider = MsalOnBehalfOfTokenProvider(settings)
    workiq_client = WorkIQMCPClient(settings)
    delegated_a = await token_provider.get_token(principal_a)
    delegated_b = await token_provider.get_token(principal_b)

    result_a = await workiq_client.call_tool(
        "fetch",
        {"entityUrls": [private_path]},
        access_token=delegated_a,
    )
    assert _contains_success_status(result_a), "User A could not read the selected private entity"
    try:
        result_b = await workiq_client.call_tool(
            "fetch",
            {"entityUrls": [private_path]},
            access_token=delegated_b,
        )
    except WorkIQClientError:
        return
    assert not _contains_success_status(result_b), (
        "User B unexpectedly received a successful result for User A's private entity"
    )


def _contains_success_status(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"statusCode", "status_code"} and isinstance(child, int):
                if 200 <= child < 300:
                    return True
            if _contains_success_status(child):
                return True
    elif isinstance(value, list):
        return any(_contains_success_status(item) for item in value)
    return False
