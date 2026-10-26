import pytest
from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal


class Validator:
    async def validate(self, token):
        return Principal(
            "tenant",
            "alice",
            "alice",
            "alice@example.test",
            frozenset({"access_as_user"}),
            "frontend",
            token,
        )


class Runtime:
    message = None

    async def chat(self, message, session_id, context=None):
        self.message = message
        return AgentResult(text="ok", tool_calls=[])

    async def stream_chat(self, message, session_id, context=None):
        self.message = message
        yield "ok"


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_selected_folder_is_passed_to_model_without_polluting_visible_history(tmp_path, endpoint):
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        enabled_tool_groups="local_read",
        azure_tenant_id="tenant",
        entra_api_audience="backend",
        auth_allowed_tenant_ids="tenant",
        local_documents_root=tmp_path,
        artifact_storage_path=tmp_path / "artifacts",
    )
    runtime = Runtime()
    with TestClient(
        create_app(settings=settings, runtime=runtime, token_validator=Validator())
    ) as client:
        headers = {"Authorization": "Bearer alice-token"}
        upload = client.post(
            "/api/local-documents/upload",
            headers=headers,
            files=[("files", ("import/Project/requirements.txt", b"Project facts"))],
        )
        assert upload.status_code == 200
        response = client.post(
            endpoint,
            headers=headers,
            json={
                "message": "Summarize the selected folder",
                "session_id": "selected",
                "local_folder": "import/Project",
            },
        )
        assert response.status_code == 200
        assert "import/Project" in runtime.message
        history = client.get("/api/sessions/selected", headers=headers).json()
        assert history["messages"][0]["text"] == "Summarize the selected folder"
        runtime.message = None
        rejected = client.post(
            endpoint,
            headers=headers,
            json={
                "message": "Read private files",
                "local_folder": "../another-user",
            },
        )
        assert rejected.status_code == 422
        assert runtime.message is None
