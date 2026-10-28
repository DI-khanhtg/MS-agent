from typing import Any
from uuid import UUID

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


def settings(**overrides: Any) -> Settings:
    values = dict(
        auth_enabled=True,
        azure_tenant_id=TENANT_ID,
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids=TENANT_ID,
        fabric_workspace_id=WORKSPACE_ID,
        fabric_data_agent_id=DATA_AGENT_ID,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


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


@pytest.mark.asyncio
async def test_organizations_fabric_obo_uses_verified_tenant_and_each_users_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    other_tenant = "55555555-5555-5555-5555-555555555555"
    authorities = []

    class FakeApplication:
        def __init__(self, **kwargs: Any) -> None:
            self.authority = kwargs["authority"]
            authorities.append(self.authority)

        def acquire_token_on_behalf_of(self, **kwargs: Any) -> dict[str, str]:
            return {"access_token": f"{self.authority}:{kwargs['user_assertion']}"}

    monkeypatch.setattr(
        "enterprise_agent.tools.fabric.auth.msal.ConfidentialClientApplication",
        FakeApplication,
    )
    provider = MsalFabricOnBehalfOfTokenProvider(
        settings(
            auth_tenant_mode="organizations",
            auth_allowed_tenant_ids="",
        )
    )
    alice = principal(other_tenant)
    bob = replace(alice, object_id="user-b", access_token="bobs-assertion")
    assert await provider.get_token(alice) == (
        "https://login.microsoftonline.com/55555555-5555-5555-5555-555555555555:"
        "incoming-user-assertion"
    )
    assert (await provider.get_token(bob)).endswith(":bobs-assertion")
    assert authorities == [f"https://login.microsoftonline.com/{other_tenant}"]


@pytest.mark.asyncio
async def test_inactive_fabric_application_is_recreated_after_organization_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_tenant_applications = 0

    class FakeApplication:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal first_tenant_applications
            if kwargs["authority"] == f"https://login.microsoftonline.com/{TENANT_ID}":
                first_tenant_applications += 1

        def acquire_token_on_behalf_of(self, **kwargs: Any) -> dict[str, str]:
            return {"access_token": kwargs["user_assertion"]}

    monkeypatch.setattr(
        "enterprise_agent.tools.fabric.auth.msal.ConfidentialClientApplication",
        FakeApplication,
    )
    provider = MsalFabricOnBehalfOfTokenProvider(
        settings(
            auth_tenant_mode="organizations",
            auth_allowed_tenant_ids="",
        )
    )
    await provider.get_token(principal())
    for index in range(1, 513):
        await provider.get_token(principal(str(UUID(int=index))))
    assert await provider.get_token(principal()) == "incoming-user-assertion"
    assert first_tenant_applications == 2
