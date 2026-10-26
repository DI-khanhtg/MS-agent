import pytest

from enterprise_agent.agent.factory import create_enterprise_agent, create_gemini_client
from enterprise_agent.config import Settings
from enterprise_agent.tools.registry import create_default_registry


class FakeArtifactTools:
    async def create_artifact(self, spec: object) -> dict[str, object]:
        return {"artifact_id": "test"}


@pytest.mark.asyncio
async def test_provider_transport_timeout_and_retry_ownership():
    client = create_gemini_client(
        Settings(
            _env_file=None,
            gemini_api_key="test-key",
            gemini_timeout_seconds=7,
            agent_turn_timeout_seconds=40,
        )
    )
    try:
        assert str(client.client.base_url) == (
            "https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        assert client.client.api_key == "test-key"
        assert client.client.timeout == 7
        assert client.client.max_retries == 0
        assert client.function_invocation_configuration["max_duration_seconds"] == 40
    finally:
        await client.client.close()


def test_factory_builds_gemini_agent_with_phase_seven_tools() -> None:
    settings = Settings(_env_file=None, gemini_api_key="test-key")

    agent = create_enterprise_agent(
        settings,
        create_default_registry(artifact_tools=FakeArtifactTools()),  # type: ignore[arg-type]
    )

    assert agent.name == "enterprise-agent"
    assert agent.client.model == "gemini-3.8-flash"
    assert "create_artifact" in agent.default_options["instructions"]
    assert "workiq_fetch" not in agent.default_options["instructions"]
    assert "fabric_query" not in agent.default_options["instructions"]
    assert {tool.name for tool in agent.default_options["tools"]} == {
        "get_current_user",
        "get_project_status",
        "search_mock_documents",
        "get_mock_calendar",
        "create_artifact",
    }


def test_factory_requires_real_key() -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="PUT_YOUR_GEMINI_API_KEY_HERE",
    )

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        create_enterprise_agent(
            settings,
            create_default_registry(artifact_tools=FakeArtifactTools()),  # type: ignore[arg-type]
        )
