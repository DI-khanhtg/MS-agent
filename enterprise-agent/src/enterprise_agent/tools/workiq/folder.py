"""Bounded, delegated listing of a Microsoft 365 folder and its subfolders."""

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.workiq.auth import WorkIQAuthenticationError, WorkIQTokenProvider
from enterprise_agent.tools.workiq.client import WorkIQClientError, WorkIQMCPClient
from enterprise_agent.tools.workiq.policy import validate_document_path, validate_read_path

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
CHILD_SELECT = "id,name,size,file,folder,webUrl"
MAX_DEPTH = 5
MAX_ENTRIES = 200
MAX_FOLDERS = 40
_GRAPH_PAGE = 200
_GRAPH_PAGES = 5
_WORKIQ_PAGE = 25  # Work IQ read policy caps $top and blocks continuation tokens.
_CONCURRENCY = 4
_ID = re.compile(r"[A-Za-z0-9!._~+-]{1,2048}")
_DRIVE_ITEM = re.compile(r"/drives/([^/]+)/items/[^/]+")


class FolderListError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class FolderEntry:
    path: str
    relative_path: str
    name: str
    is_folder: bool
    size_bytes: int | None
    web_url: str
    level: int
    expanded: bool = False


@dataclass(slots=True)
class FolderListing:
    entries: list[FolderEntry] = field(default_factory=list)
    incomplete_folders: list[str] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)


@dataclass(slots=True)
class _Pending:
    path: str
    prefix: str
    level: int


