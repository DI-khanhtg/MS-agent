import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent.actions.executor import (
    ExecutionReceipt,
    WorkIQActionExecutor,
    _build_workiq_call,
)
from enterprise_agent.actions.models import (
    ActionRecord,
    ActionStatus,
    ActionType,
    validate_action_payload,
)
from enterprise_agent.actions.policy import ApprovalPolicy, ApprovalPolicyError
from enterprise_agent.actions.service import ApprovalService
from enterprise_agent.actions.store import (
    ActionNotFoundError,
    ActionStateError,
    InMemoryApprovalStore,
)
from enterprise_agent.actions.tools import ControlledActionTools
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.recording import (
    begin_tool_recording,
    end_tool_recording,
    get_recorded_tool_calls,
)


def principal(object_id: str = "user-a") -> Principal:
    return Principal(
        tenant_id="tenant-a",
        object_id=object_id,
        subject=f"subject-{object_id}",
        username=f"{object_id}@example.com",
        scopes=frozenset({"access_as_user"}),
        client_id="frontend",
        access_token=f"token-{object_id}",
    )


def approval_policy(tmp_path: Path) -> ApprovalPolicy:
    path = tmp_path / "approvals.yaml"
    path.write_text(
        """
approval_expiry_minutes: 30
enabled_actions: [send_email, create_calendar_event, post_teams_message, update_entity]
blocked_operations: [delete_entity]
allowed_update_paths:
  /me/events/: [subject, location]
""".strip(),
        encoding="utf-8",
    )
    return ApprovalPolicy.from_yaml(path)


def email_payload() -> dict[str, object]:
    return {
        "to": ["Recipient@Example.com"],
        "subject": " Project update ",
        "body": "Ready for review",
    }


def test_action_payloads_are_strict_and_calendar_requires_valid_range() -> None:
    validated = validate_action_payload(ActionType.SEND_EMAIL, email_payload())
    assert validated["to"] == ["recipient@example.com"]
    assert validated["subject"] == "Project update"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        validate_action_payload(ActionType.SEND_EMAIL, {**email_payload(), "send_now": True})
    with pytest.raises(ValidationError, match="must not be blank"):
        validate_action_payload(
            ActionType.SEND_EMAIL,
            {"to": ["a@example.com"], "subject": "   ", "body": "Body"},
        )
    with pytest.raises(ValidationError, match="must include a UTC offset"):
        validate_action_payload(
            ActionType.CREATE_CALENDAR_EVENT,
            {
                "subject": "Review",
                "start": "2026-09-14T09:00:00",
                "end": "2026-09-14T10:00:00",
            },
        )
    with pytest.raises(ValidationError, match="must be after start"):
        validate_action_payload(
            ActionType.CREATE_CALENDAR_EVENT,
            {
                "subject": "Review",
                "start": "2026-09-14T10:00:00+07:00",
                "end": "2026-09-14T09:00:00+07:00",
            },
        )


def test_update_policy_allows_only_relative_allowlisted_fields(tmp_path: Path) -> None:
    policy = approval_policy(tmp_path)
    valid = policy.validate(
        ActionType.UPDATE_ENTITY,
        {"entity_url": "/me/events/event-1", "changes": {"subject": "New title"}},
    )
    assert valid["entity_url"] == "/me/events/event-1"

    for payload in (
        {"entity_url": "https://graph.microsoft.com/me/events/1", "changes": {"subject": "x"}},
        {"entity_url": "/me/events/../messages/1", "changes": {"subject": "x"}},
        {"entity_url": "/me/events/1", "changes": {"isCancelled": True}},
        {"entity_url": "/me/messages/1", "changes": {"subject": "x"}},
    ):
        with pytest.raises(ApprovalPolicyError):
            policy.validate(ActionType.UPDATE_ENTITY, payload)


def test_policy_requires_delete_to_be_explicitly_blocked(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        "approval_expiry_minutes: 30\nenabled_actions: [send_email]\n",
        encoding="utf-8",
    )

    with pytest.raises(ApprovalPolicyError, match="delete_entity"):
        ApprovalPolicy.from_yaml(path)


@pytest.mark.asyncio
async def test_store_enforces_owner_and_one_way_state_machine() -> None:
    store = InMemoryApprovalStore(expiry_minutes=30)
    draft = await store.create(
        principal(), ActionType.SEND_EMAIL, email_payload(), "Send an email"
    )

    with pytest.raises(ActionNotFoundError):
        await store.get(draft.action_id, principal("user-b"))
    with pytest.raises(ActionStateError, match="pending action"):
        await store.approve(draft.action_id, principal())

    pending = await store.request_approval(draft.action_id, principal())
    assert pending.status is ActionStatus.PENDING_APPROVAL
    approved = await store.approve(draft.action_id, principal())
    assert approved.status is ActionStatus.APPROVED
    executing = await store.claim_execution(draft.action_id, principal())
    assert executing.status is ActionStatus.EXECUTING
    completed = await store.complete(
        draft.action_id,
        principal(),
        status_code=202,
        resource_id=None,
        verification="provider_accepted",
    )
    assert completed.status is ActionStatus.SUCCEEDED
    with pytest.raises(ActionStateError):
        await store.claim_execution(draft.action_id, principal())


