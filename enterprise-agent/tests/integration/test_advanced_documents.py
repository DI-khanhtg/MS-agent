import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from enterprise_agent.config import Settings
from enterprise_agent.security.identity import (
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.document_formats import CONVERSIONS, DocumentLimits
from enterprise_agent.tools.document_sandbox import SandboxClient
from enterprise_agent.tools.document_worker import process_document
from tests.fixtures.document_corpus import readable_corpus
from tests.integration.test_data_lifecycle import Validator, headers, make_app


@pytest.fixture
def offline_sandbox(monkeypatch, tmp_path):
    """In-process test double only; production has no inline advanced-parser switch."""
    jobs = []

    def extract(self, content, suffix, limits):
        directory = tmp_path / f"test-job-{len(jobs)}"
        directory.mkdir()
        jobs.append(directory)
        try:
            return process_document(content, suffix, limits, directory)
        finally:
            directory.rmdir()

    monkeypatch.setattr(SandboxClient, "extract", extract)
    return jobs


def test_email_upload_handles_privacy_artifact_and_user_deletion(tmp_path, offline_sandbox):
    app = make_app(
        tmp_path,
        document_sandbox_enabled=True,
        document_sandbox_image="sha256:" + "a" * 64,
        document_sandbox_all_formats=False,
    )
    message = next(fixture for fixture in readable_corpus() if fixture.name == "mail-1.eml")
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/local-documents/upload",
                headers=headers(),
                files={"files": ("mail.eml", message.content, "message/rfc822")},
            ).status_code
            == 200
        )
        reader = app.state.local_document_tools
        token = set_request_context(
            RequestContext(asyncio.run(Validator().validate("tenant:alice")))
        )
        try:
            reference = asyncio.run(reader.list_local_documents())["data"]["files"][0][
                "document_id"
            ]
            result = asyncio.run(reader.read_local_document(document_id=reference))["data"]
            assert result["trust"] == "untrusted_document_data"
            assert "sender@example.test" not in str(result)
            assert "reader@example.test" not in str(result)
            assert result["extraction"]["format"] == "eml"
            assert "Email body" in result["text"]
        finally:
            reset_request_context(token)
        token = set_request_context(RequestContext(asyncio.run(Validator().validate("tenant:bob"))))
        try:
            assert "error" in asyncio.run(reader.read_local_document(document_id=reference))["data"]
        finally:
            reset_request_context(token)
        artifact = client.post(
            "/api/artifacts",
            headers=headers(),
            json={
                "artifact_type": "markdown",
                "title": "Email report",
                "sections": [{"heading": "Summary", "body": "Readable email summary"}],
            },
        )
        assert artifact.status_code == 201
        assert client.delete("/api/me/data", headers=headers()).status_code == 204
        assert not list((tmp_path / "documents").rglob("*.eml"))
        assert not reader._document_references
        assert all(not job.exists() for job in offline_sandbox)


def test_upload_rejects_mime_mismatch_and_disabled_format(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        result = client.post(
            "/api/local-documents/upload",
            headers=headers(),
            files={"files": ("hello.txt", b"hello", "application/pdf")},
        )
        assert result.status_code == 422
        assert (
            client.post(
                "/api/local-documents/upload",
                headers=headers(),
                files={"files": ("macro.docm", b"fake")},
            ).status_code
            == 422
        )
        assert (
            ".eml" not in client.get("/api/config", headers=headers()).json()["document_extensions"]
        )
        assert not list((tmp_path / "documents").rglob("*.txt"))


@pytest.mark.asyncio
async def test_workiq_email_uses_the_same_parser_and_sanitizes_headers(tmp_path, offline_sandbox):
    import base64

    from enterprise_agent.security.egress import EgressGate
    from enterprise_agent.tools.workiq.tools import WorkIQReadTools

    message = next(fixture for fixture in readable_corpus() if fixture.name == "mail-1.eml")
    settings = Settings(
        _env_file=None,
        document_sandbox_enabled=True,
        document_sandbox_image="sha256:" + "a" * 64,
        document_sandbox_all_formats=False,
    )

    class Client:
        async def call_tool(self, name, arguments, **kwargs):
            assert name == "fetch_blob"
            return {
                "structuredContent": {"base64Content": base64.b64encode(message.content).decode()}
            }

    class Tokens:
        async def get_token(self, principal):
            return "offline-obo-token"

    reader = WorkIQReadTools(
        Client(), Tokens(), EgressGate.from_yaml(settings.egress_policy_path), settings=settings
    )
    token = set_request_context(RequestContext(await Validator().validate("tenant:alice")))
    try:
        result = await reader.workiq_read_document(
            path="/me/drive/items/item-one", filename="mail.eml"
        )
        assert result["data"]["trust"] == "untrusted_document_data"
        assert "Email body" in result["data"]["text"]
        assert "sender@example.test" not in str(result)
        assert "reader@example.test" not in str(result)
    finally:
        reset_request_context(token)


@pytest.mark.parametrize("suffix,target", CONVERSIONS.items())
def test_conversion_routes_to_existing_parser_and_preserves_metadata(
    suffix, target, document_files, monkeypatch, tmp_path
):
    from enterprise_agent.tools import document_conversion

    calls = []

    def convert(content, extension, directory, **kwargs):
        calls.append((content, extension, directory))
        return document_files[target[1:]][1], target

    monkeypatch.setattr(document_conversion, "convert_document", convert)
    if suffix in {".doc", ".ppt", ".xls"}:
        filename = {".doc": "simple.doc", ".ppt": "basic_test.ppt", ".xls": "simple.xls"}[suffix]
        content = (Path(__file__).parents[1] / "fixtures" / "legacy" / filename).read_bytes()
    else:
        content = next(
            fixture.content for fixture in readable_corpus() if fixture.name.endswith(suffix)
        )
    result = process_document(content, suffix, DocumentLimits(), tmp_path)
    assert calls and calls[0][0] == content
    assert result.format == suffix[1:]
    assert result.metadata["converted_to"] == target[1:]
    assert result.metadata["method"] == "libreoffice_conversion"
    assert document_files[target[1:]][2] in result.text
