from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal, RequestContext

TENANT_ID = "11111111-1111-1111-1111-111111111111"


class CapturingRuntime:
    def __init__(self) -> None:
        self.context: RequestContext | None = None

    async def chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AgentResult:
        self.context = context
        return AgentResult(text="ok", tool_calls=[])

    async def stream_chat(
        self,
        message: str,
        session_id: str,
        context: RequestContext | None = None,
    ) -> AsyncIterator[str]:
        self.context = context
        yield "ok"


class FakeValidator:
    async def validate(self, token: str) -> Principal:
        assert token == "valid-token"
        return Principal(
            tenant_id=TENANT_ID,
            object_id="user-a",
            subject="subject-a",
            username="user.a@example.com",
            scopes=frozenset({"access_as_user"}),
            client_id="frontend",
            access_token=token,
        )


def auth_settings() -> Settings:
    return Settings(
        _env_file=None,
        auth_enabled=True,
        azure_tenant_id=TENANT_ID,
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids=TENANT_ID,
    )


def test_protected_chat_requires_bearer_token() -> None:
    client = TestClient(
        create_app(
            settings=auth_settings(),
            runtime=CapturingRuntime(),
            token_validator=FakeValidator(),
        )
    )

    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_verified_identity_is_passed_to_runtime() -> None:
    runtime = CapturingRuntime()
    client = TestClient(
        create_app(
            settings=auth_settings(),
            runtime=runtime,
            token_validator=FakeValidator(),
        )
    )

    response = client.post(
        "/api/chat",
        json={"message": "Hello", "session_id": "shared-name"},
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert runtime.context is not None
    assert runtime.context.principal.object_id == "user-a"
    assert runtime.context.principal.access_token == "valid-token"


def test_me_endpoint_returns_verified_claims_without_exposing_token() -> None:
    client = TestClient(
        create_app(
            settings=auth_settings(),
            runtime=CapturingRuntime(),
            token_validator=FakeValidator(),
        )
    )

    response = client.get(
        "/api/me",
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "tenant_id": TENANT_ID,
        "object_id": "user-a",
        "username": "user.a@example.com",
        "scopes": ["access_as_user"],
    }
    assert "valid-token" not in response.text
