from typing import Any
from uuid import UUID

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.tools.workiq.auth import (
    MsalOnBehalfOfTokenProvider,
    WorkIQAuthenticationError,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_graph_file_provider_uses_graph_audience_and_the_verified_user(monkeypatch):
    from enterprise_agent.tools.workiq.auth import MsalGraphFileTokenProvider

    captured = {}

    class Application:
        def __init__(self, **kwargs):
            captured["authority"] = kwargs["authority"]

        def acquire_token_on_behalf_of(self, **kwargs):
            captured.update(kwargs)
            return {"access_token": "graph-delegated-token"}

    monkeypatch.setattr(
        "enterprise_agent.tools.workiq.auth.msal.ConfidentialClientApplication", Application
    )
    provider = MsalGraphFileTokenProvider(settings())
    assert await provider.get_token(principal()) == "graph-delegated-token"
    assert captured["scopes"] == ["https://graph.microsoft.com/.default"]
    assert captured["user_assertion"] == "incoming-user-assertion"
    assert captured["authority"].endswith("/" + TENANT_ID)


def settings(**overrides: Any) -> Settings:
    values = dict(
        auth_enabled=True,
        azure_tenant_id=TENANT_ID,
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids=TENANT_ID,
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
async def test_obo_uses_incoming_user_assertion(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeApplication:
        def __init__(self, **kwargs: Any) -> None:
            captured["constructor"] = kwargs

        def acquire_token_on_behalf_of(self, **kwargs: Any) -> dict[str, str]:
            captured["obo"] = kwargs
            return {"access_token": "delegated-workiq-token"}

    monkeypatch.setattr(
        "enterprise_agent.tools.workiq.auth.msal.ConfidentialClientApplication",
        FakeApplication,
    )
    provider = MsalOnBehalfOfTokenProvider(settings())

    token = await provider.get_token(principal())

    assert token == "delegated-workiq-token"
    assert captured["obo"]["user_assertion"] == "incoming-user-assertion"
    assert captured["obo"]["scopes"] == ["api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask"]


@pytest.mark.asyncio
async def test_obo_rejects_cross_tenant_principal() -> None:
    provider = MsalOnBehalfOfTokenProvider(settings())

    with pytest.raises(WorkIQAuthenticationError, match="tenant is not allowed"):
        await provider.get_token(principal("other-tenant"))


@pytest.mark.asyncio
async def test_obo_rejects_anonymous_user() -> None:
    provider = MsalOnBehalfOfTokenProvider(settings())

    with pytest.raises(WorkIQAuthenticationError, match="authenticated user"):
        await provider.get_token(Principal.anonymous())


@pytest.mark.asyncio
async def test_organizations_obo_uses_verified_tenant_and_each_users_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_tenant = "22222222-2222-2222-2222-222222222222"
    authorities = []

    class FakeApplication:
        def __init__(self, **kwargs: Any) -> None:
            self.authority = kwargs["authority"]
            authorities.append(self.authority)

        def acquire_token_on_behalf_of(self, **kwargs: Any) -> dict[str, str]:
            return {"access_token": f"{self.authority}:{kwargs['user_assertion']}"}

    monkeypatch.setattr(
        "enterprise_agent.tools.workiq.auth.msal.ConfidentialClientApplication",
        FakeApplication,
    )
    provider = MsalOnBehalfOfTokenProvider(
        settings(
            auth_tenant_mode="organizations",
            auth_allowed_tenant_ids="",
        )
    )
    alice = principal(other_tenant)
    from dataclasses import replace

    bob = replace(alice, object_id="user-b", access_token="bobs-assertion")
    alice_token = await provider.get_token(alice)
    bob_token = await provider.get_token(bob)
    assert alice_token == (
        "https://login.microsoftonline.com/22222222-2222-2222-2222-222222222222:"
        "incoming-user-assertion"
    )
    assert bob_token.endswith(":bobs-assertion")
    assert alice_token != bob_token
    assert authorities == [f"https://login.microsoftonline.com/{other_tenant}"]


@pytest.mark.asyncio
async def test_inactive_obo_application_is_recreated_after_organization_churn(
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
        "enterprise_agent.tools.workiq.auth.msal.ConfidentialClientApplication",
        FakeApplication,
    )
    provider = MsalOnBehalfOfTokenProvider(
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
