"""Agent-facing Work IQ read-only functions."""

from typing import Any

from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.recording import record_tool_call
from enterprise_agent.tools.workiq.auth import WorkIQTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQMCPClient
from enterprise_agent.tools.workiq.policy import (
    validate_entity_urls,
    validate_path_filter,
    validate_schema_request,
)


class WorkIQReadTools:
    def __init__(
        self,
        client: WorkIQMCPClient,
        token_provider: WorkIQTokenProvider,
        egress_gate: EgressGate,
    ) -> None:
        self.client = client
        self.token_provider = token_provider
        self.egress_gate = egress_gate

    async def workiq_fetch(self, entity_urls: list[str]) -> dict[str, Any]:
        """Read Microsoft 365 entities from allowlisted relative paths.

        Examples include /me/messages for email, /me/events for calendar,
        /me/chats/{id}/messages for Teams, /me/drive/root/children for OneDrive,
        /me/people for people, and /sites/{id}/drive for SharePoint. Add a bounded
        $top and narrow $select whenever possible. Multiple paths can be fetched together.
        """
        validated_urls = validate_entity_urls(entity_urls)
        record_tool_call("workiq_fetch", {"entity_urls": validated_urls})
        return await self._call("fetch", {"entityUrls": validated_urls})

    async def workiq_get_schema(
        self,
        path: str,
        operation_type: str = "fetch",
    ) -> dict[str, Any]:
        """Get a Work IQ JSON schema for an allowlisted read operation."""
        validated_path, validated_operation = validate_schema_request(path, operation_type)
        arguments = {
            "path": validated_path,
            "operationType": validated_operation,
            "format": "jsonschema",
        }
        record_tool_call("workiq_get_schema", arguments)
        return await self._call("get_schema", arguments)

    async def workiq_search_paths(self, filter: str) -> dict[str, Any]:
        """Discover Work IQ resource paths by a short prefix or regular-expression filter."""
        validated_filter = validate_path_filter(filter)
        arguments = {"filter": validated_filter}
        record_tool_call("workiq_search_paths", arguments)
        return await self._call("search_paths", arguments)

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        context = get_request_context()
        token = await self.token_provider.get_token(context.principal)
        result = await self.client.call_tool(name, arguments, access_token=token)
        return self.egress_gate.enforce(
            result,
            source=f"workiq:{name}",
            principal=context.principal,
        )
