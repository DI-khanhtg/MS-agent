import json
import runpy
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from enterprise_agent.artifacts.models import ArtifactFormat
from enterprise_agent.artifacts.store import ArtifactStore
from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.storage.database import Database
from enterprise_agent.tools.recording import (
    begin_tool_recording,
    end_tool_recording,
    get_recorded_tool_calls,
    record_tool_call,
)

backup = runpy.run_path(str(PROJECT_ROOT / "scripts" / "backup.py"))["backup"]
restore = runpy.run_path(str(PROJECT_ROOT / "scripts" / "restore.py"))["restore"]


def storage_settings(root: Path) -> Settings:
    return Settings(
        _env_file=None, database_path=root / "agent.db", artifact_storage_path=root / "artifacts"
    )


def test_backup_restore_preserves_owner_state_and_completed_artifacts(tmp_path):
    source = storage_settings(tmp_path / "source")
    database = Database(source)
    database.put("conversations", "session", "user-a", {"text": "private data"}, 0)
    database.engine.dispose()
    store = ArtifactStore(source.artifact_storage_path)
    artifact = store.save(
        principal=Principal.anonymous(),
        title="Report",
        artifact_type=ArtifactFormat.MARKDOWN,
        extension="md",
        media_type="text/markdown",
        content=b"# Report\n",
    )
    (source.artifact_storage_path / ".env").write_text("SECRET=do-not-export")
    (source.artifact_storage_path / "pending.tmp").write_text("partial")
    archive = backup(source, tmp_path / "backup.zip")
    with ZipFile(archive) as snapshot:
        assert all(".env" not in name and not name.endswith(".tmp") for name in snapshot.namelist())
    target = storage_settings(tmp_path / "target")
    restore(target, archive)
    restored = Database(target)
    assert restored.get("conversations", "session", "user-a")[0]["text"] == "private data"
    assert restored.get("conversations", "session", "user-b") is None
    restored.engine.dispose()
    _, path = ArtifactStore(target.artifact_storage_path).get(
        artifact.artifact_id, Principal.anonymous()
    )
    assert path.read_bytes() == b"# Report\n"
    with pytest.raises(ValueError, match="empty artifact"):
        restore(target, archive)
    with pytest.raises(FileExistsError):
        backup(source, archive)


@pytest.mark.parametrize(
    "path", ["artifacts/../escaped.txt", "artifacts/C:/escaped.txt", "artifacts/..\\escaped.txt"]
)
def test_restore_rejects_unsafe_archive_without_restoring_records(tmp_path, path):
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as snapshot:
        snapshot.writestr("backup.json", json.dumps({"schema_version": 1}))
        snapshot.writestr("records.json", "[]")
        snapshot.writestr(path, "bad")
    settings = storage_settings(tmp_path / "target")
    with pytest.raises(ValueError, match="Unsafe archive"):
        restore(settings, archive)
    database = Database(settings)
    with database.engine.connect() as conn:
        assert conn.execute(select(database.records)).first() is None
    database.engine.dispose()
    assert not (tmp_path / "escaped.txt").exists()


def test_tool_budget_stops_before_extra_side_effect():
    token = begin_tool_recording(2)
    try:
        record_tool_call("read", {})
        record_tool_call("draft", {})
        with pytest.raises(RuntimeError, match="budget exceeded"):
            record_tool_call("write", {})
        assert [call["name"] for call in get_recorded_tool_calls()] == ["read", "draft"]
    finally:
        end_tool_recording(token)
