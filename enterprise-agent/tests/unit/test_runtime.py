import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from enterprise_agent.agent.runtime import (
    AgentConfigurationError,
    AgentEgressBlocked,
    AgentRequestTimeout,
    MicrosoftAgentRuntime,
)
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal, RequestContext


@dataclass
class FakeResponse:
    text: str


class FakeUpdate:
    def __init__(self, text: str) -> None:
        self.text = text


class RetryableFailure(RuntimeError):
    status_code = 500


class FakeAgent:
    def __init__(self, failures: int = 0, delay: float = 0) -> None:
        self.failures = failures
        self.delay = delay
        self.calls = 0
        self.created_session_ids: list[str] = []

    def create_session(self, *, session_id: str) -> dict[str, str]:
        self.created_session_ids.append(session_id)
        return {"session_id": session_id}

    def run(self, message: str, *, session: object, stream: bool = False):  # type: ignore[no-untyped-def]
        self.calls += 1
        if stream:
            return self._stream(message)
        return self._chat(message)

    async def _chat(self, message: str) -> FakeResponse:
        if self.calls <= self.failures:
            raise RetryableFailure("temporary")
        if self.delay:
            await asyncio.sleep(self.delay)
        return FakeResponse(f"answer: {message}")

    async def _stream(self, message: str) -> AsyncIterator[FakeUpdate]:
        if self.calls <= self.failures:
            raise RetryableFailure("temporary")
        for part in ("answer: ", message):
            yield FakeUpdate(part)


def settings(**overrides: object) -> Settings:
    values = {
        "deepseek_api_key": "test-key",
        "deepseek_max_retries": 2,
        "deepseek_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_runtime_retries_transient_failure() -> None:
    fake_agent = FakeAgent(failures=1)
    runtime = MicrosoftAgentRuntime(settings(), agent=fake_agent)

    result = await runtime.chat("hello", "session-one")

    assert result.text == "answer: hello"
    assert fake_agent.calls == 2


@pytest.mark.asyncio
async def test_runtime_streams_updates_after_retry() -> None:
    fake_agent = FakeAgent(failures=1)
    runtime = MicrosoftAgentRuntime(settings(), agent=fake_agent)

    updates = [part async for part in runtime.stream_chat("hello", "session-one")]

    assert updates == ["answer: ", "hello"]
    assert fake_agent.calls == 2


@pytest.mark.asyncio
async def test_runtime_times_out() -> None:
    fake_agent = FakeAgent(delay=0.05)
    runtime = MicrosoftAgentRuntime(
        settings(deepseek_timeout_seconds=0.01, deepseek_max_retries=0),
        agent=fake_agent,
    )

    with pytest.raises(AgentRequestTimeout):
        await runtime.chat("hello", "session-one")


def request_context(tenant_id: str, object_id: str) -> RequestContext:
    return RequestContext(
        Principal(
            tenant_id=tenant_id,
            object_id=object_id,
            subject=f"subject-{object_id}",
            username=None,
            scopes=frozenset({"access_as_user"}),
            client_id="frontend",
            access_token=f"token-{object_id}",
        )
    )


@pytest.mark.asyncio
async def test_same_public_session_id_is_isolated_by_tenant_and_user() -> None:
    fake_agent = FakeAgent()
    runtime = MicrosoftAgentRuntime(settings(), agent=fake_agent)

    await runtime.chat("hello", "shared-session", request_context("tenant-a", "user-a"))
    await runtime.chat("hello", "shared-session", request_context("tenant-a", "user-b"))
    await runtime.chat("hello", "shared-session", request_context("tenant-b", "user-a"))

    assert set(fake_agent.created_session_ids) == {
        "tenant-a:user-a:shared-session",
        "tenant-a:user-b:shared-session",
        "tenant-b:user-a:shared-session",
    }


@pytest.mark.asyncio
async def test_secret_in_user_input_is_blocked_before_model_call() -> None:
    fake_agent = FakeAgent()
    runtime = MicrosoftAgentRuntime(settings(), agent=fake_agent)

    with pytest.raises(AgentEgressBlocked):
        await runtime.chat(
            "Use Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
            "session-one",
        )

    assert fake_agent.calls == 0


@pytest.mark.asyncio
async def test_workiq_group_requires_auth_and_obo_configuration() -> None:
    runtime = MicrosoftAgentRuntime(
        settings(enabled_tool_groups="workiq_read", auth_enabled=False),
    )

    with pytest.raises(AgentConfigurationError, match="workiq_read requires"):
        await runtime.chat("Find my latest email", "session-one")


@pytest.mark.asyncio
async def test_fabric_group_requires_auth_and_obo_configuration() -> None:
    runtime = MicrosoftAgentRuntime(
        settings(enabled_tool_groups="fabric_read", auth_enabled=False),
    )

    with pytest.raises(AgentConfigurationError, match="fabric_read requires"):
        await runtime.chat("Compare budget to actual", "session-one")


@pytest.mark.asyncio
async def test_write_group_fails_closed_when_approval_is_disabled() -> None:
    runtime = MicrosoftAgentRuntime(
        settings(
            enabled_tool_groups="workiq_write",
            auth_enabled=True,
            entra_api_audience="api-client",
            auth_allowed_tenant_ids="tenant-a",
            azure_client_id="api-client",
            azure_client_secret="client-secret",
            require_write_approval=False,
        ),
    )

    with pytest.raises(AgentConfigurationError, match="approval enforcement"):
        await runtime.chat("Send the email", "session-one")