class MicrosoftFolderLister:
    """List with Graph for complete paging; fall back to Work IQ's first page per folder."""

    def __init__(
        self,
        client: WorkIQMCPClient,
        provider: WorkIQTokenProvider,
        graph_provider: WorkIQTokenProvider | None,
    ) -> None:
        self.client, self.provider, self.graph_provider = client, provider, graph_provider

    async def list_tree(
        self,
        root_path: str,
        *,
        max_depth: int,
        max_entries: int = MAX_ENTRIES,
        max_folders: int = MAX_FOLDERS,
    ) -> FolderListing:
        root = validate_document_path(root_path).removesuffix("/content")
        if not _DRIVE_ITEM.fullmatch(root):
            raise FolderListError("invalid_folder_path", "Use a resolved /drives/{id}/items/{id}.")
        graph_token = await self._graph_token()
        listing = FolderListing()
        level_folders = [_Pending(root, "", 1)]
        listed = 0
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def children(folder: _Pending):
            async with semaphore:
                return await self._children(folder.path, graph_token)

        while level_folders:
            batch: list[_Pending] = []
            for folder in level_folders:
                if listed >= max_folders:
                    listing.reasons.add("folder_limit")
                    self._mark_unexpanded(listing, folder)
                    continue
                listed += 1
                batch.append(folder)
            results = await asyncio.gather(
                *(children(folder) for folder in batch), return_exceptions=True
            )
            level_folders = []
            for folder, result in zip(batch, results, strict=True):
                if isinstance(result, BaseException):
                    recoverable = isinstance(result, (FolderListError, WorkIQClientError))
                    if folder.level == 1 or not recoverable:
                        raise result
                    listing.incomplete_folders.append(folder.prefix.rstrip("/"))
                    listing.reasons.add("folder_unreadable")
                    continue
                items, more = result
                if more:
                    listing.incomplete_folders.append(folder.prefix.rstrip("/") or ".")
                    listing.reasons.add("folder_has_more_items")
                for item in sorted(items, key=lambda i: (not i["folder"], i["name"].casefold())):
                    if len(listing.entries) >= max_entries:
                        listing.reasons.add("entry_limit")
                        break
                    relative = folder.prefix + item["name"]
                    child_path = f"/drives/{_DRIVE_ITEM.fullmatch(root)[1]}/items/{item['id']}"
                    expand = item["folder"] and folder.level < max_depth
                    listing.entries.append(
                        FolderEntry(
                            child_path,
                            relative,
                            item["name"],
                            item["folder"],
                            item["size"],
                            item["web_url"],
                            folder.level,
                            expanded=expand,
                        )
                    )
                    if expand:
                        level_folders.append(_Pending(child_path, relative + "/", folder.level + 1))
                    elif item["folder"]:
                        listing.reasons.add("depth_limit")
            if len(listing.entries) >= max_entries:
                # Folders already queued are kept as references but not expanded.
                if level_folders:
                    listing.reasons.add("entry_limit")
                for folder in level_folders:
                    self._mark_unexpanded(listing, folder)
                break
        return listing

    @staticmethod
    def _mark_unexpanded(listing: FolderListing, folder: _Pending) -> None:
        for entry in listing.entries:
            if entry.path == folder.path:
                entry.expanded = False

    async def _graph_token(self) -> str | None:
        if self.graph_provider is None:
            return None
        try:
            return await self.graph_provider.get_token(get_request_context().principal)
        except WorkIQAuthenticationError:
            return None

    async def _children(self, path: str, graph_token: str | None):
        if graph_token is not None:
            result = await self._graph_children(path, graph_token)
            if result is not None:
                return result
        return await self._workiq_children(path)

    async def _graph_children(self, path: str, token: str):
        """Return (items, has_more), or None when Graph access is unavailable."""
        base = f"{GRAPH_ROOT}{path}/children"
        url: str | None = f"{base}?$top={_GRAPH_PAGE}&$select={CHILD_SELECT}"
        items: list[dict] = []
        for _ in range(_GRAPH_PAGES):
            status, payload = await self._graph_get(url, token)
            if status in (401, 403):
                return None
            if status in (404, 410):
                raise FolderListError(
                    "folder_not_found", "Microsoft could not find this folder for the user."
                )
            if status == 429:
                raise FolderListError(
                    "folder_throttled", "Microsoft throttled folder listing; retry later."
                )
            if status != 200 or not isinstance(payload, dict):
                raise FolderListError(
                    "folder_list_failed", "Microsoft did not return the folder contents."
                )
            items.extend(_normalize(payload.get("value")))
            url = payload.get("@odata.nextLink")
            if not url:
                return items, False
            self._validate_next_link(url, base)
        return items, True

    async def _graph_get(self, url: str, token: str) -> tuple[int, dict | None]:
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.get(url, headers={"Authorization": "Bearer " + token})
            body = response.json() if response.status_code == 200 else None
            return response.status_code, body if isinstance(body, dict) else None
        except httpx.TimeoutException:
            raise TimeoutError("Folder listing timed out") from None
        except (httpx.HTTPError, ValueError):
            raise FolderListError(
                "folder_list_failed", "Microsoft folder listing failed."
            ) from None

    @staticmethod
    def _validate_next_link(url: object, base: str) -> None:
        parsed = urlsplit(url) if isinstance(url, str) and len(url) <= 8192 else None
        expected = urlsplit(base)
        if (
            parsed is None
            or parsed.scheme != "https"
            or parsed.hostname != expected.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path.casefold() != expected.path.casefold()
        ):
            raise FolderListError(
                "folder_list_failed", "Microsoft returned an unsupported continuation link."
            )

    async def _workiq_children(self, path: str):
        token = await self.provider.get_token(get_request_context().principal)
        target = validate_read_path(f"{path}/children?$top={_WORKIQ_PAGE}&$select={CHILD_SELECT}")
        result = await self.client.call_tool("fetch", {"entityUrls": [target]}, access_token=token)
        payload = result.get("structuredContent", {})
        rows = payload.get("results") if isinstance(payload, dict) else None
        if (
            result.get("isError")
            or not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
        ):
            raise FolderListError(
                "folder_list_failed", "Work IQ returned an invalid folder listing."
            )
        row = rows[0]
        status = row.get("statusCode")
        if status in (401, 403):
            raise FolderListError(
                "folder_access_denied",
                "Microsoft denied access to this folder. Check the user's permission and "
                "application consent; do not infer that it is empty.",
            )
        if status in (404, 410):
            raise FolderListError(
                "folder_not_found", "Microsoft could not find this folder for the user."
            )
        if status == 429:
            raise FolderListError(
                "folder_throttled", "Microsoft throttled folder listing; retry later."
            )
        data = row.get("data")
        if status != 200 or not isinstance(data, dict):
            raise FolderListError(
                "folder_list_failed", "Work IQ did not return the folder contents."
            )
        values = data.get("value")
        items = _normalize(values)
        more = bool(data.get("@odata.nextLink")) or (
            isinstance(values, list) and len(values) >= _WORKIQ_PAGE
        )
        return items, more


def _normalize(values: object) -> list[dict]:
    """Keep only well-formed files and folders with a safe ID and bounded name."""
    items = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        item_id, name = value.get("id"), value.get("name")
        is_folder = isinstance(value.get("folder"), dict)
        if (
            not isinstance(item_id, str)
            or not _ID.fullmatch(item_id)
            or item_id in (".", "..")
            or not isinstance(name, str)
            or not name
            or len(name) > 400
            or not (is_folder or isinstance(value.get("file"), dict))
        ):
            continue
        size = value.get("size")
        web_url = value.get("webUrl")
        items.append(
            {
                "id": item_id,
                "name": name,
                "folder": is_folder,
                "size": size if type(size) is int and size >= 0 else None,
                "web_url": web_url if isinstance(web_url, str) else "",
            }
        )
    return items
