import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from enterprise_agent.agent.runtime import (
    AgentConfigurationError,
    AgentEgressBlocked,
    AgentRequestTimeout,
    AgentUpstreamError,
    MicrosoftAgentRuntime,
)
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal, RequestContext
from enterprise_agent.telemetry.operations import AuditLog, audit_sink, identity_hash
from enterprise_agent.tools.recording import record_tool_call


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
        "gemini_api_key": "test-key",
        "gemini_max_retries": 2,
        "gemini_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_shared_link_is_kept_private_before_input_pii_masking():
    runtime = MicrosoftAgentRuntime(settings(enabled_tool_groups="workiq_read"), agent=FakeAgent())
    url = (
        "https://company-my.sharepoint.com/:p:/g/personal/owner/123456789012?e=privateSharingToken"
    )
    result = await runtime.chat("Read " + url, "link-input")
    assert "link_id=wiq_" in result.text
    assert "privateSharingToken" not in result.text
    assert "123456789012" not in result.text


@pytest.mark.asyncio
async def test_shared_link_protection_does_not_bypass_original_secret_detection():
    runtime = MicrosoftAgentRuntime(settings(enabled_tool_groups="workiq_read"), agent=FakeAgent())
    jwt = "eyJ" + "a" * 20 + "." + "b" * 20 + "." + "c" * 20
    with pytest.raises(AgentEgressBlocked):
        await runtime.chat("Read https://company.sharepoint.com/file?token=" + jwt, "blocked-link")


@pytest.mark.asyncio
async def test_runtime_retries_transient_failure() -> None:
    fake_agent = FakeAgent(failures=1)
    runtime = MicrosoftAgentRuntime(settings(), agent=fake_agent)

    result = await runtime.chat("hello", "session-one")

    assert result.text == "answer: hello"
    assert fake_agent.calls == 2


@pytest.mark.asyncio
async def test_runtime_never_replays_turn_after_a_tool_call() -> None:
    class ToolThenFailure(FakeAgent):
        async def _chat(self, message):
            record_tool_call("draft_email", {})
            raise RetryableFailure("provider timed out after draft creation")

    fake_agent = ToolThenFailure()
    runtime = MicrosoftAgentRuntime(settings(), agent=fake_agent)
    with pytest.raises(AgentUpstreamError):
        await runtime.chat("Draft an email", "session-one")
    assert fake_agent.calls == 1


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
        settings(
            agent_turn_timeout_seconds=0.01,
            agent_synthesis_reserve_seconds=0,
            gemini_max_retries=0,
        ),
        agent=fake_agent,
    )

    with pytest.raises(AgentRequestTimeout):
        await runtime.chat("hello", "session-one")


async def run_turn(runtime, stream=False, session="budget-test"):
    if stream:
        return "".join([part async for part in runtime.stream_chat("hello", session)])
    return (await runtime.chat("hello", session)).text


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_tool_turn_can_outlive_individual_model_request_timeout(stream):
    class SlowAgent(FakeAgent):
        async def _stream(self, message):
            await asyncio.sleep(0.04)
            yield FakeUpdate("answer: hello")

    runtime = MicrosoftAgentRuntime(
        settings(
            gemini_timeout_seconds=0.01,
            agent_turn_timeout_seconds=1,
            agent_synthesis_reserve_seconds=0,
            gemini_max_retries=0,
        ),
        agent=SlowAgent(delay=0.04),
    )
    assert await run_turn(runtime, stream) == "answer: hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_retry_backoff_cannot_extend_turn_deadline(stream):
    agent = FakeAgent(failures=10)
    runtime = MicrosoftAgentRuntime(
        settings(
            agent_turn_timeout_seconds=0.04,
            agent_synthesis_reserve_seconds=0,
        ),
        agent=agent,
    )
    with pytest.raises(AgentRequestTimeout):
        await run_turn(runtime, stream)
    assert agent.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_session_queue_wait_is_inside_turn_deadline(stream):
    agent = FakeAgent()
    runtime = MicrosoftAgentRuntime(
        settings(
            agent_turn_timeout_seconds=0.03,
            agent_synthesis_reserve_seconds=0,
        ),
        agent=agent,
    )
    runtime._ensure_initialized()
    entry = await runtime._sessions.get_or_create("anonymous:anonymous:budget-test")
    async with entry.lock:
        with pytest.raises(AgentRequestTimeout):
            # Safety watchdog makes a missing queue deadline fail instead of hanging.
            async with asyncio.timeout(0.5):
                await run_turn(runtime, stream)
    assert agent.calls == 0
    assert runtime._circuit.failures == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("outcome", ["ok", "error", "timeout", "cancelled"])
