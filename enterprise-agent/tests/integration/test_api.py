import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import (
    AgentConfigurationError,
    AgentEgressBlocked,
    AgentResult,
    MicrosoftAgentRuntime,
)
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings


class FakeRuntime:
    async def chat(self, message: str, session_id: str, context: object = None) -> AgentResult:
        return AgentResult(
            text=f"Echo: {message}",
            tool_calls=[{"name": "get_project_status", "arguments": {"project_id": "alpha"}}],
        )

    async def stream_chat(
        self,
        message: str,
        session_id: str,
        context: object = None,
    ) -> AsyncIterator[str]:
        yield "Echo: "
        yield message


class UnconfiguredRuntime(FakeRuntime):
    async def chat(self, message: str, session_id: str, context: object = None) -> AgentResult:
        raise AgentConfigurationError("Gemini is not configured")


class EgressBlockedRuntime(FakeRuntime):
    async def chat(self, message: str, session_id: str, context: object = None) -> AgentResult:
        raise AgentEgressBlocked("Enterprise data was blocked by egress policy")


def settings() -> Settings:
    return Settings(
        _env_file=None,
        gemini_api_key="PUT_YOUR_GEMINI_API_KEY_HERE",
        artifact_tool_node_modules=Path("missing-artifact-tool"),
    )


def test_health_reports_configuration_without_secret() -> None:
    client = TestClient(create_app(settings=settings(), runtime=FakeRuntime()))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "enterprise-agent",
        "environment": "development",
        "model": "gemini-3.8-flash",
        "gemini_configured": False,
        "auth_enabled": False,
        "entra_configured": False,
        "workiq_obo_configured": False,
        "fabric_obo_configured": False,
        "artifact_tool_configured": False,
        "write_approval_required": True,
        "enabled_tool_groups": ["artifact_generation", "mock_read"],
    }


def test_chat_returns_session_and_tool_calls() -> None:
    client = TestClient(create_app(settings=settings(), runtime=FakeRuntime()))

    response = client.post(
        "/api/chat",
        json={"message": "Status of Alpha", "session_id": "session-123"},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-123"
    assert response.json()["message"] == "Echo: Status of Alpha"
    assert response.json()["tool_calls"][0]["name"] == "get_project_status"


def test_chat_generates_session_id() -> None:
    client = TestClient(create_app(settings=settings(), runtime=FakeRuntime()))

    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 200
    assert response.json()["session_id"]


def test_chat_rejects_blank_message() -> None:
    client = TestClient(create_app(settings=settings(), runtime=FakeRuntime()))

    response = client.post("/api/chat", json={"message": "   "})

    assert response.status_code == 422


def test_chat_returns_503_when_key_is_missing() -> None:
    client = TestClient(create_app(settings=settings(), runtime=UnconfiguredRuntime()))

    response = client.post("/api/chat", json={"message": "Hello"})

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_chat_returns_422_when_egress_blocks_input() -> None:
    client = TestClient(create_app(settings=settings(), runtime=EgressBlockedRuntime()))

    response = client.post("/api/chat", json={"message": "sensitive input"})

    assert response.status_code == 422
    assert "blocked" in response.json()["detail"]


def test_streaming_chat_uses_sse_events() -> None:
    client = TestClient(create_app(settings=settings(), runtime=FakeRuntime()))

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "Hello", "session_id": "stream-session"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: metadata" in body
    assert '"session_id": "stream-session"' in body
    assert "event: delta" in body
    assert "event: done" in body


@pytest.mark.parametrize("failure,code", [("timeout", "timeout"), ("provider", "upstream_error")])
def test_real_runtime_stream_preserves_partial_text_and_never_finishes_on_error(
    tmp_path, failure, code
):
    class PartialAgent:
        calls = 0

        def create_session(self, **kwargs):
            return {}

        def run(self, *args, **kwargs):
            self.calls += 1
            return self.updates()

        async def updates(self):
            yield SimpleNamespace(text="Partial answer")
            if failure == "timeout":
                await asyncio.sleep(10)
            raise ConnectionError("private-provider-error")

    config = Settings(
        _env_file=None,
        agent_turn_timeout_seconds=0.1,
        agent_synthesis_reserve_seconds=0,
        artifact_storage_path=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    agent = PartialAgent()
    client = TestClient(
        create_app(settings=config, runtime=MicrosoftAgentRuntime(config, agent=agent))
    )
    with client.stream("POST", "/api/chat/stream", json={"message": "hello"}) as response:
        body = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: delta" in body and "Partial answer" in body
    assert "event: error" in body and f'"code": "{code}"' in body
    assert "event: done" not in body
    assert "private-provider-error" not in body
    assert agent.calls == 1
