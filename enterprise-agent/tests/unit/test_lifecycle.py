from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select

from enterprise_agent.config import Settings
from enterprise_agent.storage.database import Database
from enterprise_agent.storage.lifecycle import RetentionPolicy


def test_policy_requires_approval_and_explicit_duration():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    assert RetentionPolicy(Settings(_env_file=None)).expiry("conversations", now) is None
    with pytest.raises(ValueError, match="approval"):
        Settings(_env_file=None, retention_enabled=True)
    settings = Settings(
        _env_file=None,
        retention_enabled=True,
        retention_policy_approval="B5-test-only",
        conversation_ttl_seconds=60,
    )
    policy = RetentionPolicy(settings)
    assert policy.expiry("conversations", now) == now + timedelta(seconds=60)
    assert policy.expiry("framework_sessions", now) is None


def test_database_expiry_uses_fake_clock_and_preserves_deadline(tmp_path):
    now = datetime(2030, 1, 1, tzinfo=UTC)
    db = Database(
        Settings(
            _env_file=None,
            database_path=tmp_path / "test.db",
            retention_enabled=True,
            retention_policy_approval="B5-test-only",
            conversation_ttl_seconds=60,
        )
    )
    db.clock = lambda: now
    db.put("conversations", "a", "tenant:a", {"messages": []}, 0)
    db.put("conversations", "b", "tenant:b", {}, 0)
    now += timedelta(seconds=30)
    db.put("conversations", "a", "tenant:a", {"messages": ["updated"]}, 1)
    with db.engine.connect() as conn:
        row = (
            conn.execute(select(db.records).where(db.records.c.key == db.key("a"))).mappings().one()
        )
    assert datetime.fromisoformat(row["expires_at"]) == now + timedelta(seconds=30)
    now += timedelta(seconds=30)
    assert db.get("conversations", "a", "tenant:a") is None
    assert db.list("conversations", "tenant:a") == []
    assert "expires_at" in {c["name"] for c in inspect(db.engine).get_columns("agent_records")}


def test_existing_database_migrates_once_and_assigns_framework_owner(tmp_path):
    import json
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE agent_records (namespace VARCHAR(40), key VARCHAR(64), "
            "owner VARCHAR(64), version INTEGER, payload TEXT, updated_at VARCHAR(40), "
            "PRIMARY KEY(namespace, key))"
        )
        conn.execute(
            "INSERT INTO agent_records VALUES (?, ?, ?, ?, ?, ?)",
            (
                "framework_sessions",
                Database.key("tenant:alice:same"),
                Database.key("runtime"),
                1,
                json.dumps({"session_id": "tenant:alice:same"}),
                "2026-01-01T00:00:00+00:00",
            ),
        )
    settings = Settings(_env_file=None, database_path=path)
    db = Database(settings)
    assert db.get("framework_sessions", "tenant:alice:same", "tenant:alice")
    assert not db.get("framework_sessions", "tenant:alice:same", "tenant:bob")
    db.engine.dispose()
    assert Database(settings).get("framework_sessions", "tenant:alice:same", "tenant:alice")


def test_audit_rotation_purges_subject_across_segments_and_applies_fake_clock(tmp_path):
    import json

    from enterprise_agent.telemetry.operations import AuditLog

    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=100, retention_seconds=60)
    now = 1000
    log.clock = lambda: now
    for _ in range(5):
        log.emit(
            "chat_completed",
            subject_hash="alice",
            input_tokens=4,
            document_body="NEVER",
            access_token="SECRET",
            message="PRIVATE",
        )
        log.emit("chat_completed", subject_hash="bob", input_tokens=2)
    assert len(list(tmp_path.glob("*.jsonl"))) > 1
    log.purge_subject("alice")
    records = [
        json.loads(line)
        for path in tmp_path.glob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert records and all(record["subject_hash"] == "bob" for record in records)
    assert all(
        "NEVER" not in path.read_text() and "SECRET" not in path.read_text()
        for path in tmp_path.glob("*.jsonl")
    )
    now += 60
    log.maintain()
    assert all(not path.read_text() for path in tmp_path.glob("*.jsonl"))


def test_audit_purge_removes_interrupted_rewrite_copies(tmp_path):
    from enterprise_agent.telemetry.operations import AuditLog

    (tmp_path / "audit.rewrite").write_text("uncommitted private usage copy")
    (tmp_path / "audit.old.rewrite").write_text("uncommitted private usage copy")
    AuditLog(tmp_path / "audit.jsonl").purge_subject("owner-hash")
    assert not list(tmp_path.glob("*.rewrite"))


@pytest.mark.asyncio
async def test_cancelled_local_writer_finishes_before_releasing_control():
    import asyncio
    import threading

    from enterprise_agent.storage.locking import run_local

    started, release = threading.Event(), threading.Event()
    finished = False

    def write():
        nonlocal finished
        started.set()
        release.wait(5)
        finished = True

    task = asyncio.create_task(run_local(write))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished


def test_request_only_token_cache_does_not_store_delegated_tokens():
    from enterprise_agent.tools.workiq.auth import RequestOnlyTokenCache

    cache = RequestOnlyTokenCache()
    cache.add({"response": {"access_token": "SECRET", "refresh_token": "SECRET"}})
    assert list(cache.search(cache.CredentialType.ACCESS_TOKEN)) == []
    assert list(cache.search(cache.CredentialType.REFRESH_TOKEN)) == []


@pytest.mark.asyncio
async def test_admitted_turn_crossing_deadline_finishes_without_extending_history(tmp_path):
    from enterprise_agent.security.identity import Principal
    from enterprise_agent.storage.conversations import ConversationStore

    now = datetime(2030, 1, 1, tzinfo=UTC)
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "db",
        retention_enabled=True,
        retention_policy_approval="B5-test-only",
        conversation_ttl_seconds=60,
    )
    db = Database(settings)
    db.clock = lambda: now
    store = ConversationStore(db)
    principal = Principal("tenant", "a", "a", None, frozenset(), "spa", "token")
    await store.append("same", principal, "first", "answer", [], {})
    now += timedelta(seconds=61)
    await store.append("same", principal, "finishing admitted turn", "answer", [], {})
    assert await store.get("same", principal) is None
    with db.engine.connect() as conn:
        expiry = conn.execute(select(db.records.c.expires_at)).scalar_one()
    assert datetime.fromisoformat(expiry) == now - timedelta(seconds=1)


