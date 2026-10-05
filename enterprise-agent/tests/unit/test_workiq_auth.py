from typing import Any

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.tools.workiq.auth import (
    MsalOnBehalfOfTokenProvider,
    WorkIQAuthenticationError,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def settings() -> Settings:
    return Settings(
        _env_file=None,
        auth_enabled=True,
        azure_tenant_id=TENANT_ID,
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids=TENANT_ID,
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
    assert captured["obo"]["scopes"] == [
        "api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask"
    ]


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

