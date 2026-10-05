"""Tenant/user-isolated local artifact store with atomic writes."""

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from enterprise_agent.artifacts.models import ArtifactFormat, ArtifactRecord, StoredArtifact
from enterprise_agent.security.identity import Principal


class ArtifactNotFoundError(FileNotFoundError):
    pass


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        principal: Principal,
        title: str,
        artifact_type: ArtifactFormat,
        extension: str,
        media_type: str,
        content: bytes,
    ) -> ArtifactRecord:
        artifact_id = str(uuid4())
        tenant_key, user_key = _owner_keys(principal)
        directory = self.root / tenant_key / user_key / artifact_id
        directory.mkdir(parents=True, exist_ok=False)
        filename = f"{_safe_filename(title)}.{extension}"
        artifact_path = directory / filename
        manifest_path = directory / "manifest.json"
        _atomic_write(artifact_path, content)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            title=title,
            artifact_type=artifact_type,
            filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            download_url=f"/api/artifacts/{artifact_id}/download",
        )
        stored = StoredArtifact(
            record=record,
            tenant_key=tenant_key,
            user_key=user_key,
            content_sha256=hashlib.sha256(content).hexdigest(),
        )
        _atomic_write(manifest_path, stored.model_dump_json(indent=2).encode("utf-8"))
        return record

    def get(self, artifact_id: str, principal: Principal) -> tuple[ArtifactRecord, Path]:
        normalized_id = str(UUID(artifact_id))
        tenant_key, user_key = _owner_keys(principal)
        directory = self.root / tenant_key / user_key / normalized_id
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            raise ArtifactNotFoundError(artifact_id)
        stored = StoredArtifact.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if stored.tenant_key != tenant_key or stored.user_key != user_key:
            raise ArtifactNotFoundError(artifact_id)
        artifact_path = directory / stored.record.filename
        if not artifact_path.is_file():
            raise ArtifactNotFoundError(artifact_id)
        if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != stored.content_sha256:
            raise RuntimeError("Stored artifact integrity check failed")
        return stored.record, artifact_path

    def list(self, principal: Principal) -> list[ArtifactRecord]:
        tenant_key, user_key = _owner_keys(principal)
        owner_root = self.root / tenant_key / user_key
        if not owner_root.is_dir():
            return []
        records: list[ArtifactRecord] = []
        for manifest_path in owner_root.glob("*/manifest.json"):
            try:
                stored = StoredArtifact.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if stored.tenant_key == tenant_key and stored.user_key == user_key:
                records.append(stored.record)
        return sorted(records, key=lambda item: item.created_at, reverse=True)


def _owner_keys(principal: Principal) -> tuple[str, str]:
    return _stable_key(principal.tenant_id), _stable_key(principal.object_id)


def _stable_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _safe_filename(title: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in title)
    slug = "-".join(part for part in slug.split("-") if part)[:80]
    return slug or "artifact"


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)

