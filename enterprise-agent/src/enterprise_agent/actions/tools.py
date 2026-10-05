"""Agent tools can draft and request approval, but cannot approve or execute."""

from typing import Any

from enterprise_agent.actions.models import ActionType
from enterprise_agent.actions.service import ApprovalService
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.recording import record_tool_call


class ControlledActionTools:
    def __init__(self, service: ApprovalService) -> None:
        self.service = service

    async def draft_action(
        self,
        action_type: ActionType,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a local draft for an email, event, Teams post, or approved entity update.

        This never changes Microsoft 365. Show the resulting summary and action_id to the
        user. Use request_action_approval only after the user asks to execute this draft.
        """
        context = get_request_context()
        record_tool_call("draft_action", _safe_action_metadata(action_type, payload))
        record = await self.service.draft(context.principal, action_type, payload)
        return record.model_dump(mode="json")

    async def request_action_approval(self, action_id: str) -> dict[str, Any]:
        """Submit an existing draft for explicit user approval without executing it.

        The user must approve through the authenticated approval API/UI. The model cannot
        approve or execute the action itself.
        """
        context = get_request_context()
        record_tool_call("request_action_approval", {"action_id": action_id})
        record = await self.service.request_approval(action_id, context.principal)
        return record.model_dump(mode="json")

    async def get_action_status(self, action_id: str) -> dict[str, Any]:
        """Read the current status and verification receipt for the user's action."""
        context = get_request_context()
        record_tool_call("get_action_status", {"action_id": action_id})
        record = await self.service.get(action_id, context.principal)
        return record.model_dump(mode="json")


def _safe_action_metadata(action_type: ActionType, payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "action_type": action_type.value,
        "payload_fields": sorted(payload),
    }
    if action_type is ActionType.SEND_EMAIL:
        metadata["recipient_count"] = len(payload.get("to", []))
    elif action_type is ActionType.CREATE_CALENDAR_EVENT:
        metadata["attendee_count"] = len(payload.get("attendees", []))
    return metadata

