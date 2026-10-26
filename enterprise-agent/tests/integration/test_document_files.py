import asyncio

import pytest
from fastapi.testclient import TestClient

from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)


def account():
    return Principal(
        "tenant",
        "reader",
        "reader",
        "reader@example.test",
        frozenset({"access_as_user"}),
        "frontend",
        "test-token",
    )


class Validator:
    async def validate(self, token):
        return account()


@pytest.mark.parametrize("kind", ["pdf", "docx", "pptx", "xlsx"])
def test_imported_office_file_can_be_read_with_coverage_metadata(tmp_path, document_files, kind):
    filename, content, expected_text = document_files[kind]
    settings = Settings(
        _env_file=None,
        auth_enabled=True,
        azure_tenant_id="tenant",
        entra_api_audience="backend",
        auth_allowed_tenant_ids="tenant",
        enabled_tool_groups="local_read",
        local_documents_root=tmp_path / "documents",
        artifact_storage_path=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    app = create_app(settings=settings, token_validator=Validator())
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer test-token"}
        config = client.get("/api/config", headers=headers).json()
        assert "." + kind in config["document_extensions"]
        response = client.post(
            "/api/local-documents/upload",
            headers=headers,
            files=[("files", ("import/" + filename, content))],
        )
        assert response.status_code == 200, response.text

        async def read():
            token = set_request_context(RequestContext(account()))
            try:
                return await app.state.local_document_tools.read_local_document(
                    "import/" + filename
                )
            finally:
                reset_request_context(token)

        result = asyncio.run(read())["data"]
        assert expected_text in result["text"]
        assert result["empty"] is False
        assert result["extraction"]["format"] == kind
        assert result["extraction"]["coverage"] == "text_only"
        assert result["source"] == "local://import/" + filename