@pytest.mark.asyncio
async def test_store_expires_pending_approval() -> None:
    store = InMemoryApprovalStore(expiry_minutes=30)
    draft = await store.create(
        principal(), ActionType.SEND_EMAIL, email_payload(), "Send an email"
    )
    pending = await store.request_approval(draft.action_id, principal())
    owned = store._items[pending.action_id]
    owned.record = owned.record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(1)})

    expired = await store.get(draft.action_id, principal())

    assert expired.status is ActionStatus.EXPIRED
    with pytest.raises(ActionStateError):
        await store.approve(draft.action_id, principal())


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, action: ActionRecord, action_principal: Principal) -> ExecutionReceipt:
        self.calls.append(action.action_id)
        return ExecutionReceipt(202, None, "provider_accepted")


@pytest.mark.asyncio
async def test_service_never_executes_before_backend_approval(tmp_path: Path) -> None:
    executor = FakeExecutor()
    service = ApprovalService(
        InMemoryApprovalStore(30),
        approval_policy(tmp_path),
        executor,  # type: ignore[arg-type]
    )
    draft = await service.draft(principal(), ActionType.SEND_EMAIL, email_payload())
    pending = await service.request_approval(draft.action_id, principal())

    assert pending.status is ActionStatus.PENDING_APPROVAL
    assert executor.calls == []

    completed = await service.approve_and_execute(draft.action_id, principal())

    assert completed.status is ActionStatus.SUCCEEDED
    assert completed.verification == "provider_accepted"
    assert executor.calls == [draft.action_id]
    with pytest.raises(ActionStateError):
        await service.approve_and_execute(draft.action_id, principal())


def test_executor_maps_only_allowlisted_workiq_mutations() -> None:
    email = ActionRecord(
        action_id="1",
        action_type=ActionType.SEND_EMAIL,
        status=ActionStatus.EXECUTING,
        summary="email",
        payload={"to": ["a@example.com"], "cc": [], "subject": "Hi", "body": "Body"},
    )
    tool, arguments = _build_workiq_call(email)
    body = json.loads(arguments["jsonBody"])
    assert tool == "do_action"
    assert arguments["actionUrl"] == "/me/sendMail"
    assert body["saveToSentItems"] is True

    teams = email.model_copy(
        update={
            "action_type": ActionType.POST_TEAMS_MESSAGE,
            "payload": {"chat_id": "19:chat@thread.v2", "content": "Update"},
        }
    )
    tool, arguments = _build_workiq_call(teams)
    assert tool == "create_entity"
    assert arguments["parentUrl"] == "/me/chats/19:chat@thread.v2/messages"
    assert "delete" not in tool


@pytest.mark.asyncio
async def test_executor_uses_explicit_workiq_write_boundary() -> None:
    class FakeTokenProvider:
        async def get_token(self, action_principal: Principal) -> str:
            return "delegated-token"

    class FakeClient:
        def __init__(self) -> None:
            self.call: tuple[str, dict[str, str], str, bool] | None = None

        async def call_tool(
            self,
            name: str,
            arguments: dict[str, str],
            *,
            access_token: str,
            write: bool = False,
        ) -> dict[str, object]:
            self.call = (name, arguments, access_token, write)
            return {"structuredContent": {"statusCode": 201, "data": {"id": "event-1"}}}

    client = FakeClient()
    action = ActionRecord(
        action_id="1",
        action_type=ActionType.CREATE_CALENDAR_EVENT,
        status=ActionStatus.EXECUTING,
        summary="event",
        payload={
            "subject": "Review",
            "start": "2026-09-14T09:00:00+07:00",
            "end": "2026-09-14T10:00:00+07:00",
            "time_zone": "SE Asia Standard Time",
            "attendees": [],
            "body": "",
            "location": "",
        },
    )
    executor = WorkIQActionExecutor(
        client,  # type: ignore[arg-type]
        FakeTokenProvider(),
    )

    receipt = await executor.execute(action, principal())

    assert client.call is not None
    assert client.call[0] == "create_entity"
    assert client.call[2:] == ("delegated-token", True)
    assert receipt.resource_id == "event-1"
    assert receipt.verification == "provider_confirmed"


@pytest.mark.asyncio
async def test_model_tools_draft_without_exposing_content_in_tool_recording(
    tmp_path: Path,
) -> None:
    service = ApprovalService(
        InMemoryApprovalStore(30),
        approval_policy(tmp_path),
        FakeExecutor(),  # type: ignore[arg-type]
    )
    tools = ControlledActionTools(service)
    context_token = set_request_context(RequestContext(principal()))
    recording_token = begin_tool_recording()
    try:
        draft = await tools.draft_action(ActionType.SEND_EMAIL, email_payload())
        calls = get_recorded_tool_calls()
    finally:
        end_tool_recording(recording_token)
        reset_request_context(context_token)

    assert draft["status"] == "draft"
    assert calls == [
        {
            "name": "draft_action",
            "arguments": {
                "action_type": "send_email",
                "payload_fields": ["body", "subject", "to"],
                "recipient_count": 1,
            },
        }
    ]
    assert "Ready for review" not in str(calls)
