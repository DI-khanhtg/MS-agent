import base64
import importlib
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from enterprise_agent.agent.budget import turn_budget
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressBlockedError, EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError
from enterprise_agent.tools.workiq.client import WorkIQClientError
from enterprise_agent.tools.workiq.policy import WorkIQPolicyError, validate_read_path
from enterprise_agent.tools.workiq.tools import WorkIQReadTools

URL = "https://company-my.sharepoint.com/:p:/g/personal/owner/123456789012?e=sharedToken"
CANONICAL = "https://company-my.sharepoint.com/personal/owner/Documents/slides.pptx"
PATH = "/drives/1234567890123456/items/9876543210"


def drive_response(**changes):
    return {
        "structuredContent": {
            "results": [
                {
                    "statusCode": 200,
                    "data": {
                        "id": "9876543210",
                        "parentReference": {"driveId": "1234567890123456"},
                        "name": "slides.pptx",
                        "file": {},
                        "webUrl": CANONICAL,
                        **changes,
                    },
                }
            ]
        }
    }


@pytest.fixture
def environment():
    principal = Principal(
        "tenant-a",
        "user-a",
        "subject-a",
        None,
        frozenset({"access_as_user"}),
        "frontend",
        "user-assertion",
    )
    client, provider = AsyncMock(), AsyncMock()
    provider.get_token.return_value = "delegated-workiq-token"
    client.call_tool.return_value = drive_response()
    tools = WorkIQReadTools(
        client, provider, EgressGate.from_yaml(Settings(_env_file=None).egress_policy_path)
    )
    token = set_request_context(RequestContext(principal))
    try:
        with turn_budget(60, 5):
            yield tools, client, provider, principal
    finally:
        reset_request_context(token)


@pytest.mark.parametrize(
    "url",
    [
        URL,
        "https://company.sharepoint.com/sites/Team/shared.pptx",
        "https://1drv.ms/p/s!opaque?e=share",
    ],
)
def test_sharing_url_encoding_matches_the_microsoft_contract(url):
    module = importlib.import_module("enterprise_agent.tools.workiq.links")
    path = module.shared_item_path(url)
    assert path.startswith("/shares/u!")
    token = path.split("/")[2][2:]
    assert base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode() == url
    assert validate_read_path(path) == path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://company.sharepoint.com/file",
        "https://sharepoint.com.evil.example/file",
        "https://company.sharepoint.com@evil.example/file",
        "https://user:pass@company.sharepoint.com/file",
        "https://company.sharepoint.com:8443/file",
        "https://127.0.0.1/file",
        "https://company.sharepoint.com/[REDACTED]",
        "https://company.sharepoint.com/%5BREDACTED%5D",
        "https://company.sharepoint.com/../private",
        "https://company.sharepoint.com/file%0d%0aheader",
        "file:///C:/private",
        "https://company.sharepoint.com/" + "x" * 5000,
    ],
)
async def test_invalid_or_masked_links_fail_before_token_or_network(environment, url):
    tools, client, provider, _ = environment
    result = await tools.workiq_resolve_document_link(url=url)
    assert result["data"]["error"]["code"] == "invalid_shared_link"
    client.call_tool.assert_not_awaited()
    provider.get_token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pdf", "pptx", "docx", "xlsx"])
async def test_link_resolves_owner_reference_and_reads_actual_office_content(
    environment, document_files, kind
):
    tools, client, provider, principal = environment
    filename, content, expected = document_files[kind]
    client.call_tool.return_value = drive_response(name=filename)
    resolved = (await tools.workiq_resolve_document_link(url=URL))["data"]
    reference = resolved["document_id"]
    assert resolved["filename"] == filename
    assert resolved["web_url"] == CANONICAL
    assert URL not in str(resolved)
    provider.get_token.assert_awaited_once_with(principal)
    call = client.call_tool.call_args
    assert call.args[0] == "fetch"
    resource = call.args[1]["entityUrls"][0]
    assert resource.startswith("/shares/u!") and "/driveItem" in resource
    assert "query" not in call.args[1]  # No indexed retrieval.
    client.call_tool.return_value = {
        "structuredContent": {"base64Content": base64.b64encode(content).decode()}
    }
    result = await tools.workiq_read_document(document_id=reference)
    assert expected in result["data"]["text"]
    assert client.call_tool.call_args.args == ("fetch_blob", {"path": PATH + "/content"})


