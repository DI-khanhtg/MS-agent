"""Bounded document reads within a configured root, with source and coverage metadata."""

import asyncio
import hashlib
import os
import re
import stat
from collections import OrderedDict
from pathlib import Path
from threading import RLock

from enterprise_agent.config import Settings
from enterprise_agent.security.egress import EgressGate
from enterprise_agent.security.identity import get_request_context
from enterprise_agent.tools.document_reader import (
    SUPPORTED,
    extract_document,
    extract_text,
)
from enterprise_agent.tools.document_reader import (
    DocumentExtractionError as LocalDocumentError,
)
from enterprise_agent.tools.recording import record_tool_call

_REFERENCE_ALPHABET = str.maketrans("0123456789abcdef", "abcdefghijklmnop")


def _is_document_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    # Windows junctions/reparse points also need rejecting on Python 3.11.
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


class LocalDocumentTools:
    def __init__(self, settings: Settings, gate: EgressGate) -> None:
        self.settings, self.gate = settings, gate
        self._base_root = settings.local_documents_root.absolute()
        self._import_lock = RLock()
        self._reference_limit = max(25, min(settings.local_max_files * 4, 10_000))
        self._document_references: OrderedDict[tuple[Path, str], str] = OrderedDict()

    @staticmethod
    def _reject_links(path: Path) -> None:
        for current in (*reversed(path.parents), path):
            if _is_document_link(current):
                raise LocalDocumentError("Document links are not supported")

    @property
    def root(self) -> Path:
        try:
            principal = get_request_context().principal
        except RuntimeError:
            if self.settings.auth_enabled:
                raise LocalDocumentError(
                    "A verified account is required for local documents"
                ) from None
            principal = None
        if principal is not None and principal.authenticated:
            owner = hashlib.sha256(principal.isolation_key.encode("utf-8")).hexdigest()
            root = self._base_root / "_users" / owner
        elif self.settings.auth_enabled:
            raise LocalDocumentError("A verified account is required for local documents")
        else:
            root = self._base_root
        self._reject_links(root)
        return root.resolve()

    def resolve(self, relative: str) -> Path:
        if len(relative) > 1024:
            raise LocalDocumentError("Document path exceeds the length limit")
        parts = relative.split("/") if relative else []
        reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10))
        if any(
            not part
            or len(part) > 255
            or part.startswith(".")
            or part.endswith((".", " "))
            or part.casefold() == "_users"
            or part.split(".")[0].upper() in reserved
            or any(char in '<>:"\\|?*' or ord(char) < 32 for char in part)
            for part in parts
        ):
            raise LocalDocumentError("Use a non-hidden relative path within the document root")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or any(p.startswith(".") for p in path.parts):
            raise LocalDocumentError("Use a non-hidden relative path within the document root")
        root = self.root
        candidate = (root / path).resolve()
        if candidate != root and root not in candidate.parents:
            raise LocalDocumentError("Path escapes the configured document root")
        current = root
        for part in path.parts:
            current = current / part
            if _is_document_link(current):
                raise LocalDocumentError("Document links are not supported")
        return candidate

    @staticmethod
    def _document_id(root: Path, relative: str) -> str:
        # Hashes reveal neither paths nor names. Letters survive numeric PII redaction.
        digest = hashlib.sha256((str(root) + "\0" + relative).encode("utf-8")).hexdigest()
        return "doc_" + digest.translate(_REFERENCE_ALPHABET)

    def _inventory(self, folder: str = "") -> tuple[list[dict], int, bool]:
        target = self.resolve(folder)
        if not target.is_dir():
            raise LocalDocumentError("Folder does not exist in the configured document root")
        root = self.root
        files, skipped, capped = [], 0, False
        for parent, dirs, names in os.walk(target, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and d.casefold() != "_users"
                and not _is_document_link(Path(parent) / d)
            )
            for name in sorted(names):
                path = Path(parent) / name
                if (
                    name.startswith(".")
                    or _is_document_link(path)
                    or path.suffix.lower() not in SUPPORTED
                ):
                    skipped += 1
                    continue
                if len(files) >= self.settings.local_max_files:
                    capped = True
                    break
                files.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "document_id": self._document_id(root, path.relative_to(root).as_posix()),
                        "bytes": path.stat().st_size,
                        "format": path.suffix.lower(),
                    }
                )
            if capped:
                break
        # Retain IDs issued by scoped listings even when the root inventory is capped.
        # This is bounded backend state; every read still revalidates the current root/path.
        with self._import_lock:
            for item in files:
                key = (root, item["document_id"])
                self._document_references[key] = item["path"]
                self._document_references.move_to_end(key)
            while len(self._document_references) > self._reference_limit:
                self._document_references.popitem(last=False)
        return files, skipped, capped

    def manifest(self, folder: str = "", offset: int = 0, limit: int = 25) -> dict:
        if offset < 0 or not 1 <= limit <= 25:
            raise LocalDocumentError("Invalid inventory page bounds")
        files, skipped, capped = self._inventory(folder)
        return {
            "files": files[offset : offset + limit],
            "listed_files": len(files),
            "skipped_files": skipped,
            "scan_capped": capped,
            "next_offset": offset + limit if offset + limit < len(files) else None,
            "coverage": "inventory_only; file contents have not been read",
        }

    def _path_for_document_id(self, document_id: str) -> str:
        if not re.fullmatch(r"doc_[a-p]{64}", document_id):
            raise LocalDocumentError(
                "Invalid document_id; list_local_documents again and copy its ID"
            )
        root = self.root
        key = (root, document_id)
        with self._import_lock:
            cached = self._document_references.get(key)
            if cached is not None:
                self._document_references.move_to_end(key)
                return cached
        files, _, _ = self._inventory()
        for item in files:
            if item["document_id"] == document_id:
                return item["path"]
        raise LocalDocumentError(
            "Document ID not found in this account's inventory; list_local_documents again "
            "for the original folder. Scoped references beyond a capped root inventory must "
            "be listed again after server restart or cache eviction."
        )

    def read(self, path: str, offset: int = 0, limit: int = 3500) -> dict:
        target = self.resolve(path)
        if not target.is_file() or target.suffix.lower() not in SUPPORTED:
            raise LocalDocumentError("Unsupported or missing document")
        if not 0 <= offset <= 100_000 or not 1 <= limit <= 3500:
            raise LocalDocumentError("Invalid document chunk bounds")
        with target.open("rb") as file:
            content = file.read(self.settings.local_max_file_bytes + 1)
        if len(content) > self.settings.local_max_file_bytes:
            raise LocalDocumentError("File exceeds the configured size limit")
        try:
            document = extract_document(content, target.suffix.lower())
            text = document.text
        except LocalDocumentError:
            raise
        except Exception as exc:
            raise LocalDocumentError("Document could not be extracted") from exc
        return {
            "source": f"local://{path}",
            "path": path,
            "document_id": self._document_id(self.root, path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "text": text[offset : offset + limit],
            "total_chars": len(text),
            "next_offset": offset + limit if offset + limit < len(text) else None,
            "empty": not text.strip(),
            "extraction": document.metadata,
            "trust": "untrusted_document_data",
        }

    def import_documents(self, files: list[tuple[str, bytes]]) -> dict:
        """Import one validated batch into the verified account's private development root."""
        try:
            principal = get_request_context().principal
        except RuntimeError:
            raise LocalDocumentError("A verified account is required for imports") from None
        if not principal.authenticated:
            raise LocalDocumentError("A verified account is required for imports")
        if self.settings.app_env != "development" or "local_read" not in self.settings.tool_groups:
            raise LocalDocumentError("Local document imports are disabled")
        if not files or len(files) > self.settings.local_max_files:
            raise LocalDocumentError("Import file count exceeds the configured limit")
        with self._import_lock:
            root = self.root
            targets: list[tuple[Path, bytes]] = []
            names: set[str] = set()
            for relative, content in files:
                target = self.resolve(relative)
                if not relative or target.suffix.lower() not in SUPPORTED:
                    raise LocalDocumentError("Unsupported document format")
                if len(content) > min(self.settings.local_max_file_bytes, 2_000_000):
                    raise LocalDocumentError("File exceeds the configured import size limit")
                key = relative.casefold()
                if key in names or target.exists():
                    raise LocalDocumentError(
                        "Import paths must be unique and cannot overwrite files"
                    )
                names.add(key)
                for parent in target.parents:
                    if parent == root:
                        break
                    if parent.exists() and not parent.is_dir():
                        raise LocalDocumentError("Import path conflicts with an existing document")
                try:
                    extract_text(content, target.suffix.lower())
                except LocalDocumentError:
                    raise
                except Exception as exc:
                    raise LocalDocumentError("Document could not be extracted") from exc
                targets.append((target, content))
            if any(
                "/".join(name.split("/")[:index]) in names
                for name in names
                for index in range(1, len(name.split("/")))
            ):
                raise LocalDocumentError("Import paths conflict with another document")
            count = 0
            for parent, dirs, existing in os.walk(root, followlinks=False):
                for directory in dirs:
                    self._reject_links(Path(parent) / directory)
                count += len(existing)
                if count + len(targets) > self.settings.local_max_files:
                    raise LocalDocumentError("Import file count exceeds the configured limit")
            written: list[Path] = []
            try:
                for target, content in targets:
                    # Check each component before creation and again before exclusive open.
                    for directory in (*reversed(target.parent.parents), target.parent):
                        self._reject_links(directory)
                        directory.mkdir(exist_ok=True)
                    self._reject_links(target)
                    with target.open("xb") as output:
                        written.append(target)
                        output.write(content)
            except OSError as exc:
                for target in written:
                    target.unlink(missing_ok=True)
                raise LocalDocumentError(
                    "Import could not be saved; no files were imported"
                ) from exc
            return {"uploaded_files": len(targets), "paths": [path for path, _ in files]}

    async def list_local_documents(self, folder: str = "", offset: int = 0) -> dict:
        """Inventory files under the account root; follow pages and read using document_id.

        Paths/names may be redacted. Pass the returned document_id to read_local_document.
        """
        record_tool_call("list_local_documents", {"folder": folder, "offset": offset})
        try:
            result = await asyncio.to_thread(self.manifest, folder, offset)
        except LocalDocumentError as exc:
            result = {"error": {"code": "local_inventory_failed", "message": str(exc)}}
        except OSError:
            result = {
                "error": {
                    "code": "local_inventory_failed",
                    "message": "Local inventory could not be read; check folder permissions.",
                }
            }
        return self.gate.enforce(
            result, source="local:inventory", principal=get_request_context().principal
        )

    async def read_local_document(
        self, path: str = "", offset: int = 0, document_id: str = ""
    ) -> dict:
        """Read PDF/DOCX/PPTX/XLSX/text using document_id from list_local_documents.

        Prefer document_id; legacy path accepts an unmasked relative path. Provide only
        one of them. Follow next_offset using the same document_id and check warnings.
        """
        record_tool_call(
            "read_local_document", {"path": path, "document_id": document_id, "offset": offset}
        )
        try:
            if path and document_id:
                raise LocalDocumentError("Provide document_id or path, not both")
            if not document_id and (
                "[redacted]" in path.casefold() or "[truncated" in path.casefold()
            ):
                result = {
                    "error": {
                        "code": "masked_document_path",
                        "message": "The path was masked by data protection. "
                        "Call list_local_documents and use its document_id; do not guess the path.",
                    }
                }
            else:
                relative = (
                    await asyncio.to_thread(self._path_for_document_id, document_id)
                    if document_id
                    else path
                )
                result = await asyncio.to_thread(self.read, relative, offset)
        except LocalDocumentError as exc:
            result = {"error": {"code": "local_document_read_failed", "message": str(exc)}}
        except OSError:
            result = {
                "error": {
                    "code": "local_document_read_failed",
                    "message": "The local file could not be opened; check access and permissions.",
                }
            }
        return self.gate.enforce(
            result, source="local:document", principal=get_request_context().principal
        )
