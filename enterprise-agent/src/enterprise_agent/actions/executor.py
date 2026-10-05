"""Translate approved action schemas into narrowly scoped Work IQ mutations."""

import json
from dataclasses import dataclass
from typing import Any

from enterprise_agent.actions.models import ActionRecord, ActionType
from enterprise_agent.security.identity import Principal
from enterprise_agent.tools.workiq.auth import WorkIQTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQMCPClient


class ActionExecutionError(RuntimeError):
    """An approved action could not be verified as accepted by Work IQ."""


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    status_code: int
    resource_id: str | None
    verification: str


class WorkIQActionExecutor:
    def __init__(
        self,
        client: WorkIQMCPClient,
        token_provider: WorkIQTokenProvider,
    ) -> None:
        self.client = client
        self.token_provider = token_provider

    async def execute(self, action: ActionRecord, principal: Principal) -> ExecutionReceipt:
        tool_name, arguments = _build_workiq_call(action)
        token = await self.token_provider.get_token(principal)
        result = await self.client.call_tool(
            tool_name,
            arguments,
            access_token=token,
            write=True,
        )
        status_code = _find_value(result, "statusCode")
        if not isinstance(status_code, int) or status_code < 200 or status_code >= 300:
            raise ActionExecutionError("Work IQ did not return a successful status code")
        resource_id = _find_value(result, "id")
        verification = "provider_accepted" if status_code == 202 else "provider_confirmed"
        return ExecutionReceipt(
            status_code=status_code,
            resource_id=resource_id if isinstance(resource_id, str) else None,
            verification=verification,
        )


def _build_workiq_call(action: ActionRecord) -> tuple[str, dict[str, str]]:
    payload = action.payload
    if action.action_type is ActionType.SEND_EMAIL:
        message = {
            "subject": payload["subject"],
            "body": {"contentType": "Text", "content": payload["body"]},
            "toRecipients": [_recipient(value) for value in payload["to"]],
            "ccRecipients": [_recipient(value) for value in payload["cc"]],
        }
        return "do_action", {
            "actionUrl": "/me/sendMail",
            "jsonBody": json.dumps(
                {"message": message, "saveToSentItems": True}, separators=(",", ":")
            ),
        }
    if action.action_type is ActionType.CREATE_CALENDAR_EVENT:
        event = {
            "subject": payload["subject"],
            "start": {"dateTime": payload["start"], "timeZone": payload["time_zone"]},
            "end": {"dateTime": payload["end"], "timeZone": payload["time_zone"]},
            "attendees": [
                {**_recipient(value), "type": "required"} for value in payload["attendees"]
            ],
            "body": {"contentType": "Text", "content": payload["body"]},
            "location": {"displayName": payload["location"]},
        }
        return "create_entity", {
            "parentUrl": "/me/events",
            "jsonBody": json.dumps(event, separators=(",", ":")),
        }
    if action.action_type is ActionType.POST_TEAMS_MESSAGE:
        return "create_entity", {
            "parentUrl": f"/me/chats/{payload['chat_id']}/messages",
            "jsonBody": json.dumps(
                {"body": {"contentType": "text", "content": payload["content"]}},
                separators=(",", ":"),
            ),
        }
    if action.action_type is ActionType.UPDATE_ENTITY:
        return "update_entity", {
            "entityUrl": payload["entity_url"],
            "jsonBody": json.dumps(payload["changes"], separators=(",", ":")),
        }
    raise ActionExecutionError("Unsupported controlled action type")


def _recipient(address: str) -> dict[str, dict[str, str]]:
    return {"emailAddress": {"address": address}}


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for nested in value.values():
            found = _find_value(nested, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_value(nested, key)
            if found is not None:
                return found
    return None