@pytest.mark.asyncio
async def test_link_resolution_reuses_metadata_and_ignores_search_quota(environment):
    from enterprise_agent.agent.budget import reserve_source_call

    tools, client, _, _ = environment
    assert reserve_source_call((id(tools), "search"), 2)
    assert reserve_source_call((id(tools), "search"), 2)
    first = await tools.workiq_resolve_document_link(url=URL)
    second = await tools.workiq_resolve_document_link(url=URL)
    assert first["data"]["document_id"] == second["data"]["document_id"]
    client.call_tool.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "link_access_denied"),
        (403, "link_access_denied"),
        (404, "link_not_found"),
        (429, "link_throttled"),
        (500, "link_resolution_failed"),
    ],
)
async def test_failed_link_resolution_reports_actual_stage(environment, status, code):
    tools, client, _, _ = environment
    client.call_tool.return_value = {
        "structuredContent": {
            "results": [{"statusCode": status, "data": {"error": {"message": "private-details"}}}]
        }
    }
    result = await tools.workiq_resolve_document_link(url=URL)
    assert result["data"]["error"]["code"] == code
    assert "private-details" not in str(result)
    assert "document_id" not in result["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"folder": {}}, "link_not_file"),
        ({"id": ""}, "invalid_link_response"),
        ({"parentReference": {}}, "invalid_link_response"),
        ({"id": "../../private"}, "invalid_link_response"),
    ],
)
async def test_folders_and_unresolved_or_unsafe_items_are_not_read(environment, changes, code):
    tools, client, _, _ = environment
    client.call_tool.return_value = drive_response(**changes)
    result = await tools.workiq_resolve_document_link(url=URL)
    assert result["data"]["error"]["code"] == code
    assert client.call_tool.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        (WorkIQAuthenticationError("private-details"), "link_authentication_failed"),
        (WorkIQClientError("private-details"), "link_resolution_unavailable"),
        (TimeoutError("private-details"), "link_resolution_timeout"),
    ],
)
async def test_provider_errors_are_safe(environment, failure, code):
    tools, _, provider, _ = environment
    provider.get_token.side_effect = failure
    result = await tools.workiq_resolve_document_link(url=URL)
    assert result["data"]["error"]["code"] == code
    assert "private-details" not in str(result)


@pytest.mark.asyncio
async def test_complete_security_metadata_is_checked_before_projection(environment):
    tools, client, _, _ = environment
    client.call_tool.return_value = drive_response(access_token="private-secret")
    with pytest.raises(EgressBlockedError):
        await tools.workiq_resolve_document_link(url=URL)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"tenant_id": "tenant-b"}, {"object_id": "user-b"}, {"access_token": "another-assertion"}],
)
async def test_private_link_handles_cannot_cross_identity_or_assertion(environment, change):
    tools, client, _, principal = environment
    module = importlib.import_module("enterprise_agent.tools.workiq.links")
    protected = module.protect_shared_links("Read " + URL)
    reference = protected.split("link_id=")[1].split()[0].rstrip(")")
    other = set_request_context(RequestContext(replace(principal, **change)))
    try:
        result = await tools.workiq_resolve_document_link(link_id=reference)
        assert result["data"]["error"]["code"] == "link_reference_expired"
        client.call_tool.assert_not_awaited()
    finally:
        reset_request_context(other)


@pytest.mark.parametrize(
    "path",
    [
        "/shares/u!abc/driveItem/children",
        "/shares/u!abc/permission/grant",
        "/shares/u!abc/driveItem?$expand=children",
        "/shares/arbitrary/driveItem",
    ],
)
def test_share_policy_does_not_enable_other_share_routes(path):
    with pytest.raises(WorkIQPolicyError):
        validate_read_path(path)


@pytest.mark.asyncio
async def test_large_linked_pptx_uses_bounded_backend_download_once(environment, document_files):
    tools, client, _, _ = environment
    filename, content, expected = document_files["pptx"]
    client.call_tool.return_value = drive_response(name=filename, size=9_525_377)
    resolved = (await tools.workiq_resolve_document_link(url=URL))["data"]
    tools.document_downloader.download = AsyncMock(return_value=content)
    first = await tools.workiq_read_document(document_id=resolved["document_id"], max_chars=14000)
    second = await tools.workiq_read_document(document_id=resolved["document_id"], max_chars=14000)
    assert expected in first["data"]["text"] and expected in second["data"]["text"]
    tools.document_downloader.download.assert_awaited_once_with(PATH, 9_525_377)
    assert client.call_tool.await_count == 1


def test_overflow_valid_links_are_not_exported_to_the_model(environment):
    module = importlib.import_module("enterprise_agent.tools.workiq.links")
    urls = [URL + str(index) for index in range(9)]
    protected = module.protect_shared_links("\n".join(urls))
    assert all(url not in protected for url in urls)
    assert "link limit" in protected


def test_links_are_not_exported_when_no_reference_scope_is_active():
    module = importlib.import_module("enterprise_agent.tools.workiq.links")
    protected = module.protect_shared_links("Read " + URL)
    assert URL not in protected


@pytest.mark.asyncio
async def test_large_link_reports_graph_consent_requirement_when_not_configured(environment):
    tools, client, _, _ = environment
    client.call_tool.return_value = drive_response(size=9_525_377)
    reference = (await tools.workiq_resolve_document_link(url=URL))["data"]["document_id"]
    result = await tools.workiq_read_document(document_id=reference)
    assert result["data"]["error"]["code"] == "document_graph_consent_required"
