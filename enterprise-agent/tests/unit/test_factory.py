import pytest

from enterprise_agent.agent.factory import create_enterprise_agent
from enterprise_agent.config import Settings
from enterprise_agent.tools.registry import create_default_registry


class FakeArtifactTools:
    async def create_artifact(self, spec: object) -> dict[str, object]:
        return {"artifact_id": "test"}


def test_factory_builds_responses_agent_with_phase_seven_tools() -> None:
    settings = Settings(_env_file=None, deepseek_api_key="test-key")

    agent = create_enterprise_agent(
        settings,
        create_default_registry(artifact_tools=FakeArtifactTools()),  # type: ignore[arg-type]
    )

    assert agent.name == "enterprise-agent"
    assert agent.client.model == "deepseek-flash"
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
        deepseek_api_key="PUT_YOUR_DEEPSEEK_API_KEY_HERE",
    )

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        create_enterprise_agent(
            settings,
            create_default_registry(artifact_tools=FakeArtifactTools()),  # type: ignore[arg-type]
        )
