import base64
from unittest.mock import AsyncMock

import pytest

from enterprise_agent.agent.budget import turn_budget
from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)
from enterprise_agent.tools.workiq.client import WorkIQClientError
from enterprise_agent.tools.workiq.tools import WorkIQReadTools

URL = "https://company.sharepoint.com/sites/Team/Shared%20Documents/Azure"
DRIVE = "/drives/drive1/items/"
ROOT, SUB, DEEP = DRIVE + "root1", DRIVE + "sub1", DRIVE + "deep1"


def child(item_id, name, *, folder=False, size=100):
    item = {"id": item_id, "name": name, "webUrl": "https://company.sharepoint.com/x/" + name}
    if folder:
        return {**item, "folder": {"childCount": 1}}
    return {**item, "file": {}, "size": size}


TREE = {
    ROOT: [
        child("sub1", "Sub", folder=True),
        child("a1", "a.docx"),
        child("big1", "big.pdf", size=30_000_000),
        child("bin1", "tool.bin"),
    ],
    SUB: [child("b1", "b.txt"), child("deep1", "Deep", folder=True)],
    DEEP: [child("c1", "c.md")],
}
FILES = {DRIVE + "b1": b"Budget is 42 units", DRIVE + "c1": b"Deep notes"}


def rows(items, status=200):
    return {"structuredContent": {"results": [{"statusCode": status, "data": {"value": items}}]}}


def folder_link_response():
    data = {
        "id": "root1",
        "name": "Azure",
        "folder": {"childCount": 4},
        "parentReference": {"driveId": "drive1"},
        "webUrl": URL,
    }
    return {"structuredContent": {"results": [{"statusCode": 200, "data": data}]}}


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
    tree = dict(TREE)
    client, provider = AsyncMock(), AsyncMock()
    provider.get_token.return_value = "delegated-workiq-token"

    async def call_tool(name, arguments, access_token=None):
        if name == "fetch_blob":
            return {
                "structuredContent": {
                    "base64Content": base64.b64encode(
                        FILES[arguments["path"].removesuffix("/content")]
                    ).decode()
                }
            }
        path = arguments["entityUrls"][0]
        if path.startswith("/shares/"):
            return folder_link_response()
        folder = path.split("/children")[0]
        if folder not in tree:
            return rows([], status=403)
        return rows(tree[folder])

    client.call_tool.side_effect = call_tool
    tools = WorkIQReadTools(
        client, provider, EgressGate.from_yaml(Settings(_env_file=None).egress_policy_path)
    )
    token = set_request_context(RequestContext(principal))
    try:
        with turn_budget(60, 5):
            yield tools, client, tree
    finally:
        reset_request_context(token)


async def resolve(tools):
    return (await tools.workiq_resolve_document_link(url=URL))["data"]


@pytest.mark.asyncio
async def test_folder_link_returns_a_folder_handle_not_an_error(environment):
    tools, client, _ = environment
    resolved = await resolve(tools)

    assert resolved["item_type"] == "folder"
    assert resolved["folder_name"] == "Azure"
    assert resolved["child_count"] == 4
    assert resolved["content_read"] is False
    assert "document_id" not in resolved and "error" not in resolved
    assert client.call_tool.await_count == 1


@pytest.mark.asyncio
async def test_listing_recurses_into_subfolders_with_relative_paths(environment):
    tools, _, _ = environment
    folder_id = (await resolve(tools))["folder_id"]

    result = (await tools.workiq_list_folder(folder_id))["data"]

    paths = {entry["path"]: entry for entry in result["entries"]}
    assert set(paths) == {
        "Sub",
        "a.docx",
        "big.pdf",
        "tool.bin",
        "Sub/b.txt",
        "Sub/Deep",
        "Sub/Deep/c.md",
    }
    assert result["total_files"] == 5 and result["total_folders"] == 2
    assert result["truncated"] is False and result["next_offset"] is None
    assert paths["a.docx"]["readable"] is True
    assert paths["big.pdf"]["readable"] is False  # above the 20 MB cap
    assert paths["tool.bin"]["readable"] is False  # unsupported type
    assert paths["Sub"]["expanded"] and paths["Sub/Deep"]["expanded"]


@pytest.mark.asyncio
async def test_listed_files_are_read_in_one_batch_with_isolated_failures(environment):
    tools, _, _ = environment
    folder_id = (await resolve(tools))["folder_id"]
    entries = {e["path"]: e for e in (await tools.workiq_list_folder(folder_id))["data"]["entries"]}

    batch = await tools.workiq_read_documents(
        [entries["Sub/b.txt"]["document_id"], entries["Sub/Deep/c.md"]["document_id"], "bogus"]
    )

    texts = [d["result"]["data"].get("text") for d in batch["documents"]]
    assert texts[:2] == ["Budget is 42 units", "Deep notes"]
    assert batch["documents"][2]["result"]["data"]["error"]["code"] == "invalid_document_reference"


@pytest.mark.asyncio
async def test_depth_limit_leaves_subfolders_closed_but_listable(environment):
    tools, _, _ = environment
    folder_id = (await resolve(tools))["folder_id"]

    shallow = (await tools.workiq_list_folder(folder_id, max_depth=1))["data"]
    sub = next(e for e in shallow["entries"] if e["path"] == "Sub")
    assert sub["expanded"] is False
    assert "depth_limit" in shallow["truncation_reasons"] and shallow["truncated"] is True
    assert all(not e["path"].startswith("Sub/") for e in shallow["entries"])

    opened = (await tools.workiq_list_folder(sub["document_id"], max_depth=2))["data"]
    assert {e["path"] for e in opened["entries"]} == {"b.txt", "Deep", "Deep/c.md"}


