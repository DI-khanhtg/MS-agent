from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from enterprise_agent.actions.executor import ExecutionReceipt
from enterprise_agent.actions.models import ActionRecord
from enterprise_agent.actions.policy import ApprovalPolicy
from enterprise_agent.actions.service import ApprovalService
from enterprise_agent.actions.store import InMemoryApprovalStore
from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal

TENANT_ID = "11111111-1111-1111-1111-111111111111"


class FakeRuntime:
    async def chat(self, message: str, session_id: str, context: object = None) -> AgentResult:
        return AgentResult(text="ok", tool_calls=[])

    async def stream_chat(
        self, message: str, session_id: str, context: object = None
    ) -> AsyncIterator[str]:
        yield "ok"


class FakeValidator:
    async def validate(self, token: str) -> Principal:
        object_id = {"token-a": "user-a", "token-b": "user-b"}[token]
        return Principal(
            tenant_id=TENANT_ID,
            object_id=object_id,
            subject=object_id,
            username=f"{object_id}@example.com",
            scopes=frozenset({"access_as_user"}),
            client_id="frontend",
            access_token=token,
        )


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, action: ActionRecord, principal: Principal) -> ExecutionReceipt:
        self.calls.append(action.action_id)
        return ExecutionReceipt(202, None, "provider_accepted")


def build_client() -> tuple[TestClient, FakeExecutor]:
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        azure_tenant_id=TENANT_ID,
        azure_client_id="api-client",
        azure_client_secret="client-secret",
        entra_api_audience="api-client",
        auth_allowed_tenant_ids=TENANT_ID,
        artifact_tool_node_modules=Path("missing-artifact-tool"),
    )
    app = create_app(
        settings=settings,
        runtime=FakeRuntime(),
        token_validator=FakeValidator(),
    )
    executor = FakeExecutor()
    policy = ApprovalPolicy.from_yaml(settings.approval_policy_path)
    app.state.approval_service = ApprovalService(
        InMemoryApprovalStore(policy.expiry_minutes),
        policy,
        executor,  # type: ignore[arg-type]
    )
    return TestClient(app), executor


def test_controlled_actions_require_authentication() -> None:
    client, _ = build_client()

    response = client.post(
        "/api/actions/drafts",
        json={
            "action_type": "send_email",
            "payload": {"to": ["a@example.com"], "subject": "Hi", "body": "Body"},
        },
    )

    assert response.status_code == 401


def test_draft_approval_execution_lifecycle_runs_exactly_once() -> None:
    client, executor = build_client()
    headers = {"Authorization": "Bearer token-a"}
    draft = client.post(
        "/api/actions/drafts",
        headers=headers,
        json={
            "action_type": "send_email",
            "payload": {"to": ["a@example.com"], "subject": "Hi", "body": "Body"},
        },
    )
    assert draft.status_code == 201
    action_id = draft.json()["action_id"]
    assert draft.json()["status"] == "draft"
    assert executor.calls == []

    pending = client.post(f"/api/actions/{action_id}/request-approval", headers=headers)
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending_approval"
    assert executor.calls == []

    approved = client.post(f"/api/approvals/{action_id}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "succeeded"
    assert approved.json()["verification"] == "provider_accepted"
    assert executor.calls == [action_id]

    duplicate = client.post(f"/api/approvals/{action_id}/approve", headers=headers)
    assert duplicate.status_code == 409
    assert executor.calls == [action_id]


def test_action_ownership_isolated_and_rejection_never_executes() -> None:
    client, executor = build_client()
    owner = {"Authorization": "Bearer token-a"}
    other = {"Authorization": "Bearer token-b"}
    draft = client.post(
        "/api/actions/drafts",
        headers=owner,
        json={
            "action_type": "post_teams_message",
            "payload": {"chat_id": "19:chat@thread.v2", "content": "Hello"},
        },
    ).json()
    action_id = draft["action_id"]

    assert client.get(f"/api/actions/{action_id}", headers=other).status_code == 404
    client.post(f"/api/actions/{action_id}/request-approval", headers=owner)
    rejected = client.post(
        f"/api/approvals/{action_id}/reject",
        headers=owner,
        json={"reason": "Needs revision"},
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert executor.calls == []
