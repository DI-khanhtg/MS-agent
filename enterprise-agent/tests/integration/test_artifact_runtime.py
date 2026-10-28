from types import SimpleNamespace

import pytest

from enterprise_agent.agent.runtime import AgentUpstreamError, MicrosoftAgentRuntime
from enterprise_agent.api.app import create_app
from enterprise_agent.artifacts.models import ArtifactSpec
from enterprise_agent.artifacts.tools import ArtifactTools
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.tools.recording import get_stream_result, record_tool_call


class ArtifactAgent:
    def __init__(self, tools, *, fail=False, exhaust_budget=False):
        self.tools = tools
        self.fail = fail
        self.exhaust_budget = exhaust_budget
        self.calls = 0

    def create_session(self, **kwargs):
        return {}

    def run(self, message, *, session, stream=False):
        self.calls += 1
        return self._stream() if stream else self._chat()

    async def _chat(self):
        if self.exhaust_budget:
            record_tool_call("read", {})
        await self.tools.create_artifact(
            ArtifactSpec(
                artifact_type="markdown",
                title="Report",
                sections=[{"heading": "Summary", "body": "Grounded content"}],
            )
        )
        if self.fail:
            raise ConnectionError("provider failed after artifact creation")
        return SimpleNamespace(text="Created", usage_details={})

    async def _stream(self):
        response = await self._chat()
        yield response


@pytest.fixture
def artifact_setup(tmp_path):
    settings = Settings(
        _env_file=None,
        runtime_mode="demo",
        artifact_storage_path=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit.jsonl",
        gemini_max_retries=1,
    )
    service = create_app(settings=settings).state.artifact_service
    return settings, service


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_artifact_is_not_replayed_after_provider_failure(artifact_setup, stream):
    settings, service = artifact_setup
    agent = ArtifactAgent(ArtifactTools(service), fail=True)
    runtime = MicrosoftAgentRuntime(settings, agent=agent)
    with pytest.raises(AgentUpstreamError):
        if stream:
            _ = [part async for part in runtime.stream_chat("Create report", "session")]
        else:
            await runtime.chat("Create report", "session")
    assert len(service.store.list(Principal.anonymous())) == 1
    assert agent.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_artifact_is_recorded_and_has_content(artifact_setup, stream):
    settings, service = artifact_setup
    runtime = MicrosoftAgentRuntime(settings, agent=ArtifactAgent(ArtifactTools(service)))
    if stream:
        answer = "".join([part async for part in runtime.stream_chat("Create report", "session")])
        assert answer.startswith("Created")
        assert "/api/artifacts/" in answer
        calls = get_stream_result()["tool_calls"]
    else:
        calls = (await runtime.chat("Create report", "session")).tool_calls
    assert [call["name"] for call in calls] == ["create_artifact"]
    assert "Grounded content" not in str(calls)
    records = service.store.list(Principal.anonymous())
    assert len(records) == 1
    _, path = service.store.get(records[0].artifact_id, Principal.anonymous())
    assert "Grounded content" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_exhausted_tool_budget_stops_artifact_before_write(artifact_setup):
    settings, service = artifact_setup
    settings.max_tool_calls = 1
    agent = ArtifactAgent(ArtifactTools(service), exhaust_budget=True)
    runtime = MicrosoftAgentRuntime(settings, agent=agent)
    with pytest.raises(AgentUpstreamError):
        await runtime.chat("Create report", "session")
    assert service.store.list(Principal.anonymous()) == []
    assert agent.calls == 1
