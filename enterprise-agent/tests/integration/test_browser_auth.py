from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from enterprise_agent.api.app import create_app
from enterprise_agent.api.auth import browser_auth_configuration
from enterprise_agent.config import Settings

TENANT = "11111111-1111-1111-1111-111111111111"
BACKEND = "22222222-2222-2222-2222-222222222222"
FRONTEND = "33333333-3333-3333-3333-333333333333"


def settings(**overrides):
    values = dict(
        auth_enabled=True,
        azure_tenant_id=TENANT,
        azure_client_id=BACKEND,
        azure_client_secret="backend-secret-must-not-leak",
        entra_api_audience=BACKEND,
        auth_allowed_tenant_ids=TENANT,
        entra_allowed_client_ids=FRONTEND,
        gemini_api_key="model-secret-must-not-leak",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_login_bootstrap_is_public_and_only_returns_public_settings():
    with TestClient(create_app(settings=settings())) as client:
        response = client.get("/api/auth/config")
        assert response.status_code == 200
        assert response.json() == {
            "deployment_mode": "local",
            "enabled": True,
            "configured": True,
            "client_id": FRONTEND,
            "authority": f"https://login.microsoftonline.com/{TENANT}",
            "redirect_uri": "http://localhost:8000/auth/callback",
            "scopes": [f"api://{BACKEND}/access_as_user"],
            "error": None,
        }
        assert "secret" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/config").status_code == 401
        assert client.post("/api/chat", json={"message": "test"}).status_code == 401


def test_multiple_clients_require_an_explicit_allowed_spa_client():
    other = "44444444-4444-4444-4444-444444444444"
    with TestClient(
        create_app(settings=settings(entra_allowed_client_ids=f"{FRONTEND},{other}"))
    ) as client:
        data = client.get("/api/auth/config").json()
        assert data["configured"] is False
        assert "ENTRA_SPA_CLIENT_ID" in data["error"]
    with TestClient(
        create_app(
            settings=settings(
                entra_allowed_client_ids=f"{FRONTEND},{other}", entra_spa_client_id=FRONTEND
            )
        )
    ) as client:
        assert client.get("/api/auth/config").json()["client_id"] == FRONTEND


def test_spa_client_outside_the_allowlist_cannot_be_used():
    with TestClient(
        create_app(settings=settings(entra_spa_client_id="44444444-4444-4444-4444-444444444444"))
    ) as client:
        assert client.get("/api/auth/config").json()["configured"] is False


def test_auth_callback_hosts_uncached_same_origin_bridge():
    with TestClient(create_app(settings=settings())) as client:
        response = client.get("/auth/callback")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        policy = response.headers["content-security-policy"]
        assert "frame-ancestors 'self'" in policy
        assert "script-src 'self'" in policy
        assert "cross-origin-opener-policy" not in response.headers
        assert client.get("/static/vendor/msal-redirect-bridge.min.js").status_code == 200
        assert client.get("/static/vendor/msal-browser.min.js").status_code == 200
        main_policy = client.get("/").headers["content-security-policy"]
        assert "connect-src 'self' https://login.microsoftonline.com" in main_policy
        assert "frame-ancestors 'none'" in main_policy


def test_disabled_auth_bootstrap_does_not_enable_login():
    with TestClient(create_app(settings=Settings(_env_file=None))) as client:
        assert client.get("/api/auth/config").json()["enabled"] is False


@pytest.mark.asyncio
async def test_shared_bootstrap_uses_organizations_and_owner_https_callback():
    import json

    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings(
                deployment_mode="shared",
                auth_tenant_mode="organizations",
                auth_allowed_tenant_ids="",
                public_base_url="https://assistant.example.com",
                entra_spa_redirect_uri="https://assistant.example.com/auth/callback",
            )
        )
    )
    response = await browser_auth_configuration(Request({"type": "http", "app": app}))
    data = json.loads(response.body)
    assert data["configured"] is True
    assert data["deployment_mode"] == "shared"
    assert data["authority"] == "https://login.microsoftonline.com/organizations"
    assert data["redirect_uri"] == "https://assistant.example.com/auth/callback"
    assert data["scopes"] == [f"api://{BACKEND}/access_as_user"]


@pytest.mark.asyncio
async def test_shared_bootstrap_configuration_errors_are_customer_friendly():
    import json

    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings(
                deployment_mode="shared",
                entra_allowed_client_ids="",
            )
        )
    )
    response = await browser_auth_configuration(Request({"type": "http", "app": app}))
    data = json.loads(response.body)
    assert data["configured"] is False
    assert data["error"]
    assert "ENTRA" not in data["error"]
    assert "AZURE" not in data["error"]