@pytest.mark.asyncio
async def test_cancelled_durable_action_does_not_leave_private_cache(tmp_path):
    import asyncio
    import threading

    from enterprise_agent.actions.durable import DurableApprovalStore
    from enterprise_agent.actions.models import ActionType
    from enterprise_agent.security.identity import Principal

    db = Database(Settings(_env_file=None, database_path=tmp_path / "test.db"))
    store = DurableApprovalStore(30, db)
    principal = Principal("tenant", "a", "a", None, frozenset(), "spa", "token")
    started, release = threading.Event(), threading.Event()
    original = db.put

    def blocked(*args):
        started.set()
        release.wait(5)
        return original(*args)

    db.put = blocked
    task = asyncio.create_task(
        store.create(principal, ActionType.SEND_EMAIL, {"body": "private"}, "draft")
    )
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store._items == {}


def test_restore_old_snapshot_reassigns_framework_owner_before_erasure(tmp_path):
    import json
    import runpy
    from zipfile import ZipFile

    from enterprise_agent.config import PROJECT_ROOT

    archive = tmp_path / "old.zip"
    with ZipFile(archive, "w") as snapshot:
        snapshot.writestr("backup.json", json.dumps({"schema_version": 1}))
        snapshot.writestr(
            "records.json",
            json.dumps(
                [
                    {
                        "namespace": "framework_sessions",
                        "key": Database.key("tenant:a:same"),
                        "owner": Database.key("runtime"),
                        "version": 1,
                        "payload": json.dumps({"session_id": "tenant:a:same"}),
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ]
            ),
        )
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "restored.db",
        artifact_storage_path=tmp_path / "artifacts",
    )
    restore = runpy.run_path(str(PROJECT_ROOT / "scripts/restore.py"))["restore"]
    restore(settings, archive)
    db = Database(settings)
    assert db.get("framework_sessions", "tenant:a:same", "tenant:a")
    assert not db.get("framework_sessions", "tenant:a:same", "runtime")
    with db.engine.begin() as conn:
        conn.execute(db.records.delete().where(db.records.c.owner == db.key("tenant:a")))
        assert conn.execute(select(db.records)).first() is None


def test_file_lock_coordinates_independent_processes(tmp_path):
    import subprocess
    import sys

    from enterprise_agent.storage.locking import FileLock

    path = tmp_path / "shared.lock"
    program = (
        "from pathlib import Path; import sys; "
        "from enterprise_agent.storage.locking import FileLock; "
        "lock=FileLock(Path(sys.argv[1])); "
        "assert lock.acquire(blocking=False); "
        "print('locked', flush=True); input(); lock.close()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", program, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lock = FileLock(path)
    try:
        assert process.stdout.readline().strip() == "locked"
        assert not lock.acquire(blocking=False)
        process.communicate("release\n", timeout=10)
        assert process.returncode == 0
        assert lock.acquire(blocking=False)
    finally:
        lock.close()
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.asyncio
async def test_anyio_cancellation_joins_writer_without_abandoning_io():
    import threading

    import anyio

    from enterprise_agent.storage.locking import run_local

    started, release = threading.Event(), threading.Event()
    finished = False
    scope = anyio.CancelScope()

    def write():
        nonlocal finished
        started.set()
        release.wait(5)
        finished = True

    async def writer():
        with scope:
            await run_local(write)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(writer)
        await anyio.to_thread.run_sync(started.wait, 5)
        scope.cancel()
        await anyio.sleep(0.01)
        assert not finished
        release.set()
    assert finished
