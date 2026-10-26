import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from enterprise_agent.agent.runtime import AgentRequestTimeout, MicrosoftAgentRuntime
from enterprise_agent.agent.session import PersistentSessionStore
from enterprise_agent.config import Settings
from enterprise_agent.storage.database import Database
from enterprise_agent.tools.fabric.client import FabricDataAgentClient
from enterprise_agent.tools.workiq.client import WorkIQMCPClient


class RecoveryAgent:
    def __init__(self):
        self.calls = 0
        self.slow = False

    def create_session(self, **kwargs):
        return {}

    def run(self, message, *, session, stream=False):
        self.calls += 1
        return self.updates() if stream else self.answer()

    async def answer(self):
        if self.slow:
            await asyncio.sleep(10)
        return SimpleNamespace(text="done", usage_details={})

    async def updates(self):
        yield await self.answer()


async def invoke(runtime, stream):
    if stream:
        return "".join([part async for part in runtime.stream_chat("hello", "session")])
    return (await runtime.chat("hello", "session")).text


def config(**kwargs):
    return Settings(
        _env_file=None, agent_turn_timeout_seconds=0.1, agent_synthesis_reserve_seconds=0, **kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_session_recovers_when_deadline_expires_during_database_save(
    tmp_path, monkeypatch, stream
):
    settings = config(database_path=tmp_path / "state.db")
    database = Database(settings)
    agent = RecoveryAgent()
    runtime = MicrosoftAgentRuntime(settings, agent=agent)
    runtime._sessions = PersistentSessionStore(database)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_put = database.put
    first = True

    def delayed_put(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            started.set()
            release.wait(3)
        try:
            return original_put(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(database, "put", delayed_put)
    try:
        first_turn = asyncio.create_task(invoke(runtime, stream))
        assert await asyncio.to_thread(started.wait, 2)
        with pytest.raises(AgentRequestTimeout):
            await first_turn
        # A second turn must not mutate the session while its previous commit is in flight.
        second_turn = asyncio.create_task(invoke(runtime, stream))
        await asyncio.sleep(0.02)
        assert agent.calls == 1
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        assert await second_turn == "done"
        assert runtime._sessions.versions["anonymous:anonymous:session"] == 2
        assert await invoke(runtime, stream) == "done"
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
        if "second_turn" in locals():
            await asyncio.gather(second_turn, return_exceptions=True)
        database.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["chat", "stream", "workiq", "fabric"])
async def test_cancelled_half_open_probe_does_not_disable_future_requests(path, monkeypatch):
    settings = config(
        circuit_failure_threshold=1,
        circuit_reset_seconds=0.01,
        fabric_workspace_id="22222222-2222-2222-2222-222222222222",
        fabric_data_agent_id="33333333-3333-3333-3333-333333333333",
    )
    if path in {"chat", "stream"}:
        agent = RecoveryAgent()
        agent.slow = True
        runtime = MicrosoftAgentRuntime(settings, agent=agent)
        circuit = runtime._circuit

        async def operation():
            return await invoke(runtime, path == "stream")

        def recover():
            agent.slow = False
    else:
        client = WorkIQMCPClient(settings) if path == "workiq" else FabricDataAgentClient(settings)
        circuit = client.circuit
        slow = True

        async def network(*args, **kwargs):
            if slow:
                await asyncio.sleep(10)
            return {"ok": True}

        monkeypatch.setattr(client, "_call_once" if path == "workiq" else "_query_once", network)

        async def operation():
            if path == "workiq":
                return await client.call_tool(
                    "fetch", {"entityUrls": ["/me/messages"]}, access_token="x"
                )
            return await client.query("Project costs", access_token="x")

        def recover():
            nonlocal slow
            slow = False

    circuit.failure()
    circuit.open_until = time.monotonic() - 1
    with pytest.raises((AgentRequestTimeout, TimeoutError)):
        async with asyncio.timeout(0.15 if path in {"chat", "stream"} else 0.03):
            await operation()
    recover()
    await asyncio.sleep(0.02)
    assert await operation()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_fails", [False, True])
async def test_delete_waits_for_cancelled_save_and_does_not_restore_history(
    tmp_path,
    monkeypatch,
    save_fails,
):
    database = Database(config(database_path=tmp_path / "state.db"))
    store = PersistentSessionStore(database)
    entry = await store.get_or_create("session")
    entry.session.state["private-history"] = "must be deleted"
    await store.save("session")
    started, release = threading.Event(), threading.Event()
    original_put = database.put

    def delayed_put(*args):
        started.set()
        release.wait(3)
        if save_fails:
            raise OSError("previous session write failed")
        return original_put(*args)

    monkeypatch.setattr(database, "put", delayed_put)
    try:
        save = asyncio.create_task(store.save("session"))
        assert await asyncio.to_thread(started.wait, 2)
        save.cancel()
        with pytest.raises(asyncio.CancelledError):
            await save
        deletion = asyncio.create_task(store.delete("session"))
        await asyncio.sleep(0.02)
        assert not deletion.done()
        release.set()
        assert await deletion
        assert database.get("framework_sessions", "session", "runtime") is None
        assert store.count == 0 and "session" not in store.versions
        fresh = await store.get_or_create("session")
        assert "private-history" not in fresh.session.state
    finally:
        release.set()
        if "deletion" in locals():
            await asyncio.gather(deletion, return_exceptions=True)
        database.engine.dispose()


@pytest.mark.asyncio
async def test_turn_queued_behind_delete_uses_fresh_session(tmp_path):
    settings = config(database_path=tmp_path / "state.db")
    database = Database(settings)

    class HistoryAgent(RecoveryAgent):
        def run(self, message, *, session, stream=False):
            async def reply():
                text = "leaked" if "private-history" in session.state else "clean"
                return SimpleNamespace(text=text, usage_details={})

            return reply()

    runtime = MicrosoftAgentRuntime(settings, agent=HistoryAgent())
    store = PersistentSessionStore(database)
    runtime._sessions = store
    key = "anonymous:anonymous:session"
    entry = await store.get_or_create(key)
    entry.session.state["private-history"] = "must be deleted"
    await store.save(key)
    try:
        async with entry.lock:
            deletion = asyncio.create_task(store.delete(key))
            await asyncio.sleep(0)
            following = asyncio.create_task(invoke(runtime, False))
            await asyncio.sleep(0)
        assert await deletion
        assert await following == "clean"
    finally:
        database.engine.dispose()
