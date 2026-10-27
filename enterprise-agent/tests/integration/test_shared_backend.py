import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal

HOME = "11111111-1111-1111-1111-111111111111"
OTHER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BACKEND = "22222222-2222-2222-2222-222222222222"
SPA = "33333333-3333-3333-3333-333333333333"


def settings(tmp_path, **overrides):
    values = dict(
        deployment_mode="shared",
        app_env="production",
        public_base_url="https://agent.example.test",
        allowed_hosts="agent.example.test,localhost,127.0.0.1",
        auth_enabled=True,
        auth_tenant_mode="organizations",
        auth_allowed_tenant_ids="",
        azure_tenant_id=HOME,
        azure_client_id=BACKEND,
        azure_client_secret="server-entra-secret",
        entra_api_audience=BACKEND,
        entra_allowed_client_ids=SPA,
        entra_spa_client_id=SPA,
        entra_spa_redirect_uri="https://agent.example.test/auth/callback",
        gemini_api_key="server-gemini-secret",
        storage_backend="database",
        database_path=tmp_path / "agent.db",
        local_documents_root=tmp_path / "documents",
        artifact_storage_path=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit.jsonl",
        document_uploads_enabled=True,
        enabled_tool_groups="workiq_read,local_read,artifact_generation",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


class Validator:
    async def validate(self, token):
        return Principal(
            HOME if token == "a" else OTHER,
            "same-oid",
            "same-sub",
            None,
            frozenset({"access_as_user"}),
            SPA,
            token,
        )


class Runtime:
    async def chat(self, message, session_id, context=None):
        return AgentResult(text="Reply to " + context.principal.tenant_id, tool_calls=[])

    async def stream_chat(self, message, session_id, context=None):
        yield "Reply to " + context.principal.tenant_id


def app(tmp_path, runtime=None, **overrides):
    return create_app(
        settings=settings(tmp_path, **overrides),
        runtime=runtime or Runtime(),
        token_validator=Validator(),
    )


def headers(user="a", origin="https://agent.example.test"):
    return {"Authorization": "Bearer " + user, "Origin": origin}


def test_shared_production_keeps_same_oid_tenants_history_files_and_artifacts_separate(tmp_path):
    with TestClient(app(tmp_path), base_url="https://agent.example.test") as client:
        assert client.get("/api/sessions").status_code == 401
        for user, text in (("a", "ALICE PRIVATE"), ("b", "BOB PRIVATE")):
            assert (
                client.post(
                    "/api/chat",
                    headers=headers(user),
                    json={
                        "message": text,
                        "session_id": "same-session",
                    },
                ).status_code
                == 200
            )
            upload = client.post(
                "/api/local-documents/upload",
                headers=headers(user),
                files={"files": ("batch/private.txt", text.encode())},
            )
            assert upload.status_code == 200, upload.text
        history = client.get("/api/sessions/same-session", headers=headers()).json()
        assert "ALICE PRIVATE" in str(history) and "BOB PRIVATE" not in str(history)
        roots = list((tmp_path / "documents" / "_users").glob("*/batch/private.txt"))
        assert len(roots) == 2
        assert {file.read_text() for file in roots} == {"ALICE PRIVATE", "BOB PRIVATE"}
        created = client.post(
            "/api/artifacts",
            headers=headers(),
            json={
                "artifact_type": "markdown",
                "title": "Private report",
                "sections": [{"heading": "Facts", "body": "ALICE PRIVATE"}],
            },
        )
        assert created.status_code == 201, created.text
        url = created.json()["download_url"]
        assert client.get(url, headers=headers("b")).status_code == 404
        assert client.get("/api/artifacts", headers=headers("b")).json() == []


def test_shared_origin_uses_public_https_origin_behind_http_proxy(tmp_path):
    with TestClient(app(tmp_path), base_url="http://agent.example.test") as client:
        response = client.post("/api/chat", headers=headers(), json={"message": "hello"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "max-age=" in response.headers["strict-transport-security"]
        assert (
            client.post(
                "/api/chat",
                headers=headers(origin="https://evil.example"),
                json={"message": "hello"},
            ).status_code
            == 403
        )


def test_shared_global_chat_rate_applies_across_users_and_streaming(tmp_path):
    with TestClient(
        app(tmp_path, shared_chat_requests_per_minute=1), base_url="https://agent.example.test"
    ) as client:
        assert (
            client.post("/api/chat", headers=headers(), json={"message": "hello"}).status_code
            == 200
        )
        denied = client.post("/api/chat/stream", headers=headers("b"), json={"message": "hello"})
        assert denied.status_code == 429
        assert denied.headers["retry-after"] == "60"


@pytest.mark.parametrize(
    "overrides",
    [
        {"public_base_url": ""},
        {"auth_enabled": False},
        {"storage_backend": "memory"},
        {"entra_allowed_client_ids": ""},
        {"gemini_api_key": ""},
        {"entra_spa_redirect_uri": "https://wrong.example.test/auth/callback"},
    ],
)
def test_shared_startup_rejects_incomplete_server_configuration(tmp_path, overrides):
    with pytest.raises(ValueError):
        app(tmp_path, **overrides)


@pytest.mark.asyncio
async def test_shared_chat_capacity_is_released_after_running_turn(tmp_path):
    class SlowRuntime(Runtime):
        def __init__(self):
            self.started, self.finish = asyncio.Event(), asyncio.Event()

        async def chat(self, *args, **kwargs):
            self.started.set()
            await self.finish.wait()
            return AgentResult(text="done", tool_calls=[])

    runtime = SlowRuntime()
    application = app(tmp_path, runtime=runtime, shared_max_concurrent_chats=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="https://agent.example.test"
    ) as client:
        running = asyncio.create_task(
            client.post("/api/chat", headers=headers(), json={"message": "a"})
        )
        await asyncio.wait_for(runtime.started.wait(), 5)
        try:
            blocked = await client.post("/api/chat", headers=headers("b"), json={"message": "b"})
            assert blocked.status_code == 503
        finally:
            runtime.finish.set()
            assert (await running).status_code == 200
        assert (
            await client.post("/api/chat", headers=headers("b"), json={"message": "c"})
        ).status_code == 200