@pytest.mark.asyncio
async def test_large_folders_are_paged_and_handles_survive_beyond_one_page(environment):
    tools, _, tree = environment
    tree[ROOT] = [child(f"f{n}", f"file{n:02}.txt") for n in range(25)]  # a full Work IQ page
    folder_id = (await resolve(tools))["folder_id"]

    first = (await tools.workiq_list_folder(folder_id))["data"]
    assert len(first["entries"]) == 25 and first["next_offset"] is None
    assert first["truncated"] and "folder_has_more_items" in first["truncation_reasons"]

    tree[ROOT] = [child(f"f{n}", f"file{n:02}.txt") for n in range(10)] + [
        child(f"g{n}", f"g{n}", folder=True) for n in range(20)
    ]
    for n in range(20):
        tree[DRIVE + f"g{n}"] = [child(f"h{n}", f"nested{n}.txt")]
    paged = (await tools.workiq_list_folder(folder_id, max_depth=2))["data"]
    assert paged["total_entries"] == 50 and paged["next_offset"] == 25
    second = (await tools.workiq_list_folder(folder_id, max_depth=2, offset=25))["data"]
    assert len(second["entries"]) == 25 and second["next_offset"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("has_subfolder", [True, False])
async def test_exact_entry_limit_reports_pending_subfolders(environment, has_subfolder):
    tools, _, tree = environment
    tree[ROOT] = [child("a1", "a.txt"), child("b1", "b.txt")]
    tree[ROOT].append(
        child("sub1", "Sub", folder=True) if has_subfolder else child("c1", "c.txt")
    )

    listing = await tools.folder_lister.list_tree(ROOT, max_depth=3, max_entries=3)

    assert len(listing.entries) == 3
    assert ("entry_limit" in listing.reasons) is has_subfolder
    if has_subfolder:
        assert next(entry for entry in listing.entries if entry.name == "Sub").expanded is False


@pytest.mark.asyncio
async def test_unreadable_subfolder_is_reported_not_fatal(environment):
    tools, _, tree = environment
    del tree[SUB]
    folder_id = (await resolve(tools))["folder_id"]

    result = (await tools.workiq_list_folder(folder_id))["data"]

    assert {e["path"] for e in result["entries"]} == {"Sub", "a.docx", "big.pdf", "tool.bin"}
    assert result["incomplete_folders"] == ["Sub"]
    assert "folder_unreadable" in result["truncation_reasons"]


@pytest.mark.asyncio
async def test_root_failure_is_a_safe_error(environment):
    tools, client, _ = environment
    folder_id = (await resolve(tools))["folder_id"]
    original = client.call_tool.side_effect

    async def failing(name, arguments, access_token=None):
        if "/children" in arguments["entityUrls"][0]:
            raise WorkIQClientError("private-details")
        return await original(name, arguments, access_token)

    client.call_tool.side_effect = failing
    result = await tools.workiq_list_folder(folder_id)

    assert result["data"]["error"]["code"] == "folder_list_failed"
    assert "private-details" not in str(result)


@pytest.mark.asyncio
async def test_references_are_validated_and_folders_are_not_readable_as_files(environment):
    tools, _, _ = environment
    folder_id = (await resolve(tools))["folder_id"]

    assert (await tools.workiq_list_folder("wiq_unknown"))["data"]["error"]["code"] == (
        "invalid_folder_reference"
    )
    assert (await tools.workiq_list_folder(folder_id, max_depth=9))["data"]["error"]["code"] == (
        "invalid_depth"
    )
    assert (await tools.workiq_read_document(document_id=folder_id))["data"]["error"]["code"] == (
        "reference_is_folder"
    )
    assert (await tools.workiq_read_documents([]))["data"]["error"]["code"] == (
        "invalid_batch_request"
    )


@pytest.mark.asyncio
async def test_graph_listing_is_used_for_complete_paging_and_falls_back_on_denial(environment):
    tools, client, _ = environment
    graph = AsyncMock()
    graph.get_token.return_value = "graph-token"
    tools.folder_lister.graph_provider = graph
    pages = {
        None: (
            200,
            {
                "value": TREE[ROOT][:2],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0"
                + ROOT
                + "/children?$skiptoken=abc",
            },
        ),
        "abc": (200, {"value": TREE[ROOT][2:]}),
    }

    async def graph_get(url, token):
        assert token == "graph-token"
        return pages["abc" if "skiptoken" in url else None]

    tools.folder_lister._graph_get = graph_get
    folder_id = (await resolve(tools))["folder_id"]
    result = (await tools.workiq_list_folder(folder_id, max_depth=1))["data"]
    assert result["total_entries"] == 4  # both Graph pages were combined
    assert not any(
        "/children" in call.args[1].get("entityUrls", [""])[0]
        for call in client.call_tool.await_args_list
    )

    async def denied(url, token):
        return 403, None

    tools.folder_lister._graph_get = denied
    other = (await tools.workiq_list_folder(folder_id, max_depth=1))["data"]
    assert other["total_entries"] == 4  # served by Work IQ instead


@pytest.mark.asyncio
async def test_graph_continuation_links_to_other_hosts_are_rejected(environment):
    tools, _, _ = environment
    graph = AsyncMock()
    graph.get_token.return_value = "graph-token"
    tools.folder_lister.graph_provider = graph

    async def graph_get(url, token):
        return 200, {
            "value": [],
            "@odata.nextLink": "https://evil.example/v1.0" + ROOT + "/children",
        }

    tools.folder_lister._graph_get = graph_get
    folder_id = (await resolve(tools))["folder_id"]
    result = await tools.workiq_list_folder(folder_id)

    assert result["data"]["error"]["code"] == "folder_list_failed"
