from pathlib import Path

from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings


class FakeRuntime:
    async def chat(self, message: str, session_id: str, context: object = None) -> AgentResult:
        return AgentResult(text=message, tool_calls=[])


def test_artifact_create_list_and_download(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        artifact_storage_path=tmp_path / "artifacts",
        artifact_tool_node_modules=tmp_path / "missing-node-modules",
    )
    client = TestClient(create_app(settings=settings, runtime=FakeRuntime()))
    payload = {
        "artifact_type": "markdown",
        "title": "Project X Status",
        "sections": [{"heading": "Summary", "body": "On track."}],
    }

    response = client.post("/api/artifacts", json=payload)

    assert response.status_code == 201
    record = response.json()
    assert record["download_url"].endswith("/download")
    assert client.get("/api/artifacts").json()[0]["artifact_id"] == record["artifact_id"]
    download = client.get(record["download_url"])
    assert download.status_code == 200
    assert download.content.startswith(b"# Project X Status")


def test_artifact_api_rejects_invalid_schema(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, artifact_storage_path=tmp_path / "artifacts")
    client = TestClient(create_app(settings=settings, runtime=FakeRuntime()))

    response = client.post(
        "/api/artifacts",
        json={"artifact_type": "docx", "title": "Empty report"},
    )

    assert response.status_code == 422