async def test_turn_timing_records_outcome_without_private_content(tmp_path, stream, outcome):
    started = asyncio.Event()

    class ObservedAgent(FakeAgent):
        async def _chat(self, message):
            started.set()
            if outcome == "error":
                raise ValueError("private-provider-diagnostic")
            if outcome in {"timeout", "cancelled"}:
                await asyncio.sleep(10)
            return FakeResponse(message)

        async def _stream(self, message):
            response = await self._chat(message)
            yield FakeUpdate(response.text)

    agent = ObservedAgent()
    runtime = MicrosoftAgentRuntime(
        settings(
            agent_turn_timeout_seconds=0.05 if outcome == "timeout" else 1,
            agent_synthesis_reserve_seconds=0,
            gemini_max_retries=0,
        ),
        agent=agent,
    )
    sink = AuditLog(tmp_path / "audit.jsonl")
    token = audit_sink.set(sink)

    async def invoke():
        if stream:
            return [
                part
                async for part in runtime.stream_chat("private-user-content", "private-session")
            ]
        return await runtime.chat("private-user-content", "private-session")

    try:
        if outcome == "cancelled":
            task = asyncio.create_task(invoke())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "ok":
            await invoke()
        else:
            with pytest.raises(AgentRequestTimeout if outcome == "timeout" else AgentUpstreamError):
                await invoke()
    finally:
        audit_sink.reset(token)
    events = [json.loads(line) for line in sink.path.read_text().splitlines()]
    turns = [event for event in events if event["event"] == "agent_turn"]
    assert len(turns) == 1
    assert turns[0]["status"] == outcome
    assert turns[0]["stage"] == ("stream" if stream else "chat")
    assert turns[0]["latency_ms"] >= 0
    assert turns[0]["attempts"] == 1
    assert ("first_delta_ms" in turns[0]) == (stream and outcome == "ok")
    assert "private-user-content" not in sink.path.read_text()
    assert "private-session" not in sink.path.read_text()
    assert "private-provider-diagnostic" not in sink.path.read_text()


@pytest.mark.asyncio
async def test_concurrent_turns_have_separate_timing_and_recorders(tmp_path):
    class ToolAgent(FakeAgent):
        async def _chat(self, message):
            record_tool_call("probe", {})
            await asyncio.sleep(0)
            return FakeResponse("done")

    runtime = MicrosoftAgentRuntime(settings(), agent=ToolAgent())
    sink = AuditLog(tmp_path / "audit.jsonl")
    token = audit_sink.set(sink)
    contexts = [
        request_context("private-tenant", "private-user-a"),
        request_context("private-tenant", "private-user-b"),
    ]
    try:
        results = await asyncio.gather(
            *[runtime.chat("private-input", "same-session", context) for context in contexts]
        )
    finally:
        audit_sink.reset(token)
    events = [
        json.loads(line)
        for line in sink.path.read_text().splitlines()
        if json.loads(line)["event"] == "agent_turn"
    ]
    assert len(events) == 2
    assert {event["subject_hash"] for event in events} == {
        identity_hash("private-tenant:private-user-a"),
        identity_hash("private-tenant:private-user-b"),
    }
    assert all(event["attempts"] == 1 for event in events)
    assert all(len(result.tool_calls) == 1 for result in results)
    assert "private-" not in sink.path.read_text()


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
