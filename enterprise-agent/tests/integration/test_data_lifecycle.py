import asyncio
import hashlib

from agent_framework import AgentSession
from fastapi.testclient import TestClient
from sqlalchemy import select

from enterprise_agent.agent.runtime import AgentResult
from enterprise_agent.agent.session import InMemorySessionStore, PersistentSessionStore
from enterprise_agent.api.app import create_app
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import (
    Principal,
    RequestContext,
    reset_request_context,
    set_request_context,
)


class OfflineRuntime:
    async def chat(self, message, session_id, context=None):
        key = context.session_storage_key(session_id)
        async with self._sessions.locked(key) as entry:
            entry.session.state["private"] = message
            await self._sessions.save(key)
        return AgentResult(text="Offline answer", tool_calls=[], usage={"input_token_count": 3})


class Validator:
    async def validate(self, token):
        tenant, user = token.split(":")
        return Principal(tenant, user, user, None, frozenset({"access_as_user"}), "spa", token)


def make_app(tmp_path, **overrides):
    values = dict(
        auth_enabled=True,
        azure_tenant_id="tenant",
        azure_client_id="api",
        enabled_tool_groups="local_read,artifact_generation",
        storage_backend="database",
        database_path=tmp_path / "agent.db",
        local_documents_root=tmp_path / "documents",
        artifact_storage_path=tmp_path / "artifacts",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    values.update(overrides)
    app = create_app(
        settings=Settings(_env_file=None, **values),
        runtime=OfflineRuntime(),
        token_validator=Validator(),
    )
    app.state.agent_runtime._sessions = (
        PersistentSessionStore(app.state.database)
        if app.state.database
        else InMemorySessionStore(AgentSession, app.state.settings)
    )
    return app


def headers(identity="tenant:alice"):
    return {"Authorization": f"Bearer {identity}"}


def populate(client, identity):
    assert (
        client.post(
            "/api/local-documents/upload",
            headers=headers(identity),
            files={"files": ("folder/a.md", b"private")},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/chat", headers=headers(identity), json={"session_id": "same", "message": "hello"}
        ).status_code
        == 200
    )
    result = client.post(
        "/api/artifacts",
        headers=headers(identity),
        json={
            "artifact_type": "markdown",
            "title": "Private",
            "sections": [{"heading": "Data", "body": "private"}],
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["artifact_id"]


def test_upload_conversation_artifact_delete_is_isolated_and_idempotent(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        alice_artifact = populate(client, "tenant:alice")
        other = populate(client, "other:alice")
        populate(client, "tenant:bob")
        principal = asyncio.run(Validator().validate("tenant:alice"))
        app.state.database.put("usage_events", "usage-a", principal.isolation_key, {"tokens": 3}, 0)
        app.state.database.put(
            "actions", "action-a", principal.isolation_key, {"draft": "private"}, 0
        )
        token = set_request_context(RequestContext(principal))
        try:
            app.state.local_document_tools.manifest()
        finally:
            reset_request_context(token)
        for _ in range(2):
            response = client.delete("/api/me/data", headers=headers())
            assert response.status_code == 204, response.text
        owner = hashlib.sha256(principal.isolation_key.encode()).hexdigest()
        with app.state.database.engine.connect() as conn:
            assert not conn.execute(
                select(app.state.database.records).where(
                    app.state.database.records.c.owner == owner
                )
            ).all()
        assert not (tmp_path / "documents" / "_users" / owner).exists()
        assert not app.state.local_document_tools._document_references
        assert (
            client.get(f"/api/artifacts/{alice_artifact}/download", headers=headers()).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/artifacts/{other}/download", headers=headers("other:alice")
            ).status_code
            == 200
        )
        assert len(client.get("/api/sessions", headers=headers("tenant:bob")).json()) == 1
        assert client.get("/api/sessions", headers=headers()).json() == []
        assert not app.state.agent_runtime._sessions._sessions
        assert not app.state.agent_runtime._sessions.versions
        for path in tmp_path.glob("audit*.jsonl"):
            assert owner[:16] not in path.read_text()


def test_interrupted_delete_resumes_after_restart_without_removing_other_owner(
    tmp_path, monkeypatch
):
    import enterprise_agent.storage.lifecycle as module

    app = make_app(tmp_path)
    client = TestClient(app)
    populate(client, "tenant:alice")
    populate(client, "tenant:bob")
    original = module.remove_path
    failed = False

    def interrupted(root, relative):
        nonlocal failed
        if relative.startswith("_users/") and not failed:
            failed = True
            raise OSError("simulated disk failure")
        return original(root, relative)

    monkeypatch.setattr(module, "remove_path", interrupted)
    assert client.delete("/api/me/data", headers=headers()).status_code == 503
    with app.state.database.engine.connect() as conn:
        assert conn.execute(select(app.state.database.cleanup_jobs)).first()
        assert conn.execute(
            select(app.state.database.records).where(
                app.state.database.records.c.owner == app.state.database.key("tenant:alice")
            )
        ).first()
    monkeypatch.setattr(module, "remove_path", original)
    restarted = make_app(tmp_path)
    asyncio.run(restarted.state.lifecycle.sweep())
    with TestClient(restarted) as resumed:
        assert resumed.get("/api/sessions", headers=headers()).json() == []
        assert len(resumed.get("/api/sessions", headers=headers("tenant:bob")).json()) == 1
        assert resumed.delete("/api/me/data", headers=headers()).status_code == 204
    assert len(list((tmp_path / "documents").rglob("*.md"))) == 1


def test_fake_clock_ttl_cleans_all_four_categories_and_preserves_other_users(tmp_path):
    from datetime import UTC, datetime, timedelta

    app = make_app(
        tmp_path,
        retention_enabled=True,
        retention_policy_approval="B5-test-only",
        conversation_ttl_seconds=60,
        session_ttl_seconds=60,
        artifact_ttl_seconds=60,
        upload_ttl_seconds=60,
    )
    now = datetime(2030, 1, 1, tzinfo=UTC)
    app.state.database.clock = lambda: now
    app.state.lifecycle.clock = lambda: now
    client = TestClient(app)
    artifact = populate(client, "tenant:alice")
    now += timedelta(seconds=30)
    populate(client, "tenant:bob")
    now += timedelta(seconds=30)
    asyncio.run(app.state.lifecycle.sweep())
    db = app.state.database
    with db.engine.connect() as conn:
        assert not conn.execute(
            select(db.records).where(db.records.c.owner == db.key("tenant:alice"))
        ).all()
        assert (
            len(
                conn.execute(
                    select(db.records).where(db.records.c.owner == db.key("tenant:bob"))
                ).all()
            )
            == 4
        )
    assert client.get(f"/api/artifacts/{artifact}/download", headers=headers()).status_code == 404
    assert len(client.get("/api/sessions", headers=headers("tenant:bob")).json()) == 1
    assert len(list((tmp_path / "documents").rglob("*.md"))) == 1


def test_memory_backend_erases_sessions_and_history(tmp_path):
    app = make_app(tmp_path, storage_backend="memory")
    client = TestClient(app)
    populate(client, "tenant:alice")
    populate(client, "tenant:bob")
    assert len(app.state.agent_runtime._sessions._sessions) == 2
    assert client.delete("/api/me/data", headers=headers()).status_code == 204
    assert list(app.state.agent_runtime._sessions._sessions) == ["tenant:bob:same"]
    assert list(app.state.conversations._memory) == [("tenant:bob", "same")]


def test_two_instances_serialize_owner_cleanup_with_ongoing_request(tmp_path):
    first, second = make_app(tmp_path), make_app(tmp_path)

    async def exercise():
        principal = await Validator().validate("tenant:alice")
        entered, release = asyncio.Event(), asyncio.Event()
        completed = False

        async def ongoing():
            async with first.state.lifecycle.request(principal):
                entered.set()
                await release.wait()
                await first.state.conversations.append("active", principal, "hi", "answer", [], {})

        async def delete():
            nonlocal completed
            await entered.wait()
            async with second.state.lifecycle.request(principal):
                await second.state.lifecycle.delete_user(principal)
            completed = True

        request_task = asyncio.create_task(ongoing())
        await entered.wait()
        delete_task = asyncio.create_task(delete())
        await asyncio.sleep(0.1)
        assert not completed
        release.set()
        await asyncio.gather(request_task, delete_task)
        assert await first.state.conversations.list(principal) == []

    asyncio.run(exercise())


def test_memory_ttl_uses_fake_clock_for_history_and_framework_session(tmp_path):
    from datetime import UTC, datetime, timedelta

    app = make_app(
        tmp_path,
        storage_backend="memory",
        retention_enabled=True,
        retention_policy_approval="B5-test-only",
        session_ttl_seconds=60,
        conversation_ttl_seconds=60,
    )
    now = datetime(2030, 1, 1, tzinfo=UTC)
    app.state.lifecycle.clock = lambda: now
    app.state.lifecycle.database.clock = lambda: now
    app.state.conversations.clock = lambda: now
    app.state.agent_runtime._sessions.clock = lambda: now
    client = TestClient(app)
    populate(client, "tenant:alice")
    now += timedelta(seconds=60)
    asyncio.run(app.state.lifecycle.sweep())
    assert not app.state.conversations._memory
    assert not app.state.agent_runtime._sessions._sessions


def test_memory_backend_refuses_second_process_or_application_on_same_storage(tmp_path):
    import pytest

    first = make_app(tmp_path, storage_backend="memory")
    second = make_app(tmp_path, storage_backend="memory")
    with (
        TestClient(first),
        pytest.raises(RuntimeError, match="one API process"),
        TestClient(second),
    ):
        pass


def test_cleanup_refuses_to_follow_storage_links(tmp_path):
    import os

    import pytest

    from enterprise_agent.storage.lifecycle import remove_path

    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "keep.md").write_text("other user's source")
    try:
        os.symlink(outside, root / "owner", target_is_directory=True)
    except OSError:
        pytest.skip("Creating filesystem links is unavailable on this host")
    with pytest.raises(OSError, match="links"):
        remove_path(root, "owner")
    assert (outside / "keep.md").is_file()


def test_delete_waits_for_entire_stream_and_removes_its_final_save(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from enterprise_agent.storage.locking import run_local

    app = make_app(tmp_path)
    runtime = app.state.agent_runtime
    started, release = threading.Event(), threading.Event()

    async def stream(message, session_id, context):
        key = context.session_storage_key(session_id)
        async with runtime._sessions.locked(key) as entry:
            entry.session.state["private"] = message
            await runtime._sessions.save(key)
            yield "First part"
            started.set()
            await run_local(release.wait, 10)
            yield "Last part"

    runtime.stream_chat = stream
    with TestClient(app) as client, ThreadPoolExecutor(2) as executor:
        response = executor.submit(
            client.post,
            "/api/chat/stream",
            headers=headers(),
            json={"session_id": "stream", "message": "private"},
        )
        assert started.wait(5)
        deletion = executor.submit(client.delete, "/api/me/data", headers=headers())
        try:
            import time

            time.sleep(0.1)
            assert not deletion.done()
        finally:
            release.set()
        assert response.result(timeout=10).status_code == 200
        assert deletion.result(timeout=10).status_code == 204
        assert client.get("/api/sessions", headers=headers()).json() == []
        assert not runtime._sessions._sessions
        with app.state.database.engine.connect() as conn:
            assert not conn.execute(select(app.state.database.records)).all()


def test_renderer_temporary_copies_are_inside_the_owned_artifact_root(tmp_path, monkeypatch):
    import tempfile
    from pathlib import Path

    from enterprise_agent.artifacts import renderers

    app = make_app(tmp_path)
    client = TestClient(app)
    original = tempfile.TemporaryDirectory
    directories = []

    def checked_directory(*args, **kwargs):
        parent = kwargs.get("dir")
        assert parent is not None, "Renderer must use an owner-scoped workspace"
        assert (tmp_path / "artifacts") in Path(parent).parents
        directories.append(Path(parent))
        return original(*args, **kwargs)

    monkeypatch.setattr(renderers.tempfile, "TemporaryDirectory", checked_directory)
    response = client.post(
        "/api/artifacts",
        headers=headers(),
        json={
            "artifact_type": "docx",
            "title": "Private",
            "sections": [{"heading": "Data", "body": "private"}],
        },
    )
    assert response.status_code == 201, response.text
    assert directories and all(not path.exists() for path in directories)
    assert len(app.state.database.list("artifacts", "tenant:alice")) == 1
    # A process killed mid-render can leave a registered workspace and no manifest.
    with app.state.artifact_service.store.workspace(
        asyncio.run(Validator().validate("tenant:alice"))
    ) as workspace:
        (workspace / "orphan.tmp").write_text("private render copy")
        assert client.delete("/api/me/data", headers=headers()).status_code == 204
        assert not workspace.exists()
