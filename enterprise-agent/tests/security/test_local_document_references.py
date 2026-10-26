from contextlib import contextmanager

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.local.documents import LocalDocumentTools


def tools(tmp_path, **overrides):
    settings = Settings(
        _env_file=None, local_documents_root=tmp_path, enabled_tool_groups="local_read", **overrides
    )
    return LocalDocumentTools(settings, EgressGate.from_yaml(settings.egress_policy_path))


@contextmanager
def account(user):
    token = set_request_context(
        RequestContext(Principal("tenant", user, user, None, frozenset(), None, "token"))
    )
    try:
        yield
    finally:
        reset_request_context(token)


@pytest.mark.asyncio
async def test_numeric_file_and_folder_remain_readable_after_egress(tmp_path, document_files):
    reader = tools(tmp_path, auth_enabled=True)
    _, content, expected = document_files["pdf"]
    with account("alice"):
        reader.import_documents([("import-1234567890/1571309665.pdf", content)])
        inventory = (await reader.list_local_documents())["data"]
        item = inventory["files"][0]
        assert "1571309665" not in item["path"]
        reference = item["document_id"]
        assert "[REDACTED]" not in reference
        result = (await reader.read_local_document(document_id=reference))["data"]
        assert expected in result["text"]
        assert result["document_id"] == reference
        assert result["extraction"]["format"] == "pdf"
        assert "1571309665" not in str(result)


@pytest.mark.asyncio
async def test_references_are_account_scoped_and_survive_tool_recreation(tmp_path):
    reader = tools(tmp_path, auth_enabled=True)
    with account("alice"):
        reader.import_documents([("1571309665.txt", b"Alice private text")])
        reference = (await reader.list_local_documents())["data"]["files"][0]["document_id"]
        restarted = LocalDocumentTools(reader.settings, reader.gate)
        result = (await restarted.read_local_document(document_id=reference))["data"]
        assert result["text"] == "Alice private text"
    with account("bob"):
        reader.import_documents([("1571309665.txt", b"Bob private text")])
        result = (await reader.read_local_document(document_id=reference))["data"]
        assert "error" in result
        assert "Alice" not in str(result)
        bob_reference = (await reader.list_local_documents())["data"]["files"][0]["document_id"]
        assert bob_reference != reference


@pytest.mark.asyncio
async def test_masked_path_returns_recovery_instructions_instead_of_ocr_guess(tmp_path):
    reader = tools(tmp_path)
    token = set_request_context(RequestContext(Principal.anonymous()))
    try:
        result = (await reader.read_local_document("import/[REDACTED].pdf"))["data"]
    finally:
        reset_request_context(token)
    assert result["error"]["code"] == "masked_document_path"
    assert "document_id" in result["error"]["message"]
    assert "OCR" not in result["error"]["message"]


@pytest.mark.asyncio
async def test_document_id_supports_chunks_and_detects_removed_files(tmp_path):
    reader = tools(tmp_path, auth_enabled=True)
    with account("alice"):
        reader.import_documents([("1571309665.txt", b"a" * 7000)])
        reference = (await reader.list_local_documents())["data"]["files"][0]["document_id"]
        first = (await reader.read_local_document(document_id=reference))["data"]
        assert first["next_offset"] == 3500
        last = (await reader.read_local_document(document_id=reference, offset=3500))["data"]
        assert len(first["text"] + last["text"]) == 7000
        assert last["next_offset"] is None
        (reader.root / "1571309665.txt").unlink()
        removed = (await reader.read_local_document(document_id=reference))["data"]
        assert "missing" in removed["error"]["message"].lower()
        assert "text" not in removed


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["../.env", "doc_[REDACTED]", "doc_" + "a" * 64])
async def test_document_ids_cannot_read_arbitrary_or_guessed_paths(tmp_path, reference):
    reader = tools(tmp_path, auth_enabled=True)
    with account("alice"):
        reader.import_documents([("valid.txt", b"private text")])
        result = (await reader.read_local_document(document_id=reference))["data"]
        assert "error" in result
        assert "private text" not in str(result)


@pytest.mark.asyncio
async def test_scoped_inventory_ids_remain_readable_after_root_inventory_cap(tmp_path):
    reader = tools(tmp_path, local_max_files=2)
    (tmp_path / "a.txt").write_text("first")
    (tmp_path / "b.txt").write_text("second")
    (tmp_path / "z").mkdir()
    (tmp_path / "z" / "1571309665.txt").write_text("scoped document")
    token = set_request_context(RequestContext(Principal.anonymous()))
    try:
        assert (await reader.list_local_documents())["data"]["scan_capped"] is True
        scoped = (await reader.list_local_documents(folder="z"))["data"]
        reference = scoped["files"][0]["document_id"]
        result = (await reader.read_local_document(document_id=reference))["data"]
        assert result["text"] == "scoped document"
        assert len(reader._document_references) <= reader._reference_limit
    finally:
        reset_request_context(token)
