from pathlib import Path

import pytest

from enterprise_agent.artifacts.models import ArtifactFormat
from enterprise_agent.artifacts.store import ArtifactNotFoundError, ArtifactStore
from enterprise_agent.security.identity import Principal


def principal(tenant: str, user: str) -> Principal:
    return Principal(
        tenant_id=tenant,
        object_id=user,
        subject=user,
        username=None,
        scopes=frozenset(),
        client_id=None,
        access_token="token",
    )


def test_store_isolates_artifacts_by_verified_owner(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    owner = principal("tenant-a", "user-a")
    other_user = principal("tenant-a", "user-b")
    record = store.save(
        principal=owner,
        title="Project X",
        artifact_type=ArtifactFormat.MARKDOWN,
        extension="md",
        media_type="text/markdown",
        content=b"# Project X\n",
    )

    loaded, path = store.get(record.artifact_id, owner)

    assert loaded == record
    assert path.read_bytes() == b"# Project X\n"
    with pytest.raises(ArtifactNotFoundError):
        store.get(record.artifact_id, other_user)


def test_store_rejects_tampered_content(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    owner = principal("tenant-a", "user-a")
    record = store.save(
        principal=owner,
        title="Project X",
        artifact_type=ArtifactFormat.JSON,
        extension="json",
        media_type="application/json",
        content=b"{}",
    )
    _, path = store.get(record.artifact_id, owner)
    path.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="integrity"):
        store.get(record.artifact_id, owner)

