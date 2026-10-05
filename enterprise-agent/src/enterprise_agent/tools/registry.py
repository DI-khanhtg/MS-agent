"""Least-privilege tool registry."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from enterprise_agent.tools.mock import (
    get_current_user,
    get_mock_calendar,
    get_project_status,
    search_mock_documents,
)

if TYPE_CHECKING:
    from enterprise_agent.actions.tools import ControlledActionTools
    from enterprise_agent.artifacts.tools import ArtifactTools
    from enterprise_agent.tools.fabric.tools import FabricReadTools
    from enterprise_agent.tools.workiq.tools import WorkIQReadTools

ToolCallable = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    group: str
    function: ToolCallable
    read_only: bool = True
    enabled: bool = True


class ToolRegistry:
    """Expose only explicitly enabled groups and reject unknown groups."""

    def __init__(self, definitions: list[ToolDefinition]) -> None:
        self._definitions = {definition.name: definition for definition in definitions}

    @property
    def names(self) -> set[str]:
        return set(self._definitions)

    def tools_for_groups(self, groups: set[str]) -> list[ToolCallable]:
        known_groups = {definition.group for definition in self._definitions.values()}
        unknown = groups - known_groups
        if unknown:
            raise ValueError(f"Unknown tool groups: {', '.join(sorted(unknown))}")
        return [
            definition.function
            for definition in self._definitions.values()
            if definition.enabled and definition.group in groups
        ]

    def definition(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc


def create_default_registry(
    workiq_tools: "WorkIQReadTools | None" = None,
    artifact_tools: "ArtifactTools | None" = None,
    fabric_tools: "FabricReadTools | None" = None,
    action_tools: "ControlledActionTools | None" = None,
) -> ToolRegistry:
    definitions = [
        ToolDefinition("get_current_user", "mock_read", get_current_user),
        ToolDefinition("get_project_status", "mock_read", get_project_status),
        ToolDefinition("search_mock_documents", "mock_read", search_mock_documents),
        ToolDefinition("get_mock_calendar", "mock_read", get_mock_calendar),
    ]
    if workiq_tools is not None:
        definitions.extend(
            [
                ToolDefinition("workiq_fetch", "workiq_read", workiq_tools.workiq_fetch),
                ToolDefinition(
                    "workiq_get_schema",
                    "workiq_read",
                    workiq_tools.workiq_get_schema,
                ),
                ToolDefinition(
                    "workiq_search_paths",
                    "workiq_read",
                    workiq_tools.workiq_search_paths,
                ),
            ]
        )
    if artifact_tools is not None:
        definitions.append(
            ToolDefinition(
                "create_artifact",
                "artifact_generation",
                artifact_tools.create_artifact,
                read_only=False,
            )
        )
    if fabric_tools is not None:
        definitions.append(
            ToolDefinition("fabric_query", "fabric_read", fabric_tools.fabric_query)
        )
    if action_tools is not None:
        definitions.extend(
            [
                ToolDefinition(
                    "draft_action",
                    "workiq_write",
                    action_tools.draft_action,
                    read_only=False,
                ),
                ToolDefinition(
                    "request_action_approval",
                    "workiq_write",
                    action_tools.request_action_approval,
                    read_only=False,
                ),
                ToolDefinition(
                    "get_action_status",
                    "workiq_write",
                    action_tools.get_action_status,
                ),
            ]
        )
    return ToolRegistry(definitions)
