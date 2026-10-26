import asyncio
import hashlib

import pytest
from fastapi.testclient import TestClient

from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.local.documents import LocalDocumentError


class LocalRuntime:
    async def chat(self, message, session_id, context=None):
        return AgentResult(text="offline", tool_calls=[])


class AccountValidator:
    async def validate(self, token):
        return Principal("tenant", token, token, None, frozenset({"access_as_user"}), "spa", token)


def make_client(tmp_path, **overrides):
    config = {
        "auth_enabled": True,
        "azure_tenant_id": "tenant",
        "azure_client_id": "api",
        "enabled_tool_groups": "local_read",
        "local_documents_root": tmp_path / "documents",
        "artifact_storage_path": tmp_path / "artifacts",
        "audit_log_path": tmp_path / "audit.jsonl",
    }
    config.update(overrides)
    return TestClient(
        create_app(
            settings=Settings(_env_file=None, **config),
            runtime=LocalRuntime(),
            token_validator=AccountValidator(),
        )
    )


def upload(client, files, user="alice", **kwargs):
    return client.post(
        "/api/local-documents/upload",
        files=[("files", (path, content, "application/octet-stream")) for path, content in files],
        headers={"Authorization": f"Bearer {user}", **kwargs.pop("headers", {})},
        **kwargs,
    )


def test_upload_requires_bearer_and_rejects_cross_origin_writes(tmp_path):
    client = make_client(tmp_path)
    assert (
        client.post("/api/local-documents/upload", files={"files": ("a.md", b"hello")}).status_code
        == 401
    )
    assert (
        upload(client, [("a.md", b"hello")], headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    assert not client.app.state.settings.local_documents_root.exists()


def test_uploaded_folders_are_readable_only_by_the_verified_owner(tmp_path):
    client = make_client(tmp_path)
    response = upload(client, [("import-1/Folder/a.md", b"Alice")])
    assert response.status_code == 200, response.text
    assert response.json() == {"uploaded_files": 1, "paths": ["import-1/Folder/a.md"]}
    assert upload(client, [("import-1/Folder/a.md", b"Bob")], user="bob").status_code == 200
    reader = client.app.state.local_document_tools
    for user, text in [("alice", "Alice"), ("bob", "Bob")]:
        principal = asyncio.run(AccountValidator().validate(user))
        token = set_request_context(RequestContext(principal))
        try:
            assert (
                asyncio.run(reader.read_local_document("import-1/Folder/a.md"))["data"]["text"]
                == text
            )
            assert asyncio.run(reader.list_local_documents())["data"]["listed_files"] == 1
            other = hashlib.sha256(
                f"tenant:{'bob' if user == 'alice' else 'alice'}".encode()
            ).hexdigest()
            with pytest.raises(LocalDocumentError):
                reader.read(f"../../{other}/import-1/Folder/a.md")
        finally:
            reset_request_context(token)
    with pytest.raises(LocalDocumentError):
        reader.read("import-1/Folder/a.md")


@pytest.mark.parametrize(
    "path",
    [
        "../escape.md",
        ".env",
        "Folder/.hidden/a.md",
        "C:/escape.md",
        "C:escape.md",
        "Folder\\a.md",
        "_users/other/a.md",
        "a.exe",
        "Folder/a.md:secret",
        "NUL.md",
    ],
)
def test_invalid_mixed_batch_writes_no_files(tmp_path, path):
    client = make_client(tmp_path)
    response = upload(client, [("Folder/good.md", b"valid"), (path, b"invalid")])
    assert response.status_code == 422, response.text
    assert not list(client.app.state.settings.local_documents_root.rglob("*.md"))


def test_cumulative_user_file_limit_and_existing_files_are_preserved(tmp_path):
    client = make_client(tmp_path, local_max_files=2)
    assert upload(client, [("one/a.md", b"first")]).status_code == 200
    assert upload(client, [("two/b.md", b"second"), ("one/a.md", b"replace")]).status_code == 422
    assert upload(client, [("two/b.md", b"second")]).status_code == 200
    assert upload(client, [("three/c.md", b"third")]).status_code == 422
    assert upload(client, [("one/a.md", b"Bob")], user="bob").status_code == 200
    assert sorted(
        path.read_bytes() for path in client.app.state.settings.local_documents_root.rglob("*.md")
    ) == [b"Bob", b"first", b"second"]


@pytest.mark.parametrize("limit,size", [(1024, 1025), (5_000_000, 2_000_001)])
def test_upload_enforces_configured_and_absolute_file_size_limits(tmp_path, limit, size):
    client = make_client(tmp_path, local_max_file_bytes=limit)
    assert upload(client, [("large.md", b"x" * size)]).status_code == 413
    assert not client.app.state.settings.local_documents_root.exists()


def test_upload_remains_subject_to_global_request_size_limit(tmp_path):
    client = make_client(tmp_path)
    assert upload(client, [("large.md", b"x" * 2_500_001)]).status_code == 413
    assert not client.app.state.settings.local_documents_root.exists()


@pytest.mark.parametrize(
    "overrides", [{"auth_enabled": False}, {"enabled_tool_groups": "mock_read"}]
)
def test_upload_requires_authenticated_local_read_mode(tmp_path, overrides):
    client = make_client(tmp_path, **overrides)
    assert upload(client, [("a.md", b"hello")]).status_code == 403
    assert not client.app.state.settings.local_documents_root.exists()
