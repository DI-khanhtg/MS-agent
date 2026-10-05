import pytest

from enterprise_agent.tools.registry import create_default_registry


class FakeArtifactTools:
    async def create_artifact(self, spec: object) -> dict[str, object]:
        return {}


class FakeFabricTools:
    async def fabric_query(self, question: str) -> dict[str, object]:
        return {}


class FakeActionTools:
    async def draft_action(self, action_type: str, payload: object) -> dict[str, object]:
        return {}

    async def request_action_approval(self, action_id: str) -> dict[str, object]:
        return {}

    async def get_action_status(self, action_id: str) -> dict[str, object]:
        return {}


def test_registry_exposes_only_requested_group() -> None:
    registry = create_default_registry()

    tools = registry.tools_for_groups({"mock_read"})

    assert {tool.__name__ for tool in tools} == registry.names
    assert len(tools) == 4


def test_registry_rejects_unknown_group() -> None:
    registry = create_default_registry()

    with pytest.raises(ValueError, match="Unknown tool groups"):
        registry.tools_for_groups({"workiq_write"})


def test_all_phase_two_tools_are_read_only() -> None:
    registry = create_default_registry()

    assert all(registry.definition(name).read_only for name in registry.names)


def test_artifact_generation_is_explicit_and_not_an_enterprise_read() -> None:
    registry = create_default_registry(
        artifact_tools=FakeArtifactTools(),  # type: ignore[arg-type]
    )

    tools = registry.tools_for_groups({"artifact_generation"})

    assert [tool.__name__ for tool in tools] == ["create_artifact"]
    assert registry.definition("create_artifact").read_only is False


def test_fabric_and_controlled_action_groups_are_explicit() -> None:
    registry = create_default_registry(
        fabric_tools=FakeFabricTools(),  # type: ignore[arg-type]
        action_tools=FakeActionTools(),  # type: ignore[arg-type]
    )

    assert [tool.__name__ for tool in registry.tools_for_groups({"fabric_read"})] == [
        "fabric_query"
    ]
    action_names = {
        tool.__name__ for tool in registry.tools_for_groups({"workiq_write"})
    }
    assert action_names == {
        "draft_action",
        "request_action_approval",
        "get_action_status",
    }
    assert not action_names & {"approve_action", "execute_action", "delete_entity"}
    assert registry.definition("fabric_query").read_only is True
    assert registry.definition("draft_action").read_only is False
