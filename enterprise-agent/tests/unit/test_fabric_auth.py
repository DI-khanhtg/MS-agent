from typing import Any

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.tools.fabric.auth import (
    FabricAuthenticationError,
    MsalFabricOnBehalfOfTokenProvider,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
DATA_AGENT_ID = "33333333-3333-3333-3333-333333333333"


def settings() -> Settings:
    return Settings(
        _env_file=None,
        auth_enabled=True,
        azure_tenant_id=TENANT_ID,
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids=TENANT_ID,
        fabric_workspace_id=WORKSPACE_ID,
        fabric_data_agent_id=DATA_AGENT_ID,
    )


def principal(tenant_id: str = TENANT_ID) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        object_id="user-a",
        subject="subject-a",
        username="user@example.com",
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token="incoming-user-assertion",
    )


@pytest.mark.asyncio
async def test_fabric_obo_uses_user_assertion_and_fabric_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeApplication:
        def __init__(self, **kwargs: Any) -> None:
            captured["constructor"] = kwargs

        def acquire_token_on_behalf_of(self, **kwargs: Any) -> dict[str, str]:
            captured["obo"] = kwargs
            return {"access_token": "delegated-fabric-token"}

    monkeypatch.setattr(
        "enterprise_agent.tools.fabric.auth.msal.ConfidentialClientApplication",
        FakeApplication,
    )

    token = await MsalFabricOnBehalfOfTokenProvider(settings()).get_token(principal())

    assert token == "delegated-fabric-token"
    assert captured["obo"] == {
        "user_assertion": "incoming-user-assertion",
        "scopes": ["https://api.fabric.microsoft.com/.default"],
    }


@pytest.mark.asyncio
async def test_fabric_obo_rejects_anonymous_and_cross_tenant_users() -> None:
    provider = MsalFabricOnBehalfOfTokenProvider(settings())

    with pytest.raises(FabricAuthenticationError, match="authenticated user"):
        await provider.get_token(Principal.anonymous())
    with pytest.raises(FabricAuthenticationError, match="tenant is not allowed"):
        await provider.get_token(principal("other-tenant"))
