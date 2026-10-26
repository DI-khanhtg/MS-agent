import asyncio

import pytest
from agent_framework import AgentSession

from enterprise_agent.actions.durable import DurableApprovalStore
from enterprise_agent.actions.models import ActionStatus, ActionType
from enterprise_agent.actions.store import ActionNotFoundError, ActionStateError
from enterprise_agent.agent.session import PersistentSessionStore
from enterprise_agent.config import Settings
from enterprise_agent.security.identity import Principal
from enterprise_agent.storage.conversations import ConversationStore
from enterprise_agent.storage.database import Database, StorageConflict
from enterprise_agent.telemetry.operations import CircuitBreaker, CircuitOpenError


def principal(user="a"):
    return Principal("tenant", user, user, None, frozenset(), "client", "token-not-persisted")


@pytest.mark.asyncio
async def test_framework_session_restored_without_credentials(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "test.db")
    db = Database(settings)
    store = PersistentSessionStore(db)
    entry = await store.get_or_create("tenant:user:session")
    entry.session.state["conversation"] = {"last_message": "hello"}
    await store.save("tenant:user:session")
    restored = await PersistentSessionStore(db).get_or_create("tenant:user:session")
    assert isinstance(restored.session, AgentSession)
    assert restored.session.state["conversation"]["last_message"] == "hello"
    assert await PersistentSessionStore(db).get_or_create("tenant:other:session")
    assert "token-not-persisted" not in settings.database_path.read_bytes().decode(errors="ignore")


@pytest.mark.asyncio
async def test_legacy_conversation_migrates_without_losing_history(tmp_path):
    db = Database(Settings(_env_file=None, database_path=tmp_path / "test.db"))
    owner = principal()
    db.put(
        "conversations",
        "old-id",
        owner.isolation_key,
        {"session_id": "old-id", "messages": [], "turns": 4},
        0,
    )
    store = ConversationStore(db)
    assert (await store.get("old-id", owner))["turns"] == 4
    await store.append("old-id", owner, "hello", "answer", [], {})
    assert (await store.get("old-id", owner))["turns"] == 5
    await store.append("old-id", principal("b"), "separate", "answer", [], {})
    assert (await store.get("old-id", principal("b")))["turns"] == 1


@pytest.mark.asyncio
async def test_approval_restart_owner_isolation_and_atomic_execution(tmp_path):
    db = Database(Settings(_env_file=None, database_path=tmp_path / "test.db"))
    first = DurableApprovalStore(30, db)
    record = await first.create(principal(), ActionType.SEND_EMAIL, {"subject": "test"}, "test")
    restarted = DurableApprovalStore(30, db)
    assert (await restarted.get(record.action_id, principal())).status == ActionStatus.DRAFT
    with pytest.raises(ActionNotFoundError):
        await restarted.get(record.action_id, principal("b"))
    await restarted.request_approval(record.action_id, principal())
    await restarted.approve(record.action_id, principal())
    results = await asyncio.gather(
        first.claim_execution(record.action_id, principal()),
        restarted.claim_execution(record.action_id, principal()),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ActionStateError) for r in results) == 1
    assert sum(getattr(r, "status", None) == ActionStatus.EXECUTING for r in results) == 1


def test_database_compare_and_swap(tmp_path):
    db = Database(Settings(_env_file=None, database_path=tmp_path / "test.db"))
    db.put("test", "id", "a", {"n": 1}, 0)
    assert db.get("test", "id", "b") is None
    with pytest.raises(StorageConflict):
        db.put("test", "id", "a", {"n": 2}, 0)


def test_circuit_breaker_stops_calls_and_recovers():
    circuit = CircuitBreaker(threshold=2, reset_seconds=0.01)
    circuit.failure()
    circuit.failure()
    with pytest.raises(CircuitOpenError):
        circuit.check()
    circuit.open_until = 0.0001
    circuit.check()
    with pytest.raises(CircuitOpenError):
        circuit.check()
    circuit.success()
    circuit.check()
